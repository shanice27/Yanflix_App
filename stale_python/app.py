from pathlib import Path
import runpy

root = Path(__file__).resolve().parent
entry = root / "serve_ui.py"

if not entry.exists():
    raise FileNotFoundError(f"Could not find the UI server entry point at {entry}")

runpy.run_path(str(entry), run_name="__main__")

