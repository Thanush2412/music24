"""
Unofficial YouTube Music Backend Server
Returns real song data with playable audio links.

Install:
    pip install fastapi uvicorn ytmusicapi yt-dlp

Run:
    python backend_server.py
    
Note: For best results, export cookies from your browser to cookies.txt
See: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
"""

from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os

# ============== YouTube Music Backend ==============

class YouTubeMusicBackend:
    """Backend that fetches real data from YouTube Music."""
    
    def __init__(self):
        self.ytmusic = YTMusic()  # No auth needed for search
        self.executor = ThreadPoolExecutor(max_workers=4)
    
    def search(self, query: str, filter_type: str = "songs", limit: int = 20) -> List[Dict[str, Any]]:
        """Search YouTube Music and return real results."""
        try:
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
        except Exception as e:
            raise Exception(f"Search failed: {str(e)}")
    
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
        """Return audio URL (m4a) using yt-dlp with cookies."""
        try:
            # Build yt-dlp options
            ydl_opts = {
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "quiet": True,
                "no_warnings": True,
            }
            
            # Add cookies if file exists (helps avoid bot detection)
            cookies_path = "cookies.txt"
            if os.path.exists(cookies_path):
                ydl_opts["cookies"] = cookies_path
            
            # Use full YouTube URL
            url = f"https://www.youtube.com/watch?v={video_id}"
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                # Get the direct stream URL
                audio_url = info.get("url")
                if not audio_url:
                    raise Exception("No audio URL found in response")
                
                return {
                    "video_id": video_id,
                    "url": audio_url,
                    "title": info.get("title", "Unknown"),
                    "artist": info.get("artist") or info.get("uploader", "Unknown"),
                    "thumbnail": info.get("thumbnail", ""),
                    "duration_seconds": int(info.get("duration", 0)),
                }
        except Exception as e:
            raise Exception(f"Stream not available: {str(e)}")
    
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
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=50, description="Number of results")
) -> List[Dict[str, Any]]:
    """Search YouTube Music and return top results."""
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, backend.search, q, "songs", limit)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/song/{video_id}", tags=["music"])
async def get_song(video_id: str) -> Dict[str, Any]:
    """Get song details."""
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id required")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, backend.get_song_details, video_id)


@app.get("/audio/{video_id}", tags=["music"])
async def get_audio(video_id: str) -> Dict[str, Any]:
    """Return audio URL (m4a) using yt-dlp."""
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id required")
    
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, backend.get_audio_url, video_id)
        # Return simplified format matching the working example
        return {
            "url": result.get("url"),
            "title": result.get("title", "Unknown"),
            "video_id": video_id,
            "artist": result.get("artist", ""),
            "thumbnail": result.get("thumbnail", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Stream not available: {str(e)}")


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
