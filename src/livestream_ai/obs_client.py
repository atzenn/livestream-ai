"""OBS Studio control via obs-websocket v5.

OBS 28+ has obs-websocket v5 built in. The protocol is a custom RPC
over WebSocket (not JSON-RPC 2.0): messages are JSON {op, d} with
op-codes. Default port 4455, enabled at Tools > WebSocket Server
Settings. OBS Studio 33.0.1 is the current stable (29/10/2026).

Controlled here: switch scene, start/stop stream and record, set the
text of the AI-disclosure overlay source. The password comes from the
OBS_WS_PASSWORD env var, never from the config file.

This module is client code only. A live OBS test needs OBS running on
the operator machine; it is NOT RUN on this headless VM. Unit tests
use a fake client (see tests/test_obs_client.py).
"""

from __future__ import annotations


class OBSError(RuntimeError):
    """Raised when OBS cannot be reached or a request fails."""


class OBSClient:
    """Thin wrapper around obsws_python.ReqClient (lazy import)."""

    def __init__(self, host: str = "localhost", port: int = 4455,
                 password: str = ""):
        self.host = host
        self.port = port
        self.password = password
        self._client = None

    def _connect(self):
        if self._client is None:
            try:
                from obsws_python import ReqClient
            except ImportError as exc:
                raise OBSError(
                    "obsws-python not installed. "
                    "Run: pip install obsws-python"
                ) from exc
            try:
                self._client = ReqClient(
                    host=self.host, port=self.port, password=self.password
                )
            except Exception as exc:
                raise OBSError(
                    f"cannot connect to OBS at {self.host}:{self.port}: "
                    f"{exc}"
                ) from exc
        return self._client

    def get_scene_list(self) -> list[str]:
        client = self._connect()
        resp = client.get_scene_list()
        return [s["sceneName"] for s in resp.scenes]

    def set_scene(self, scene_name: str) -> None:
        client = self._connect()
        client.set_current_program_scene(scene_name)

    def start_stream(self) -> None:
        client = self._connect()
        client.start_stream()

    def stop_stream(self) -> None:
        client = self._connect()
        client.stop_stream()

    def start_record(self) -> None:
        client = self._connect()
        client.start_record()

    def stop_record(self) -> None:
        client = self._connect()
        client.stop_record()

    def set_overlay_text(self, source_name: str, text: str) -> None:
        """Set the text of a Text (GDI+) source (AI disclosure overlay)."""
        client = self._connect()
        client.set_input_settings(
            source_name, {"text": text}, overlay=True
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None


class FakeOBSClient:
    """Offline stand-in used by tests and dry runs. Records calls."""

    def __init__(self):
        self.scenes: list[str] = []
        self.overlay: dict[str, str] = {}
        self.streaming = False
        self.recording = False

    def get_scene_list(self) -> list[str]:
        return list(self.scenes)

    def set_scene(self, scene_name: str) -> None:
        self.scenes.append(scene_name)

    def start_stream(self) -> None:
        self.streaming = True

    def stop_stream(self) -> None:
        self.streaming = False

    def start_record(self) -> None:
        self.recording = True

    def stop_record(self) -> None:
        self.recording = False

    def set_overlay_text(self, source_name: str, text: str) -> None:
        self.overlay[source_name] = text

    def close(self) -> None:
        pass
