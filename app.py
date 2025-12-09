from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import json
from ytmusicapi import YTMusic
from innertube import InnerTube
from concurrent.futures import ThreadPoolExecutor
import time
import random
from urllib.parse import parse_qs, unquote
from collections import defaultdict
import requests

app = FastAPI(title="Pure YouTube Music API - All-in-One Production")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== FIXED YTMUSIC INITIALIZATION ====================
# Use requests_session to set custom headers for all requests
def create_session_with_headers():
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": "2.20240917.01.00",
        "X-Goog-Visitor-Id": "CgtQYjhWVjNHSFB2SSi3griuBjIKCgJVUxIEGgAgOw%3D%3D",
        "Origin": "https://music.youtube.com",
        "Referer": "https://music.youtube.com/",
    }
    session.headers.update(headers)
    return session

custom_session = create_session_with_headers()
ytmusic = YTMusic(requests_session=custom_session)  # Correct way: no auth, use session for headers
yt_web = InnerTube("WEB")
yt_android = InnerTube("ANDROID_MUSIC", "7.12.33")

executor = ThreadPoolExecutor(max_workers=20)

# Audio cache
audio_cache = {}
cache_timestamps = {}
CACHE_TTL = 14400  # 4 hours

# Rate limiting
request_tracker = defaultdict(list)
RATE_LIMIT_REQUESTS = 25
RATE_LIMIT_WINDOW = 60

def is_cache_valid(video_id: str) -> bool:
    return video_id in cache_timestamps and (time.time() - cache_timestamps[video_id]) < CACHE_TTL

async def check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    request_tracker[client_ip] = [t for t in request_tracker[client_ip] if now - t < RATE_LIMIT_WINDOW]
    if len(request_tracker[client_ip]) >= RATE_LIMIT_REQUESTS:
        return False
    request_tracker[client_ip].append(now)
    return True

def decode_signature_cipher(cipher_string: str) -> str | None:
    try:
        params = parse_qs(cipher_string)
        url = params.get("url", [None])[0]
        if url:
            if "signature" in cipher_string:
                s = params.get("s", [""])[0]
                sp = params.get("sp", ["sig"])[0]
                return f"{unquote(url)}&{sp}={s}"
            return unquote(url)
        return None
    except Exception:
        return None

def get_audio_url_sync(video_id: str, retry: bool = True) -> str | None:
    if video_id in audio_cache and is_cache_valid(video_id):
        return audio_cache[video_id]

    try:
        time.sleep(random.uniform(0.005, 0.015))
        player = yt_android.player(video_id)
        streaming_data = player.get("streamingData", {})

        # Try adaptiveFormats first
        formats = streaming_data.get("adaptiveFormats", [])
        audio_formats = [f for f in formats if f.get("mimeType", "").startswith("audio/")]

        if audio_formats:
            # Prefer lowest bitrate AAC for faster loading
            best = min(audio_formats, key=lambda x: x.get("bitrate", float('inf')))
            url = best.get("url")
            if not url:
                url = decode_signature_cipher(best.get("signatureCipher", "") or best.get("cipher", ""))
            if url:
                audio_cache[video_id] = url
                cache_timestamps[video_id] = time.time()
                return url

        # Fallback to regular formats
        formats = streaming_data.get("formats", [])
        audio_formats = [f for f in formats if f.get("mimeType", "").startswith("audio/")]
        if audio_formats:
            best = min(audio_formats, key=lambda x: x.get("bitrate", float('inf')))
            url = best.get("url")
            if not url:
                url = decode_signature_cipher(best.get("signatureCipher", "") or best.get("cipher", ""))
            if url:
                audio_cache[video_id] = url
                cache_timestamps[video_id] = time.time()
                return url

    except Exception as e:
        print(f"Error fetching audio for {video_id}: {e}")
        if retry:
            time.sleep(random.uniform(0.3, 0.7))
            return get_audio_url_sync(video_id, retry=False)
    return None

async def get_audio_url(video_id: str) -> str | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, get_audio_url_sync, video_id)

# ==================== SEARCH STREAM ====================
async def search_stream(q: str, filter: str, limit: int):
    try:
        results = await asyncio.get_event_loop().run_in_executor(
            executor, lambda: ytmusic.search(q, filter=filter or None, limit=limit)
        )
    except Exception as e:
        yield f"event: message\ndata: {json.dumps({'error': str(e)})}\n\n"
        return

    sent_count = 0
    video_ids_to_fetch = []

    for item in results:
        if filter in ["songs", "videos"]:
            if item.get("videoId"):
                video_ids_to_fetch.append(item["videoId"])
                sent_count += 1
                yield f"event: message\ndata: {json.dumps(item)}\n\n"
        else:
            sent_count += 1
            yield f"event: message\ndata: {json.dumps(item)}\n\n"
        await asyncio.sleep(0)

    # Fetch audio URLs in background for songs/videos
    if filter in ["songs", "videos"] and video_ids_to_fetch:
        for vid in video_ids_to_fetch:
            url = await get_audio_url(vid)
            if url:
                yield f"event: message\ndata: {json.dumps({'type': 'audio_update', 'videoId': vid, 'audioUrl': url})}\n\n"
            await asyncio.sleep(0.01)

    yield f"event: message\ndata: {json.dumps({'_done': True, 'count': sent_count})}\n\n"

# ==================== ENDPOINTS ====================
@app.get("/search")
async def search(request: Request, q: str, filter: str = "songs", limit: int = 50):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    limit = max(1, min(limit, 100))
    return StreamingResponse(search_stream(q, filter, limit), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/search/songs")
async def search_songs(request: Request, q: str, limit: int = 50):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    limit = max(1, min(limit, 100))
    return StreamingResponse(search_stream(q, "songs", limit), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/search/videos")
async def search_videos(request: Request, q: str, limit: int = 50):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    limit = max(1, min(limit, 100))
    return StreamingResponse(search_stream(q, "videos", limit), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/search/albums")
async def search_albums(request: Request, q: str, limit: int = 30):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    results = await asyncio.get_event_loop().run_in_executor(
        executor, lambda: ytmusic.search(q, filter="albums", limit=limit)
    )
    return {"query": q, "albums": results, "count": len(results)}

@app.get("/search/artists")
async def search_artists(request: Request, q: str, limit: int = 20):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    results = await asyncio.get_event_loop().run_in_executor(
        executor, lambda: ytmusic.search(q, filter="artists", limit=limit)
    )
    return {"query": q, "artists": results, "count": len(results)}

@app.get("/search/playlists")
async def search_playlists(request: Request, q: str, limit: int = 30):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    results = await asyncio.get_event_loop().run_in_executor(
        executor, lambda: ytmusic.search(q, filter="playlists", limit=limit)
    )
    return {"query": q, "playlists": results, "count": len(results)}

@app.get("/audio/{video_id}")
async def audio(request: Request, video_id: str):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    url = await get_audio_url(video_id)
    return {
        "videoId": video_id,
        "audioUrl": url,
        "cached": is_cache_valid(video_id)
    }

@app.get("/song/{video_id}")
async def song(request: Request, video_id: str):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})

    song_task = asyncio.get_event_loop().run_in_executor(executor, lambda: ytmusic.get_song(video_id))
    audio_task = get_audio_url(video_id)
    song_data, audio_url = await asyncio.gather(song_task, audio_task)
    if audio_url:
        song_data["audioUrl"] = audio_url
    return song_data

@app.get("/playlist/{browse_id}")
async def get_playlist(request: Request, browse_id: str, audio: bool = True):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    playlist_data = await asyncio.get_event_loop().run_in_executor(executor, lambda: ytmusic.get_playlist(browse_id))
    if audio:
        for track in playlist_data.get("tracks", []):
            if vid := track.get("videoId"):
                track["audioUrl"] = await get_audio_url(vid)
    return playlist_data

@app.get("/album/{browse_id}")
async def album(request: Request, browse_id: str, audio: bool = True):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    album_data = await asyncio.get_event_loop().run_in_executor(executor, lambda: ytmusic.get_album(browse_id))
    if audio:
        for track in album_data.get("tracks", []):
            if vid := track.get("videoId"):
                track["audioUrl"] = await get_audio_url(vid)
    return album_data

@app.get("/artist/{channel_id}")
async def artist(request: Request, channel_id: str, audio: bool = True):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    artist_data = await asyncio.get_event_loop().run_in_executor(executor, lambda: ytmusic.get_artist(channel_id))
    if audio and artist_data.get("songs"):
        for song in artist_data["songs"].get("results", []):
            if vid := song.get("videoId"):
                song["audioUrl"] = await get_audio_url(vid)
    return artist_data

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "cache_size": len(audio_cache),
        "cache_ttl_hours": CACHE_TTL / 3600,
        "active_workers": executor._max_workers
    }

@app.get("/")
async def root():
    return {"service": "Pure YouTube Music API", "version": "9.2", "status": "running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False, workers=1, log_level="info")
