"""Config loading and validation.

Config file is YAML. It never contains secrets: YouTube OAuth is a
file path, OBS password is read from an env var (OBS_WS_PASSWORD).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """Raised when config is missing or invalid."""


@dataclass
class TTSConfig:
    engine: str = "vieneu"  # vieneu | piper | mock
    voice: str = "Hải Đăng"
    precision: str = "fp32"  # fp32 | int8 (CPU only, vieneu)
    sample_rate: int = 48000
    piper_voice_path: str = ""  # path to .onnx voice model, piper only
    # Local VieNeu ONNX model dirs (downloaded once, see README).
    # Empty = let the SDK download from Hugging Face.
    onnx_dir: str = ""
    codec_dir: str = ""


@dataclass
class AvatarConfig:
    engine: str = "musetalk"  # musetalk | null (null = audio pass-through)
    # LiveTalking checkout path and checkpoint dir are set on the GPU box.
    livetalking_path: str = ""
    checkpoint_dir: str = ""
    avatar_bundle: str = ""


@dataclass
class OBSConfig:
    host: str = "localhost"
    port: int = 4455
    # Password is NOT in the config file. Set OBS_WS_PASSWORD in env.
    password_env: str = "OBS_WS_PASSWORD"
    scene_live: str = "Live"
    scene_human: str = "Human"
    overlay_source: str = "AI Disclosure"


@dataclass
class YouTubeConfig:
    # OAuth 2.0 client secrets file (Google Cloud project, YouTube Data
    # API v3 enabled). Created by the operator on their own account.
    # Never committed. Phase 1 needs a verified channel (24h first enable).
    client_secrets_path: str = ""
    token_path: str = ""
    privacy_status: str = "unlisted"  # unlisted for tests, public for real
    latency: str = "low"  # normal | low | ultra-low
    default_title: str = ""
    default_description: str = ""


@dataclass
class ComplianceConfig:
    disclosure_overlay: str = "Nội dung AI - Avatar AI đang nói"
    log_path: str = "logs/spoken.jsonl"
    block_medical_claims: bool = True
    block_before_after: bool = True
    block_impersonation: bool = True


@dataclass
class AppConfig:
    tts: TTSConfig = field(default_factory=TTSConfig)
    avatar: AvatarConfig = field(default_factory=AvatarConfig)
    obs: OBSConfig = field(default_factory=OBSConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)
    language: str = "vi"

    @property
    def obs_password(self) -> str:
        return os.environ.get(self.obs.password_env, "")


def load_config(path: str | Path) -> AppConfig:
    """Load and validate config.yaml. Raises ConfigError on problems."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config must be a YAML mapping")

    cfg = AppConfig()
    tts = raw.get("tts") or {}
    cfg.tts.engine = str(tts.get("engine", cfg.tts.engine))
    cfg.tts.voice = str(tts.get("voice", cfg.tts.voice))
    cfg.tts.precision = str(tts.get("precision", cfg.tts.precision))
    cfg.tts.sample_rate = int(tts.get("sample_rate", cfg.tts.sample_rate))
    cfg.tts.piper_voice_path = str(tts.get("piper_voice_path", ""))
    cfg.tts.onnx_dir = str(tts.get("onnx_dir", ""))
    cfg.tts.codec_dir = str(tts.get("codec_dir", ""))

    av = raw.get("avatar") or {}
    cfg.avatar.engine = str(av.get("engine", cfg.avatar.engine))
    cfg.avatar.livetalking_path = str(av.get("livetalking_path", ""))
    cfg.avatar.checkpoint_dir = str(av.get("checkpoint_dir", ""))
    cfg.avatar.avatar_bundle = str(av.get("avatar_bundle", ""))

    obs = raw.get("obs") or {}
    cfg.obs.host = str(obs.get("host", cfg.obs.host))
    cfg.obs.port = int(obs.get("port", cfg.obs.port))
    cfg.obs.scene_live = str(obs.get("scene_live", cfg.obs.scene_live))
    cfg.obs.scene_human = str(obs.get("scene_human", cfg.obs.scene_human))
    cfg.obs.overlay_source = str(
        obs.get("overlay_source", cfg.obs.overlay_source)
    )

    yt = raw.get("youtube") or {}
    cfg.youtube.client_secrets_path = str(yt.get("client_secrets_path", ""))
    cfg.youtube.token_path = str(yt.get("token_path", ""))
    cfg.youtube.privacy_status = str(
        yt.get("privacy_status", cfg.youtube.privacy_status)
    )
    cfg.youtube.latency = str(yt.get("latency", cfg.youtube.latency))
    cfg.youtube.default_title = str(yt.get("default_title", ""))
    cfg.youtube.default_description = str(yt.get("default_description", ""))

    comp = raw.get("compliance") or {}
    cfg.compliance.disclosure_overlay = str(
        comp.get("disclosure_overlay", cfg.compliance.disclosure_overlay)
    )
    cfg.compliance.log_path = str(
        comp.get("log_path", cfg.compliance.log_path)
    )
    cfg.compliance.block_medical_claims = bool(
        comp.get("block_medical_claims", True)
    )
    cfg.compliance.block_before_after = bool(
        comp.get("block_before_after", True)
    )
    cfg.compliance.block_impersonation = bool(
        comp.get("block_impersonation", True)
    )

    cfg.language = str(raw.get("language", cfg.language))
    _validate(cfg)
    return cfg


def _validate(cfg: AppConfig) -> None:
    if cfg.tts.engine not in ("vieneu", "piper", "mock"):
        raise ConfigError(f"unknown tts.engine: {cfg.tts.engine}")
    if cfg.tts.precision not in ("fp32", "int8"):
        raise ConfigError(f"unknown tts.precision: {cfg.tts.precision}")
    if cfg.tts.sample_rate not in (16000, 22050, 24000, 48000):
        raise ConfigError(f"unsupported sample_rate: {cfg.tts.sample_rate}")
    if cfg.avatar.engine not in ("musetalk", "null"):
        raise ConfigError(f"unknown avatar.engine: {cfg.avatar.engine}")
    if cfg.youtube.privacy_status not in ("public", "unlisted", "private"):
        raise ConfigError(
            f"unknown youtube.privacy_status: {cfg.youtube.privacy_status}"
        )
    if cfg.youtube.latency not in ("normal", "low", "ultra-low"):
        raise ConfigError(f"unknown youtube.latency: {cfg.youtube.latency}")
