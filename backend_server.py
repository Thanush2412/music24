from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import StreamingResponse
import uvicorn
from innertube import InnerTube
import json
import asyncio
import requests
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import time

app = FastAPI()

yt_web = InnerTube("WEB")
yt_android = InnerTube("ANDROID")
yt_music = InnerTube("WEB_MUSIC")

# Thread pool for parallel audio URL fetching
executor = ThreadPoolExecutor(max_workers=10)

# Global queue and cache management
play_queue = {}
search_cache = {}
trending_cache = {"data": None, "timestamp": 0}


def get_audio_url_fast(video_id):
    """Fast audio URL fetch - returns immediately if fails"""
    try:
        player = yt_android.player(video_id)
        data = player.get("streamingData", {})
        adaptive = data.get("adaptiveFormats", [])
        audio = [a for a in adaptive if "audio" in a.get("mimeType", "")]
        if not audio:
            return None
        best = max(audio, key=lambda x: x.get("bitrate", 0))
        return best.get("url")
    except:
        return None


def extract_video_data(video_renderer, fetch_audio=True):
    """Extract video data - optionally skip audio for speed"""
    try:
        vid = video_renderer.get("videoId")
        title = video_renderer.get("title", {}).get("runs", [{}])[0].get("text", "")
        
        # Author/artist
        author = ""
        if "longBylineText" in video_renderer:
            author = video_renderer["longBylineText"].get("runs", [{}])[0].get("text", "")
        elif "ownerText" in video_renderer:
            author = video_renderer["ownerText"].get("runs", [{}])[0].get("text", "")
        
        # Thumbnail
        thumb = video_renderer.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", "")
        
        # Duration
        duration = video_renderer.get("lengthText", {}).get("simpleText", "")
        
        # Views
        views = ""
        if "viewCountText" in video_renderer:
            views = video_renderer["viewCountText"].get("simpleText", "")
        
        # Published time
        published = ""
        if "publishedTimeText" in video_renderer:
            published = video_renderer["publishedTimeText"].get("simpleText", "")
        
        # Audio URL - fetch only if requested
        audio = None
        if fetch_audio:
            audio = get_audio_url_fast(vid)
        
        return {
            "type": "video",
            "title": title,
            "videoId": vid,
            "artist": author,
            "thumbnail": thumb,
            "duration": duration,
            "views": views,
            "published": published,
            "audioUrl": audio
        }
    except:
        return None


def extract_playlist_data(playlist_renderer):
    """Extract playlist data"""
    try:
        playlist_id = playlist_renderer.get("playlistId")
        title = playlist_renderer.get("title", {}).get("simpleText", "")
        
        thumb = playlist_renderer.get("thumbnails", [{}])[0].get("thumbnails", [{}])[-1].get("url", "")
        video_count = playlist_renderer.get("videoCount", "")
        
        # Channel info
        channel = ""
        if "longBylineText" in playlist_renderer:
            channel = playlist_renderer["longBylineText"].get("runs", [{}])[0].get("text", "")
        
        first_video = None
        videos = playlist_renderer.get("videos", [])
        if videos and "childVideoRenderer" in videos[0]:
            first_video = videos[0]["childVideoRenderer"].get("videoId")
        
        return {
            "type": "playlist",
            "title": title,
            "playlistId": playlist_id,
            "thumbnail": thumb,
            "videoCount": video_count,
            "channel": channel,
            "firstVideoId": first_video
        }
    except:
        return None


def extract_channel_data(channel_renderer):
    """Extract channel/artist data"""
    try:
        channel_id = channel_renderer.get("channelId")
        title = channel_renderer.get("title", {}).get("simpleText", "")
        
        thumb = channel_renderer.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", "")
        
        subscribers = ""
        if "subscriberCountText" in channel_renderer:
            subscribers = channel_renderer["subscriberCountText"].get("simpleText", "")
        
        video_count = ""
        if "videoCountText" in channel_renderer:
            video_count = channel_renderer["videoCountText"].get("runs", [{}])[0].get("text", "")
        
        return {
            "type": "channel",
            "title": title,
            "channelId": channel_id,
            "thumbnail": thumb,
            "subscribers": subscribers,
            "videoCount": video_count
        }
    except:
        return None


def extract_all_content(response, fetch_audio=True):
    """Extract all content types"""
    content = []
    continuation = None
    
    def search_data(data):
        nonlocal continuation
        
        if isinstance(data, dict):
            if "videoRenderer" in data:
                item = extract_video_data(data["videoRenderer"], fetch_audio)
                if item:
                    content.append(item)
            
            elif "playlistRenderer" in data:
                item = extract_playlist_data(data["playlistRenderer"])
                if item:
                    content.append(item)
            
            elif "channelRenderer" in data:
                item = extract_channel_data(data["channelRenderer"])
                if item:
                    content.append(item)
            
            if "continuationCommand" in data and not continuation:
                token = data.get("continuationCommand", {}).get("token")
                if token:
                    continuation = token
            
            if "continuation" in data and not continuation and isinstance(data["continuation"], str):
                continuation = data["continuation"]
            
            for value in data.values():
                search_data(value)
        
        elif isinstance(data, list):
            for item in data:
                search_data(item)
    
    search_data(response)
    return content, continuation


async def ultra_fast_search_stream(query: str):
    """
    ULTRA FAST - First result in <100ms, rest stream instantly
    """
    
    print(f"\n⚡ FAST SEARCH: {query}")
    
    total_count = 0
    page = 1
    
    # Initial search - NO audio URLs for speed
    start = time.time()
    response = yt_web.search(query)
    
    all_content, continuation = extract_all_content(response, fetch_audio=False)
    
    print(f"✓ Found {len(all_content)} items in {(time.time()-start)*1000:.0f}ms")
    
    # Stream ALL results from page 1 INSTANTLY (no audio URLs yet)
    for item in all_content:
        total_count += 1
        yield f"data: {json.dumps(item)}\n\n"
        await asyncio.sleep(0)  # Yield control but don't wait
    
    # Now fetch audio URLs in background and send updates
    for item in all_content:
        if item["type"] == "video" and not item["audioUrl"]:
            audio = await asyncio.get_event_loop().run_in_executor(
                executor, get_audio_url_fast, item["videoId"]
            )
            if audio:
                update = {
                    "type": "audio_update",
                    "videoId": item["videoId"],
                    "audioUrl": audio
                }
                yield f"data: {json.dumps(update)}\n\n"
    
    # Load more pages
    seen_tokens = set()
    
    while continuation and page < 10:
        if continuation in seen_tokens:
            break
        seen_tokens.add(continuation)
        
        page += 1
        await asyncio.sleep(0.2)
        
        try:
            response = requests.post(
                url="https://www.youtube.com/youtubei/v1/search",
                params={"key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"},
                json={
                    "context": {
                        "client": {
                            "clientName": "WEB",
                            "clientVersion": "2.20231219.01.00"
                        }
                    },
                    "continuation": continuation
                },
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0"
                },
                timeout=30
            )
            
            if response.status_code != 200:
                break
            
            data = response.json()
            content, continuation = extract_all_content(data, fetch_audio=False)
            
            if not content:
                break
            
            # Stream new items
            for item in content:
                total_count += 1
                yield f"data: {json.dumps(item)}\n\n"
                await asyncio.sleep(0)
            
            # Fetch audio URLs
            for item in content:
                if item["type"] == "video":
                    audio = await asyncio.get_event_loop().run_in_executor(
                        executor, get_audio_url_fast, item["videoId"]
                    )
                    if audio:
                        update = {
                            "type": "audio_update",
                            "videoId": item["videoId"],
                            "audioUrl": audio
                        }
                        yield f"data: {json.dumps(update)}\n\n"
                
        except Exception as e:
            print(f"❌ {e}")
            break
    
    print(f"✅ {total_count} items from {page} pages\n")
    yield f"data: {json.dumps({'_done': True, 'total': total_count, 'pages': page})}\n\n"


@app.get("/search")
async def search_ultra_fast(q: str):
    """
    🚀 ULTRA FAST search - first result in <100ms
    Returns items instantly, audio URLs follow
    """
    return StreamingResponse(
        ultra_fast_search_stream(q),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*"
        }
    )


@app.get("/video/{video_id}")
async def get_video_details(video_id: str):
    """Get full video details with audio URL"""
    try:
        player = yt_android.player(video_id)
        
        # Video info
        details = player.get("videoDetails", {})
        
        # Audio URL
        audio_url = get_audio_url_fast(video_id)
        
        # Related videos
        response = yt_web.next(video_id)
        related = []
        
        def find_related(data):
            if isinstance(data, dict):
                if "compactVideoRenderer" in data:
                    v = data["compactVideoRenderer"]
                    related.append({
                        "videoId": v.get("videoId"),
                        "title": v.get("title", {}).get("simpleText", ""),
                        "thumbnail": v.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", "")
                    })
                for value in data.values():
                    find_related(value)
            elif isinstance(data, list):
                for item in data:
                    find_related(item)
        
        find_related(response)
        
        return {
            "videoId": video_id,
            "title": details.get("title"),
            "author": details.get("author"),
            "duration": details.get("lengthSeconds"),
            "views": details.get("viewCount"),
            "thumbnail": details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url"),
            "audioUrl": audio_url,
            "related": related[:10]
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/playlist/{playlist_id}")
async def get_playlist(playlist_id: str):
    """Get playlist with all videos"""
    try:
        response = yt_web.browse(f"VL{playlist_id}")
        
        videos = []
        def extract_playlist_videos(data):
            if isinstance(data, dict):
                if "playlistVideoRenderer" in data:
                    renderer = data["playlistVideoRenderer"]
                    videos.append({
                        "videoId": renderer.get("videoId"),
                        "title": renderer.get("title", {}).get("runs", [{}])[0].get("text", ""),
                        "thumbnail": renderer.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", ""),
                        "duration": renderer.get("lengthText", {}).get("simpleText", "")
                    })
                for value in data.values():
                    extract_playlist_videos(value)
            elif isinstance(data, list):
                for item in data:
                    extract_playlist_videos(item)
        
        extract_playlist_videos(response)
        
        return {"playlistId": playlist_id, "videos": videos, "count": len(videos)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/channel/{channel_id}")
async def get_channel(channel_id: str):
    """Get channel/artist info and videos"""
    try:
        response = yt_web.browse(channel_id)
        
        # Extract videos
        videos = []
        def extract_channel_videos(data):
            if isinstance(data, dict):
                if "gridVideoRenderer" in data or "videoRenderer" in data:
                    renderer = data.get("gridVideoRenderer") or data.get("videoRenderer")
                    videos.append({
                        "videoId": renderer.get("videoId"),
                        "title": renderer.get("title", {}).get("runs", [{}])[0].get("text", ""),
                        "thumbnail": renderer.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", "")
                    })
                for value in data.values():
                    extract_channel_videos(value)
            elif isinstance(data, list):
                for item in data:
                    extract_channel_videos(item)
        
        extract_channel_videos(response)
        
        return {"channelId": channel_id, "videos": videos[:30], "count": len(videos)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/trending")
async def get_trending():
    """Get trending music videos (cached for 1 hour)"""
    
    # Check cache
    if trending_cache["data"] and (time.time() - trending_cache["timestamp"]) < 3600:
        return trending_cache["data"]
    
    try:
        response = yt_web.browse("FEmusic_trending")
        
        videos = []
        def extract_trending(data):
            if isinstance(data, dict):
                if "videoRenderer" in data:
                    item = extract_video_data(data["videoRenderer"], fetch_audio=False)
                    if item:
                        videos.append(item)
                for value in data.values():
                    extract_trending(value)
            elif isinstance(data, list):
                for item in data:
                    extract_trending(item)
        
        extract_trending(response)
        
        result = {"trending": videos[:50], "count": len(videos)}
        trending_cache["data"] = result
        trending_cache["timestamp"] = time.time()
        
        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/recommendations/{video_id}")
async def get_recommendations(video_id: str):
    """Get recommended/related videos"""
    try:
        response = yt_web.next(video_id)
        
        related = []
        def find_related(data):
            if isinstance(data, dict):
                if "compactVideoRenderer" in data:
                    item = extract_video_data(data["compactVideoRenderer"], fetch_audio=False)
                    if item:
                        related.append(item)
                for value in data.values():
                    find_related(value)
            elif isinstance(data, list):
                for item in data:
                    find_related(item)
        
        find_related(response)
        
        return {"videoId": video_id, "recommendations": related[:20]}
    except Exception as e:
        return {"error": str(e)}


@app.post("/queue/create")
async def create_queue(session_id: str, video_ids: list[str]):
    """Create play queue"""
    play_queue[session_id] = {
        "queue": video_ids,
        "current_index": 0,
        "history": [],
        "shuffle": False,
        "repeat": "off"  # off, one, all
    }
    return {"status": "created", "queue_size": len(video_ids)}


@app.post("/queue/add")
async def add_to_queue(session_id: str, video_id: str, position: Optional[int] = None):
    """Add to queue at specific position"""
    if session_id not in play_queue:
        play_queue[session_id] = {"queue": [], "current_index": 0, "history": [], "shuffle": False, "repeat": "off"}
    
    if position is not None:
        play_queue[session_id]["queue"].insert(position, video_id)
    else:
        play_queue[session_id]["queue"].append(video_id)
    
    return {"status": "added", "queue_size": len(play_queue[session_id]["queue"])}


@app.get("/queue/next/{session_id}")
async def next_in_queue(session_id: str):
    """Next song"""
    if session_id not in play_queue:
        return {"error": "Queue not found"}
    
    queue = play_queue[session_id]
    
    # Handle repeat one
    if queue.get("repeat") == "one":
        video_id = queue["queue"][queue["current_index"]]
    else:
        if queue["current_index"] < len(queue["queue"]) - 1:
            queue["current_index"] += 1
        elif queue.get("repeat") == "all":
            queue["current_index"] = 0
        else:
            return {"status": "queue_finished"}
        
        video_id = queue["queue"][queue["current_index"]]
    
    audio_url = get_audio_url_fast(video_id)
    
    return {
        "videoId": video_id,
        "audioUrl": audio_url,
        "queue_position": queue["current_index"],
        "queue_size": len(queue["queue"])
    }


@app.get("/queue/previous/{session_id}")
async def previous_in_queue(session_id: str):
    """Previous song"""
    if session_id not in play_queue:
        return {"error": "Queue not found"}
    
    queue = play_queue[session_id]
    
    if queue["current_index"] > 0:
        queue["current_index"] -= 1
        video_id = queue["queue"][queue["current_index"]]
        audio_url = get_audio_url_fast(video_id)
        
        return {
            "videoId": video_id,
            "audioUrl": audio_url,
            "queue_position": queue["current_index"]
        }
    
    return {"error": "Already at first song"}


@app.post("/queue/shuffle/{session_id}")
async def toggle_shuffle(session_id: str):
    """Toggle shuffle"""
    if session_id not in play_queue:
        return {"error": "Queue not found"}
    
    play_queue[session_id]["shuffle"] = not play_queue[session_id].get("shuffle", False)
    
    return {"shuffle": play_queue[session_id]["shuffle"]}


@app.post("/queue/repeat/{session_id}")
async def set_repeat(session_id: str, mode: str):
    """Set repeat mode: off, one, all"""
    if session_id not in play_queue:
        return {"error": "Queue not found"}
    
    if mode not in ["off", "one", "all"]:
        return {"error": "Invalid mode"}
    
    play_queue[session_id]["repeat"] = mode
    
    return {"repeat": mode}


@app.get("/queue/status/{session_id}")
async def queue_status(session_id: str):
    """Queue status"""
    if session_id not in play_queue:
        return {"error": "Queue not found"}
    
    queue = play_queue[session_id]
    
    return {
        "current_index": queue["current_index"],
        "queue_size": len(queue["queue"]),
        "queue": queue["queue"],
        "shuffle": queue.get("shuffle", False),
        "repeat": queue.get("repeat", "off"),
        "current_video": queue["queue"][queue["current_index"]] if queue["current_index"] < len(queue["queue"]) else None
    }


@app.get("/audio/{video_id}")
async def get_audio_url(video_id: str):
    """Get just the audio URL quickly"""
    audio = get_audio_url_fast(video_id)
    return {"videoId": video_id, "audioUrl": audio}


@app.get("/")
async def root():
    return {
        "service": "⚡ Ultra Fast YouTube Music API",
        "features": "Instant search, Playlists, Queue, Trending, Recommendations",
        "endpoints": {
            "search": "GET /search?q=QUERY - Ultra fast streaming search",
            "video": "GET /video/{video_id} - Full video details",
            "playlist": "GET /playlist/{playlist_id} - Playlist videos",
            "channel": "GET /channel/{channel_id} - Channel videos",
            "trending": "GET /trending - Trending music",
            "recommendations": "GET /recommendations/{video_id} - Related videos",
            "audio": "GET /audio/{video_id} - Quick audio URL",
            "queue_create": "POST /queue/create",
            "queue_add": "POST /queue/add",
            "queue_next": "GET /queue/next/{session_id}",
            "queue_prev": "GET /queue/previous/{session_id}",
            "queue_shuffle": "POST /queue/shuffle/{session_id}",
            "queue_repeat": "POST /queue/repeat/{session_id}?mode=off|one|all",
            "queue_status": "GET /queue/status/{session_id}"
        }
    }


if __name__ == "__main__":
    uvicorn.run("backend_server:app", host="0.0.0.0", port=8000, reload=True)
