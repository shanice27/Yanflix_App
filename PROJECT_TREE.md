# Yanflix Dubbing Studio — Project Tree

> Generated 2026-06-14. Excludes `node_modules/`, `.next/`, `__pycache__/`.

```
yanflix-dubbing-studio/
├── app/
│   ├── globals.css
│   ├── layout.tsx
│   ├── page.tsx
│   ├── pipeline/
│   │   └── page.tsx
│   ├── studio/
│   │   └── page.tsx
│   ├── workflow_specs/
│   │   ├── yanflix_master_specification.md
│   │   ├── yanflix_master_spec_v4.md
│   │   ├── yanflix_wf0_runpod_spec.md
│   │   ├── yanflix_workflow_0.md
│   │   ├── yanflix_workflow_1.md
│   │   ├── yanflix_workflow_2.md
│   │   └── yanflix_workflow_3.md
│   └── api/
│       ├── actor/route.ts
│       ├── cast-review/route.ts
│       ├── cast-status/route.ts
│       ├── characters/route.ts
│       ├── clone-speakers/route.ts
│       ├── debug-groq/route.ts
│       ├── diarize/
│       │   ├── route.ts
│       │   └── cancel/route.ts
│       ├── diarize-speakers/route.ts
│       ├── direct/route.ts
│       ├── dub_song/route.ts
│       ├── elevenlabs/route.ts
│       ├── episodes/route.ts
│       ├── eval-capture/route.ts
│       ├── fit-audio/route.ts
│       ├── generate-audio/route.ts
│       ├── gpu-status/route.ts
│       ├── harvest-seeds/route.ts
│       ├── isolate/route.ts
│       ├── isolate-status/route.ts
│       ├── jobs/
│       │   ├── pending/route.ts
│       │   └── queue/route.ts
│       ├── r2-fetch-stems/route.ts
│       ├── r2-upload/route.ts
│       ├── read-state/route.ts
│       ├── regen_line/route.ts
│       ├── regen_speaker/route.ts
│       ├── render/route.ts
│       ├── render-video/route.ts
│       ├── request-review/route.ts
│       ├── runpod-poll/route.ts
│       ├── runpod-submit/route.ts
│       ├── save-state/route.ts
│       ├── save_cast/route.ts
│       ├── save_segments/route.ts
│       ├── save_speaker_to_vault/route.ts
│       ├── segment-lines/route.ts
│       ├── segment-status/route.ts
│       ├── status/route.ts
│       ├── synth-aave/route.ts
│       ├── synth-standard/route.ts
│       ├── transcribe/route.ts
│       ├── transcribe-status/route.ts
│       ├── translate/route.ts
│       ├── upload-source/route.ts
│       ├── voice-registry/route.ts
│       ├── voice_test/route.ts
│       ├── ytdlp/route.ts
│       ├── api_bridge.py
│       ├── app.py
│       ├── audio_processor.py
│       ├── streamlit_app.py
│       ├── theme.css
│       ├── vault_manager.py
│       └── __init__.py
│
├── lib/
│   └── r2.ts
│
├── prompts/
│   ├── 01_character_and_song_detection.md
│   ├── 01_script_director.md
│   ├── 02_dual_translation.md
│   ├── 02_script_translation_director.md
│   └── 03_song_translation.md
│
├── python_backend/
│   ├── actor.py
│   ├── audio_fitter.py
│   ├── build_emotion_bank.py
│   ├── diarize_speakers.py
│   ├── director.py
│   ├── dub_song.py
│   ├── harvest_voices.py
│   ├── isolate.py
│   ├── llm_client.py
│   ├── pipeline.py
│   ├── render_video.py
│   ├── segment_lines.py
│   ├── serve_ui.py
│   ├── sync.py
│   ├── synthesize_dub.py
│   ├── transcribe.py
│   ├── _actor_runner.py
│   ├── _clone_speakers_runner.py
│   ├── _dedup_segments.py
│   ├── _diarize_runner.py
│   ├── _direct_runner.py
│   ├── _stage1_runner.py
│   ├── _translate_runner.py
│   └── _whisper_runner.py
│
├── characters/
│   ├── global_roster/
│   │   ├── dante_basco/          (avatar_monologue.wav, raw_prompt.m4a)
│   │   ├── rihanna/              (avatar_monologue.wav, raw_prompt.m4a)
│   │   ├── tara_strong/          (avatar_monologue.wav, raw_prompt.m4a)
│   │   └── zeno_robinson/        (avatar_monologue.wav, raw_prompt.m4a)
│   └── shows/
│       └── smoking_behind_the_supermarket_with_you/
│           ├── s01e01_scene_context.json
│           ├── chief_male_supporting/     (meta.json, profile.json, ref_*.wav x8, seeds/seed_00–04.wav)
│           ├── female_passerby_generic/   (meta.json)
│           ├── imase_male_singer/         (meta.json, profile.json, ref_*.wav x6, seeds/seed_00–04.wav)
│           ├── office_worker_male_background/ (meta.json, profile.json, seeds/seed_00–01.wav)
│           ├── older_lady_clerk_female_supporting/ (meta.json, profile.json, seeds/seed_00–04.wav)
│           ├── sasaki_male_lead/          (meta.json, profile.json, seeds/seed_00–04.wav)
│           ├── suzuki_male_supporting/    (meta.json, profile.json, avatar_monologue.wav, seeds/seed_00–04.wav)
│           ├── tayama/                    (meta.json, profile.json, seeds/seed_00–04.wav)
│           ├── yamada/                    (meta.json, profile.json, avatar_monologue.wav, seeds/seed_00–04.wav)
│           └── zutomayo_female_singer/    (meta.json, profile.json, avatar_monologue.wav, seeds/seed_00–03.wav)
│
├── jobs/
│   └── smoking_supermarket_s01e01/
│       ├── state_director.json
│       ├── state_director.json.bak
│       ├── state_whisper.json
│       ├── status_clone.json
│       ├── status_diarize.json
│       ├── status_harvest.json
│       ├── status_isolate.json
│       ├── status_segment.json
│       ├── status_transcribe.json
│       ├── status_translate.json
│       ├── diarize_chunks/
│       │   ├── chunk_0.json
│       │   └── chunk_1.json
│       └── line_clips/
│           └── line_000.wav … line_421.wav  (422 files)
│
├── workspace/
│   ├── n8n_workflow_1_autopilot.json
│   ├── wf1_codes.json
│   ├── wf2_reference.json
│   ├── wf3_reference.json
│   ├── wf4_reference.json
│   ├── 0_raw_videos/
│   │   └── Smoking Behind the Supermarket with You Episode 1.mp4
│   ├── 1_inputs/
│   │   ├── smoking_behind_the_supermarket_s01e01/  (video_no_audio.mp4)
│   │   └── smoking_supermarket_s01e01/             (video_no_audio.mp4)
│   ├── 2_isolated/
│   │   ├── smoking_behind_the_supermarket_s01e01/  (vocals.wav, no_vocals.wav, instrumental.wav, htdemucs/)
│   │   └── smoking_supermarket_s01e01/             (vocals.wav, no_vocals.wav, instrumental.wav, htdemucs/)
│   ├── 3_transcripts/
│   │   └── smoking_supermarket_s01e01/
│   │       └── transcript.json
│   ├── 4_cloned_cached/    (empty)
│   └── 5_outputs/          (empty)
│
├── stale_python/           (archived older Python scripts — not active)
│   ├── actor.py, app.py, config.json, director.py
│   ├── generate_voices.py, generate_voices_xtts.py
│   ├── pipeline.py, run_pipeline.py, serve_ui.py, sync.py
│   ├── _actor_runner.py, _clone_speakers_runner.py, _dedup_segments.py
│   ├── _diarize_runner.py, _direct_runner.py, _stage1_runner.py
│   ├── _translate_runner.py, _whisper_runner.py
│   ├── test_director.py, requirements.txt, README.md
│   └── Open Yanflix UI.bat
│
├── chroma_data/            (vector DB data)
│
├── apply_scene_cast.py
├── build_wf1.py
├── build_wf2.py
├── build_wf3.py
├── run_diarize_ollama.py
├── test_models.py
│
├── .env.local
├── next-env.d.ts
├── tsconfig.json
├── package.json
├── pyrightconfig.json
├── Reports.md
├── Yanflix.html
└── PROJECT_TREE.md         (this file)
```
