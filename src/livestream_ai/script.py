"""Script parsing.

Script file format (Markdown or plain text, Vietnamese):
- One blank-line-separated paragraph = one spoken segment.
- A line starting with "# " is a scene marker, not spoken.
  Example: "# Scene: Live" switches the OBS scene before the next segment.
- A line starting with "[pause Xs]" inserts a pause (not spoken).
- Markdown bold/italic markers are stripped; links keep their text.
- Lines starting with "<!--" are comments, skipped.

The parser never invents text: every segment comes from the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Segment:
    index: int
    text: str
    scene: str = ""  # OBS scene to switch to before this segment
    pause_before_s: float = 0.0


@dataclass
class Script:
    title: str = ""
    segments: list[Segment] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.segments)


_SCENE_RE = re.compile(r"^#\s+Scene:\s*(.+?)\s*$", re.IGNORECASE)
_PAUSE_RE = re.compile(r"^\[pause\s+(\d+(?:\.\d+)?)s\]\s*$", re.IGNORECASE)
_COMMENT_RE = re.compile(r"^\s*<!--.*-->\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_RE = re.compile(r"[*_]{1,3}([^*_]+)[*_]{1,3}")


def _clean_text(text: str) -> str:
    text = _LINK_RE.sub(r"\1", text)
    text = _MD_RE.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_script(path: str) -> Script:
    """Parse a script file into ordered segments."""
    with open(path, encoding="utf-8") as f:
        raw_lines = f.read().splitlines()

    script = Script()
    current_scene = ""
    pending_pause = 0.0
    buffer: list[str] = []

    def flush(index: int) -> None:
        nonlocal buffer, pending_pause
        text = _clean_text(" ".join(buffer))
        buffer = []
        if not text:
            return
        script.segments.append(
            Segment(
                index=index,
                text=text,
                scene=current_scene,
                pause_before_s=pending_pause,
            )
        )
        pending_pause = 0.0

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            flush(len(script.segments))
            continue
        scene = _SCENE_RE.match(stripped)
        if scene:
            flush(len(script.segments))
            current_scene = scene.group(1)
            continue
        pause = _PAUSE_RE.match(stripped)
        if pause:
            pending_pause = float(pause.group(1))
            continue
        if _COMMENT_RE.match(stripped):
            continue
        if stripped.startswith("# ") and not scene:
            # A plain heading is the script title (first one only).
            if not script.title:
                script.title = stripped[2:].strip()
            continue
        buffer.append(stripped)
    flush(len(script.segments))
    return script
