"""Background RSS polling and Bluesky publishing loop."""

from __future__ import annotations

import os
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
BACKUP_SECONDS = 24 * 3600
REPOST_POLL_SECONDS = 15 * 60
REPOST_FEED_URI = os.getenv(
    "BLUESKY_REPOST_FEED_URI",
    "at://did:plc:uwnijebztfbgkyeqkhp2ddmx/app.bsky.feed.generator/ap-world-news",
)
REPOST_ACCOUNT_DID = os.getenv(
    "BLUESKY_REPOST_ACCOUNT_DID", "did:plc:uwnijebztfbgkyeqkhp2ddmx"
)


class BotWorker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._post_thread: threading.Thread | None = None
        self._repost_thread: threading.Thread | None = None
        self._publisher = BlueskyPublisher()
        self._caught_up_feed_ids: set[int] = set()
        self._repost_caught_up = False
        self._poll_lock = threading.Lock()
        self.started_at = time.time()
        self.last_poll_finished_at: float | None = None
        self.last_publish_check_at: float | None = None
        self.last_repost_poll_finished_at: float | None = None
        self._last_backup_at: float | None = None

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
        reposter = loop_state(
            self._repost_thread, self.last_repost_poll_finished_at, REPOST_POLL_SECONDS
        )
        healthy = all(s["alive"] and not s["stalled"] for s in (poller, poster, reposter))
        return {
            "ok": healthy,
            "uptime_seconds": round(now - self.started_at),
            "poller": poller,
            "publisher": poster,
            "reposter": reposter,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rss-poller", daemon=True)
        self._post_thread = threading.Thread(
            target=self._run_posts, name="bluesky-publisher", daemon=True
        )
        self._repost_thread = threading.Thread(
            target=self._run_reposts, name="bluesky-reposter", daemon=True
        )
        self._thread.start()
        self._post_thread.start()
        self._repost_thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            started_at = time.monotonic()
            try:
                self.poll_enabled_feeds()
            except Exception as exc:
                db.add_event("error", f"Feed poll recovered from an unexpected error: {exc}")
            self._maybe_backup()
            self.last_poll_finished_at = time.time()
            self._stop.wait(max(0, POLL_SECONDS - (time.monotonic() - started_at)))

    def _maybe_backup(self) -> None:
        if self._last_backup_at and time.time() - self._last_backup_at < BACKUP_SECONDS:
            return
        self._last_backup_at = time.time()
        try:
            db.backup_database()
        except Exception as exc:
            db.add_event("error", f"Daily database backup failed: {exc}")

    def _run_posts(self) -> None:
        while not self._stop.is_set():
            try:
                if db.setting("autopost_enabled", "0") == "1":
                    self.publish_pending()
            except Exception as exc:
                db.add_event("error", f"Publisher recovered from an unexpected error: {exc}")
            self.last_publish_check_at = time.time()
            self._stop.wait(POST_CHECK_SECONDS)

    def _run_reposts(self) -> None:
        while not self._stop.is_set():
            started_at = time.monotonic()
            try:
                if db.setting("reposting_enabled", "0") == "1":
                    if self._repost_caught_up:
                        self.poll_custom_repost_feed()
                    else:
                        self.activate_custom_repost_feed()
            except Exception as exc:
                db.add_event("error", f"Custom-feed repost poll failed: {exc}")
            self.last_repost_poll_finished_at = time.time()
            self._stop.wait(max(0, REPOST_POLL_SECONDS - (time.monotonic() - started_at)))

    def _custom_repost_feed_items(self):
        self._publisher.check_repost_account(REPOST_ACCOUNT_DID)
        feed_items = []
        cursor = None
        seen_cursors = set()
        while True:
            response = self._publisher.get_feed(REPOST_FEED_URI, cursor)
            feed_items.extend(getattr(response, "feed", []) or [])
            cursor = getattr(response, "cursor", None)
            if not cursor:
                break
            if cursor in seen_cursors:
                raise RuntimeError("Bluesky returned a repeated custom-feed cursor.")
            seen_cursors.add(cursor)
        return feed_items

    def activate_custom_repost_feed(self) -> int:
        """Set a feed baseline so existing posts are never replayed after startup."""
        feed_items = self._custom_repost_feed_items()
        posts = []
        for item in feed_items:
            post = getattr(item, "post", None)
            uri = getattr(post, "uri", None) if post else None
            cid = getattr(post, "cid", None) if post else None
            if not uri:
                db.add_event("error", "Skipped a custom-feed baseline item without a post URI.")
                continue
            posts.append((uri, cid or ""))
        db.seed_reposts(posts)
        self._repost_caught_up = True
        db.add_event(
            "info",
            f"Established the custom-feed repost baseline with {len(posts)} existing post(s); "
            "they were not reposted.",
        )
        return len(posts)

    def poll_custom_repost_feed(self) -> None:
        feed_items = self._custom_repost_feed_items()
        for item in reversed(feed_items):
            if db.setting("reposting_enabled", "0") != "1":
                return
            post = getattr(item, "post", None)
            uri = getattr(post, "uri", None) if post else None
            cid = getattr(post, "cid", None) if post else None
            if not uri:
                db.add_event("error", "Skipped a custom-feed item without a post URI.")
                continue
            if not db.claim_repost(uri, cid or ""):
                continue
            if not cid:
                error = "Feed item has no CID."
                db.finish_repost(uri, error=error)
                db.add_event("error", f"Could not repost {uri}: {error}")
                continue
            try:
                repost_uri = self._publisher.repost(uri, cid, REPOST_ACCOUNT_DID)
                db.finish_repost(uri, repost_uri=repost_uri)
                db.add_event("success", f"Reposted custom-feed item {uri}.")
            except Exception as exc:
                db.finish_repost(uri, error=f"{type(exc).__name__}: {exc}")
                db.add_event("error", f"Could not repost {uri}; it will not be retried: {exc}")

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
