"""Generate avatar monologue WAVs for each character using IndexTTS2."""
import sys
sys.path.insert(0, r"C:\Users\shani\OneDrive\Desktop\IndexTTS2")

from indextts.infer import IndexTTS

CHECKPOINTS = r"C:\Users\shani\OneDrive\Desktop\IndexTTS2\checkpoints"
TEXT = (
    "Water. Earth. Fire. Air. Long ago, the four nations lived together in harmony. "
    "Then, everything changed when the Fire Nation attacked. Only the Avatar, master "
    "of all four elements, could stop them, but when the world needed him most, he vanished."
)

CHARACTERS = [
    "dante_basco",
    "rihanna",
    "tara_strong",
    "zeno_robinson",
]

BASE = r"C:\Users\shani\OneDrive\Desktop\yanflix (1)\yanflix\characters\global_roster"

tts = IndexTTS(
    cfg_path=f"{CHECKPOINTS}/config.yaml",
    model_dir=CHECKPOINTS,
    use_cuda_kernel=False,
)

for char in CHARACTERS:
    prompt = f"{BASE}\\{char}\\raw_prompt.m4a"
    output = f"{BASE}\\{char}\\avatar_monologue.wav"
    print(f"\n[{char}] Generating...")
    tts.infer(audio_prompt=prompt, text=TEXT, output_path=output, verbose=True)
    print(f"[{char}] Saved -> {output}")

print("\nAll voices generated.")
