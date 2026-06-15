import shutil
import threading
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from app.audio_processor import YanflixAudioEngine

app = FastAPI()
audio_engine = YanflixAudioEngine()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def run_demucs_pipeline(input_path: Path):
    try:
        stems = audio_engine.separate_audio(input_path)
        print(f"✅ Demucs Processing Complete! Vocals: {stems['vocals']}")
    except Exception as e:
        print(f"❌ Demucs Pipeline Error: {str(e)}")


@app.post("/upload")
async def upload_audio(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    input_destination = audio_engine.dirs["inputs"] / file.filename

    with open(input_destination, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    print(f"📥 Received file from UI: {file.filename}")
    background_tasks.add_task(run_demucs_pipeline, input_destination)

    return {
        "status": "success",
        "message": f"File '{file.filename}' uploaded. Demucs separation initialized.",
        "saved_path": str(input_destination),
    }


def _start_api():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


def launch_api_thread():
    api_thread = threading.Thread(target=_start_api, daemon=True)
    api_thread.start()
