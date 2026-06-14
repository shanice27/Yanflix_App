# 🎬 Yanflix — Private AI Dubbing Suite

Build once. Dub forever.

---

## First-Time Setup (Do This Once)

### 1. Open a terminal in VS Code and activate your conda environment

```bash
conda activate sonitranslate   # use whatever your SoniTranslate env is named
```

### 2. Install Yanflix dependencies

```bash
pip install -r requirements.txt
```

### 3. Launch the app

```bash
streamlit run app.py
```

Your browser will open automatically to `http://localhost:8501`

---

## Every Time After That

```bash
conda activate sonitranslate
streamlit run app.py
```

That's it. The browser opens, you drop in a video file, and click Start.

---

## Folder Structure

```
yanflix/
├── app.py              ← The UI. Only file you ever launch.
├── director.py         ← Gemini emotion-tag stage
├── actor.py            ← Fish Speech TTS stage
├── sync.py             ← Time-stretch + mux stage
├── pipeline.py         ← Ties all stages together
├── requirements.txt    ← Dependencies
├── config.json         ← Auto-created when you save settings (holds API keys)
├── characters/         ← Drop 10-sec .wav clips here, one per character
├── jobs/               ← Checkpoint files saved here during processing
└── output/             ← Completed dubbed .mp4 files land here
```

---

## First Run Checklist

- [ ] Go to **Settings** and enter your Gemini API key and HF token
- [ ] Go to **Character Vault** and add at least one character with a reference clip
- [ ] Go to **New Dub Job**, upload your video, assign speakers, hit Start

---

## API Keys (Both Free)

| Key | Where to get it |
|-----|----------------|
| Gemini API Key | https://aistudio.google.com/ |
| HF Read Token | https://huggingface.co/settings/tokens |

---

## Notes

- A 30-minute episode takes roughly 45-90 minutes to process end-to-end.
- If a job crashes, re-run it — the checkpoint system skips completed stages automatically.
- Character voice clips: 10 seconds of clean dialogue, no background music. WAV preferred.
- The `jobs/` folder accumulates checkpoint files. Safe to delete old ones to free space.

---

## Switching Fish Speech to Local (Optional Upgrade)

In `actor.py`, change the `get_client()` function:

```python
# HF Spaces (current default)
client = Client("fishaudio/fish-speech-1", hf_token=hf_token)

# Local Fish Speech server (if you run it on your machine)
client = Client("http://localhost:7860")
```

Run Fish Speech locally first: https://github.com/fishaudio/fish-speech
