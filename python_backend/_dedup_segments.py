import json
from pathlib import Path
from collections import Counter

f = Path(__file__).parent / "jobs/smoking_supermarket_s01e01/state_director.json"
segs = json.loads(f.read_text(encoding="utf-8"))

counts = Counter(s["text"].strip() for s in segs)
hallucinated = {t for t, c in counts.items() if c > 3}

print("Removing hallucinated repeats:")
for t in sorted(hallucinated, key=lambda x: -counts[x]):
    print(f"  {counts[t]}x  {repr(t[:60])}")

cleaned = [s for s in segs if s["text"].strip() not in hallucinated]
for i, s in enumerate(cleaned):
    s["id"] = i

f.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nDone: {len(segs)} → {len(cleaned)} segments ({len(segs)-len(cleaned)} removed)")
