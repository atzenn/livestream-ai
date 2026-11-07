# Phase 1 status - livestream-ai MVP (07/11/2026)

Source study: `livestream-ai-study-2026-11-09-rev2.md` (608 lines,
verdict CHẤP NHẬN CÓ ĐIỀU KIỆN, HỌC THIẾT KẾ, 17/17 critic conditions
fixed). Scope: study section 11, Pha 1 (YouTube-first).

Repo: `atzenn/livestream-ai` on GitHub.
- GitHub API (anonymous): 404 Not Found.
- `git ls-remote https://github.com/atzenn/livestream-ai.git`:
  `fatal: could not read Username` (private repo, no credential).
- Conclusion: repo cannot be cloned from this VM. Scaffold built
  locally in `~/workspace/livestream-ai-mvp/` for the Leader to push
  once the push path is decided. No push, no merge, no public post.

## Build

- Scaffold: `~/workspace/livestream-ai-mvp/` (README, LICENSE MIT,
  config.example.yaml, requirements.txt, .gitignore).
- Package `src/livestream_ai/`: config, script, compliance, tts,
  avatar, obs_client, youtube_live, pipeline.
- Sample script: `scripts/sample_script_vi.md` (4 segments, 2 scenes).
- Smoke runner: `scripts/run_phase1_smoke.py` (--dry-run, --real-tts,
  --real-obs; --go-live is refused on purpose).

## Tests

- Unit tests: 32/32 PASS, exit 0
  (`.venv/bin/python -m unittest discover -s tests`).
  Covers: config load/validation, script parsing (scene, pause,
  markdown strip), compliance gate (medical / before-after /
  impersonation + spoken-log roundtrip), YouTube request bodies
  (broadcast/stream/synthetic-media/bind/transition), OBS client
  (fake + request-shape), TTS mock (real WAV output), avatar
  (GPU gate + command shape), pipeline end-to-end (dry run +
  blocked-line stop).
- Dry-run smoke: exit 0, 4 segments, 8.9 s mock audio, spoken log
  written (logs/spoken.jsonl), scene markers Live -> Human, 1 s
  pause honored.

## VieNeu-TTS v3 Turbo (real, CPU/ONNX) - PASS

- SDK `vieneu` 3.8.3 installed (torch-free minimal install).
- Model files: ONNX fp32 subfolder `onnx_update` 453.4 MB
  (backbone_shared.data 396.1 MB, v3_heads.npz 49.8 MB,
  acoustic_cached.onnx 6.9 MB, prefill/decode 0.3 MB each) +
  MOSS codec ONNX 86.4 MB. Total ~540 MB, downloaded with curl
  in 49 s (huggingface_hub stalled at 0 bytes for 8 min on the
  same files through the egress proxy).
- Load: 17.6 s cold, 7.6 s warm (this VM, 4 cores, ~1 GB RAM free).
- One sentence ("Chào mọi người, mình là avatar AI của kênh."):
  synth 4.8 s for 2.6 s of audio (RTF ~1.85, fp32, non-streaming
  `infer`). Full sample script (4 segments, 21.4 s audio): 46.2 s
  wall including model load. All 4 WAVs verified real speech
  (seg-000 RMS 3901.6, peak 24928, 207625/211200 nonzero samples;
  seg-001 RMS 3430.9, peak 28176). 48 kHz, mono, 16-bit.
- Voice: SDK default is "Hải Đăng" (male, Northern, natural).
  25 preset voices in SDK 3.8.3. The upstream README's default
  "Minh Quân" and "23 voices" (SDK v3.7.1) are stale - "Minh Quân"
  is not in the preset list. Config default updated to "Hải Đăng".
- Known upstream quirk (documented, worked around): the `vieneu`
  factory does not forward `codec_dir` to the ONNX engine, and the
  MOSS codec ONNX files fail ONNX Runtime external-data validation
  when loaded through the Hugging Face cache symlinks. Fix applied:
  `scripts/setup_models.sh` replaces the codec snapshot symlinks
  with real copies. Reproducible via that script.
- Streaming first-audio latency (`infer_stream`) NOT measured yet.
  The study's Phase 0 asks for it; it needs a separate run on the
  GPU box or a streaming test here.

## Piper fallback - PASS

- `piper-tts` installed; voice `vi_VN-25hours_single-low` (63 MB,
  16 kHz). One sentence: 2.8 s for 2.6 s audio, RMS 6053.5,
  peak 32767, real speech. Warnings "Missing phoneme from id map"
  from espeak are harmless.
- Note: the vivos x-low voice (`vi_VN-vivos-x-low.onnx`) 404s on
  the Hub despite being listed by the tree API (repo-side
  inconsistency, checked 07/11/2026). The 25hours_single voice
  is the working fallback. Config points at it.
- Code fix during the build: piper-tts 1.3 `synthesize()` returns
  `Iterable[AudioChunk]` (float arrays), it does not write a WAV
  directly. `PiperTTS.synthesize` now converts chunks to int16 WAV.

## MuseTalk / LiveTalking

- NOT RUN. This VM has no NVIDIA GPU (`nvidia-smi` not found).
  MuseTalk needs RTX 3060 or better; weights ~4 GB (UNet v1.5
  3.40 GB fp32 + VAE, Whisper-tiny, DWPose, face-parse).
- Code written and tested: `MuseTalkAvatar.check_ready()` refuses
  clearly without a GPU; `build_command()` shape verified in tests.
- Needs: GPU box + LiveTalking checkout + checkpoints + avatar bundle.

## OBS WebSocket

- NOT RUN live. No OBS on this headless VM. Client code written
  (`obsws-python`, obs-websocket v5, port 4455) and tested against a
  fake client + request-shape checks. Needs: OBS 33 running on the
  operator machine with the WebSocket server on.

## YouTube Live Streaming API

- WAITING on operator input. Request bodies built and unit-tested
  offline (insert/bind/transition/synthetic-media). No real call made.
- Needs: Google Cloud project + YouTube Data API v3 enabled + OAuth
  client secrets JSON + verified channel (first live enable can take
  up to 24 h). No credential was created here.
- Disclosure field: `status.containsSyntheticMedia` via
  videos.update (no "AI use" field on liveBroadcasts.insert).

## Multistream

- NOT RUN (step 6 of Phase 1, after step 5). Never to TikTok at the
  same time as another platform.

## Needs a decision (Leader)

1. Push path for `atzenn/livestream-ai` (repo is private, no
   credential on this VM) - where does this scaffold go?
2. GPU box for MuseTalk / LiveTalking (which machine, checkpoints,
   avatar bundle) - or is Phase 1 TTS + OBS + YouTube enough for now?
3. YouTube channel + Google Cloud project for the live test
   (operator's account, OAuth consent) - who provides it?
4. License: scaffold uses MIT (ZenCode default). Confirm or change.
