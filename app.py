"""
SERVER VERSION - Enhanced YouTube Music API for Render Deployment
Features: Region-wise content, mood playlists, genre hubs, charts, explore sections
NO audio URLs - metadata only for client-side playback
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
import random

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
home_cache: Dict[str, tuple] = {}

# Cache TTLs
SEARCH_TTL = timedelta(minutes=10)
CONTENT_TTL = timedelta(minutes=30)
HOME_TTL = timedelta(minutes=15)

# Supported regions
REGIONS = {
    'IN': 'India',
    'US': 'United States',
    'GB': 'United Kingdom',
    'CA': 'Canada',
    'AU': 'Australia',
    'JP': 'Japan',
    'KR': 'South Korea',
    'BR': 'Brazil',
    'MX': 'Mexico',
    'DE': 'Germany',
    'FR': 'France',
    'IT': 'Italy',
    'ES': 'Spain',
    'NL': 'Netherlands',
    'SE': 'Sweden',
    'NO': 'Norway',
    'PL': 'Poland',
    'RU': 'Russia',
    'AR': 'Argentina',
    'CL': 'Chile',
}

# Mood categories
MOODS = {
    'happy': ['happy music', 'feel good songs', 'upbeat music'],
    'sad': ['sad songs', 'emotional music', 'breakup songs'],
    'energetic': ['workout music', 'gym songs', 'energy boost'],
    'relaxed': ['chill music', 'lofi beats', 'calm songs'],
    'romantic': ['romantic songs', 'love songs', 'date night music'],
    'party': ['party music', 'dance hits', 'club songs'],
    'focus': ['study music', 'concentration', 'focus playlist'],
    'sleep': ['sleep music', 'bedtime songs', 'relaxing sleep'],
    'motivation': ['motivational songs', 'inspiring music', 'pump up'],
    'nostalgia': ['throwback songs', 'retro hits', '90s music']
}

# Genre categories
GENRES = {
    'pop': 'pop music hits',
    'rock': 'rock music classics',
    'hip-hop': 'hip hop rap music',
    'edm': 'electronic dance music',
    'jazz': 'jazz music classics',
    'classical': 'classical music',
    'country': 'country music hits',
    'indie': 'indie alternative music',
    'metal': 'metal rock music',
    'r&b': 'r&b soul music',
    'latin': 'latin music hits',
    'k-pop': 'k-pop korean music',
    'bollywood': 'bollywood hindi songs',
    'reggae': 'reggae music',
    'blues': 'blues music'
}


# ==================== HELPER FUNCTIONS ====================

def get_dynamic_trending_query(region: str = 'IN') -> str:
    """Generate time and region-based trending query"""
    hour = datetime.now().hour
    minute = datetime.now().minute
    
    region_names = {
        'IN': 'india',
        'US': 'usa',
        'GB': 'uk',
        'CA': 'canada',
        'AU': 'australia',
        'JP': 'japan',
        'KR': 'korea',
        'BR': 'brazil',
        'MX': 'mexico'
    }
    
    region_name = region_names.get(region, 'global')
    
    if hour < 12:
        queries = [
            f"trending songs {region_name} morning",
            f"viral hits {region_name} today",
            f"most played songs {region_name} now"
        ]
    elif hour < 18:
        queries = [
            f"viral songs {region_name} today",
            f"trending hits {region_name}",
            f"most streamed songs {region_name}"
        ]
    else:
        queries = [
            f"most played songs {region_name} tonight",
            f"trending music {region_name} now",
            f"viral hits {region_name} evening"
        ]
    
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
    
    result_type = item.get('resultType', '').lower()
    if result_type in ['song', 'video']:
        return 'song'
    if result_type == 'album':
        return 'album'
    if result_type == 'playlist':
        return 'playlist'
    if result_type == 'artist':
        return 'artist'
    
    if extract_video_id(item):
        return 'song'
    if 'subscribers' in item:
        return 'artist'
    
    browse_id = extract_browse_id(item)
    if browse_id:
        if browse_id.startswith('VL'):
            return 'playlist'
        if browse_id.startswith('MPREb_'):
            return 'album'
        if browse_id.startswith('UC'):
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
        
        if cache_key in search_cache:
            cache_entry = search_cache[cache_key]
            if is_cache_valid(cache_entry, SEARCH_TTL):
                data, _ = cache_entry
                return jsonify(data)
        
        results = ytmusic.search(query, filter=filter_type, limit=limit)
        search_cache[cache_key] = (results, datetime.now())
        
        return jsonify(results)
    except Exception as e:
        print(f"❌ Search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def index():
    """Enhanced API documentation"""
    return jsonify({
        "name": "YouTube Music API - Enhanced Server Version",
        "version": "2.0.0",
        "description": "Full-featured YouTube Music API with region-wise content, moods, genres, and more",
        "features": [
            "Region-wise trending & charts (20+ countries)",
            "10+ mood categories with playlists",
            "15+ genre categories",
            "Home feed with YouTube-like sections",
            "Explore page with multiple discovery sections",
            "New releases tracking",
            "Top artists & songs charts",
            "Smart caching with TTL",
            "Deduplication across all endpoints"
        ],
        "endpoints": {
            "search": {
                "universal": "GET /api/search?q=query&filter=songs&limit=20",
                "songs": "GET /api/search/songs?q=query&limit=20",
                "albums": "GET /api/search/albums?q=query&limit=20",
                "artists": "GET /api/search/artists?q=query&limit=20",
                "playlists": "GET /api/search/playlists?q=query&limit=20"
            },
            "home_and_explore": {
                "home": "GET /api/home?region=IN",
                "explore": "GET /api/explore?region=IN"
            },
            "trending_and_charts": {
                "trending": "GET /api/trending?region=IN&limit=20",
                "charts": "GET /api/charts?country=IN",
                "top_songs": "GET /api/charts/top-songs?country=IN&limit=50",
                "top_artists": "GET /api/charts/top-artists?country=IN&limit=50",
                "trending_videos": "GET /api/charts/trending-videos?country=IN&limit=50"
            },
            "new_releases": {
                "all": "GET /api/new-releases?limit=20",
                "albums": "GET /api/new-releases/albums?limit=20&genre=pop",
                "singles": "GET /api/new-releases/singles?limit=20"
            },
            "moods": {
                "list_all": "GET /api/moods",
                "specific_mood": "GET /api/mood/<mood_name>?limit=20",
                "all_moods_grid": "GET /api/moods/all",
                "available_moods": ["happy", "sad", "energetic", "relaxed", "romantic", "party", "focus", "sleep", "motivation", "nostalgia"]
            },
            "genres": {
                "list_all": "GET /api/genres",
                "specific_genre": "GET /api/genre/<genre_name>?limit=20",
                "all_genres_grid": "GET /api/genres/all",
                "available_genres": ["pop", "rock", "hip-hop", "edm", "jazz", "classical", "country", "indie", "metal", "r&b", "latin", "k-pop", "bollywood", "reggae", "blues"]
            },
            "regions": {
                "list_all": "GET /api/regions",
                "region_trending": "GET /api/region/<region_code>/trending?limit=20",
                "region_playlists": "GET /api/region/<region_code>/top-playlists?limit=20",
                "supported_regions": ["IN", "US", "GB", "CA", "AU", "JP", "KR", "BR", "MX", "DE", "FR", "IT", "ES", "NL", "SE", "NO", "PL", "RU", "AR", "CL"]
            },
            "content_details": {
                "playlist": "GET /api/playlist/<browse_id>",
                "album": "GET /api/album/<browse_id>",
                "artist": "GET /api/artist/<channel_id>"
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
        "usage_examples": {
            "get_trending_india": "/api/trending?region=IN&limit=50",
            "get_happy_playlists": "/api/mood/happy?limit=20",
            "get_rock_playlists": "/api/genre/rock?limit=20",
            "get_us_charts": "/api/charts?country=US",
            "explore_new_music": "/api/explore?region=IN",
            "get_all_moods": "/api/moods/all"
        },
        "notes": [
            "No audio URLs - use YouTube IFrame API on client",
            "All responses are JSON metadata only",
            "Smart caching improves performance",
            "Region codes: ISO 3166-1 alpha-2 (IN, US, GB, etc.)",
            "Deduplication applied automatically"
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


# ==================== HOME & EXPLORE ====================

@app.route('/api/home', methods=['GET'])
def api_home():
    """Enhanced home feed with YouTube-like sections"""
    try:
        region = request.args.get('region', 'IN')
        
        cache_key = f"home_{region}"
        if cache_key in home_cache:
            cache_entry = home_cache[cache_key]
            if is_cache_valid(cache_entry, HOME_TTL):
                data, _ = cache_entry
                return jsonify(data)
        
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
        
        # Cache
        home_cache[cache_key] = (processed_sections, datetime.now())
        
        return jsonify(processed_sections)
    except Exception as e:
        print(f"❌ Home feed error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/explore', methods=['GET'])
def api_explore():
    """Explore page with multiple discovery sections"""
    try:
        region = request.args.get('region', 'IN')
        
        sections = []
        seen_video_ids = set()
        seen_browse_ids = set()
        
        # Trending Now
        try:
            trending = ytmusic.search(get_dynamic_trending_query(region), filter='songs', limit=20)
            trending = deduplicate_items(trending, seen_video_ids, seen_browse_ids)
            if trending:
                sections.append({
                    'title': 'Trending Now',
                    'type': 'songs',
                    'contents': trending
                })
        except:
            pass
        
        # New Releases
        try:
            new_releases = ytmusic.search(f"new albums {CURRENT_YEAR}", filter='albums', limit=15)
            new_releases = deduplicate_items(new_releases, seen_video_ids, seen_browse_ids)
            if new_releases:
                sections.append({
                    'title': 'New Releases',
                    'type': 'albums',
                    'contents': new_releases
                })
        except:
            pass
        
        # Top Artists
        try:
            top_artists = ytmusic.search(f"top artists {CURRENT_YEAR}", filter='artists', limit=12)
            top_artists = deduplicate_items(top_artists, seen_video_ids, seen_browse_ids)
            if top_artists:
                sections.append({
                    'title': 'Top Artists',
                    'type': 'artists',
                    'contents': top_artists
                })
        except:
            pass
        
        # Featured Playlists
        try:
            playlists = ytmusic.search("featured playlists", filter='playlists', limit=15)
            playlists = deduplicate_items(playlists, seen_video_ids, seen_browse_ids)
            if playlists:
                sections.append({
                    'title': 'Featured Playlists',
                    'type': 'playlists',
                    'contents': playlists
                })
        except:
            pass
        
        # Discover New Music
        try:
            discover = ytmusic.search("new underrated songs", filter='songs', limit=20)
            discover = deduplicate_items(discover, seen_video_ids, seen_browse_ids)
            if discover:
                sections.append({
                    'title': 'Discover New Music',
                    'type': 'songs',
                    'contents': discover
                })
        except:
            pass
        
        return jsonify(sections)
    except Exception as e:
        print(f"❌ Explore error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== TRENDING & CHARTS (REGION-WISE) ====================

@app.route('/api/trending', methods=['GET'])
def api_trending():
    """Region-wise trending songs"""
    try:
        region = request.args.get('region', 'IN')
        limit = int(request.args.get('limit', 20))
        
        # Try charts first
        try:
            charts = ytmusic.get_charts(country=region)
            if charts and 'videos' in charts:
                videos = charts['videos'].get('results', [])
                return jsonify(videos[:limit])
        except:
            pass
        
        # Fallback to search
        results = ytmusic.search(get_dynamic_trending_query(region), filter='songs', limit=limit)
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


@app.route('/api/charts/top-songs', methods=['GET'])
def api_charts_top_songs():
    """Top songs chart"""
    try:
        country = request.args.get('country', 'IN')
        limit = int(request.args.get('limit', 50))
        
        charts = ytmusic.get_charts(country=country)
        if charts and 'videos' in charts:
            return jsonify(charts['videos'].get('results', [])[:limit])
        
        return jsonify([])
    except Exception as e:
        print(f"❌ Top songs chart error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/charts/top-artists', methods=['GET'])
def api_charts_top_artists():
    """Top artists chart"""
    try:
        country = request.args.get('country', 'IN')
        limit = int(request.args.get('limit', 50))
        
        charts = ytmusic.get_charts(country=country)
        if charts and 'artists' in charts:
            return jsonify(charts['artists'].get('results', [])[:limit])
        
        # Fallback
        results = ytmusic.search(f"top artists {country}", filter='artists', limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Top artists chart error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/charts/trending-videos', methods=['GET'])
def api_charts_trending_videos():
    """Trending videos chart"""
    try:
        country = request.args.get('country', 'IN')
        limit = int(request.args.get('limit', 50))
        
        charts = ytmusic.get_charts(country=country)
        if charts and 'videos' in charts:
            return jsonify(charts['videos'].get('results', [])[:limit])
        
        return jsonify([])
    except Exception as e:
        print(f"❌ Trending videos error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== NEW RELEASES ====================

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


@app.route('/api/new-releases/albums', methods=['GET'])
def api_new_albums():
    """New albums"""
    try:
        limit = int(request.args.get('limit', 20))
        genre = request.args.get('genre', '')
        
        query = f"new albums {genre} {CURRENT_YEAR}" if genre else f"new albums {CURRENT_YEAR}"
        results = ytmusic.search(query, filter="albums", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ New albums error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/new-releases/singles', methods=['GET'])
def api_new_singles():
    """New singles"""
    try:
        limit = int(request.args.get('limit', 20))
        results = ytmusic.search(f"new singles {CURRENT_YEAR}", filter="songs", limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ New singles error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== MOOD PLAYLISTS ====================

@app.route('/api/moods', methods=['GET'])
def api_moods():
    """Get all mood categories"""
    return jsonify(list(MOODS.keys()))


@app.route('/api/mood/<mood_name>', methods=['GET'])
def api_mood_playlists(mood_name):
    """Get playlists for specific mood"""
    try:
        limit = int(request.args.get('limit', 20))
        
        if mood_name not in MOODS:
            return jsonify({"error": "Invalid mood"}), 400
        
        # Get random query for variety
        queries = MOODS[mood_name]
        query = random.choice(queries)
        
        results = ytmusic.search(query, filter='playlists', limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Mood playlists error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/moods/all', methods=['GET'])
def api_all_moods():
    """Get playlists for all moods (YouTube-like grid)"""
    try:
        all_moods = {}
        
        for mood, queries in MOODS.items():
            query = random.choice(queries)
            results = ytmusic.search(query, filter='playlists', limit=6)
            if results:
                all_moods[mood] = results
        
        return jsonify(all_moods)
    except Exception as e:
        print(f"❌ All moods error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== GENRE PLAYLISTS ====================

@app.route('/api/genres', methods=['GET'])
def api_genres():
    """Get all genre categories"""
    return jsonify(list(GENRES.keys()))


@app.route('/api/genre/<genre_name>', methods=['GET'])
def api_genre_playlists(genre_name):
    """Get playlists for specific genre"""
    try:
        limit = int(request.args.get('limit', 20))
        
        if genre_name not in GENRES:
            return jsonify({"error": "Invalid genre"}), 400
        
        query = GENRES[genre_name]
        results = ytmusic.search(query, filter='playlists', limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Genre playlists error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/genres/all', methods=['GET'])
def api_all_genres():
    """Get playlists for all genres"""
    try:
        all_genres = {}
        
        for genre, query in GENRES.items():
            results = ytmusic.search(query, filter='playlists', limit=6)
            if results:
                all_genres[genre] = results
        
        return jsonify(all_genres)
    except Exception as e:
        print(f"❌ All genres error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== REGIONS ====================

@app.route('/api/regions', methods=['GET'])
def api_regions():
    """Get all supported regions"""
    return jsonify(REGIONS)


@app.route('/api/region/<region_code>/trending', methods=['GET'])
def api_region_trending(region_code):
    """Get trending for specific region"""
    try:
        if region_code not in REGIONS:
            return jsonify({"error": "Invalid region"}), 400
        
        limit = int(request.args.get('limit', 20))
        
        try:
            charts = ytmusic.get_charts(country=region_code)
            if charts and 'videos' in charts:
                return jsonify(charts['videos'].get('results', [])[:limit])
        except:
            pass
        
        results = ytmusic.search(get_dynamic_trending_query(region_code), filter='songs', limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Region trending error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/region/<region_code>/top-playlists', methods=['GET'])
def api_region_playlists(region_code):
    """Get top playlists for specific region"""
    try:
        if region_code not in REGIONS:
            return jsonify({"error": "Invalid region"}), 400
        
        limit = int(request.args.get('limit', 20))
        region_name = REGIONS[region_code].lower()
        
        results = ytmusic.search(f"top playlists {region_name}", filter='playlists', limit=limit)
        return jsonify(results)
    except Exception as e:
        print(f"❌ Region playlists error: {e}")
        return jsonify({"error": str(e)}), 500


# ==================== PLAYLIST/ALBUM/ARTIST ====================

@app.route('/api/playlist/<browse_id>', methods=['GET'])
def api_playlist(browse_id):
    """Get playlist details"""
    try:
        if browse_id in playlist_cache:
            cache_entry = playlist_cache[browse_id]
            if is_cache_valid(cache_entry, CONTENT_TTL):
                data, _ = cache_entry
                return jsonify(data)
        
        playlist = ytmusic.get_playlist(browse_id)
        playlist_cache[browse_id] = (playlist, datetime.now())
        
        return jsonify(playlist)
    except Exception as e:
        print(f"❌ Playlist error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/album/<browse_id>', methods=['GET'])
def api_album(browse_id):
    """Get album details"""
    try:
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
        
        album_cache[browse_id] = (album, datetime.now())
        
        return jsonify(album)
    except Exception as e:
        print(f"❌ Album error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/artist/<channel_id>', methods=['GET'])
def api_artist(channel_id):
    """Get artist details"""
    try:
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
        
        artist_cache[channel_id] = (artist, datetime.now())
        
        return jsonify(artist)
    except Exception as e:
        print(f"❌ Artist error: {e}")
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
            "artists": len(artist_cache),
            "home": len(home_cache)
        }
    })


@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """Clear all caches"""
    try:
        search_cache.clear()
        playlist_cache.clear()
        album_cache.clear()
        artist_cache.clear()
        home_cache.clear()
        return jsonify({"success": True, "message": "All caches cleared"})
    except Exception as e:
        print(f"❌ Cache clear error: {e}")
        return json



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
