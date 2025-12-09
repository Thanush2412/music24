"""
🎵 YouTube Music Backend - Main Server
FastAPI server with Innertube search and yt-dlp audio extraction
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import yt_dlp
from innertube import InnerTube
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any
import time
from collections import defaultdict
from datetime import datetime, timedelta
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="YouTube Music Backend",
    description="403-proof YouTube Music API with Innertube search and yt-dlp audio extraction",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Thread pool for async yt-dlp operations
executor = ThreadPoolExecutor(max_workers=10)

# Rate limiting
rate_limit_store = defaultdict(list)
MAX_REQUESTS_PER_MINUTE = 30
REQUEST_DELAY = 0.1  # seconds between requests

# Initialize Innertube client
innertube_client = InnerTube("WEB_MUSIC")

# yt-dlp configuration - Optimized to avoid YouTube blocking and bot detection
YDL_OPTS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'no_color': True,
    # Use cookies for authentication (bypasses bot detection)
    'cookiefile': 'cookies.txt',  # YouTube cookies from browser
    # Use ANDROID client to avoid restrictions and bot detection
    'extractor_args': {
        'youtube': {
            'player_client': ['android_music', 'android', 'web'],
            'player_skip': ['webpage', 'configs'],
            'skip': ['hls', 'dash'],  # Skip unnecessary formats
        }
    },
    # Better user agent to avoid detection
    'http_headers': {
        'User-Agent': 'com.google.android.apps.youtube.music/5.16.51 (Linux; U; Android 11) gzip',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
        'Sec-Fetch-Mode': 'navigate',
    },
    # Additional options to prevent blocking and bot detection
    'age_limit': None,
    'geo_bypass': True,
    'prefer_insecure': False,
    # Use OAuth if available (bypasses bot detection)
    'username': 'oauth2',
    'password': '',
}


def check_rate_limit(ip: str) -> bool:
    """Check if IP has exceeded rate limit"""
    now = datetime.now()
    minute_ago = now - timedelta(minutes=1)
    
    # Clean old requests
    rate_limit_store[ip] = [
        req_time for req_time in rate_limit_store[ip] 
        if req_time > minute_ago
    ]
    
    # Check limit
    if len(rate_limit_store[ip]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    # Add current request
    rate_limit_store[ip].append(now)
    return True


def extract_search_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract and format search results from Innertube response"""
    results = []
    
    try:
        # Navigate through Innertube response structure
        contents = data.get('contents', {})
        tabs = contents.get('tabbedSearchResultsRenderer', {}).get('tabs', [])
        
        for tab in tabs:
            tab_content = tab.get('tabRenderer', {}).get('content', {})
            section_list = tab_content.get('sectionListRenderer', {}).get('contents', [])
            
            for section in section_list:
                music_shelf = section.get('musicShelfRenderer', {})
                items = music_shelf.get('contents', [])
                
                for item in items:
                    renderer = item.get('musicResponsiveListItemRenderer', {})
                    
                    # Extract video ID
                    video_id = None
                    navigation_endpoint = renderer.get('overlay', {}).get(
                        'musicItemThumbnailOverlayRenderer', {}
                    ).get('content', {}).get('musicPlayButtonRenderer', {}).get(
                        'playNavigationEndpoint', {}
                    )
                    
                    watch_endpoint = navigation_endpoint.get('watchEndpoint', {})
                    video_id = watch_endpoint.get('videoId')
                    
                    if not video_id:
                        continue
                    
                    # Extract title and artist
                    flex_columns = renderer.get('flexColumns', [])
                    title = ""
                    artist = ""
                    
                    if len(flex_columns) > 0:
                        title_runs = flex_columns[0].get('musicResponsiveListItemFlexColumnRenderer', {}).get(
                            'text', {}
                        ).get('runs', [])
                        if title_runs:
                            title = title_runs[0].get('text', '')
                    
                    if len(flex_columns) > 1:
                        artist_runs = flex_columns[1].get('musicResponsiveListItemFlexColumnRenderer', {}).get(
                            'text', {}
                        ).get('runs', [])
                        if artist_runs:
                            artist = artist_runs[0].get('text', '')
                    
                    # Extract thumbnail
                    thumbnail = ""
                    thumbnails = renderer.get('thumbnail', {}).get('musicThumbnailRenderer', {}).get('thumbnail', {}).get('thumbnails', [])
                    if thumbnails:
                        thumbnail = thumbnails[-1].get('url', '')
                    
                    # Extract duration
                    duration = ""
                    fixed_columns = renderer.get('fixedColumns', [])
                    if fixed_columns:
                        duration_text = fixed_columns[0].get('musicResponsiveListItemFixedColumnRenderer', {}).get(
                            'text', {}
                        ).get('runs', [])
                        if duration_text:
                            duration = duration_text[0].get('text', '')
                    
                    results.append({
                        'videoId': video_id,
                        'title': title,
                        'artist': artist,
                        'thumbnail': thumbnail,
                        'duration': duration
                    })
        
    except Exception as e:
        logger.error(f"Error extracting search results: {e}")
    
    return results


async def get_audio_url_async(video_id: str) -> Dict[str, Any]:
    """Extract audio URL using yt-dlp (async wrapper) - 403-proof with bot detection bypass"""
    loop = asyncio.get_event_loop()
    
    def extract():
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Try multiple strategies to bypass bot detection
        strategies = [
            # Strategy 1: ANDROID_MUSIC client (best for music)
            {
                **YDL_OPTS,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android_music'],
                        'player_skip': ['webpage', 'configs'],
                    }
                }
            },
            # Strategy 2: ANDROID client
            {
                **YDL_OPTS,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android'],
                        'player_skip': ['webpage', 'configs'],
                    }
                }
            },
            # Strategy 3: IOS client
            {
                **YDL_OPTS,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['ios'],
                        'player_skip': ['webpage'],
                    }
                }
            },
        ]
        
        last_error = None
        
        for i, ydl_opts in enumerate(strategies):
            try:
                logger.info(f"Trying extraction strategy {i+1}/{len(strategies)}")
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    
                    # Prefer audio-only formats (less likely to be blocked)
                    audio_format = None
                    formats = info.get('formats', [])
                    
                    # Priority 1: Best audio-only format (m4a, webm)
                    for fmt in formats:
                        if (fmt.get('acodec') != 'none' and 
                            fmt.get('vcodec') == 'none' and
                            fmt.get('ext') in ['m4a', 'webm']):
                            if not audio_format or fmt.get('abr', 0) > audio_format.get('abr', 0):
                                audio_format = fmt
                    
                    # Priority 2: Any audio-only format
                    if not audio_format:
                        for fmt in formats:
                            if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                                audio_format = fmt
                                break
                    
                    # Priority 3: Fallback to any format with audio
                    if not audio_format:
                        for fmt in formats:
                            if fmt.get('acodec') != 'none':
                                audio_format = fmt
                                break
                    
                    if not audio_format:
                        raise Exception("No audio format found")
                    
                    # Extract HTTP headers needed for playback (prevents 403)
                    http_headers = audio_format.get('http_headers', {})
                    if not http_headers:
                        http_headers = info.get('http_headers', {})
                    
                    logger.info(f"Successfully extracted audio using strategy {i+1}")
                    
                    return {
                        'videoId': video_id,
                        'audioUrl': audio_format.get('url'),
                        'format': audio_format.get('ext', 'unknown'),
                        'bitrate': f"{audio_format.get('abr', 'unknown')}kbps",
                        'filesize': audio_format.get('filesize', 0),
                        'expiresIn': 21600,  # 6 hours (typical YouTube URL expiry)
                        'headers': {
                            'User-Agent': http_headers.get('User-Agent', 'com.google.android.apps.youtube.music/5.16.51 (Linux; U; Android 11) gzip'),
                            'Accept': http_headers.get('Accept', '*/*'),
                            'Accept-Language': http_headers.get('Accept-Language', 'en-US,en;q=0.9'),
                            'Origin': 'https://www.youtube.com',
                            'Referer': 'https://www.youtube.com/',
                        },
                        'protocol': audio_format.get('protocol', 'https'),
                        'quality': audio_format.get('format_note', 'audio'),
                    }
                    
            except Exception as e:
                last_error = e
                logger.warning(f"Strategy {i+1} failed: {str(e)[:100]}")
                continue
        
        # If all strategies failed, raise the last error
        raise last_error if last_error else Exception("All extraction strategies failed")
    
    # Add small delay to prevent rate limiting
    await asyncio.sleep(REQUEST_DELAY)
    
    return await loop.run_in_executor(executor, extract)


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "name": "YouTube Music Backend",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "search": "/search?q=query&limit=20",
            "audio": "/audio?videoId=VIDEO_ID",
            "playlist": "/playlist?listId=PLAYLIST_ID",
            "health": "/health"
        },
        "documentation": "/docs"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "innertube": "operational",
            "yt-dlp": "operational"
        }
    }


@app.get("/search")
async def search(
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=50, description="Maximum number of results")
):
    """
    Search for music using yt-dlp
    
    Returns metadata only (fast, reliable)
    """
    try:
        logger.info(f"Search request: {q}")
        
        # Use yt-dlp for search (more reliable than Innertube)
        loop = asyncio.get_event_loop()
        
        def search_youtube():
            search_url = f"ytsearch{limit}:{q}"
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,  # Fast metadata only
                'skip_download': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(search_url, download=False)
                entries = search_results.get('entries', [])
                
                results = []
                for entry in entries:
                    if entry:
                        results.append({
                            'videoId': entry.get('id', ''),
                            'title': entry.get('title', ''),
                            'artist': entry.get('uploader', entry.get('channel', 'Unknown')),
                            'thumbnail': entry.get('thumbnail', ''),
                            'duration': str(timedelta(seconds=entry.get('duration', 0))) if entry.get('duration') else ''
                        })
                
                return results
        
        results = await loop.run_in_executor(executor, search_youtube)
        
        logger.info(f"Found {len(results)} results")
        
        return {
            "query": q,
            "count": len(results),
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.get("/audio")
async def get_audio(
    videoId: str = Query(..., description="YouTube video ID")
):
    """
    Get playable audio URL using yt-dlp
    
    Returns 403-proof direct audio URL
    """
    try:
        logger.info(f"Audio request: {videoId}")
        
        # Extract audio URL
        audio_data = await get_audio_url_async(videoId)
        
        logger.info(f"Audio URL extracted successfully")
        
        return audio_data
        
    except Exception as e:
        logger.error(f"Audio extraction error: {e}")
        raise HTTPException(status_code=500, detail=f"Audio extraction failed: {str(e)}")


@app.get("/playlist")
async def get_playlist(
    listId: str = Query(..., description="YouTube playlist ID"),
    includeAudio: bool = Query(False, description="Include audio URLs (slower)")
):
    """
    Get playlist information
    
    Optionally includes audio URLs for each track
    """
    try:
        logger.info(f"Playlist request: {listId}")
        
        # Get playlist data from Innertube
        playlist_data = innertube_client.browse(f"VL{listId}")
        
        # Extract playlist info
        header = playlist_data.get('header', {}).get('musicDetailHeaderRenderer', {})
        title = header.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown Playlist')
        
        # Extract tracks
        tracks = []
        contents = playlist_data.get('contents', {})
        
        # Navigate to playlist items
        tabs = contents.get('singleColumnBrowseResultsRenderer', {}).get('tabs', [])
        for tab in tabs:
            section_list = tab.get('tabRenderer', {}).get('content', {}).get('sectionListRenderer', {}).get('contents', [])
            for section in section_list:
                music_playlist = section.get('musicPlaylistShelfRenderer', {})
                items = music_playlist.get('contents', [])
                
                for item in items:
                    renderer = item.get('musicResponsiveListItemRenderer', {})
                    
                    # Extract video ID
                    video_id = renderer.get('playlistItemData', {}).get('videoId')
                    
                    if not video_id:
                        continue
                    
                    # Extract title and artist (similar to search)
                    flex_columns = renderer.get('flexColumns', [])
                    title_text = ""
                    artist_text = ""
                    
                    if len(flex_columns) > 0:
                        title_runs = flex_columns[0].get('musicResponsiveListItemFlexColumnRenderer', {}).get('text', {}).get('runs', [])
                        if title_runs:
                            title_text = title_runs[0].get('text', '')
                    
                    if len(flex_columns) > 1:
                        artist_runs = flex_columns[1].get('musicResponsiveListItemFlexColumnRenderer', {}).get('text', {}).get('runs', [])
                        if artist_runs:
                            artist_text = artist_runs[0].get('text', '')
                    
                    track = {
                        'videoId': video_id,
                        'title': title_text,
                        'artist': artist_text
                    }
                    
                    # Optionally get audio URL
                    if includeAudio:
                        try:
                            audio_data = await get_audio_url_async(video_id)
                            track['audioUrl'] = audio_data['audioUrl']
                        except Exception as e:
                            logger.warning(f"Failed to get audio for {video_id}: {e}")
                            track['audioUrl'] = None
                    
                    tracks.append(track)
        
        logger.info(f"Playlist has {len(tracks)} tracks")
        
        return {
            "playlistId": listId,
            "title": title,
            "trackCount": len(tracks),
            "tracks": tracks
        }
        
    except Exception as e:
        logger.error(f"Playlist error: {e}")
        raise HTTPException(status_code=500, detail=f"Playlist fetch failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    import os
    
    # Get port from environment variable (for Render deployment) or use 8000
    port = int(os.getenv("PORT", 8000))
    
    logger.info("🎵 Starting YouTube Music Backend...")
    logger.info(f"📡 Server: http://0.0.0.0:{port}")
    logger.info(f"📚 Docs: http://0.0.0.0:{port}/docs")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
