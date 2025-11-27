"""
Unofficial YouTube Music Backend Server
Returns real song data with playable audio links.

Install:
    pip install fastapi uvicorn ytmusicapi yt-dlp

Run:
    python backend_server.py
"""

from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp
import asyncio
from concurrent.futures import ThreadPoolExecutor

# ============== YouTube Music Backend ==============

class YouTubeMusicBackend:
    """Backend that fetches real data from YouTube Music."""
    
    def __init__(self):
        self.ytmusic = YTMusic()
        self.executor = ThreadPoolExecutor(max_workers=4)
    
    def search(self, query: str, filter_type: str = "songs", limit: int = 10) -> List[Dict[str, Any]]:
        """Search YouTube Music and return real results."""
        results = self.ytmusic.search(query, filter=filter_type, limit=limit)
        
        songs = []
        for item in results:
            song = {
                "title": item.get("title", "Unknown"),
                "video_id": item.get("videoId", ""),
                "artist": ", ".join([a.get("name", "") for a in item.get("artists", [])]) if item.get("artists") else "Unknown",
                "album": item.get("album", {}).get("name", "") if item.get("album") else "",
                "duration": item.get("duration", ""),
                "duration_seconds": item.get("duration_seconds", 0),
                "thumbnail": item.get("thumbnails", [{}])[-1].get("url", "") if item.get("thumbnails") else "",
                "is_explicit": item.get("isExplicit", False),
                "year": item.get("year", ""),
            }
            songs.append(song)
        
        return songs
    
    def get_song_details(self, video_id: str) -> Dict[str, Any]:
        """Get detailed song information."""
        try:
            song = self.ytmusic.get_song(video_id)
            video_details = song.get("videoDetails", {})
            
            return {
                "video_id": video_id,
                "title": video_details.get("title", "Unknown"),
                "artist": video_details.get("author", "Unknown"),
                "duration_seconds": int(video_details.get("lengthSeconds", 0)),
                "thumbnail": video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", ""),
                "view_count": video_details.get("viewCount", "0"),
                "description": video_details.get("shortDescription", ""),
                "is_live": video_details.get("isLiveContent", False),
                "channel_id": video_details.get("channelId", ""),
            }
        except Exception as e:
            return {"error": str(e), "video_id": video_id}
    
    def get_audio_url(self, video_id: str) -> Dict[str, Any]:
        """Extract playable audio URL using yt-dlp (old method - play video as audio)."""
        import time
        
        # Get song details first using ytmusicapi
        try:
            song = self.ytmusic.get_song(video_id)
            video_details = song.get("videoDetails", {})
            title = video_details.get("title", "Unknown")
            artist = video_details.get("author", "Unknown")
            thumbnail = video_details.get("thumbnail", {}).get("thumbnails", [{}])[-1].get("url", "")
            duration_seconds = int(video_details.get("lengthSeconds", 0))
        except:
            title = "Unknown"
            artist = "Unknown"
            thumbnail = ""
            duration_seconds = 0
        
        # Use yt-dlp to extract audio stream URLs
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Try different yt-dlp configurations
        configs = [
            {
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "referer": "https://www.youtube.com/",
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android"],
                        "player_skip": ["webpage"],
                    }
                },
            },
            {
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                "referer": "https://www.youtube.com/",
                "extractor_args": {
                    "youtube": {
                        "player_client": ["ios"],
                        "player_skip": ["webpage"],
                    }
                },
            },
        ]
        
        for attempt, ydl_opts in enumerate(configs):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    
                    # Get best audio format
                    audio_url = None
                    audio_formats = []
                    
                    for f in info.get("formats", []):
                        if f.get("acodec") != "none" and f.get("vcodec") == "none":
                            stream_url = f.get("url")
                            if stream_url and stream_url.startswith("http"):
                                audio_formats.append({
                                    "url": stream_url,
                                    "ext": f.get("ext", "m4a"),
                                    "bitrate": f.get("abr", 0),
                                    "format": str(f.get("format_id", "")),
                                })
                    
                    # Sort by bitrate and get best
                    audio_formats.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                    
                    if audio_formats:
                        audio_url = audio_formats[0]["url"]
                    else:
                        # Fallback to best available format
                        audio_url = info.get("url")
                    
                    if audio_url and audio_url.startswith("http"):
                        return {
                            "video_id": video_id,
                            "title": info.get("title", title),
                            "artist": info.get("artist") or info.get("uploader", artist),
                            "album": info.get("album", ""),
                            "duration_seconds": int(info.get("duration", duration_seconds)),
                            "thumbnail": info.get("thumbnail", thumbnail),
                            "audio_url": audio_url,
                            "audio_formats": audio_formats[:3],
                            "genre": info.get("genre", ""),
                            "upload_date": info.get("upload_date", ""),
                            "view_count": info.get("view_count", 0),
                            "like_count": info.get("like_count", 0),
                        }
            except Exception as e:
                error_msg = str(e)
                # Check if it's a bot detection error
                if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                    if attempt < len(configs) - 1:
                        # Wait before retry with different config
                        time.sleep(2)
                        continue
                    else:
                        # Last attempt failed - raise error so React Native can use Piped
                        raise Exception("YouTube bot detection. React Native will use Piped API fallback.")
                else:
                    # Other error - try next config
                    if attempt < len(configs) - 1:
                        continue
                    raise
        
        raise Exception("Failed to extract audio after all attempts")
    
    def get_playlist(self, playlist_id: str) -> Dict[str, Any]:
        """Get playlist with all tracks."""
        try:
            playlist = self.ytmusic.get_playlist(playlist_id, limit=100)
            
            tracks = []
            for track in playlist.get("tracks", []):
                tracks.append({
                    "title": track.get("title", "Unknown"),
                    "video_id": track.get("videoId", ""),
                    "artist": ", ".join([a.get("name", "") for a in track.get("artists", [])]) if track.get("artists") else "Unknown",
                    "album": track.get("album", {}).get("name", "") if track.get("album") else "",
                    "duration": track.get("duration", ""),
                    "thumbnail": track.get("thumbnails", [{}])[-1].get("url", "") if track.get("thumbnails") else "",
                })
            
            return {
                "playlist_id": playlist_id,
                "title": playlist.get("title", "Unknown Playlist"),
                "description": playlist.get("description", ""),
                "author": playlist.get("author", {}).get("name", "Unknown"),
                "track_count": len(tracks),
                "tracks": tracks,
            }
        except Exception as e:
            return {"error": str(e), "playlist_id": playlist_id}
    
    def get_artist(self, channel_id: str) -> Dict[str, Any]:
        """Get artist information and top songs."""
        try:
            artist = self.ytmusic.get_artist(channel_id)
            
            top_songs = []
            for song in artist.get("songs", {}).get("results", [])[:10]:
                top_songs.append({
                    "title": song.get("title", "Unknown"),
                    "video_id": song.get("videoId", ""),
                    "album": song.get("album", {}).get("name", "") if song.get("album") else "",
                })
            
            return {
                "channel_id": channel_id,
                "name": artist.get("name", "Unknown"),
                "description": artist.get("description", ""),
                "subscriber_count": artist.get("subscribers", ""),
                "thumbnail": artist.get("thumbnails", [{}])[-1].get("url", "") if artist.get("thumbnails") else "",
                "top_songs": top_songs,
            }
        except Exception as e:
            return {"error": str(e), "channel_id": channel_id}


# ============== FastAPI App ==============

app = FastAPI(
    title="YouTube Music Backend",
    description="Returns real song data with playable audio links",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

backend = YouTubeMusicBackend()


@app.get("/", tags=["info"])
async def root() -> Dict[str, Any]:
    """API endpoints overview."""
    return {
        "name": "YouTube Music Backend",
        "version": "2.0.0",
        "endpoints": {
            "search": "/search?query=YOUR_QUERY&limit=10",
            "song_details": "/song/{video_id}",
            "audio_url": "/audio/{video_id}",
            "playlist": "/playlist/{playlist_id}",
            "artist": "/artist/{channel_id}",
        }
    }


@app.get("/search", tags=["music"])
async def search(
    query: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50, description="Number of results")
) -> Dict[str, Any]:
    """Search for songs and return real results."""
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, backend.search, query, "songs", limit)
    return {
        "query": query,
        "count": len(results),
        "results": results
    }


@app.get("/song/{video_id}", tags=["music"])
async def get_song(video_id: str) -> Dict[str, Any]:
    """Get song details."""
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id required")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, backend.get_song_details, video_id)


@app.get("/audio/{video_id}", tags=["music"])
async def get_audio(video_id: str) -> Dict[str, Any]:
    """Get playable audio URL for a song."""
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id required")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, backend.get_audio_url, video_id)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=str(e)
        )


@app.get("/playlist/{playlist_id}", tags=["music"])
async def get_playlist(playlist_id: str) -> Dict[str, Any]:
    """Get playlist with tracks."""
    if not playlist_id:
        raise HTTPException(status_code=400, detail="playlist_id required")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, backend.get_playlist, playlist_id)


@app.get("/artist/{channel_id}", tags=["music"])
async def get_artist(channel_id: str) -> Dict[str, Any]:
    """Get artist info and top songs."""
    if not channel_id:
        raise HTTPException(status_code=400, detail="channel_id required")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, backend.get_artist, channel_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend_server:app", host="0.0.0.0", port=8000, reload=True)
