"""Pipeline: script -> compliance gate -> TTS -> avatar -> OBS.

One run:
1. Parse the script file into segments.
2. For each segment: compliance-check the text. A violation stops the
   run with a clear error (never silently skipped).
3. Synthesize the segment with TTS (VieNeu primary, Piper fallback,
   mock for dry runs).
4. Send the audio to the avatar engine (MuseTalk via LiveTalking on
   the GPU box; null pass-through for dry runs).
5. Switch the OBS scene on scene markers, keep the AI disclosure
   overlay on, and record every spoken line in the spoken log.

The pipeline never goes live by itself: starting the OBS stream and
the YouTube broadcast are separate, explicit operator steps (see
scripts/run_phase1_smoke.py and the README). A dry run exercises
every step except real TTS, real avatar video, real OBS and real
YouTube.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .compliance import SpokenLog, check_text
from .script import parse_script


class PipelineError(RuntimeError):
    """Raised when the pipeline cannot continue (blocked line, etc.)."""


@dataclass
class SegmentReport:
    index: int
    text: str
    audio_path: str
    duration_s: float
    engine: str
    scene: str


@dataclass
class PipelineReport:
    script_title: str
    segments: list[SegmentReport] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.segments)


def run_script(
    config,
    script_path: str,
    tts,
    avatar,
    obs,
    out_dir: str,
    video_out: str = "",
    dry_run: bool = False,
) -> PipelineReport:
    """Run a script file through the pipeline. Returns a report."""
    from datetime import datetime, timezone

    script = parse_script(script_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log = SpokenLog(config.compliance.log_path)
    report = PipelineReport(
        script_title=script.title,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Disclosure overlay on from the start of the run.
    if obs is not None and not dry_run:
        try:
            obs.set_overlay_text(
                config.obs.overlay_source,
                config.compliance.disclosure_overlay,
            )
        except Exception:
            pass

    for seg in script.segments:
        result = check_text(
            seg.text,
            block_medical=config.compliance.block_medical_claims,
            block_before_after=config.compliance.block_before_after,
            block_impersonation=config.compliance.block_impersonation,
        )
        if not result.passed:
            report.blocked.append(
                {
                    "segment": seg.index,
                    "text": seg.text,
                    "violations": [
                        {"rule": v.rule, "matched": v.matched}
                        for v in result.violations
                    ],
                }
            )
            raise PipelineError(
                f"segment {seg.index} blocked by compliance gate: "
                + ", ".join(
                    f"{v.rule} ({v.matched})" for v in result.violations
                )
            )
        if seg.pause_before_s:
            time.sleep(seg.pause_before_s)
        if seg.scene and obs is not None and not dry_run:
            obs.set_scene(seg.scene)
        audio_path = str(out / f"seg-{seg.index:03d}.wav")
        audio = tts.synthesize(seg.text, audio_path)
        if avatar is not None:
            avatar.speak(audio.path, video_out)
        log.record(
            segment_index=seg.index,
            text=seg.text,
            voice=getattr(tts, "voice", "mock"),
            source="script",
            audio_path=audio.path,
        )
        report.segments.append(
            SegmentReport(
                index=seg.index,
                text=seg.text,
                audio_path=audio.path,
                duration_s=audio.duration_s,
                engine=audio.engine,
                scene=seg.scene,
            )
        )
    report.finished_at = datetime.now(timezone.utc).isoformat()
    return report
