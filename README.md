# livestream-ai MVP - Phase 1 (YouTube-first, local-first)

Scaffold + Phase 1 build for the AI livestream MVP, from the closed
study `livestream-ai-study-2026-11-09-rev2.md` (verdict CHẤP NHẬN CÓ
ĐIỀU KIỆN, HỌC THIẾT KẾ, 17/17 critic conditions fixed).

Repo target: private `atzenn/livestream-ai` on GitHub. This scaffold
was built locally because the repo cannot be cloned from here: the
GitHub API returns 404 (private) and `git ls-remote` fails with no
credential. When the Leader provides the push path, this whole
directory is the content to push.

## Phase 1 scope (study section 11, Pha 1)

1. TTS Vietnamese offline (VieNeu-TTS v3 Turbo, Piper fallback).
2. MuseTalk speaks from existing audio.
3. LiveTalking connects (1) and (2).
4. OBS WebSocket switches scenes from the script.
5. RTMP to 1 YouTube destination via the Live Streaming API
   (insert / bind / transition), tested on a verified channel.
6. Multistream only after (5), and never to TikTok at the same time
   as another platform.

Compliance gate from the start: AI disclosure overlay on screen +
title/description saying it is AI, a spoken-line log for every line
the avatar says, and a block on medical claims, fake before/after
and impersonation. No pre-recorded loop, no fake live.

## What runs here, what does not

| Piece | Status on this VM |
|---|---|
| Config + script parser + compliance gate | Runs, unit-tested |
| Mock TTS + null avatar + fake OBS (dry run) | Runs, unit-tested |
| VieNeu-TTS v3 Turbo (CPU/ONNX) | See `docs/PHASE1_STATUS.md` |
| Piper fallback | See `docs/PHASE1_STATUS.md` |
| MuseTalk / LiveTalking | NOT RUN - no GPU on this VM (needs RTX 3060+) |
| Real OBS WebSocket | NOT RUN - no OBS on this headless VM |
| YouTube Live API (real calls) | WAITING - needs operator OAuth + verified channel |
| Real RTMP broadcast | WAITING - needs the above + GPU box |

## Layout

- `src/livestream_ai/` - package: `config`, `script`, `compliance`,
  `tts`, `avatar`, `obs_client`, `youtube_live`, `pipeline`.
- `scripts/sample_script_vi.md` - sample Vietnamese script.
- `scripts/run_phase1_smoke.py` - smoke runner (dry run / real TTS).
- `tests/` - offline unit tests (`python -m unittest discover -s tests`).
- `config.example.yaml` - copy to `config.yaml`, edit, never commit.
- `docs/PHASE1_STATUS.md` - measured status of this build.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
bash scripts/setup_models.sh   # downloads ~600 MB of model files (free)
.venv/bin/python scripts/run_phase1_smoke.py --config config.yaml \
    --script scripts/sample_script_vi.md --out out/smoke --dry-run
.venv/bin/python -m unittest discover -s tests -v
```

Real TTS (VieNeu-TTS v3 Turbo, CPU/ONNX, voice "Hải Đăng" - the
SDK 3.8.3 default; the README's "Minh Quân" / "23 voices" is stale,
this SDK ships 25 presets):

```bash
.venv/bin/python scripts/run_phase1_smoke.py --config config.yaml \
    --script scripts/sample_script_vi.md --out out/real --real-tts
```

Piper fallback (voice `vi_VN-25hours_single-low`, 16 kHz):

```bash
# in config.yaml set: tts.engine: piper
.venv/bin/python scripts/run_phase1_smoke.py --config config.yaml \
    --script scripts/sample_script_vi.md --out out/piper
```

VieNeu from Zen's PC (MCP server, port 8790) - new source, not
yet tested live:

```bash
# in config.yaml set: tts.engine: vieneu-pc, pc_mcp_enabled: true,
# pc_mcp_host: <PC address from the Leader>
.venv/bin/python scripts/run_phase1_smoke.py --config config.yaml \
    --script scripts/sample_script_vi.md --out out/pc --pc-tts
```

The PC runs VieNeu-TTS v3 Turbo (voice "Hải Đăng") behind an MCP
server on port 8790; the Leader made a 5.9 s sample voice on the PC
on 03/11/2026. The access path from the livestream machine to the
PC is arranged by the Leader. When the PC server is unreachable the
pipeline falls back to Piper automatically and logs the fallback;
it never stops the run. The real MCP interface was confirmed by
the Leader (tin 7485): the server has three tools - list_voices,
text_to_speech, get_speech_status. text_to_speech takes text,
voice (id "local:Hải Đăng", omit for the server default),
file_name, folder and speed, and NO model argument. It does NOT
return audio bytes: it returns the file path in the VieNeu library
on the PC, a browser listen link, and the engine name. Long text
may return while the server is still reading; the client polls
get_speech_status with the job id until the job is done. The
audio file stays on the PC, so the PC backend returns the PC-side
library path and writes no local file - the avatar/OBS side must
read the audio from the PC. The PC client is tested against a
local mock MCP server only; a real test with the PC is still owed.

Model files live in `models/` (gitignored, ~600 MB). They are
re-downloadable with `scripts/setup_models.sh`. The script also
fixes a Hugging Face cache quirk: the MOSS codec ONNX files
reference external .data files, and ONNX Runtime rejects them when
loaded through the cache's symlinks, so the script replaces the
codec snapshot symlinks with real copies. The `vieneu` SDK factory
does not forward `codec_dir` to the engine, so the cache fix is
needed even though local copies are kept in `models/moss-codec/`.

## Going live (operator steps, not automated)

1. Verify the YouTube channel (phone) and wait up to 24 h for the
   first live enable.
2. Create a Google Cloud project, enable YouTube Data API v3,
   download the OAuth client secrets JSON, set
   `youtube.client_secrets_path` in `config.yaml`.
3. Run OBS with the WebSocket server on (port 4455), set
   `OBS_WS_PASSWORD`, build the Live / Human scenes.
4. Run MuseTalk / LiveTalking on the GPU box with the checkpoints
   (~4 GB) and the prepared avatar bundle.
5. Start the broadcast unlisted first, check the AI disclosure
   overlay and the spoken log, then go public.

## Free-first

No paid service, no trial, no credential in code or config. The
paid options in the study (HeyGen, Synthesia, D-ID, Syntopia BOX)
are reference only and are not used here.

## License

MIT (7EN Labs). Model licenses are per-model: VieNeu-TTS v3 Turbo
Apache-2.0, MuseTalk MIT, LiveTalking Apache-2.0, OBS GPLv2, Piper
MIT / piper1-gpl GPL-3.0 (voice models licensed per voice).
