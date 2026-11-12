import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import db
import feeds
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

    def test_sport_entries_are_filtered_only_when_geopolitics_only_is_enabled(self):
        body = b"""<rss version="2.0"><channel><title>Sports</title>
        <link>https://example.com/</link><item><title>Local derby</title>
        <link>https://example.com/news/sport/1</link><category>Sport</category>
        </item></channel></rss>"""
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body
        response.geturl.return_value = "https://example.com/rss"
        response.headers = {}
        with patch.object(feeds, "_public_url_opener") as opener:
            opener.return_value.open.return_value = response
            snapshot = feeds.fetch_snapshot("https://example.com/rss")

        db.set_setting("autopost_enabled", "1")
        self.assertTrue(snapshot.entries[0]["sport"])
        inserted = db.store_feed_items(
            self.feed_id, snapshot.entries, "pending", etag=None, last_modified=None
        )
        self.assertEqual("filtered", inserted[0][1])
        self.assertEqual([], db.pending_items())

        db.set_setting("geopolitics_only", "0")
        allowed = dict(
            snapshot.entries[0],
            key="https://example.com/news/sport/2",
            url="https://example.com/news/sport/2",
        )
        inserted = db.store_feed_items(
            self.feed_id, [allowed], "pending", etag=None, last_modified=None
        )
        self.assertEqual("pending", inserted[0][1])

    def test_story_key_upgrade_rekeys_retained_seen_story_history(self):
        old_dw_key = "https://dw.com/en/old-title/a-79462926"
        old_abc_key = "https://abc.net.au/news/2026-10-02/old-title/107221348"
        first_seen_at = "2026-09-30T09:00:00+00:00"
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO seen_stories(story_key, first_seen_at) VALUES(?, ?)",
                (old_dw_key, first_seen_at),
            )
            conn.execute(
                "INSERT INTO seen_stories(story_key, first_seen_at) VALUES(?, ?)",
                (old_abc_key, first_seen_at),
            )
            conn.execute(
                """INSERT INTO items
                   (feed_id, item_key, headline, article_url, status, created_at)
                   VALUES(?, ?, ?, ?, 'baseline', ?)""",
                (
                    self.feed_id,
                    old_dw_key,
                    "Old DW headline",
                    old_dw_key,
                    "2026-10-01T09:00:00+00:00",
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES('story_key_version', '3')"
            )

        db.init_db()

        with db.connection() as conn:
            stories = {
                row["story_key"]: row["first_seen_at"]
                for row in conn.execute("SELECT story_key, first_seen_at FROM seen_stories")
            }
        self.assertEqual(first_seen_at, stories["https://dw.com/a-79462926"])
        self.assertEqual(first_seen_at, stories["https://abc.net.au/news/107221348"])
        self.assertNotIn(old_dw_key, stories)
        self.assertNotIn(old_abc_key, stories)
        self.assertEqual(db.STORY_KEY_VERSION, db.setting("story_key_version"))

    def test_off_topic_stories_are_filtered_not_queued(self):
        db.set_setting("autopost_enabled", "1")
        off = dict(story("off"), geopolitical=False, topic_reason="not geopolitical")
        on = dict(story("on"), geopolitical=True)
        inserted = dict((s, i) for i, s in db.store_feed_items(
            self.feed_id, [off, on], "pending", etag=None, last_modified=None
        ))
        self.assertIn("filtered", inserted)
        self.assertIn("pending", inserted)
        self.assertEqual(1, len(db.pending_items()))
        db.set_setting("geopolitics_only", "0")
        inserted = db.store_feed_items(
            self.feed_id, [dict(off, key="k2", url="https://example.com/k2")], "pending",
            etag=None, last_modified=None,
        )
        self.assertEqual("pending", inserted[0][1])

    def test_same_story_from_another_url_is_marked_duplicate(self):
        db.set_setting("autopost_enabled", "1")
        first = dict(story("a"), headline="Israel-bound flight diverted after fight between pilots")
        repeat = dict(story("b"), headline="Flight to Israel diverts to Saudi Arabia as pilots fight")
        development = dict(story("c"), headline="Israel names suspect in flight attack")
        statuses = [s for _, s in db.store_feed_items(
            self.feed_id, [first, repeat, development], "pending", etag=None, last_modified=None
        )]
        self.assertEqual(["pending", "duplicate", "pending"], statuses)
        with db.connection() as conn:
            reason = conn.execute("SELECT last_error FROM items WHERE status = 'duplicate'").fetchone()[0]
        self.assertIn("Israel-bound flight diverted", reason)

    def test_backup_is_readable_and_old_copies_are_pruned(self):
        backup_dir = db.INSTANCE_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (backup_dir / f"bot-2000010{i}-000000.sqlite3").write_bytes(b"")
        target = db.backup_database(keep=2)
        remaining = sorted(p.name for p in backup_dir.glob("bot-*.sqlite3"))
        self.assertEqual(2, len(remaining))
        self.assertIn(target.name, remaining)
        import sqlite3
        copy = sqlite3.connect(target)
        self.addCleanup(copy.close)
        self.assertEqual(1, copy.execute("SELECT COUNT(*) FROM feeds").fetchone()[0])

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
        self.bot._last_backup_at = worker.time.time()  # never touch the real database
        self.feed = {
            "id": 12, "url": "https://example.com/rss", "etag": None,
            "last_modified": None, "last_error": None, "source_name": "Example",
        }

    def test_health_reports_stalled_or_dead_loops(self):
        self.assertFalse(self.bot.health()["ok"])  # threads never started
        alive = SimpleNamespace(is_alive=lambda: True)
        self.bot._thread = self.bot._post_thread = alive
        now = worker.time.time()
        self.bot.last_poll_finished_at = now
        self.bot.last_publish_check_at = now
        self.assertTrue(self.bot.health()["ok"])
        self.bot.last_poll_finished_at = now - 3600
        state = self.bot.health()
        self.assertFalse(state["ok"])
        self.assertTrue(state["poller"]["stalled"])

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
