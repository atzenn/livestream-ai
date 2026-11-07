"""YouTube Live Streaming API client (Phase 1 target platform).

Flow (YouTube Data API v3, Live Streaming API):
  liveBroadcasts.insert -> liveStreams.insert -> liveBroadcasts.bind
  -> liveBroadcasts.transition (testing -> live) -> transition
  (complete) when done.

Auth: OAuth 2.0 only for write calls. An API key alone cannot create
a broadcast. The operator creates a Google Cloud project, enables
YouTube Data API v3, downloads the OAuth client secrets JSON, and
runs the consent flow once on their own machine. The secrets file and
the token file live outside the repo and are referenced by path in
config. This module never creates credentials by itself.

Quota (default project, resets midnight Pacific): 10,000 units/day.
Live calls cost 1 unit each (insert/bind/transition/list for
liveBroadcasts and liveStreams); liveChatMessages.list costs 5,
insert/delete 20. Polling chat is the real quota cost, so the poll
interval is configurable and defaults to 10 s.

AI disclosure: there is no "AI use" field on liveBroadcasts.insert.
The equivalent is status.containsSyntheticMedia on the video resource,
set via videos.insert / videos.update. Whether a live broadcast gets
the "AI generated or altered" label automatically is still unverified
(study rev2 section 5.4, point 8) - the overlay + title/description
disclosure in this scaffold is the working disclosure path until a
live test proves otherwise.

No live call is made in this module's tests: request bodies are built
by pure functions and checked offline. Real calls need the operator's
verified channel (first live enable can take up to 24 h).
"""

from __future__ import annotations

from dataclasses import dataclass


class YouTubeError(RuntimeError):
    """Raised when the YouTube API call fails or auth is missing."""


# --- pure request-body builders (tested offline, no network) -------------

def build_broadcast_body(
    title: str,
    description: str,
    scheduled_start_time: str,
    privacy_status: str = "unlisted",
) -> dict:
    """Body for liveBroadcasts.insert."""
    return {
        "snippet": {
            "title": title,
            "description": description,
            "scheduledStartTime": scheduled_start_time,
        },
        "status": {"privacyStatus": privacy_status},
        "contentDetails": {
            "enableAutoStart": False,
            "enableAutoStop": True,
            "latencyPreference": "low",
        },
    }


def build_stream_body(
    title: str,
    ingestion_type: str = "rtmp",
    resolution: str = "1080p",
    frame_rate: str = "30fps",
) -> dict:
    """Body for liveStreams.insert."""
    return {
        "snippet": {"title": title},
        "cdn": {
            "ingestionType": ingestion_type,
            "resolution": resolution,
            "frameRate": frame_rate,
        },
    }


def build_synthetic_media_body(video_id: str) -> dict:
    """Body for videos.update to flag AI-generated content.

    status.containsSyntheticMedia is the video-resource field that
    corresponds to the Studio "AI use" disclosure.
    """
    return {
        "id": video_id,
        "status": {"containsSyntheticMedia": True},
    }


def build_bind_params(broadcast_id: str, stream_id: str) -> dict:
    return {
        "part": "id,contentDetails",
        "id": broadcast_id,
        "streamId": stream_id,
    }


def build_transition_params(broadcast_id: str, status: str) -> dict:
    if status not in ("testing", "live", "complete"):
        raise YouTubeError(f"invalid broadcast status: {status}")
    return {"part": "id,status", "id": broadcast_id, "broadcastStatus": status}


def build_chat_insert_body(live_chat_id: str, text: str) -> dict:
    """Body for liveChatMessages.insert (post one chat message)."""
    if not live_chat_id:
        raise YouTubeError("live_chat_id is empty")
    if not text.strip():
        raise YouTubeError("chat message text is empty")
    if len(text) > 200:
        raise YouTubeError("chat message text is over 200 characters")
    return {
        "snippet": {
            "liveChatId": live_chat_id,
            "type": "textMessageEvent",
            "textMessageDetails": {"messageText": text},
        }
    }


# --- live client (needs operator OAuth) -----------------------------------

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


@dataclass
class LiveBroadcast:
    broadcast_id: str
    stream_id: str
    stream_name: str
    ingestion_address: str
    watch_url: str = ""


class YouTubeLiveClient:
    """Live client. All methods need a working OAuth token."""

    def __init__(self, client_secrets_path: str, token_path: str):
        self.client_secrets_path = client_secrets_path
        self.token_path = token_path
        self._service = None

    def _service_or_raise(self):
        if self._service is not None:
            return self._service
        if not self.client_secrets_path:
            raise YouTubeError(
                "youtube.client_secrets_path is empty in config. "
                "Create a Google Cloud project, enable YouTube Data "
                "API v3, download the OAuth client secrets JSON, and "
                "set its path in config. This step needs the operator."
            )
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise YouTubeError(
                "google-api-python-client / google-auth-oauthlib not "
                "installed. Run: pip install google-api-python-client "
                "google-auth-oauthlib"
            ) from exc
        import os

        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(
                self.token_path, SCOPES
            )
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_path, SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(self.token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    def create_broadcast(
        self,
        title: str,
        description: str,
        scheduled_start_time: str,
        privacy_status: str = "unlisted",
        latency: str = "low",
    ) -> str:
        service = self._service_or_raise()
        body = build_broadcast_body(
            title, description, scheduled_start_time, privacy_status
        )
        body["contentDetails"]["latencyPreference"] = latency
        resp = (
            service.liveBroadcasts()
            .insert(part="snippet,status,contentDetails", body=body)
            .execute()
        )
        return resp["id"]

    def create_stream(self, title: str) -> dict:
        service = self._service_or_raise()
        body = build_stream_body(title)
        resp = (
            service.liveStreams()
            .insert(part="snippet,cdn,status", body=body)
            .execute()
        )
        return {
            "id": resp["id"],
            "stream_name": resp["cdn"]["ingestionInfo"]["streamName"],
            "ingestion_address": resp["cdn"]["ingestionInfo"][
                "ingestionAddress"
            ],
        }

    def bind(self, broadcast_id: str, stream_id: str) -> None:
        service = self._service_or_raise()
        params = build_bind_params(broadcast_id, stream_id)
        service.liveBroadcasts().bind(**params).execute()

    def transition(self, broadcast_id: str, status: str) -> None:
        service = self._service_or_raise()
        params = build_transition_params(broadcast_id, status)
        service.liveBroadcasts().transition(**params).execute()

    def mark_synthetic_media(self, video_id: str) -> None:
        """Flag the video as AI-generated (Studio 'AI use' equivalent)."""
        service = self._service_or_raise()
        body = build_synthetic_media_body(video_id)
        service.videos().update(part="status", body=body).execute()

    def list_chat_messages(
        self, live_chat_id: str, page_token: str = ""
    ) -> dict:
        service = self._service_or_raise()
        req = service.liveChatMessages().list(
            liveChatId=live_chat_id,
            part="snippet,authorDetails",
            pageToken=page_token or None,
        )
        return req.execute()

    def insert_chat_message(self, live_chat_id: str, text: str) -> str:
        """Post one message to live chat. Returns the message id."""
        service = self._service_or_raise()
        body = build_chat_insert_body(live_chat_id, text)
        resp = (
            service.liveChatMessages()
            .insert(part="snippet", body=body)
            .execute()
        )
        return resp["id"]
