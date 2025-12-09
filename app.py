from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor

from ytmusicapi import YTMusic
from yt_dlp import YoutubeDL

# ---------------------------------------------------------
# INITIAL SETUP
# ---------------------------------------------------------

app = FastAPI(title="Ultra Fast YouTube Music Backend")

# CORS for Web
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

yt = YTMusic()  # for metadata
executor = ThreadPoolExecutor(max_workers=10)  # for yt-dlp threads

# ---------------------------------------------------------
# ASYNC YT-DLP FUNCTION
# ---------------------------------------------------------

async def get_audio_url(video_id: str):
    loop = asyncio.get_event_loop()

    def run():
        try:
            with YoutubeDL({
                "quiet": True,
                "format": "bestaudio/best",
                "no_warnings": True,
            }) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                return {
                    "url": info.get("url"),
                    "title": info.get("title"),
                    "duration": info.get("duration"),
                }
        except Exception as e:
            return {"error": str(e)}

    return await loop.run_in_executor(executor, run)

# ---------------------------------------------------------
# SEARCH ENDPOINT (YT MUSIC)
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
# AUDIO URL ENDPOINT (403-PROOF)
# ---------------------------------------------------------

@app.get("/audio")
async def audio(videoId: str = Query(...)):
    data = await get_audio_url(videoId)
    return data

# ---------------------------------------------------------
# PLAYLIST ENDPOINT (OPTIONAL)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
