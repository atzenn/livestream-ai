#!/usr/bin/env python3
"""Phase 1 smoke runner.

Usage:
  python scripts/run_phase1_smoke.py --config config.yaml \
      --script scripts/sample_script_vi.md --out out/smoke

Modes:
  --dry-run   : mock TTS + null avatar + fake OBS. No model, no GPU,
                no OBS, no YouTube. Exercises the whole pipeline.
  --real-tts  : real VieNeu-TTS (downloads ~317 MB of model files on
                first run), null avatar, fake OBS. No live broadcast.
  --pc-tts    : VieNeu-TTS from Zen's PC via its MCP server (port
                8790). Needs the Leader's access path to the PC; the
                real tool is text_to_speech (Leader tin 7485), which
                returns the audio file path in the VieNeu library on
                the PC + a listen link, not audio bytes. Falls back
                to Piper when the PC server is unreachable.
  --real-obs  : also connects to a real OBS (needs OBS running with
                WebSocket server on, OBS_WS_PASSWORD set).
  --go-live   : NOT implemented in Phase 1. Going live needs the
                operator: verified YouTube channel + OAuth + OBS +
                GPU box. This flag is refused on purpose.

Exit codes: 0 ok, 1 config/pipeline error, 2 compliance block.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.avatar import build_avatar
from livestream_ai.config import load_config
from livestream_ai.obs_client import FakeOBSClient, OBSClient
from livestream_ai.pipeline import PipelineError, run_script
from livestream_ai.tts import build_tts


def main() -> int:
    ap = argparse.ArgumentParser(description="livestream-ai phase 1 smoke")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--script", default="scripts/sample_script_vi.md")
    ap.add_argument("--out", default="out/smoke")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--real-tts", action="store_true")
    ap.add_argument("--pc-tts", action="store_true")
    ap.add_argument("--real-obs", action="store_true")
    ap.add_argument("--go-live", action="store_true")
    args = ap.parse_args()

    if args.go_live:
        print("REFUSED: --go-live is not implemented in Phase 1.")
        print("Going live needs the operator: verified YouTube channel,")
        print("OAuth credentials, OBS running, and the GPU box.")
        return 1

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"config error: {exc}")
        return 1

    tts_engine = "mock" if args.dry_run else cfg.tts.engine
    if args.real_tts:
        tts_engine = "vieneu"
    if args.pc_tts:
        tts_engine = "vieneu-pc"
    tts = build_tts(
        tts_engine,
        voice=cfg.tts.voice,
        precision=cfg.tts.precision,
        sample_rate=cfg.tts.sample_rate,
        piper_voice_path=cfg.tts.piper_voice_path,
        onnx_dir=cfg.tts.onnx_dir,
        codec_dir=cfg.tts.codec_dir,
        pc_mcp_host=cfg.tts.pc_mcp_host,
        pc_mcp_port=cfg.tts.pc_mcp_port,
        pc_mcp_path=cfg.tts.pc_mcp_path,
        pc_mcp_tool=cfg.tts.pc_mcp_tool,
        pc_mcp_folder=cfg.tts.pc_mcp_folder,
        pc_mcp_speed=cfg.tts.pc_mcp_speed,
        pc_mcp_timeout_s=cfg.tts.pc_mcp_timeout_s,
        pc_mcp_status_timeout_s=cfg.tts.pc_mcp_status_timeout_s,
        pc_mcp_poll_interval_s=cfg.tts.pc_mcp_poll_interval_s,
    )
    avatar = build_avatar(
        cfg.avatar.engine,
        livetalking_path=cfg.avatar.livetalking_path,
        checkpoint_dir=cfg.avatar.checkpoint_dir,
        avatar_bundle=cfg.avatar.avatar_bundle,
    )
    if args.real_obs and not args.dry_run:
        obs = OBSClient(cfg.obs.host, cfg.obs.port, cfg.obs_password)
    else:
        obs = FakeOBSClient()

    try:
        report = run_script(
            cfg,
            args.script,
            tts,
            avatar,
            obs,
            out_dir=args.out,
            dry_run=args.dry_run,
        )
    except PipelineError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        print(f"pipeline error: {exc}")
        return 1

    print(f"script: {report.script_title or args.script}")
    print(f"segments: {len(report.segments)}")
    for seg in report.segments:
        print(
            f"  [{seg.index}] {seg.engine} {seg.duration_s:.1f}s "
            f"scene={seg.scene or '-'} :: {seg.text[:60]}"
        )
    print(f"total audio: {report.total_duration_s():.1f}s")
    print(f"spoken log: {cfg.compliance.log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
