"""Background RSS polling and Bluesky publishing loop."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import db
from feeds import fetch_snapshot
from publisher import BlueskyPublisher


POLL_SECONDS = 90
MAX_POST_ATTEMPTS = 8
RETRY_MINUTES = [1, 5, 15, 60, 360, 720, 1440]


class BotWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._publisher = BlueskyPublisher()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rss-publisher", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_enabled_feeds()
                if db.setting("autopost_enabled", "0") == "1":
                    self.publish_pending()
            except Exception as exc:  # Keep the worker alive after an unexpected feed/API error.
                db.add_event("error", f"Worker recovered from an unexpected error: {exc}")
            self._stop.wait(POLL_SECONDS)

    def activate_feed(self, feed_id: int) -> int:
        feed = db.get_feed(feed_id)
        if not feed:
            raise ValueError("Feed not found.")
        snapshot = fetch_snapshot(feed["url"])
        count = db.enable_feed_and_seed(
            feed_id,
            etag=snapshot.etag,
            last_modified=snapshot.last_modified,
            items=snapshot.entries,
        )
        db.add_event(
            "info",
            f"Enabled {feed['source_name']} feed. Its current stories were set as the starting point.",
        )
        return count

    def poll_enabled_feeds(self) -> None:
        auto_post = db.setting("autopost_enabled", "0") == "1"
        for feed in db.enabled_feeds():
            try:
                snapshot = fetch_snapshot(
                    feed["url"], etag=feed["etag"], last_modified=feed["last_modified"]
                )
                if not snapshot.not_modified:
                    for item in snapshot.entries:
                        status = "pending" if auto_post else "paused"
                        inserted = db.store_item(feed["id"], item, status)
                        if inserted and auto_post:
                            db.add_event(
                                "info", f"Queued a new story from {feed['source_name']}."
                            )
                db.update_feed_fetch(
                    feed["id"],
                    etag=snapshot.etag,
                    last_modified=snapshot.last_modified,
                )
            except Exception as exc:
                if feed["last_error"] != str(exc):
                    db.add_event("error", f"Could not read {feed['source_name']} feed: {exc}")
                db.update_feed_fetch(
                    feed["id"],
                    etag=feed["etag"],
                    last_modified=feed["last_modified"],
                    error=str(exc),
                )

    def publish_pending(self) -> None:
        for item in db.pending_items(limit=10):
            if db.setting("autopost_enabled", "0") != "1":
                return
            current_feed = db.get_feed(item["feed_id"])
            if not current_feed or not current_feed["enabled"]:
                continue
            try:
                source = db.get_source(item["source_id"])
                logo_path = None
                if source:
                    logo_path = db.LOGO_DIR / source["logo_filename"]
                post_uri = self._publisher.post_story(
                    headline=item["headline"],
                    source_name=item["source_name"],
                    article_url=item["article_url"],
                    logo_path=logo_path,
                    logo_alt=item["logo_alt"],
                )
                db.mark_posted(item["id"], post_uri)
                db.add_event("success", f"Posted a story from {item['source_name']} to Bluesky.")
            except Exception as exc:
                attempts = int(item["attempts"]) + 1
                permanent_content_error = isinstance(exc, ValueError)
                if attempts >= MAX_POST_ATTEMPTS or permanent_content_error:
                    retry_at = None
                    db.add_event(
                        "error",
                        f"A {item['source_name']} story could not be posted: {exc}",
                    )
                else:
                    delay = RETRY_MINUTES[min(attempts - 1, len(RETRY_MINUTES) - 1)]
                    retry_at = (
                        datetime.now(timezone.utc) + timedelta(minutes=delay)
                    ).isoformat(timespec="seconds")
                    db.add_event(
                        "error",
                        f"Bluesky post failed for {item['source_name']}; retrying in {delay} minutes.",
                    )
                db.mark_post_failed(item["id"], attempts, str(exc), retry_at)
