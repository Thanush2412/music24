from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ytmusicapi import YTMusic
from innertube import InnerTube

# ---------------------------------------------------------
# INITIAL SETUP
# ---------------------------------------------------------

app = FastAPI(title="Ultra Fast YouTube Music Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

yt = YTMusic()                  # For metadata
ytm = InnerTube("WEB_REMIX")    # For audio (403-proof)

# ---------------------------------------------------------
# REAL AUDIO URL FETCHER (INNER TUBE)
# ---------------------------------------------------------

def get_audio_streams(video_id: str):
    try:
        player = ytm.player(video_id=video_id)
        formats = player["streamingData"]["adaptiveFormats"]

        audio_streams = []
        for f in formats:
            if f.get("mimeType", "").startswith("audio/"):
                audio_streams.append({
                    "itag": f.get("itag"),
                    "mime": f.get("mimeType"),
                    "bitrate": f.get("bitrate"),
                    "url": f.get("url"),  # REAL playable audio URL
                })

        return {
            "videoId": video_id,
            "streams": audio_streams
        }

    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------
# SEARCH ENDPOINT
# ---------------------------------------------------------

@app.get("/search")
async def search_music(q: str = Query(...)):
    try:
        results = yt.search(q, filter="songs")

        final = []
        for r in results:
            final.append({
                "type": r.get("resultType"),
                "title": r.get("title"),
                "videoId": r.get("videoId"),
                "artists": r.get("artists"),
                "album": r.get("album"),
                "thumbnails": r.get("thumbnails"),
                "duration": r.get("duration"),
            })

        return {"query": q, "results": final}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ---------------------------------------------------------
# AUDIO URL ENDPOINT – 403-PROOF
# ---------------------------------------------------------

@app.get("/audio")
async def audio(videoId: str = Query(...)):
    return get_audio_streams(videoId)

# ---------------------------------------------------------
# PLAYLIST ENDPOINT
# ---------------------------------------------------------

@app.get("/playlist")
async def playlist(listId: str = Query(...)):
    try:
        details = yt.get_playlist(listId)

        tracks = []
        for t in details["tracks"]:
            tracks.append({
                "title": t.get("title"),
                "videoId": t.get("videoId"),
                "artists": t.get("artists"),
                "thumbnails": t.get("thumbnails")
            })

        return {
            "playlist": {
                "title": details.get("title"),
                "count": len(tracks),
                "tracks": tracks
            }
        }

    except Exception as e:
        return {"error": str(e)}

# ---------------------------------------------------------
# START SERVER (Render auto-detects)
# ---------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
