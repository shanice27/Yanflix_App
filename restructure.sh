#!/bin/bash
set -e

echo "=========================================================="
echo "🚀 Running Structural Fix..."
echo "=========================================================="

# Phase 6 Fix — Standardized paths without wildcard loops
SRC_JOB_DIR="jobs/smoking_supermarket_s01e01"
if [ -d "$SRC_JOB_DIR" ]; then
    echo "📂 Restructuring jobs folder..."
    mv "$SRC_JOB_DIR"/state_*.json jobs/smoking_supermarket/s01/e01/states/ 2>/dev/null || true
    mv "$SRC_JOB_DIR"/status_*.json jobs/smoking_supermarket/s01/e01/status/ 2>/dev/null || true
fi

# Phase 7 Fix — Move workspace files safely
echo "📂 Migrating workspace assets..."
if [ -d "workspace/0_raw_videos" ]; then
    mv workspace/0_raw_videos/* workspace/smoking_supermarket/s01/e01/stage_01_harvest/ 2>/dev/null || true
    rmdir workspace/0_raw_videos 2>/dev/null || true
fi

if [ -d "workspace/1_inputs/smoking_supermarket_s01e01" ]; then
    mv workspace/1_inputs/smoking_supermarket_s01e01/* workspace/smoking_supermarket/s01/e01/stage_01_harvest/ 2>/dev/null || true
    rmdir workspace/1_inputs/smoking_supermarket_s01e01 2>/dev/null || true
fi

if [ -d "workspace/2_isolated/smoking_supermarket_s01e01" ]; then
    mv workspace/2_isolated/smoking_supermarket_s01e01/* workspace/smoking_supermarket/s01/e01/stage_02_isolate/ 2>/dev/null || true
    rmdir workspace/2_isolated/smoking_supermarket_s01e01 2>/dev/null || true
fi

if [ -f "workspace/3_transcripts/smoking_supermarket_s01e01/transcript.json" ]; then
    mv "workspace/3_transcripts/smoking_supermarket_s01e01/transcript.json" workspace/smoking_supermarket/s01/e01/stage_05_transcribe/transcript.json
fi

if [ -d "$SRC_JOB_DIR/diarize_chunks" ]; then
    mv "$SRC_JOB_DIR"/diarize_chunks/* workspace/smoking_supermarket/s01/e01/stage_03_diarize/chunks/ 2>/dev/null || true
    rmdir "$SRC_JOB_DIR/diarize_chunks" 2>/dev/null || true
fi

if [ -d "$SRC_JOB_DIR/line_clips" ]; then
    mv "$SRC_JOB_DIR"/line_clips/* workspace/smoking_supermarket/s01/e01/stage_04_segment/line_clips/ 2>/dev/null || true
    rmdir "$SRC_JOB_DIR/line_clips" 2>/dev/null || true
fi

if [ -d "$SRC_JOB_DIR" ] && [ -z "$(ls -A "$SRC_JOB_DIR" 2>/dev/null)" ]; then
    rmdir "$SRC_JOB_DIR"
fi

# Archive variations
if [ -d "workspace/1_inputs/smoking_behind_the_supermarket_s01e01" ]; then
    mv "workspace/1_inputs/smoking_behind_the_supermarket_s01e01" archive/
fi
if [ -d "workspace/2_isolated/smoking_behind_the_supermarket_s01e01" ]; then
    mv "workspace/2_isolated/smoking_behind_the_supermarket_s01e01" archive/
fi

# Cleanup outer legacy folders if empty
for legacy in "workspace/1_inputs" "workspace/2_isolated" "workspace/3_transcripts"; do
    if [ -d "$legacy" ] && [ -z "$(ls -A "$legacy" 2>/dev/null)" ]; then
        rmdir "$legacy"
    fi
done

if [ -f "Yanflix.html" ]; then
    cp "Yanflix.html" archive/Yanflix.html
    echo "💾 Preserved copy of Yanflix.html inside archive/"
fi

echo "=========================================================="
echo "✅ Structural Migration Complete!"
echo "=========================================================="
