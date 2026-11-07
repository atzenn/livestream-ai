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
    engine: str = "vieneu"  # vieneu | vieneu-pc | piper | mock
    voice: str = "Hải Đăng"
    precision: str = "fp32"  # fp32 | int8 (CPU only, vieneu)
    sample_rate: int = 48000
    piper_voice_path: str = ""  # path to .onnx voice model, piper only
    # Local VieNeu ONNX model dirs (downloaded once, see README).
    # Empty = let the SDK download from Hugging Face.
    onnx_dir: str = ""
    codec_dir: str = ""
    # VieNeu-TTS on Zen's PC via its MCP server (port 8790).
    # Default OFF: the access path is arranged by the Leader, and
    # the MCP tool name / argument names are ASSUMED until the
    # Leader confirms them (see tts.py VieNeuPC_TTS).
    pc_mcp_enabled: bool = False
    pc_mcp_host: str = ""  # PC address; env LIVESTREAM_PC_HOST wins
    pc_mcp_url: str = ""  # full MCP endpoint URL; when set, host/port/path are ignored
    pc_mcp_port: int = 8790
    pc_mcp_path: str = "/mcp"  # streamable-HTTP endpoint (assumed)
    pc_mcp_tool: str = "synthesize"  # ASSUMED tool name
    pc_mcp_model: str = "v3-turbo"
    pc_mcp_timeout_s: int = 60


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
    cfg.tts.pc_mcp_enabled = bool(tts.get("pc_mcp_enabled", False))
    # Host from config; env LIVESTREAM_PC_HOST wins when set.
    cfg.tts.pc_mcp_host = str(
        os.environ.get("LIVESTREAM_PC_HOST", "")
        or tts.get("pc_mcp_host", "")
    )
    cfg.tts.pc_mcp_port = int(tts.get("pc_mcp_port", 8790))
    cfg.tts.pc_mcp_path = str(tts.get("pc_mcp_path", "/mcp"))
    cfg.tts.pc_mcp_tool = str(tts.get("pc_mcp_tool", "synthesize"))
    cfg.tts.pc_mcp_model = str(tts.get("pc_mcp_model", "v3-turbo"))
    cfg.tts.pc_mcp_timeout_s = int(tts.get("pc_mcp_timeout_s", 60))
    cfg.tts.pc_mcp_url = str(
        os.environ.get("LIVESTREAM_PC_URL", "")
        or tts.get("pc_mcp_url", "")
    )
    # Nested tts.pc_server block (preferred form) overrides the flat
    # pc_mcp_* keys above when present. Keys: enabled, host, url,
    # port, path, tool, model, timeout_s (or timeout).
    pc = tts.get("pc_server") or {}
    if not isinstance(pc, dict):
        raise ConfigError("tts.pc_server must be a mapping")
    if "enabled" in pc:
        cfg.tts.pc_mcp_enabled = bool(pc["enabled"])
    if "host" in pc:
        cfg.tts.pc_mcp_host = str(
            os.environ.get("LIVESTREAM_PC_HOST", "") or pc["host"]
        )
    if "url" in pc:
        cfg.tts.pc_mcp_url = str(
            os.environ.get("LIVESTREAM_PC_URL", "") or pc["url"]
        )
    if "port" in pc:
        cfg.tts.pc_mcp_port = int(pc["port"])
    if "path" in pc:
        cfg.tts.pc_mcp_path = str(pc["path"])
    if "tool" in pc:
        cfg.tts.pc_mcp_tool = str(pc["tool"])
    if "model" in pc:
        cfg.tts.pc_mcp_model = str(pc["model"])
    if "timeout_s" in pc or "timeout" in pc:
        cfg.tts.pc_mcp_timeout_s = int(pc.get("timeout_s", pc.get("timeout")))

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
    if cfg.tts.engine not in (
        "vieneu",
        "vieneu-pc",
        "piper",
        "mock",
        "fallback",
    ):
        raise ConfigError(f"unknown tts.engine: {cfg.tts.engine}")
    if cfg.tts.engine == "vieneu-pc" and not cfg.tts.pc_mcp_enabled:
        raise ConfigError(
            "tts.engine is vieneu-pc but tts.pc_mcp_enabled is false"
        )
    if cfg.tts.pc_mcp_enabled and not cfg.tts.pc_mcp_host \
            and not cfg.tts.pc_mcp_url:
        raise ConfigError(
            "tts.pc_mcp_enabled is true but no PC endpoint is set "
            "(tts.pc_server.host / tts.pc_server.url, or "
            "LIVESTREAM_PC_HOST / LIVESTREAM_PC_URL)"
        )
    if cfg.tts.pc_mcp_url and not cfg.tts.pc_mcp_url.startswith(
        ("http://", "https://")
    ):
        raise ConfigError(
            "tts.pc_server.url must start with http:// or https://, "
            f"got: {cfg.tts.pc_mcp_url}"
        )
    if cfg.tts.pc_mcp_enabled and not cfg.tts.pc_mcp_tool:
        raise ConfigError("tts.pc_server.tool must not be empty")
    if not 1 <= cfg.tts.pc_mcp_port <= 65535:
        raise ConfigError(
            f"invalid tts.pc_mcp_port: {cfg.tts.pc_mcp_port}"
        )
    if cfg.tts.pc_mcp_timeout_s <= 0:
        raise ConfigError(
            f"invalid tts.pc_mcp_timeout_s: {cfg.tts.pc_mcp_timeout_s}"
        )
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
