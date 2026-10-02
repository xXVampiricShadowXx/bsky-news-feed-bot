import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import db
import worker


def story(key):
    return {
        "key": f"https://example.com/{key}",
        "headline": key,
        "url": f"https://example.com/{key}",
        "published_at": None,
    }


class FeedStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.original_paths = db.INSTANCE_DIR, db.LOGO_DIR, db.DB_PATH
        self.addCleanup(self.restore_database)
        db.INSTANCE_DIR = Path(self.temp_dir.name)
        db.LOGO_DIR = db.INSTANCE_DIR / "logos"
        db.DB_PATH = db.INSTANCE_DIR / "test.sqlite3"
        db._conn = None
        db.init_db()
        source_id = db.add_source("Example", "example.webp", "Example logo")
        self.feed_id = db.add_feed(source_id, "https://example.com/rss")
        db.enable_feed_and_seed(self.feed_id, etag=None, last_modified=None, items=[])

    def restore_database(self):
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        db.INSTANCE_DIR, db.LOGO_DIR, db.DB_PATH = self.original_paths

    def test_batch_deduplicates_and_records_fetch_metadata(self):
        db.set_setting("autopost_enabled", "1")
        inserted = db.store_feed_items(
            self.feed_id,
            [story("first"), story("first"), story("second")],
            "pending",
            etag="etag-1",
            last_modified=None,
        )
        self.assertEqual(2, len(inserted))
        self.assertTrue(all(status == "pending" for _, status in inserted))
        self.assertEqual("etag-1", db.get_feed(self.feed_id)["etag"])
        self.assertEqual([], db.store_feed_items(
            self.feed_id, [story("first")], "pending", etag="etag-2", last_modified=None
        ))
        self.assertEqual("etag-2", db.get_feed(self.feed_id)["etag"])
        self.assertEqual(2, len(db.pending_items()))

    def test_pausing_or_disabling_feed_prevents_late_queueing(self):
        db.set_setting("autopost_enabled", "0")
        inserted = db.store_feed_items(
            self.feed_id, [story("paused")], "pending", etag=None, last_modified=None
        )
        self.assertEqual("paused", inserted[0][1])
        db.disable_feed(self.feed_id)
        self.assertEqual([], db.store_feed_items(
            self.feed_id, [story("after-disable")], "pending", etag=None, last_modified=None
        ))
        with db.connection() as conn:
            self.assertEqual(
                0, conn.execute(
                    "SELECT COUNT(*) FROM seen_stories WHERE story_key = ?",
                    (story("after-disable")["key"],),
                ).fetchone()[0]
            )

    def test_bad_batch_rolls_back_seen_keys_and_feed_metadata(self):
        with self.assertRaises(KeyError):
            db.store_feed_items(
                self.feed_id,
                [story("first"), {"key": "https://example.com/bad"}],
                "pending",
                etag="should-not-stick",
                last_modified=None,
            )
        with db.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM seen_stories").fetchone()[0])
        self.assertIsNone(db.get_feed(self.feed_id)["etag"])

    def test_staggering_uses_publication_order_not_arrival_order(self):
        db.set_setting("autopost_enabled", "1")
        newer = {**story("newer"), "published_at": "2026-10-01T10:00:00+00:00"}
        older = {**story("older"), "published_at": "2026-10-01T09:00:00+00:00"}
        inserted = db.store_feed_items(
            self.feed_id, [newer, older], "pending", etag=None, last_modified=None
        )
        db.stagger_pending([item_id for item_id, _ in inserted], 30)
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT headline, next_attempt_at FROM items ORDER BY published_at"
            ).fetchall()
        self.assertEqual("older", rows[0]["headline"])
        self.assertIsNone(rows[0]["next_attempt_at"])
        self.assertGreater(
            datetime.fromisoformat(rows[1]["next_attempt_at"]), datetime.now(timezone.utc)
        )


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.bot = worker.BotWorker()
        self.feed = {
            "id": 12, "url": "https://example.com/rss", "etag": None,
            "last_modified": None, "last_error": None, "source_name": "Example",
        }

    def test_failed_first_fetch_still_establishes_baseline(self):
        with patch.object(worker.db, "setting", return_value="1"), \
             patch.object(worker.db, "enabled_feeds", return_value=[self.feed]), \
             patch.object(worker.db, "update_feed_fetch"), \
             patch.object(worker.db, "add_event"), \
             patch.object(worker, "fetch_snapshot", side_effect=RuntimeError("offline")):
            self.bot.poll_enabled_feeds()
        self.assertNotIn(12, self.bot._caught_up_feed_ids)

        snapshot = SimpleNamespace(
            not_modified=False, entries=[story("old")], etag=None, last_modified=None
        )
        with patch.object(worker.db, "setting", return_value="1"), \
             patch.object(worker.db, "enabled_feeds", return_value=[self.feed]), \
             patch.object(worker.db, "store_feed_items", return_value=[(1, "baseline")]) as store, \
             patch.object(worker.db, "add_event"), \
             patch.object(worker, "fetch_snapshot", return_value=snapshot):
            self.bot.poll_enabled_feeds()
        self.assertEqual("baseline", store.call_args.args[2])
        self.assertIn(12, self.bot._caught_up_feed_ids)

    def test_recovery_does_not_prevent_other_feed_from_staggering(self):
        self.bot._caught_up_feed_ids.add(13)
        second_feed = {**self.feed, "id": 13, "url": "https://example.com/other"}
        snapshot = SimpleNamespace(not_modified=False, entries=[], etag=None, last_modified=None)
        with patch.object(worker.db, "setting", return_value="1"), \
             patch.object(worker.db, "enabled_feeds", return_value=[self.feed, second_feed]), \
             patch.object(
                 worker.db, "store_feed_items",
                 side_effect=lambda feed_id, *args, **kwargs:
                 [] if feed_id == 12 else [(1, "pending"), (2, "pending")]
             ), \
             patch.object(worker.db, "stagger_pending") as stagger, \
             patch.object(worker.db, "add_event"), \
             patch.object(worker, "fetch_snapshot", return_value=snapshot):
            self.bot.poll_enabled_feeds()
        stagger.assert_called_once_with([1, 2], worker.POLL_SECONDS / 2)

    def test_slow_publishing_does_not_block_polling(self):
        posting = threading.Event()
        release = threading.Event()
        polled_while_posting = threading.Event()
        poll_count = 0
        self.addCleanup(release.set)

        def block_post():
            posting.set()
            release.wait(3)

        def record_poll():
            nonlocal poll_count
            poll_count += 1
            if posting.is_set() and not release.is_set():
                polled_while_posting.set()

        with patch.object(worker.db, "setting", return_value="1"), \
             patch.object(worker, "POLL_SECONDS", 0.05), \
             patch.object(self.bot, "poll_enabled_feeds", side_effect=record_poll), \
             patch.object(self.bot, "publish_pending", side_effect=block_post):
            self.bot.start()
            try:
                self.assertTrue(posting.wait(2))
                self.assertTrue(polled_while_posting.wait(2))
                self.assertGreaterEqual(poll_count, 2)
            finally:
                self.bot._stop.set()
                release.set()
                self.bot._thread.join(3)
                self.bot._post_thread.join(3)

    def test_publisher_waits_for_completed_poll_before_reading_new_items(self):
        entered_poll = threading.Event()
        release_poll = threading.Event()
        read_pending = threading.Event()
        self.addCleanup(release_poll.set)

        def block_poll():
            entered_poll.set()
            release_poll.wait(3)

        def record_pending(*args, **kwargs):
            read_pending.set()
            return []

        with patch.object(self.bot, "_poll_enabled_feeds", side_effect=block_poll), \
             patch.object(worker.db, "pending_items", side_effect=record_pending):
            polling = threading.Thread(target=self.bot.poll_enabled_feeds)
            posting = threading.Thread(target=self.bot.publish_pending)
            polling.start()
            try:
                self.assertTrue(entered_poll.wait(2))
                posting.start()
                self.assertFalse(read_pending.wait(0.05))
            finally:
                release_poll.set()
                polling.join(3)
                if posting.ident is not None:
                    posting.join(3)
            self.assertTrue(read_pending.is_set())


if __name__ == "__main__":
    unittest.main()
