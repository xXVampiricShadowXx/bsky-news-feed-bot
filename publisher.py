"""Bluesky posting using an app password and a source-logo image attachment."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from atproto import Client, client_utils


class BlueskyPublisher:
    def __init__(self) -> None:
        self.handle = os.getenv("BLUESKY_HANDLE", "oninews.bsky.social").strip()
        self.app_password = os.getenv("BLUESKY_APP_PASSWORD", "").strip()
        self._client: Client | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.handle and self.app_password)

    def _get_client(self) -> Client:
        if not self.configured:
            raise RuntimeError(
                "Bluesky is not configured. Add a Bluesky app password to the local .env file."
            )
        with self._lock:
            if self._client is None:
                client = Client()
                client.login(self.handle, self.app_password)
                self._client = client
            return self._client

    def check_connection(self) -> str:
        self._get_client()
        return self.handle

    def post_story(
        self,
        *,
        headline: str,
        source_name: str,
        article_url: str,
        logo_path: Path | None,
        logo_alt: str,
    ) -> str:
        client = self._get_client()
        lead = f"{headline}\n\n(Source: {source_name})\n\n"
        if len(lead) + len(article_url) > 300:
            raise ValueError(
                "The full headline, source line, and link exceed Bluesky's post-length limit. "
                "This story was left unposted rather than shortening the headline."
            )
        text = client_utils.TextBuilder().text(lead).link(article_url, article_url)
        if logo_path and logo_path.is_file():
            image_data = logo_path.read_bytes()
            result = client.send_image(
                text=text,
                image=image_data,
                image_alt=logo_alt or f"{source_name} logo",
                profile_identify=self.handle,
            )
        else:
            result = client.send_post(text=text, profile_identify=self.handle)
        return str(result.uri)
