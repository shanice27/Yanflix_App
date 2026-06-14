"""Thin IndexTTS-1.5 inference wrapper called by actor.py via conda run."""
import argparse, sys
sys.path.insert(0, r"C:\Users\shani\OneDrive\Desktop\IndexTTS2")
from indextts.infer import IndexTTS

parser = argparse.ArgumentParser()
parser.add_argument("--checkpoints", required=True)
parser.add_argument("--text", required=True)
parser.add_argument("--prompt", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

tts = IndexTTS(
    cfg_path=f"{args.checkpoints}/config.yaml",
    model_dir=args.checkpoints,
    use_cuda_kernel=False,
)
tts.infer(audio_prompt=args.prompt, text=args.text, output_path=args.output)
