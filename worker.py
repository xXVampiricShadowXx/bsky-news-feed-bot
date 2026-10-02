"""Background RSS polling and Bluesky publishing loop."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import db
from feeds import fetch_snapshot
from publisher import BlueskyPublisher


POLL_SECONDS = 90  # how often we check feeds for new stories
POST_CHECK_SECONDS = 5  # how often we check for anything due to post right now
MAX_POST_ATTEMPTS = 8
RETRY_MINUTES = [1, 5, 15, 60, 360, 720, 1440]


class BotWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._post_thread: threading.Thread | None = None
        self._publisher = BlueskyPublisher()
        self._caught_up_feed_ids: set[int] = set()
        self._poll_lock = threading.Lock()
        self.started_at = time.time()
        self.last_poll_finished_at: float | None = None
        self.last_publish_check_at: float | None = None

    def health(self) -> dict:
        """Liveness of both loops. A loop is stalled if it hasn't completed a cycle
        in several times its normal interval (a hung network call, deadlock, etc.)."""
        now = time.time()

        def loop_state(thread: threading.Thread | None, last: float | None, interval: float) -> dict:
            reference = last or self.started_at
            stalled = now - reference > max(interval * 6, 600)
            return {
                "alive": bool(thread and thread.is_alive()),
                "seconds_since_cycle": round(now - reference, 1) if last else None,
                "stalled": stalled,
            }

        poller = loop_state(self._thread, self.last_poll_finished_at, POLL_SECONDS)
        poster = loop_state(self._post_thread, self.last_publish_check_at, POST_CHECK_SECONDS)
        healthy = all(s["alive"] and not s["stalled"] for s in (poller, poster))
        return {"ok": healthy, "uptime_seconds": round(now - self.started_at), "poller": poller, "publisher": poster}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rss-poller", daemon=True)
        self._post_thread = threading.Thread(
            target=self._run_posts, name="bluesky-publisher", daemon=True
        )
        self._thread.start()
        self._post_thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            started_at = time.monotonic()
            try:
                self.poll_enabled_feeds()
            except Exception as exc:
                db.add_event("error", f"Feed poll recovered from an unexpected error: {exc}")
            self.last_poll_finished_at = time.time()
            self._stop.wait(max(0, POLL_SECONDS - (time.monotonic() - started_at)))

    def _run_posts(self) -> None:
        while not self._stop.is_set():
            try:
                if db.setting("autopost_enabled", "0") == "1":
                    self.publish_pending()
            except Exception as exc:
                db.add_event("error", f"Publisher recovered from an unexpected error: {exc}")
            self.last_publish_check_at = time.time()
            self._stop.wait(POST_CHECK_SECONDS)

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
        self._caught_up_feed_ids.add(feed_id)
        db.add_event(
            "info",
            f"Enabled {feed['source_name']} feed. Its current stories were set as the starting point.",
        )
        return count

    def poll_enabled_feeds(self) -> None:
        with self._poll_lock:
            self._poll_enabled_feeds()

    def _poll_enabled_feeds(self) -> None:
        auto_post = db.setting("autopost_enabled", "0") == "1"
        feeds = db.enabled_feeds()
        if not feeds:
            return

        # Each feed's first successful poll after startup establishes its baseline.
        # A failed request must not make a later successful poll queue the backlog.
        skipped = 0
        new_pending_ids: list[int] = []
        caught_up_now = 0

        # Fetch every feed concurrently (this is the slow, network-bound part) so one
        # slow or unresponsive feed doesn't stretch out the whole cycle for the rest.
        # All db reads/writes below still happen on this one worker thread, in the
        # as_completed loop, not inside the pool workers.
        with ThreadPoolExecutor(max_workers=min(8, len(feeds))) as pool:
            future_to_feed = {
                pool.submit(
                    fetch_snapshot,
                    feed["url"],
                    etag=feed["etag"],
                    last_modified=feed["last_modified"],
                ): feed
                for feed in feeds
            }
            for future in as_completed(future_to_feed):
                feed = future_to_feed[future]
                catching_up = feed["id"] not in self._caught_up_feed_ids
                try:
                    snapshot = future.result()
                    status = "baseline" if catching_up else "pending" if auto_post else "paused"
                    inserted = db.store_feed_items(
                        feed["id"],
                        [] if snapshot.not_modified else snapshot.entries,
                        status,
                        etag=snapshot.etag,
                        last_modified=snapshot.last_modified,
                    )
                    skipped += len(inserted) if catching_up else 0
                    queued = [item_id for item_id, actual_status in inserted if actual_status == "pending"]
                    new_pending_ids.extend(queued)
                    if queued:
                        db.add_event(
                            "info", f"Queued {len(queued)} new story(s) from {feed['source_name']}."
                        )
                    if catching_up:
                        self._caught_up_feed_ids.add(feed["id"])
                        caught_up_now += 1
                except Exception as exc:
                    if feed["last_error"] != str(exc):
                        db.add_event("error", f"Could not read {feed['source_name']} feed: {exc}")
                    db.update_feed_fetch(
                        feed["id"],
                        etag=feed["etag"],
                        last_modified=feed["last_modified"],
                        error=str(exc),
                    )

        if caught_up_now:
            db.add_event(
                "info",
                f"Established a fresh starting point for {caught_up_now} feed(s): "
                f"found {skipped} existing stories and left them unposted.",
            )
        if len(new_pending_ids) > 1:
            # Schedule by publication time, not the order network requests finish.
            gap = POLL_SECONDS / len(new_pending_ids)
            db.stagger_pending(new_pending_ids, gap)

    def publish_pending(self) -> None:
        # Do not read newly inserted rows until the poll has finished staggering them.
        with self._poll_lock:
            due_items = db.pending_items(limit=10)
        for item in due_items:
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
                    credit=item["credit"],
                    first_seen_at=item["created_at"],
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
                        f"Bluesky post failed for {item['source_name']}; retrying in {delay} "
                        f"minutes. ({type(exc).__name__}: {exc})",
                    )
                db.mark_post_failed(item["id"], attempts, str(exc), retry_at)
