from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from ytmusicapi import YTMusic
import asyncio
import subprocess
import json
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import time

app = FastAPI(title="Music API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Maximum parallelism for batch processing
executor = ThreadPoolExecutor(max_workers=50)

# Initialize YTMusic client
ytmusic = YTMusic()

async def audio_url_from_id(video_id: str, timeout: int = 8) -> Optional[str]:
    """Get direct audio URL with aggressive timeout"""
    cmd = [
        'yt-dlp',
        '--no-warnings',
        '--no-playlist',
        '--format', 'bestaudio/best',
        '--get-url',
        '--socket-timeout', '5',
        '--no-check-certificate',
        '--extract-audio',
        f'https://music.youtube.com/watch?v={video_id}'
    ]
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                executor,
                partial(subprocess.run, cmd, capture_output=True, text=True, timeout=timeout)
            ),
            timeout=timeout + 1
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except Exception as e:
        print(f"Audio fetch error for {video_id}: {e}")
        return None

def get_thumbnail(thumbnails: List[Dict], quality: str = "high") -> Optional[str]:
    """Get thumbnail URL of specified quality"""
    if not thumbnails:
        return None
    try:
        if len(thumbnails) > 0:
            url = thumbnails[-1].get('url', '')
            return url.split('=')[0] + '=w720-h720-l90-rj' if url else None
        return thumbnails[0].get('url', '') if thumbnails else None
    except:
        return None

def parse_song(item: Dict) -> Optional[Dict]:
    """Parse song/video item from YTMusic"""
    try:
        video_id = item.get('videoId')
        if not video_id:
            return None
            
        artists = item.get('artists', [])
        artist_name = artists[0].get('name') if artists else None
        artist_id = artists[0].get('id') if artists else None
        
        album = item.get('album')
        album_name = album.get('name') if album else None
        album_id = album.get('id') if album else None
        
        return {
            "type": "song",
            "video_id": video_id,
            "title": item.get('title', 'Unknown'),
            "artist": artist_name,
            "artist_id": artist_id,
            "album": album_name,
            "album_id": album_id,
            "duration": item.get('duration'),
            "duration_seconds": item.get('duration_seconds'),
            "thumbnail": get_thumbnail(item.get('thumbnails', [])),
            "year": item.get('year'),
            "is_explicit": item.get('isExplicit', False),
            "audio_url": None,
            "audio_loading": True
        }
    except Exception as e:
        print(f"Error parsing song: {e}")
        return None

def parse_album(item: Dict) -> Optional[Dict]:
    """Parse album item"""
    try:
        browse_id = item.get('browseId')
        if not browse_id:
            return None
            
        artists = item.get('artists', [])
        artist_name = artists[0].get('name') if artists else None
        artist_id = artists[0].get('id') if artists else None
        
        return {
            "type": "album",
            "browse_id": browse_id,
            "title": item.get('title', 'Unknown'),
            "artist": artist_name,
            "artist_id": artist_id,
            "year": item.get('year'),
            "thumbnail": get_thumbnail(item.get('thumbnails', [])),
            "album_type": item.get('type', 'Album'),
            "is_explicit": item.get('isExplicit', False)
        }
    except Exception as e:
        print(f"Error parsing album: {e}")
        return None

def parse_artist(item: Dict) -> Optional[Dict]:
    """Parse artist item"""
    try:
        browse_id = item.get('browseId')
        if not browse_id:
            return None
            
        return {
            "type": "artist",
            "browse_id": browse_id,
            "artist": item.get('artist', 'Unknown'),
            "thumbnail": get_thumbnail(item.get('thumbnails', [])),
            "subscribers": item.get('subscribers')
        }
    except Exception as e:
        print(f"Error parsing artist: {e}")
        return None

def parse_playlist(item: Dict, playlist_type: str = "playlist") -> Optional[Dict]:
    """Parse playlist item"""
    try:
        browse_id = item.get('browseId')
        if not browse_id:
            return None
            
        return {
            "type": playlist_type,
            "browse_id": browse_id,
            "title": item.get('title', 'Unknown'),
            "author": item.get('author'),
            "item_count": item.get('itemCount'),
            "thumbnail": get_thumbnail(item.get('thumbnails', []))
        }
    except Exception as e:
        print(f"Error parsing playlist: {e}")
        return None

def parse_video(item: Dict) -> Optional[Dict]:
    """Parse video item"""
    try:
        video_id = item.get('videoId')
        if not video_id:
            return None
            
        artists = item.get('artists', [])
        artist_name = artists[0].get('name') if artists else None
        
        return {
            "type": "video",
            "video_id": video_id,
            "title": item.get('title', 'Unknown'),
            "artist": artist_name,
            "views": item.get('views'),
            "duration": item.get('duration'),
            "thumbnail": get_thumbnail(item.get('thumbnails', [])),
            "audio_url": None,
            "audio_loading": True
        }
    except Exception as e:
        print(f"Error parsing video: {e}")
        return None

async def stream_with_audio_updates(initial_data: Dict, items: List[Dict], max_concurrent: int = 45):
    """Stream initial data immediately, then stream audio URLs as they complete with progress"""
    
    # Send initial data with ALL metadata immediately
    yield f"data: {json.dumps({'type': 'initial', 'data': initial_data})}\n\n"
    
    # Get items that need audio
    audio_items = [(i, item) for i, item in enumerate(items) 
                   if item and item.get('video_id') and item.get('type') in ['song', 'video']]
    
    if not audio_items:
        yield f"data: {json.dumps({'type': 'complete', 'total': 0, 'audio_ready': 0})}\n\n"
        return
    
    # Batch processing with aggressive parallelism
    semaphore = asyncio.Semaphore(max_concurrent)
    completed = 0
    successful = 0
    start_time = time.time()
    
    async def fetch_and_notify(idx, item):
        nonlocal completed, successful
        async with semaphore:
            fetch_start = time.time()
            audio_url = await audio_url_from_id(item['video_id'])
            fetch_time = time.time() - fetch_start
            
            completed += 1
            is_direct = bool(audio_url and 'googlevideo' in audio_url)
            if is_direct:
                successful += 1
            
            elapsed = time.time() - start_time
            remaining = ((elapsed / completed) * (len(audio_items) - completed)) if completed > 0 else 0
            
            return {
                'type': 'audio_update',
                'index': idx,
                'video_id': item['video_id'],
                'audio_url': audio_url or f'https://music.youtube.com/watch?v={item["video_id"]}',
                'direct': is_direct,
                'fetch_time': f"{fetch_time:.2f}s",
                'progress': {
                    'completed': completed,
                    'total': len(audio_items),
                    'percent': int((completed / len(audio_items)) * 100),
                    'successful': successful,
                    'elapsed': f"{elapsed:.1f}s",
                    'estimated_remaining': f"{remaining:.1f}s"
                }
            }
    
    # Create all tasks
    tasks = [fetch_and_notify(idx, item) for idx, item in audio_items]
    
    # Stream updates as they complete in real-time
    for coro in asyncio.as_completed(tasks):
        try:
            update = await coro
            yield f"data: {json.dumps(update)}\n\n"
        except Exception as e:
            print(f"Error in audio fetch: {e}")
    
    # Send completion signal with summary
    total_time = time.time() - start_time
    completion_data = {
        'type': 'complete', 
        'total': len(audio_items),
        'audio_ready': successful,
        'failed': len(audio_items) - successful,
        'total_time': f'{total_time:.2f}s',
        'avg_time': f'{total_time / len(audio_items):.2f}s'
    }
    yield f"data: {json.dumps(completion_data)}\n\n"

@app.get("/search")
async def search(
    q: str = Query(..., description="Search query or browse ID (album/playlist/artist)"),
    filter: Optional[str] = Query(None, description="songs, videos, albums, artists, playlists, community_playlists, featured_playlists, album, playlist, artist"),
    limit: int = Query(20, ge=1, le=500),
    max_concurrent: int = Query(45, ge=10, le=50, description="Concurrent audio fetches")
):
    """
    UNIVERSAL ENDPOINT - Handles everything with SSE streaming:
    - Regular search: /search?q=lofi&filter=songs
    - Album tracks: /search?q=MPREb_xxx OR /search?q=MPREb_xxx&filter=album
    - Playlist tracks: /search?q=PLxxx OR /search?q=PLxxx&filter=playlist
    - Artist content: /search?q=UCxxx OR /search?q=UCxxx&filter=artist
    
    Returns: SSE stream with instant metadata + progressive audio URLs
    """
    start = time.time()
    
    try:
        loop = asyncio.get_event_loop()
        
        # Smart detection of request type
        is_album = filter == 'album' or q.startswith('MPREb_') or q.startswith('OLAK5')
        is_playlist = filter == 'playlist' or q.startswith('PL') or q.startswith('RDCLAK') or q.startswith('VL')
        is_artist = filter == 'artist' or q.startswith('UC')
        
        items = []
        metadata = {
            "query": q,
            "filter": filter
        }
        
        # ALBUM REQUEST - Get all tracks
        if is_album:
            album = await loop.run_in_executor(
                executor,
                partial(ytmusic.get_album, browseId=q)
            )
            
            for track in album.get('tracks', []):
                parsed = parse_song(track)
                if parsed:
                    items.append(parsed)
            
            artists = album.get('artists', [])
            metadata.update({
                "content_type": "album",
                "browse_id": q,
                "title": album.get('title', 'Unknown'),
                "album_type": album.get('type', 'Album'),
                "artist": artists[0].get('name') if artists else None,
                "artist_id": artists[0].get('id') if artists else None,
                "year": album.get('year'),
                "thumbnail": get_thumbnail(album.get('thumbnails', [])),
                "track_count": len(items),
                "duration": album.get('duration')
            })
        
        # PLAYLIST REQUEST - Get all tracks
        elif is_playlist:
            playlist = await loop.run_in_executor(
                executor,
                partial(ytmusic.get_playlist, playlistId=q, limit=limit)
            )
            
            for track in playlist.get('tracks', []):
                parsed = parse_song(track)
                if parsed:
                    items.append(parsed)
            
            author = playlist.get('author', {})
            author_name = author.get('name') if isinstance(author, dict) else author
            
            metadata.update({
                "content_type": "playlist",
                "browse_id": q,
                "title": playlist.get('title', 'Unknown'),
                "author": author_name,
                "description": playlist.get('description'),
                "thumbnail": get_thumbnail(playlist.get('thumbnails', [])),
                "track_count": len(items),
                "duration": playlist.get('duration')
            })
        
        # ARTIST REQUEST - Get top songs + albums
        elif is_artist:
            artist = await loop.run_in_executor(
                executor,
                partial(ytmusic.get_artist, channelId=q)
            )
            
            # Get top songs
            songs = []
            if artist.get('songs', {}).get('results'):
                for song in artist['songs']['results'][:20]:
                    parsed = parse_song(song)
                    if parsed:
                        songs.append(parsed)
                        items.append(parsed)
            
            # Get albums
            albums = []
            if artist.get('albums', {}).get('results'):
                for album in artist['albums']['results'][:20]:
                    parsed = parse_album(album)
                    if parsed:
                        albums.append(parsed)
            
            # Get singles
            singles = []
            if artist.get('singles', {}).get('results'):
                for single in artist['singles']['results'][:20]:
                    parsed = parse_album(single)
                    if parsed:
                        singles.append(parsed)
            
            metadata.update({
                "content_type": "artist",
                "browse_id": q,
                "name": artist.get('name', 'Unknown'),
                "description": artist.get('description'),
                "thumbnail": get_thumbnail(artist.get('thumbnails', [])),
                "subscribers": artist.get('subscribers'),
                "top_songs": songs,
                "albums": albums,
                "singles": singles,
                "top_songs_count": len(songs)
            })
        
        # REGULAR SEARCH
        else:
            results = await loop.run_in_executor(
                executor,
                partial(ytmusic.search, query=q, filter=filter, limit=limit)
            )
            
            for item in results:
                if not item:
                    continue
                    
                result_type = (item.get('resultType') or '').lower()
                category = (item.get('category') or '').lower()
                
                try:
                    parsed_item = None
                    
                    if result_type == 'song' or category == 'songs' or 'song' in result_type:
                        parsed_item = parse_song(item)
                    elif result_type == 'video' or category == 'videos' or 'video' in result_type:
                        parsed_item = parse_video(item)
                    elif result_type == 'album' or category == 'albums' or 'album' in result_type:
                        parsed_item = parse_album(item)
                    elif result_type == 'artist' or category == 'artists' or 'artist' in result_type:
                        parsed_item = parse_artist(item)
                    elif 'community' in category:
                        parsed_item = parse_playlist(item, 'community_playlist')
                    elif 'featured' in category:
                        parsed_item = parse_playlist(item, 'featured_playlist')
                    elif result_type == 'playlist' or 'playlist' in category:
                        parsed_item = parse_playlist(item, 'playlist')
                    
                    if parsed_item:
                        items.append(parsed_item)
                        
                except Exception as e:
                    print(f"Error parsing: {e}")
                    continue
            
            type_counts = {}
            for item in items:
                t = item.get('type', 'unknown')
                type_counts[t] = type_counts.get(t, 0) + 1
            
            metadata.update({
                "content_type": "search",
                "total": len(items),
                "by_type": type_counts
            })
        
        fetch_time = time.time() - start
        
        # Add items to metadata
        if metadata.get('content_type') == 'artist':
            metadata['tracks'] = items  # top_songs with audio
        else:
            metadata['results'] = items
        
        metadata['stats'] = {
            "fetch_time": f"{fetch_time:.2f}s",
            "total_items": len(items),
            "songs_with_audio": sum(1 for i in items if i.get('type') in ['song', 'video'])
        }
        
        # Return SSE stream with progressive audio loading
        return StreamingResponse(
            stream_with_audio_updates(metadata, items, max_concurrent),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive"
            }
        )
        
    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/audio/batch")
async def batch_audio(
    video_ids: str = Query(..., description="Comma-separated video IDs"),
    max_concurrent: int = Query(45, ge=10, le=50)
):
    """
    BATCH AUDIO PROCESSOR - Get multiple audio URLs in parallel
    Extremely fast batch processing with SSE streaming
    """
    start = time.time()
    
    ids = [vid.strip() for vid in video_ids.split(',') if vid.strip()]
    
    if not ids:
        raise HTTPException(status_code=400, detail="No video IDs provided")
    
    if len(ids) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 video IDs per batch")
    
    items = [{"video_id": vid, "type": "song", "title": f"Track {i+1}"} for i, vid in enumerate(ids)]
    
    metadata = {
        "content_type": "batch_audio",
        "total_ids": len(ids),
        "results": items
    }
    
    return StreamingResponse(
        stream_with_audio_updates(metadata, items, max_concurrent),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )

@app.get("/song/{video_id}")
async def get_song(video_id: str):
    """Get single song with immediate audio fetch"""
    start = time.time()
    
    try:
        loop = asyncio.get_event_loop()
        song_task = loop.run_in_executor(executor, partial(ytmusic.get_song, videoId=video_id))
        audio_task = audio_url_from_id(video_id)
        
        song, audio_url = await asyncio.gather(song_task, audio_task)
        
        video_details = song.get('videoDetails', {})
        
        return {
            "video_id": video_id,
            "title": video_details.get('title', 'Unknown'),
            "artist": video_details.get('author', 'Unknown'),
            "thumbnail": get_thumbnail(video_details.get('thumbnail', {}).get('thumbnails', [])),
            "audio_url": audio_url or f'https://music.youtube.com/watch?v={video_id}',
            "direct_audio": bool(audio_url and 'googlevideo' in audio_url),
            "time_taken": f"{time.time() - start:.2f}s"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/audio/{video_id}")
async def get_audio(video_id: str):
    """Get direct audio URL only"""
    start = time.time()
    audio_url = await audio_url_from_id(video_id)
    
    return {
        "video_id": video_id,
        "audio_url": audio_url or f'https://music.youtube.com/watch?v={video_id}',
        "direct": bool(audio_url and 'googlevideo' in audio_url),
        "time_taken": f"{time.time() - start:.2f}s"
    }

@app.get("/")
async def root():
    """API documentation"""
    return {
        "name": "YouTube Music API - Ultra-Fast Batch Processing",
        "version": "5.0",
        "description": "ONE /search endpoint for everything with SSE streaming and batch audio processing",
        
        "✨ Key Features": [
            "🚀 INSTANT response (0.3-0.5s) - shows ALL data immediately",
            "⚡ Batch audio processing - 45+ concurrent fetches",
            "📊 Real-time progress - see completion percentage",
            "🎵 Works for songs, albums, playlists, artists",
            "🔄 SSE streaming - no polling, pure real-time",
            "📦 Up to 500 tracks with progressive loading"
        ],
        
        "📡 Usage Examples": {
            "search_songs": {
                "url": "/search?q=lofi&filter=songs&limit=20",
                "description": "Search songs with streaming audio"
            },
            "get_album": {
                "url": "/search?q=MPREb_xxxxx",
                "description": "Get album with ALL tracks + audio URLs streaming"
            },
            "get_playlist": {
                "url": "/search?q=PLxxxxxxx&limit=100",
                "description": "Get playlist (up to 500 tracks) with audio streaming"
            },
            "get_artist": {
                "url": "/search?q=UCxxxxxxx",
                "description": "Get artist top songs + albums with audio"
            },
            "batch_audio": {
                "url": "/audio/batch?video_ids=id1,id2,id3&max_concurrent=45",
                "description": "Batch process audio URLs - ultra fast!"
            },
            "single_song": {
                "url": "/song/abc123",
                "description": "Get single song with audio (JSON)"
            }
        },
        
        "🎯 SSE Client Example": """
const es = new EventSource('/search?q=MPREb_album123');

es.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  if (data.type === 'initial') {
    // INSTANT: Show album info + ALL tracks
    console.log(data.data.title); // Album title
    console.log(data.data.results); // ALL 15 tracks with metadata
    displayTracks(data.data.results); // Show everything NOW
  }
  
  if (data.type === 'audio_update') {
    // PROGRESSIVE: Audio URL ready for track at index
    const track = tracks[data.index];
    track.audio_url = data.audio_url;
    enablePlayButton(data.index);
    
    // Progress info
    console.log(`${data.progress.percent}% complete`);
    console.log(`${data.progress.completed}/${data.progress.total}`);
    console.log(`ETA: ${data.progress.estimated_remaining}`);
  }
  
  if (data.type === 'complete') {
    console.log(`All done! ${data.audio_ready} URLs ready`);
    console.log(`Total time: ${data.total_time}`);
    es.close();
  }
};
        """,
        
        "🔥 Performance": {
            "initial_response": "0.3-0.5s (instant display)",
            "first_audio_url": "~1s (can start playing)",
            "20_songs": "~2-3s (all audio URLs)",
            "100_songs": "~5-7s (playlist)",
            "concurrent_fetches": "45-50 parallel processes"
        },
        
        "📋 Supported Filters": [
            "songs", "videos", "albums", "artists",
            "playlists", "community_playlists", "featured_playlists"
        ],
        
        "🎨 Response Structure": {
            "search": "Shows results by type (songs/albums/artists/playlists)",
            "album": "Shows album info + all tracks with audio",
            "playlist": "Shows playlist info + all tracks with audio",
            "artist": "Shows artist info + top songs + albums + singles"
        }
    }

@app.on_event("shutdown")
async def shutdown():
    executor.shutdown(wait=True)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    
