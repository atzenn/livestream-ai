"""Text-to-speech engines.

Primary: VieNeu-TTS v3 Turbo (Apache-2.0, Vietnamese, 48 kHz, 23 preset
voices). CPU path is torch-free (ONNX Runtime). GPU path needs CUDA
and is auto-detected by the SDK. First audio ~140 ms int8 / ~300 ms
fp32 on CPU per the upstream README; the study says to measure it on
the real machine instead of trusting README numbers.

Fallback: Piper (offline, CPU, ONNX). Piper core is MIT (archived) /
piper1-gpl GPL-3.0; voice model licenses are per-voice. Vietnamese
voice vi_VN (VIVOS) is used when configured.

PC source: VieNeu-TTS v3 Turbo running on Zen's PC, reached through
an MCP server on port 8790 (voice "Hải Đăng"). The Leader confirmed
a 5.9 s sample voice was made on the PC on 03/11/2026. The access
path from the livestream machine to Zen's PC is arranged by the
Leader; this client never dials the PC directly in tests - it is
tested against a local mock MCP server only.

REAL MCP INTERFACE (confirmed by the Leader, tin 7485, 07/11/2026):
the VieNeu server exposes exactly three tools - list_voices,
text_to_speech and get_speech_status. The tool name "synthesize"
used before was WRONG. text_to_speech takes text (required), voice
(a voice id such as "local:Hải Đăng"; omit it to use the server
default), file_name, folder and speed. There is NO model argument.
The tool does NOT return audio bytes: it returns the path of the
file in the VieNeu library on the PC, a link to listen in the
browser, and the engine name. Long text may return while the server
is still reading, so the client polls get_speech_status with the
job id until the job is done. The audio file lives on the PC;
synthesize() therefore returns the PC-side library path and does
NOT write a local WAV at out_path. Only the MCP JSON-RPC transport
itself (initialize, notifications/initialized, tools/call over
streamable HTTP) is standard. The exact JSON shape of the tool
result is not documented, so the client accepts the file path,
listen link, job id, status, engine and duration under several
common key names, from a JSON text block or from structured
content.

Mock: writes a silent WAV of plausible length (0.1 s per word,
minimum 0.5 s) so the pipeline can be tested offline without any
model. The mock never pretends to be real speech: output files are
named *.mock.wav.

All engines share one interface: synthesize(text) -> SynthesizedAudio.
No engine contacts a paid service. Edge TTS (Microsoft Read Aloud,
no key) is intentionally NOT wired in: it is a consumer service with
no contract and can rate-limit, so it is not a reliable live path.
"""

from __future__ import annotations

import json
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path


class TTSError(RuntimeError):
    """Raised when synthesis fails."""


@dataclass
class SynthesizedAudio:
    path: str
    sample_rate: int
    duration_s: float
    engine: str
    voice: str
    # VieNeu-PC only: the audio file lives on the PC, so `path` is
    # the PC-side library path; `listen_url` is the browser link
    # the server returns, and `job_id` is the server job id.
    remote_path: str = ""
    listen_url: str = ""
    job_id: str = ""


def _write_silent_wav(path: str, sample_rate: int, duration_s: float) -> None:
    frames = max(1, int(sample_rate * duration_s))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))


class MockTTS:
    """Offline stand-in. Writes silent WAVs, records every call."""

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.calls: list[str] = []

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        self.calls.append(text)
        words = max(1, len(text.split()))
        duration = max(0.5, words * 0.1)
        _write_silent_wav(out_path, self.sample_rate, duration)
        return SynthesizedAudio(
            path=out_path,
            sample_rate=self.sample_rate,
            duration_s=duration,
            engine="mock",
            voice="mock",
        )


class VieNeuTTS:
    """VieNeu-TTS v3 Turbo via the `vieneu` SDK (lazy import).

    `onnx_dir` / `codec_dir` point at local model files (downloaded
    once with curl, because huggingface_hub stalls on this machine's
    egress proxy). When empty, the SDK downloads from Hugging Face.
    """

    def __init__(
        self,
        voice: str = "Hải Đăng",
        precision: str = "fp32",
        sample_rate: int = 48000,
        onnx_dir: str = "",
        codec_dir: str = "",
    ):
        self.voice = voice
        self.precision = precision
        self.sample_rate = sample_rate
        self.onnx_dir = onnx_dir
        self.codec_dir = codec_dir
        self._engine = None

    def _load(self):
        if self._engine is None:
            try:
                from vieneu import Vieneu
            except ImportError as exc:
                raise TTSError(
                    "vieneu SDK not installed. Run: pip install vieneu"
                ) from exc
            # First call downloads ~317 MB of model files from Hugging
            # Face (pnnbao-ump/VieNeu-TTS-v3-Turbo) into the HF cache,
            # unless onnx_dir/codec_dir point at local files.
            kwargs = {"precision": self.precision}
            if self.onnx_dir:
                kwargs["onnx_dir"] = self.onnx_dir
            if self.codec_dir:
                kwargs["codec_dir"] = self.codec_dir
            self._engine = Vieneu(**kwargs)
        return self._engine

    def list_voices(self) -> list[tuple[str, str]]:
        engine = self._load()
        return list(engine.list_preset_voices())

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        engine = self._load()
        audio = engine.infer(text, voice=self.voice)
        engine.save(audio, out_path)
        duration = self._wav_duration(out_path)
        return SynthesizedAudio(
            path=out_path,
            sample_rate=self.sample_rate,
            duration_s=duration,
            engine="vieneu",
            voice=self.voice,
        )

    @staticmethod
    def _wav_duration(path: str) -> float:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())


class PiperTTS:
    """Piper offline TTS (fallback). Voice model path comes from config."""

    def __init__(self, voice_path: str, sample_rate: int = 22050):
        self.voice_path = voice_path
        self.sample_rate = sample_rate
        self._voice = None

    def _load(self):
        if self._voice is None:
            try:
                from piper import PiperVoice
            except ImportError as exc:
                raise TTSError(
                    "piper-tts not installed. Run: pip install piper-tts"
                ) from exc
            if not Path(self.voice_path).is_file():
                raise TTSError(
                    f"piper voice model not found: {self.voice_path}"
                )
            self._voice = PiperVoice.load(self.voice_path)
        return self._voice

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        import numpy as np

        voice = self._load()
        chunks = list(voice.synthesize(text))
        if not chunks:
            raise TTSError(f"piper produced no audio for: {text[:60]}")
        sample_rate = int(chunks[0].sample_rate)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            for chunk in chunks:
                audio = np.clip(chunk.audio_float_array, -1.0, 1.0)
                ints = (audio * 32767).astype(np.int16)
                w.writeframes(ints.tobytes())
        duration = VieNeuTTS._wav_duration(out_path)
        return SynthesizedAudio(
            path=out_path,
            sample_rate=sample_rate,
            duration_s=duration,
            engine="piper",
            voice=self.voice_path,
        )


class VieNeuPC_TTS:
    """VieNeu-TTS v3 Turbo on Zen's PC, via its MCP server (port 8790).

    Transport: MCP streamable HTTP (JSON-RPC 2.0 POST). Stdlib only.
    The endpoint comes from config/env, never hardcoded: `host` is the
    PC address as the Leader provides it, `port` defaults to 8790,
    `path` defaults to "/mcp". Pass a full `url` instead to override
    host/port/path (e.g. when the PC is reached through a tunnel).

    REAL INTERFACE (Leader tin 7485): the server has three tools -
    list_voices, text_to_speech, get_speech_status. text_to_speech
    takes text (required), voice (a voice id such as
    "local:Hải Đăng"; omit it to use the server default), file_name,
    folder and speed. There is NO model argument and the tool does
    NOT return audio bytes: it returns the file path in the VieNeu
    library on the PC, a browser listen link, and the engine name.
    Long text may return while the server is still reading; the
    client then polls get_speech_status with the job id until the
    job finishes. The audio file stays on the PC, so synthesize()
    returns the PC-side library path and writes NO local file at
    out_path. The caller must play the audio from the PC (or open
    the listen link); the livestream machine cannot read a PC-local
    path by itself. file_name defaults to the out_path basename so
    the library file is named after the segment.

    Voice ids: a bare preset name ("Hải Đăng") is normalised to
    "local:Hải Đăng". Pass voice="" to omit the argument entirely
    and let the server use its default voice.
    """

    # Real tool names (Leader tin 7485). "synthesize" was wrong and
    # is not used anywhere in this client.
    TOOL_SYNTHESIZE = "text_to_speech"
    TOOL_LIST_VOICES = "list_voices"
    TOOL_STATUS = "get_speech_status"
    DEFAULT_TOOL = TOOL_SYNTHESIZE

    # Statuses the server may report for a speech job.
    _DONE_STATUSES = {"completed", "done", "finished", "success", "ready"}
    _FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}

    def __init__(
        self,
        host: str = "",
        port: int = 8790,
        path: str = "/mcp",
        tool: str = DEFAULT_TOOL,
        voice: str = "local:Hải Đăng",
        timeout_s: int = 60,
        url: str = "",
        folder: str = "",
        speed: float | None = None,
        status_timeout_s: int = 120,
        poll_interval_s: float = 2.0,
    ):
        if url:
            if not url.startswith(("http://", "https://")):
                raise TTSError(
                    f"VieNeu PC url must start with http:// or "
                    f"https://, got: {url}"
                )
            self._url = url
            self.host = host
            self.port = port
            self.path = path
        else:
            if not host:
                raise TTSError(
                    "VieNeu PC host is not set. Set tts.pc_server.host "
                    "in config.yaml (or LIVESTREAM_PC_HOST in env), "
                    "or give a full tts.pc_server.url."
                )
            self.host = host
            self.port = port
            self.path = path if path.startswith("/") else "/" + path
            self._url = f"http://{host}:{port}{self.path}"
        self.tool = tool
        self.voice = self._normalise_voice(voice)
        self.timeout_s = timeout_s
        self.folder = folder
        self.speed = speed
        self.status_timeout_s = status_timeout_s
        self.poll_interval_s = poll_interval_s
        self._session_id = ""
        self._next_id = 1
        self._initialized = False

    @staticmethod
    def _normalise_voice(voice: str) -> str:
        """Bare preset name -> voice id.
        ("Hải Đăng" -> "local:Hải Đăng").

        A value that already carries a scheme ("local:...", "clone:...")
        is kept as-is; "" stays "" (voice argument omitted).
        """
        voice = (voice or "").strip()
        if not voice or ":" in voice:
            return voice
        return f"local:{voice}"

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(
            self._url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                session = resp.headers.get("mcp-session-id")
                if session:
                    self._session_id = session
                content_type = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise TTSError(
                f"PC MCP server HTTP {exc.code}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise TTSError(
                f"PC MCP server unreachable at {self._url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise TTSError(
                f"PC MCP server timed out after {self.timeout_s}s: "
                f"{self._url}"
            ) from exc
        text = raw.decode("utf-8", errors="replace")
        return self._parse_response(text, content_type)

    @staticmethod
    def _parse_response(text: str, content_type: str) -> dict:
        # Streamable HTTP servers may answer with SSE (data: {...}
        # lines); plain JSON is the other shape. Take the last
        # JSON-RPC message in either case.
        if "text/event-stream" in content_type:
            last = ""
            for line in text.splitlines():
                if line.startswith("data:"):
                    last = line[len("data:"):].strip()
            if not last:
                raise TTSError("PC MCP server sent an empty SSE stream")
            text = last
        try:
            msg = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TTSError(
                f"PC MCP server sent non-JSON: {text[:120]}"
            ) from exc
        if not isinstance(msg, dict) or "jsonrpc" not in msg:
            raise TTSError(f"PC MCP server sent a bad message: {text[:120]}")
        if "error" in msg:
            err = msg["error"]
            raise TTSError(
                f"PC MCP server error {err.get('code')}: "
                f"{err.get('message')}"
            )
        if "result" not in msg:
            raise TTSError(f"PC MCP server sent no result: {text[:120]}")
        return msg["result"]

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        payload: dict = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        self._next_id += 1
        if params is not None:
            payload["params"] = params
        return self._post(payload)

    def _notify(self, method: str) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(
            self._url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s):
                pass
        except Exception:
            # A failed initialized notification is not fatal; the
            # tools/call below will fail loudly if the server is down.
            pass

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "livestream-ai", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized")
        self._initialized = True

    def _tts_arguments(self, text: str, out_path: str) -> dict:
        """Arguments for text_to_speech. Voice is omitted when empty
        (server default); folder and speed are sent only when set.
        file_name is the out_path basename, so the library file is
        named after the segment. No model argument: the real tool
        has none."""
        args: dict = {"text": text, "file_name": Path(out_path).name}
        if self.voice:
            args["voice"] = self.voice
        if self.folder:
            args["folder"] = self.folder
        if self.speed is not None:
            args["speed"] = self.speed
        return args

    @staticmethod
    def _parse_speech_result(result: dict) -> dict:
        """Pull the speech result out of a tools/call result.

        The server returns a file path in the VieNeu library, a
        browser listen link, the engine name, and (for long text) a
        job id plus a not-yet-done status - never audio bytes. The
        exact JSON shape is not documented, so accept the fields
        under several common key names, from a JSON text block, from
        structured content, or from the result itself. A plain-text
        (non-JSON) block is scanned for a listen URL and an audio
        file path as a last resort. Returns a dict with any of:
        file_path, listen_url, engine, job_id, status, duration_s,
        sample_rate. Raises TTSError when nothing usable is found.
        """
        merged: dict = {}
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            merged.update(structured)
        content = result.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    text = block["text"].strip()
                    try:
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        parsed = None
                    if isinstance(parsed, dict):
                        merged.update(parsed)
                    elif isinstance(parsed, list):
                        merged.setdefault("voices", parsed)
                    else:
                        url = re.search(r"https?://\S+", text)
                        if url:
                            merged.setdefault(
                                "listen_url", url.group(0).rstrip(".,)\"'")
                            )
                        path = re.search(
                            r"[A-Za-z]:\\[^\s\"']+\.(?:wav|mp3)\b"
                            r"|/[^\s\"']+\.(?:wav|mp3)\b",
                            text,
                            re.IGNORECASE,
                        )
                        if path:
                            merged.setdefault("file_path", path.group(0))
        for key, value in result.items():
            if key not in ("content", "structuredContent", "isError"):
                merged.setdefault(key, value)
        out: dict = {}
        for src_key in (
            "file_path", "path", "file", "audio_path", "audio_file",
        ):
            if merged.get(src_key):
                out["file_path"] = str(merged[src_key])
                break
        for src_key in (
            "listen_url", "listenUrl", "url", "audio_url", "play_url",
            "playback_url",
        ):
            if merged.get(src_key):
                out["listen_url"] = str(merged[src_key])
                break
        for src_key in ("job_id", "jobId", "job", "id"):
            if merged.get(src_key):
                out["job_id"] = str(merged[src_key])
                break
        for src_key in ("status", "state"):
            if merged.get(src_key):
                out["status"] = str(merged[src_key]).lower()
                break
        if merged.get("engine"):
            out["engine"] = str(merged["engine"])
        for src_key in ("duration_s", "duration"):
            if merged.get(src_key) is not None:
                try:
                    out["duration_s"] = float(merged[src_key])
                except (TypeError, ValueError):
                    pass
                break
        if merged.get("sample_rate") is not None:
            try:
                out["sample_rate"] = int(merged["sample_rate"])
            except (TypeError, ValueError):
                pass
        if "voices" in merged:
            out["voices"] = merged["voices"]
        if not out:
            raise TTSError(
                "PC MCP tools/call returned no usable speech result "
                "(expected a file path / listen link / job id, got "
                f"{str(result)[:200]})"
            )
        return out

    def list_voices(self) -> list[str]:
        """List voice ids from the PC server (tool list_voices)."""
        self._initialize()
        result = self._rpc(
            "tools/call",
            {"name": self.TOOL_LIST_VOICES, "arguments": {}},
        )
        parsed = self._parse_speech_result(result)
        voices = parsed.get("voices")
        if isinstance(voices, list):
            ids: list[str] = []
            for item in voices:
                if isinstance(item, str):
                    ids.append(item)
                elif isinstance(item, dict):
                    for key in ("id", "voice_id", "name"):
                        if item.get(key):
                            ids.append(str(item[key]))
                            break
            return ids
        raise TTSError(
            "PC MCP list_voices returned no voice list: "
            f"{str(result)[:200]}"
        )

    def get_speech_status(self, job_id: str) -> dict:
        """Poll one speech job (tool get_speech_status)."""
        self._initialize()
        result = self._rpc(
            "tools/call",
            {
                "name": self.TOOL_STATUS,
                "arguments": {"job_id": job_id},
            },
        )
        return self._parse_speech_result(result)

    def _wait_for_speech(self, first: dict) -> dict:
        """Wait until a speech job finishes, polling get_speech_status.

        Returns the merged result (later polls override earlier
        fields, so a file path that only appears when the job is
        done is kept). Jobs already done, or without a job id,
        return at once. Raises TTSError on a failed job or on
        timeout.
        """
        merged = dict(first)
        job_id = merged.get("job_id", "")
        status = merged.get("status", "")
        if not job_id or status in self._DONE_STATUSES:
            return merged
        if status in self._FAILED_STATUSES:
            raise TTSError(
                f"PC MCP speech job {job_id} failed (status: {status})"
            )
        deadline = time.monotonic() + self.status_timeout_s
        while True:
            if time.monotonic() >= deadline:
                raise TTSError(
                    f"PC MCP speech job {job_id} did not finish "
                    f"within {self.status_timeout_s}s "
                    f"(last status: {status})"
                )
            time.sleep(self.poll_interval_s)
            update = self.get_speech_status(job_id)
            merged.update(update)
            status = merged.get("status", "")
            if status in self._DONE_STATUSES:
                return merged
            if status in self._FAILED_STATUSES:
                raise TTSError(
                    f"PC MCP speech job {job_id} failed "
                    f"(status: {status})"
                )

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        self._initialize()
        result = self._rpc(
            "tools/call",
            {
                "name": self.tool,
                "arguments": self._tts_arguments(text, out_path),
            },
        )
        parsed = self._parse_speech_result(result)
        # Long text may come back while the server is still reading.
        parsed = self._wait_for_speech(parsed)
        file_path = parsed.get("file_path", "")
        if not file_path:
            raise TTSError(
                "PC MCP text_to_speech returned no file path "
                "(the server keeps the audio in its VieNeu library; "
                f"job: {parsed.get('job_id', '-')}, "
                f"status: {parsed.get('status', '-')})"
            )
        # The audio lives on the PC: no local file is written at
        # out_path. sample_rate falls back to 48 kHz, the VieNeu
        # v3 Turbo output rate; duration is 0.0 when the server
        # does not report it. engine is always "vieneu-pc": it
        # identifies this backend in pipeline reports, where
        # "vieneu" already means the local SDK engine.
        return SynthesizedAudio(
            path=file_path,
            sample_rate=parsed.get("sample_rate", 48000),
            duration_s=parsed.get("duration_s", 0.0),
            engine="vieneu-pc",
            voice=self.voice,
            remote_path=file_path,
            listen_url=parsed.get("listen_url", ""),
            job_id=parsed.get("job_id", ""),
        )


# Canonical name for this engine (Leader tin 7451 task). The
# underscore spelling stays as an alias for existing callers.
VieNeuPCServerTTS = VieNeuPC_TTS


class FallbackTTS:
    """Try engines in order; first success wins.

    Chain is PC server -> local VieNeu -> Piper (build_tts
    "fallback"), but any list works: FallbackTTS(a, b, c) or
    FallbackTTS([a, b, c]). Each failure is reported through
    on_fallback (default: stderr) and the next engine is tried.
    The returned SynthesizedAudio carries the engine that actually
    produced the audio. Only when every engine fails does
    synthesize raise TTSError, chaining the last failure.
    """

    def __init__(self, *engines, on_fallback=None, voice=""):
        if len(engines) == 1 and isinstance(engines[0], (list, tuple)):
            engines = tuple(engines[0])
        if not engines:
            raise TTSError(
                "fallback chain is empty: no TTS engine configured"
            )
        self.engines = list(engines)
        # First engine is the primary; second is the first fallback,
        # kept as attributes for callers that used the old 2-engine
        # form FallbackTTS(primary, fallback).
        self.primary = self.engines[0]
        self.fallback = (
            self.engines[1] if len(self.engines) > 1 else None
        )
        self.on_fallback = on_fallback or self._default_log
        self._voice = voice

    @staticmethod
    def _default_log(message: str) -> None:
        print(f"[tts] {message}", file=sys.stderr)

    @property
    def voice(self) -> str:
        if self._voice:
            return self._voice
        for engine in self.engines:
            v = getattr(engine, "voice", "")
            if v:
                return v
        return ""

    def synthesize(self, text: str, out_path: str) -> SynthesizedAudio:
        failures: list[str] = []
        for engine in self.engines:
            try:
                return engine.synthesize(text, out_path)
            except Exception as exc:
                failures.append(f"{type(engine).__name__}: {exc}")
                self.on_fallback(
                    f"TTS engine {type(engine).__name__} failed: "
                    f"{exc}; falling back to next engine"
                )
        raise TTSError(
            "all TTS engines failed: " + "; ".join(failures)
        )


def _pc_kwargs(kwargs: dict) -> dict:
    """Merge PC server settings from a `pc_server` dict (preferred)
    and the flat pc_mcp_* kwargs. Returns kwargs for VieNeuPC_TTS.
    There is no model setting: the real text_to_speech tool takes
    no model argument (Leader tin 7485)."""
    pc = kwargs.get("pc_server") or {}
    if not isinstance(pc, dict):
        raise TTSError("pc_server must be a mapping")
    speed = pc.get(
        "speed", kwargs.get("pc_mcp_speed", kwargs.get("speed"))
    )
    return {
        "host": str(pc.get("host", kwargs.get("pc_mcp_host", ""))),
        "url": str(
            pc.get(
                "url",
                kwargs.get(
                    "pc_mcp_url", kwargs.get("pc_server_url", "")
                ),
            )
        ),
        "port": int(pc.get("port", kwargs.get("pc_mcp_port", 8790))),
        "path": str(pc.get("path", kwargs.get("pc_mcp_path", "/mcp"))),
        "tool": str(
            pc.get(
                "tool", kwargs.get("pc_mcp_tool", VieNeuPC_TTS.DEFAULT_TOOL)
            )
        ),
        "folder": str(
            pc.get("folder", kwargs.get("pc_mcp_folder", ""))
        ),
        "speed": None if speed is None else float(speed),
        "status_timeout_s": int(
            pc.get(
                "status_timeout_s",
                kwargs.get("pc_mcp_status_timeout_s", 120),
            )
        ),
        "poll_interval_s": float(
            pc.get(
                "poll_interval_s",
                kwargs.get("pc_mcp_poll_interval_s", 2.0),
            )
        ),
        "timeout_s": int(
            pc.get(
                "timeout_s",
                pc.get("timeout", kwargs.get("pc_mcp_timeout_s", 60)),
            )
        ),
        "voice": str(kwargs.get("voice", "Hải Đăng")),
    }


def _pc_enabled(kwargs: dict) -> bool:
    """True when the PC source is switched on, from a `pc_server`
    dict (preferred) or the flat pc_mcp_enabled kwarg."""
    pc = kwargs.get("pc_server") or {}
    if isinstance(pc, dict) and "enabled" in pc:
        return bool(pc["enabled"])
    return bool(kwargs.get("pc_mcp_enabled", False))


def build_tts(engine: str, **kwargs):
    """Factory. `engine` is one of: vieneu | vieneu-pc | piper |
    mock | fallback.

    vieneu-pc is the VieNeu-TTS server on Zen's PC (MCP, port 8790).
    It calls the real text_to_speech tool and returns the audio
    file path in the VieNeu library on the PC plus the browser
    listen link - no local audio file is written. When
    piper_voice_path is set, the PC client is wrapped with a
    Piper fallback; without it the PC client stands alone.
    fallback is the full chain PC server -> local VieNeu -> Piper
    (each step only when configured: PC only when enabled, Piper
    only when piper_voice_path is set). With a single engine the
    chain collapses to that engine.
    """
    if engine == "vieneu":
        return VieNeuTTS(
            voice=kwargs.get("voice", "Hải Đăng"),
            precision=kwargs.get("precision", "fp32"),
            sample_rate=int(kwargs.get("sample_rate", 48000)),
            onnx_dir=kwargs.get("onnx_dir", ""),
            codec_dir=kwargs.get("codec_dir", ""),
        )
    if engine == "vieneu-pc":
        pc = VieNeuPC_TTS(**_pc_kwargs(kwargs))
        piper_path = kwargs.get("piper_voice_path", "")
        if piper_path:
            return FallbackTTS(
                pc,
                PiperTTS(
                    voice_path=piper_path,
                    sample_rate=int(kwargs.get("sample_rate", 22050)),
                ),
                on_fallback=kwargs.get("on_fallback"),
                voice=kwargs.get("voice", "Hải Đăng"),
            )
        return pc
    if engine == "fallback":
        chain = []
        if _pc_enabled(kwargs):
            chain.append(VieNeuPC_TTS(**_pc_kwargs(kwargs)))
        chain.append(
            VieNeuTTS(
                voice=kwargs.get("voice", "Hải Đăng"),
                precision=kwargs.get("precision", "fp32"),
                sample_rate=int(kwargs.get("sample_rate", 48000)),
                onnx_dir=kwargs.get("onnx_dir", ""),
                codec_dir=kwargs.get("codec_dir", ""),
            )
        )
        piper_path = kwargs.get("piper_voice_path", "")
        if piper_path:
            chain.append(
                PiperTTS(
                    voice_path=piper_path,
                    sample_rate=int(kwargs.get("sample_rate", 22050)),
                )
            )
        if len(chain) == 1:
            return chain[0]
        return FallbackTTS(
            chain,
            on_fallback=kwargs.get("on_fallback"),
            voice=kwargs.get("voice", "Hải Đăng"),
        )
    if engine == "piper":
        return PiperTTS(
            voice_path=kwargs.get("piper_voice_path", ""),
            sample_rate=int(kwargs.get("sample_rate", 22050)),
        )
    if engine == "mock":
        return MockTTS(sample_rate=int(kwargs.get("sample_rate", 48000)))
    raise TTSError(f"unknown TTS engine: {engine}")
