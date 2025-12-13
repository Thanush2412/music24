"""
SERVER VERSION - YouTube Music API for Render Deployment
- NO audio URL generation (client handles video playback)
- NO user data storage (use external DB like MongoDB/Redis)
- Pure API endpoints only
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from ytmusicapi import YTMusic
from typing import List, Dict, Any, Set, Optional
import json
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import os

# Initialize Flask
app = Flask(__name__)
CORS(app)

# Current year
CURRENT_YEAR = datetime.now().year

# Initialize YTMusic client
ytmusic = YTMusic()

# In-memory caching (resets on restart - use Redis for production)
search_cache: Dict[str, tuple] = {}  # (data, timestamp)
playlist_cache: Dict[str, tuple] = {}
album_cache: Dict[str, tuple] = {}
artist_cache: Dict[str, tuple] = {}

# Cache TTLs
SEARCH_TTL = timedelta(minutes=10)
CONTENT_TTL = timedelta(minutes=30)


# ==================== HELPER FUNCTIONS ====================

def get_dynamic_trending_query() -> str:
    """Generate time-based trending query"""
    hour = datetime.now().hour
    minute = datetime.now().minute
    
    if hour < 12:
        queries = ["trending songs india morning", "viral hits india today", "most played songs india now"]
    elif hour < 18:
        queries = ["viral songs india today", "trending hits india", "most streamed songs india"]
    else:
        queries = ["most played songs india tonight", "trending music india now", "viral hits india evening"]
    
    return queries[minute % len(queries)]


def extract_video_id(item: Dict[str, Any]) -> Optional[str]:
    """Extract video ID"""
    if not item:
        return None
    if 'videoId' in item:
        return item['videoId']
    if 'navigationEndpoint' in item:
        nav = item['navigationEndpoint']
        if 'watchEndpoint' in nav:
            return nav['watchEndpoint'].get('videoId')
    return None


def extract_browse_id(item: Dict[str, Any]) -> Optional[str]:
    """Extract browse ID"""
    if not item:
        return None
    if 'browseId' in item:
        return item['browseId']
    if 'navigationEndpoint' in item:
        nav = item['navigationEndpoint']
        if 'browseEndpoint' in nav:
            return nav['browseEndpoint'].get('browseId')
    return None


def get_item_type(item: Dict[str, Any]) -> str:
    """Detect item type"""
    if not item:
        return 'unknown'
    if 'tracks' in item or 'track' in item.get('resultType', '').lower():
        return 'song'
    if extract_video_id(item):
        return 'song'
    if 'subscribers' in item or 'artist' in item.get('resultType', '').lower():
        return 'artist'
    
    browse_id = extract_browse_id(item)
    if browse_id:
        if browse_id.startswith('VL') or 'playlist' in item.get('resultType', '').lower():
            return 'playlist'
        if browse_id.startswith('MPREb_') or 'album' in item.get('resultType', '').lower():
            return 'album'
        if browse_id.startswith('UC') or 'channel' in browse_id.lower():
            return 'artist'
    
    return 'mixed'


def deduplicate_items(items: List[Dict], seen_video_ids: Set[str], 
                     seen_browse_ids: Set[str]) -> List[Dict]:
    """Remove duplicates"""
    unique_items = []
    for item in items:
        video_id = extract_video_id(item)
        browse_id = extract_browse_id(item)
        
        if video_id:
            if video_id not in seen_video_ids:
                unique_items.append(item)
                seen_video_ids.add(video_id)
        elif browse_id:
            if browse_id not in seen_browse_ids:
                unique_items.append(item)
                seen_browse_ids.add(browse_id)
        else:
            unique_items.append(item)
    
    return unique_items


def is_cache_valid(cache_entry: tuple, ttl: timedelta) -> bool:
    """Check if cache entry is valid"""
    if not cache_entry:
        return False
    _, timestamp = cache_entry
    return datetime.now() - timestamp < ttl


# ==================== SEARCH ENDPOINTS ====================

@app.route('/api/search', methods=['GET'])
def api_search():
    """Universal search endpoint"""
    try:
        query = request.args.get('q', '').strip()
        filter_type = request.args.get('filter', 'songs')
        limit = int(request.args.get('limit', 20))
        
        if not query:
            return jsonify({"error": "Query parameter 'q' is required"}), 400
        
        cache_key = hashlib.md5(f"{query}_{filter_type}_{limit}".encode()).hexdigest()
        
        # Check cache
        if cache_key in search_cache:
            cache_entry = search_cache[cache_key]
            if is_cache_valid(cache_entry, SEARCH_TTL):
                data, _ = cache_entry
                return jsonify(data)
        
        # Search
        results = ytmusic.search(query, filter=filter_type, limit=limit)
        
        # Cache results
        search_cache[cache_key] = (results, datetime.now())
        
        return jsonify(results)
    except Exception as e:
        print(f"❌ Search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/search/songs', methods=['GET'])
def api_search_songs():
    """Search songs only"""
    try:
        query = request.args.get('q', '').strip()
        limit = int(request.args.get('limit', 20))
        
        if not query:
            return jsonify({"error": "Query parameter 'q' is required"}), 400
        
        results = ytmusic.search(query, filter="songs", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Songs search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/search/albums', methods=['GET'])
def api_search_albums():
    """Search albums only"""
    try:
        query = request.args.get('q', '').strip()
        limit = int(request.args.get('limit', 20))
        
        if not query:
            return jsonify({"error": "Query parameter 'q' is required"}), 400
        
        results = ytmusic.search(query, filter="albums", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Albums search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/search/artists', methods=['GET'])
def api_search_artists():
    """Search artists only"""
    try:
        query = request.args.get('q', '').strip()
        limit = int(request.args.get('limit', 20))
        
        if not query:
            return jsonify({"error": "Query parameter 'q' is required"}), 400
        
        results = ytmusic.search(query, filter="artists", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Artists search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/search/playlists', methods=['GET'])
def api_search_playlists():
    """Search playlists only"""
    try:
        query = request.args.get('q', '').strip()
        limit = int(request.args.get('limit', 20))
        
        if not query:
            return jsonify({"error": "Query parameter 'q' is required"}), 400
        
        results = ytmusic.search(query, filter="playlists", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Playlists search error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== CONTENT ENDPOINTS ====================

@app.route('/api/home', methods=['GET'])
def api_home():
    """Home feed with deduplication"""
    try:
        seen_video_ids = set()
        seen_browse_ids = set()
        
        home = ytmusic.get_home()
        processed_sections = []
        
        if isinstance(home, list):
            for section in home:
                if not isinstance(section, dict):
                    continue
                
                section_data = {
                    'title': section.get('title', 'Recommended'),
                    'contents': section.get('contents', []),
                    'browseId': section.get('browseId'),
                }
                
                # Deduplicate
                section_data['contents'] = deduplicate_items(
                    section_data['contents'], 
                    seen_video_ids, 
                    seen_browse_ids
                )
                
                if section_data['contents']:
                    section_data['type'] = get_item_type(section_data['contents'][0])
                    processed_sections.append(section_data)
            
            return jsonify(processed_sections)
        
        return jsonify([])
    except Exception as e:
        print(f"❌ Home feed error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/trending', methods=['GET'])
def api_trending():
    """Trending songs"""
    try:
        limit = int(request.args.get('limit', 20))
        
        # Try charts first
        try:
            charts = ytmusic.get_charts()
            if charts and 'videos' in charts:
                videos = charts['videos'].get('results', [])
                return jsonify(videos[:limit])
        except:
            pass
        
        # Fallback to search
        results = ytmusic.search(get_dynamic_trending_query(), filter='songs', limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Trending error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/charts', methods=['GET'])
def api_charts():
    """Top charts by country"""
    try:
        country = request.args.get('country', 'IN')
        charts = ytmusic.get_charts(country=country)
        return jsonify(charts)
    except Exception as e:
        print(f"❌ Charts error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/new-releases', methods=['GET'])
def api_new_releases():
    """New album releases"""
    try:
        limit = int(request.args.get('limit', 20))
        results = ytmusic.search(f"new albums {CURRENT_YEAR}", filter="albums", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ New releases error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== PLAYLIST/ALBUM/ARTIST ====================

@app.route('/api/playlist/<browse_id>', methods=['GET'])
def api_playlist(browse_id):
    """Get playlist details"""
    try:
        # Check cache
        if browse_id in playlist_cache:
            cache_entry = playlist_cache[browse_id]
            if is_cache_valid(cache_entry, CONTENT_TTL):
                data, _ = cache_entry
                return jsonify(data)
        
        playlist = ytmusic.get_playlist(browse_id)
        
        # Cache
        playlist_cache[browse_id] = (playlist, datetime.now())
        
        return jsonify(playlist)
    except Exception as e:
        print(f"❌ Playlist error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/album/<browse_id>', methods=['GET'])
def api_album(browse_id):
    """Get album details"""
    try:
        # Check cache
        if browse_id in album_cache:
            cache_entry = album_cache[browse_id]
            if is_cache_valid(cache_entry, CONTENT_TTL):
                data, _ = cache_entry
                return jsonify(data)
        
        album = ytmusic.get_album(browse_id)
        
        if 'tracks' in album:
            album['trackCount'] = len(album['tracks'])
        else:
            album['trackCount'] = 0
        
        # Cache
        album_cache[browse_id] = (album, datetime.now())
        
        return jsonify(album)
    except Exception as e:
        print(f"❌ Album error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/artist/<channel_id>', methods=['GET'])
def api_artist(channel_id):
    """Get artist details"""
    try:
        # Check cache
        if channel_id in artist_cache:
            cache_entry = artist_cache[channel_id]
            if is_cache_valid(cache_entry, CONTENT_TTL):
                data, _ = cache_entry
                return jsonify(data)
        
        artist = ytmusic.get_artist(channel_id)
        
        if 'songs' in artist:
            songs = artist['songs'].get('results', []) if isinstance(artist['songs'], dict) else artist['songs']
            artist['songCount'] = len(songs)
        
        if 'albums' in artist:
            albums = artist['albums'].get('results', []) if isinstance(artist['albums'], dict) else artist['albums']
            artist['albumCount'] = len(albums)
        
        # Cache
        artist_cache[channel_id] = (artist, datetime.now())
        
        return jsonify(artist)
    except Exception as e:
        print(f"❌ Artist error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== CATEGORY ENDPOINTS ====================

@app.route('/api/category/romantic', methods=['GET'])
def api_romantic():
    """Romantic music playlists"""
    try:
        limit = int(request.args.get('limit', 20))
        results = ytmusic.search(f"romantic bollywood love songs {CURRENT_YEAR}", 
                                filter="playlists", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Romantic category error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/category/party', methods=['GET'])
def api_party():
    """Party music playlists"""
    try:
        limit = int(request.args.get('limit', 20))
        results = ytmusic.search(f"party dance edm hits {CURRENT_YEAR}", 
                                filter="playlists", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Party category error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/category/workout', methods=['GET'])
def api_workout():
    """Workout music playlists"""
    try:
        limit = int(request.args.get('limit', 20))
        results = ytmusic.search(f"gym workout motivation {CURRENT_YEAR}", 
                                filter="playlists", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Workout category error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/category/chill', methods=['GET'])
def api_chill():
    """Chill music playlists"""
    try:
        limit = int(request.args.get('limit', 20))
        results = ytmusic.search(f"lofi chill ambient study {CURRENT_YEAR}", 
                                filter="playlists", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Chill category error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/category/retro', methods=['GET'])
def api_retro():
    """Retro music playlists"""
    try:
        limit = int(request.args.get('limit', 20))
        results = ytmusic.search("classic 80s 90s retro bollywood", 
                                filter="playlists", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Retro category error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== RECOMMENDATIONS ====================

@app.route('/api/recommendations/<video_id>', methods=['GET'])
def api_recommendations(video_id):
    """Get song recommendations"""
    try:
        limit = int(request.args.get('limit', 20))
        watch = ytmusic.get_watch_playlist(videoId=video_id, limit=limit)
        tracks = watch.get('tracks', [])
        return jsonify(tracks)
    except Exception as e:
        print(f"❌ Recommendations error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== LYRICS ====================

@app.route('/api/lyrics/<video_id>', methods=['GET'])
def api_lyrics(video_id):
    """Get song lyrics"""
    try:
        watch_playlist = ytmusic.get_watch_playlist(videoId=video_id)
        lyrics_browse_id = watch_playlist.get("lyrics")
        if lyrics_browse_id:
            lyrics_data = ytmusic.get_lyrics(lyrics_browse_id)
            return jsonify(lyrics_data)
        return jsonify({"error": "No lyrics available"}), 404
    except Exception as e:
        print(f"❌ Lyrics error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== UTILITY ENDPOINTS ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "cache_sizes": {
            "search": len(search_cache),
            "playlists": len(playlist_cache),
            "albums": len(album_cache),
            "artists": len(artist_cache)
        }
    })


@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """Clear all caches (admin endpoint)"""
    try:
        search_cache.clear()
        playlist_cache.clear()
        album_cache.clear()
        artist_cache.clear()
        return jsonify({"success": True, "message": "All caches cleared"})
    except Exception as e:
        print(f"❌ Cache clear error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def index():
    """API documentation"""
    return jsonify({
        "name": "YouTube Music API - Server Version",
        "version": "1.0.0",
        "description": "Metadata-only API. No audio streaming. Use client-side player.",
        "endpoints": {
            "search": {
                "universal": "GET /api/search?q=query&filter=songs&limit=20",
                "songs": "GET /api/search/songs?q=query&limit=20",
                "albums": "GET /api/search/albums?q=query&limit=20",
                "artists": "GET /api/search/artists?q=query&limit=20",
                "playlists": "GET /api/search/playlists?q=query&limit=20"
            },
            "content": {
                "home": "GET /api/home",
                "trending": "GET /api/trending?limit=20",
                "charts": "GET /api/charts?country=IN",
                "new_releases": "GET /api/new-releases?limit=20"
            },
            "details": {
                "playlist": "GET /api/playlist/<browse_id>",
                "album": "GET /api/album/<browse_id>",
                "artist": "GET /api/artist/<channel_id>"
            },
            "categories": {
                "romantic": "GET /api/category/romantic",
                "party": "GET /api/category/party",
                "workout": "GET /api/category/workout",
                "chill": "GET /api/category/chill",
                "retro": "GET /api/category/retro"
            },
            "features": {
                "recommendations": "GET /api/recommendations/<video_id>?limit=20",
                "lyrics": "GET /api/lyrics/<video_id>"
            },
            "utility": {
                "health": "GET /api/health",
                "cache_clear": "POST /api/cache/clear"
            }
        },
        "notes": [
            "No audio URLs provided - use YouTube IFrame API or react-youtube on client",
            "No user storage - implement with MongoDB/Redis/Firebase on client",
            "Caching resets on server restart - use external cache for production"
        ]
    })


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ==================== RUN SERVER ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
