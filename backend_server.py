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

app = FastAPI(title="Pure YouTube Music API - All-in-One Production")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global clients
ytmusic = YTMusic(headers_raw="""
user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36
accept: */*
accept-language: en-US,en;q=0.9
x-youtube-client-name: 1
x-youtube-client-version: 2.20240917.01.00
x-goog-visitor-id: CgtQYjhWVjNHSFB2SSi3griuBjIKCgJVUxIEGgAgOw%3D%3D
""")
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

def decode_signature_cipher(cipher_string: str) -> str:
    try:
        params = parse_qs(cipher_string)
        return unquote(params.get('url', [''])[0])
    except:
        return None

def get_audio_url_sync(video_id: str, retry: bool = True) -> str:
    if video_id in audio_cache and is_cache_valid(video_id):
        return audio_cache[video_id]
    try:
        time.sleep(random.uniform(0.005, 0.015))
        player = yt_android.player(video_id)
        streaming = player.get("streamingData", {})
        formats = streaming.get("adaptiveFormats", [])
        audio_formats = [f for f in formats if "audio" in f.get("mimeType", "")]
        if not audio_formats:
            return None
        best_audio = min(audio_formats, key=lambda x: x.get("bitrate", 0))  # low-latency AAC
        url = best_audio.get("url") or decode_signature_cipher(best_audio.get("signatureCipher", ""))
        if url:
            audio_cache[video_id] = url
            cache_timestamps[video_id] = time.time()
        return url
    except:
        if retry:
            time.sleep(random.uniform(0.2, 0.5))
            return get_audio_url_sync(video_id, retry=False)
        return None

async def get_audio_url(video_id: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, get_audio_url_sync, video_id)

# -------------------- SEARCH STREAM --------------------
async def search_stream(q: str, filter: str, limit: int):
    results = await asyncio.get_event_loop().run_in_executor(
        executor, lambda: ytmusic.search(q, filter=filter, limit=limit)
    )
    valid_results = []
    for item in results:
        if filter in ["songs", "videos"] and item.get("videoId"):
            valid_results.append(item)
            yield "event: message\n"
            yield f"data: {json.dumps(item)}\n\n"
        elif filter not in ["songs", "videos"]:
            valid_results.append(item)
            yield "event: message\n"
            yield f"data: {json.dumps(item)}\n\n"
        await asyncio.sleep(0)
    if filter in ["songs", "videos"] and valid_results:
        video_ids = [item.get("videoId") for item in valid_results if item.get("videoId")]
        for vid in video_ids:
            await asyncio.sleep(random.uniform(0.005, 0.015))
            url = await get_audio_url(vid)
            if url:
                yield "event: message\n"
                yield f"data: {json.dumps({'type':'audio_update','videoId':vid,'audioUrl':url})}\n\n"
    yield "event: message\n"
    yield f"data: {json.dumps({'_done': True, 'count': len(valid_results)})}\n\n"

# -------------------- ENDPOINTS --------------------
@app.get("/search")
async def search(request: Request, q: str, filter: str = "songs", limit: int = 50):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    limit = max(1, min(limit, 100))
    return StreamingResponse(search_stream(q, filter, limit), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.get("/search/songs")
async def search_songs(request: Request, q: str, limit: int = 50):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    limit = max(1, min(limit, 100))
    return StreamingResponse(search_stream(q, "songs", limit), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.get("/search/videos")
async def search_videos(request: Request, q: str, limit: int = 50):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    limit = max(1, min(limit, 100))
    return StreamingResponse(search_stream(q, "videos", limit), media_type="text/event-stream",
                             headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

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

# -------------------- AUDIO --------------------
@app.get("/audio/{video_id}")
async def audio(request: Request, video_id: str):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    url = await get_audio_url(video_id)
    return {"videoId": video_id, "audioUrl": url, "cached": video_id in audio_cache and is_cache_valid(video_id)}

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
            vid = track.get("videoId")
            if vid:
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
            vid = track.get("videoId")
            if vid:
                track["audioUrl"] = await get_audio_url(vid)
    return album_data

@app.get("/artist/{channel_id}")
async def artist(request: Request, channel_id: str, audio: bool = True):
    client_ip = request.client.host
    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})
    artist_data = await asyncio.get_event_loop().run_in_executor(executor, lambda: ytmusic.get_artist(channel_id))
    if audio:
        for song in artist_data.get("songs", {}).get("results", []):
            vid = song.get("videoId")
            if vid:
                song["audioUrl"] = await get_audio_url(vid)
    return artist_data

@app.get("/health")
async def health_check():
    return {"status": "healthy", "cache_size": len(audio_cache), "cache_ttl": f"{CACHE_TTL/3600} hours"}

@app.get("/")
async def root():
    return {"service": "Pure YouTube Music API - Production All-in-One", "version": "9.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
