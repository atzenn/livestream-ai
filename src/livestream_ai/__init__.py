"""livestream-ai - Phase 1 scaffold (YouTube-first, local-first).

Pipeline: script (Markdown) -> TTS (VieNeu-TTS v3 Turbo, Piper fallback)
-> avatar (MuseTalk via LiveTalking, GPU-gated) -> OBS (obs-websocket)
-> RTMP to YouTube Live Streaming API.

Free-first: no paid service, no trial, no credential in code or config.
Credentials (YouTube OAuth) live only in a local file outside the repo,
referenced by path in config.yaml. Nothing here pushes, merges or
publishes anything.
"""

__version__ = "0.1.0-phase1"
