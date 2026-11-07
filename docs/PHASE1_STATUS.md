# Phase 1 status - livestream-ai MVP (07/11/2026)

Source study: `livestream-ai-study-2026-11-09-rev2.md` (608 lines,
verdict CHẤP NHẬN CÓ ĐIỀU KIỆN, HỌC THIẾT KẾ, 17/17 critic conditions
fixed). Scope: study section 11, Pha 1 (YouTube-first).

Repo: `atzenn/livestream-ai` on GitHub.
- Scaffold pushed by the Leader: commit fa3a973
  ("feat: livestream AI MVP phase 1 - TTS + OBS + YouTube scaffold",
  30 tracked files). The local scaffold in
  `~/workspace/livestream-ai-mvp/` matches the pushed commit.
- The repo was private at build time (API 404, ls-remote needed a
  credential); it is public now, so the scaffold can be cloned.

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

## VieNeu-TTS via Zen's PC (MCP server, port 8790) - NEW, WAITING real test

- Source: VieNeu-TTS v3 Turbo on Zen's PC (model v3-turbo, voice
  "Hải Đăng"), reached through an MCP server on port 8790. The
  Leader connected and made a 5.9 s sample voice on the PC on
  03/11/2026. The access path from the livestream machine to the PC
  is arranged by the Leader.
- Code: new backend `VieNeuPC_TTS` in `src/livestream_ai/tts.py`
  (MCP streamable HTTP, JSON-RPC 2.0, stdlib only), engine name
  `vieneu-pc`, config section `tts.pc_mcp_*` in
  `config.example.yaml` (default OFF). Endpoint comes from config
  or env `LIVESTREAM_PC_HOST`; no PC address is hardcoded, no
  credential in code or config. Smoke runner flag `--pc-tts`.
- Fallback: when the PC server is unreachable the pipeline falls
  back to Piper automatically and logs the fallback (stderr
  "[tts] ... falling back to PiperTTS"); the run never stops.
  Verified end-to-end: config with a closed PC port produced 4
  Piper segments (28.2 s audio, exit 0).
- ASSUMED INTERFACE (not verified): no document in the workspace
  describes the PC server's tool names or argument names. The
  client assumes tool "synthesize" with arguments text / voice /
  model, and accepts audio back as an MCP audio content block, an
  embedded resource blob, or base64 WAV text. Only the MCP JSON-RPC
  transport itself (initialize / tools/call) is standard. The
  Leader must confirm the tool name and argument names before the
  first real call with the PC.
- Tests: 10 new unit tests (6 TTS + 4 config), all PASS, total
  42/32 old + 10 new. The PC backend is tested against a local
  mock MCP server only (sine WAV audio block, session id carried,
  tool error, unreachable server, fallback logging). The real PC
  was NEVER dialled in tests. A real test with the PC is still
  owed, through the Leader's access path.

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

1. Push path for `atzenn/livestream-ai` - DONE. Scaffold pushed
   at commit fa3a973 (30 tracked files, unit tests 32/32 re-run
   from a fresh clone).
2. GPU box for MuseTalk / LiveTalking (which machine, checkpoints,
   avatar bundle) - or is Phase 1 TTS + OBS + YouTube enough for now?
3. YouTube channel + Google Cloud project for the live test
   (operator's account, OAuth consent) - who provides it?
4. License: scaffold uses MIT (ZenCode default). Confirmed by the
   Leader (tin 7403).
5. VieNeu PC path: confirm the MCP tool name ("synthesize" assumed)
   and argument names (text / voice / model assumed), provide the
   access path to Zen's PC, and arrange the first real test call
   with the PC.
