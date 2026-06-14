"""Generate avatar monologue WAVs for each character using Coqui XTTS v2."""
import torch
from TTS.api import TTS

TEXT = (
    "Water... Earth... Fire... Air. "
    "Long ago, the four nations lived together in harmony. "
    "Then... everything changed, when the Fire Nation attacked. "
    "Only the Avatar — master of all four elements — could stop them. "
    "But when the world needed him most... he vanished."
)

CHARACTERS = [
    "dante_basco",
    "rihanna",
    "tara_strong",
    "zeno_robinson",
]

BASE = r"C:\Users\shani\OneDrive\Desktop\yanflix (1)\yanflix\characters\global_roster"

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(
    "cuda" if torch.cuda.is_available() else "cpu"
)

for char in CHARACTERS:
    prompt = f"{BASE}\\{char}\\raw_prompt.m4a"
    output = f"{BASE}\\{char}\\avatar_monologue_xtts.wav"
    print(f"\n[{char}] Generating...")
    tts.tts_to_file(
        text=TEXT,
        speaker_wav=prompt,
        language="en",
        file_path=output,
    )
    print(f"[{char}] Saved -> {output}")

print("\nAll XTTS v2 voices generated.")
