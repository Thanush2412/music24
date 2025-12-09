from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from yt_dlp import YoutubeDL
from ytmusicapi import YTMusic

app = FastAPI(title="Pure Music API - One File Server")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ytmusic = YTMusic()

# ============ SEARCH ============

@app.get("/search")
def search_music(q: str = Query(...)):
    try:
        result = ytmusic.search(query=q, filter="songs")
        return {"query": q, "results": result}
    except Exception as e:
        return {"error": str(e)}

# ============ AUDIO URL ============

def get_audio_url(video_id):
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "forceurl": True,
        "format": "bestaudio/best",
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return info.get("url")

@app.get("/audio")
def audio_endpoint(videoId: str):
    try:
        url = get_audio_url(videoId)
        return {"videoId": videoId, "audioUrl": url}
    except Exception as e:
        return {"error": str(e)}

# ============ SAVE TXT ============

@app.get("/save")
def save_txt(data: str):
    try:
        with open("data.txt", "a", encoding="utf-8") as f:
            f.write(data + "\n")
        return {"status": "saved", "data": data}
    except Exception as e:
        return {"error": str(e)}

# ============ READ TXT ============

@app.get("/read")
def read_txt():
    try:
        with open("data.txt", "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except Exception:
        return {"error": "No data.txt file found"}

# ============ ROOT ============

@app.get("/")
def home():
    return {"status": "running", "endpoints": ["/search", "/audio", "/save", "/read"]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

