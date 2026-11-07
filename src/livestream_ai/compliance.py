"""Compliance gate (Phase 1, YouTube-first).

Rules from the closed study (rev2, sections 5.4 and 8):
- The avatar must be disclosed as AI: on-screen overlay + title /
  description must say it is AI. The overlay text lives in config.
- Log every line the avatar speaks (append-only JSONL), so a complaint
  can be checked against what was actually said.
- Block: medical claims, fake before/after, impersonation of a real
  person or authority (doctor, celebrity, official). This is a
  first-pass keyword gate, not a legal guarantee. A blocked line stops
  the pipeline with a clear error; it is never silently skipped.

Detection is keyword based on purpose-built lists. The lists are
deliberately narrow (high precision, some misses) so normal sales
talk is not blocked. The Leader reviews the lists before any real
broadcast.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# --- keyword lists (Vietnamese, lowercase matching) -----------------------

_MEDICAL = [
    "chữa khỏi",
    "chữa bệnh",
    "điều trị",
    "khỏi bệnh",
    "khỏi hẳn",
    "thuốc chữa",
    "công dụng chữa",
    "trị dứt điểm",
    "trị khỏi",
    "giảm cân cấp tốc",
    "tăng chiều cao",
    "trắng da cấp tốc",
]

_BEFORE_AFTER = [
    "trước khi dùng",
    "sau khi dùng",
    "trước và sau",
    "before after",
    "before-after",
    "da trắng sau 7 ngày",
    "giảm 5kg trong 7 ngày",
    "hiệu quả sau 3 ngày",
]

_IMPERSONATION = [
    "tôi là bác sĩ",
    "mình là bác sĩ",
    "bác sĩ khuyên",
    "theo bác sĩ",
    "người nổi tiếng",
    "ca sĩ",
    "diễn viên",
    "tôi là chuyên gia",
    "chuyên gia y tế",
    "quan chức",
    "bộ y tế",
]


@dataclass
class Violation:
    rule: str  # medical | before_after | impersonation
    matched: str
    text: str


@dataclass
class ComplianceResult:
    passed: bool
    violations: list[Violation] = field(default_factory=list)


def check_text(
    text: str,
    block_medical: bool = True,
    block_before_after: bool = True,
    block_impersonation: bool = True,
) -> ComplianceResult:
    """Check one spoken line against the three block lists."""
    lowered = text.lower()
    violations: list[Violation] = []
    if block_medical:
        for kw in _MEDICAL:
            if kw in lowered:
                violations.append(
                    Violation("medical", kw, text)
                )
    if block_before_after:
        for kw in _BEFORE_AFTER:
            if kw in lowered:
                violations.append(
                    Violation("before_after", kw, text)
                )
    if block_impersonation:
        for kw in _IMPERSONATION:
            if kw in lowered:
                violations.append(
                    Violation("impersonation", kw, text)
                )
    return ComplianceResult(passed=not violations, violations=violations)


class SpokenLog:
    """Append-only JSONL log of every line the avatar speaks."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        segment_index: int,
        text: str,
        voice: str,
        source: str,
        audio_path: str = "",
    ) -> dict:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "segment": segment_index,
            "text": text,
            "voice": voice,
            "source": source,
            "audio": audio_path,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read_all(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
