"""SQLite persistence for publishers, feeds, items, and bot activity."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from feeds import canonical_story_key


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = Path(os.getenv("BOT_DATA_DIR", str(BASE_DIR / "instance"))).resolve()
LOGO_DIR = INSTANCE_DIR / "logos"
DB_PATH = INSTANCE_DIR / "bot.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                logo_filename TEXT NOT NULL,
                logo_alt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                url TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 0,
                etag TEXT,
                last_modified TEXT,
                last_checked TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
                item_key TEXT NOT NULL,
                headline TEXT NOT NULL,
                article_url TEXT NOT NULL,
                published_at TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                bsky_uri TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(feed_id, item_key)
            );

            CREATE TABLE IF NOT EXISTS seen_stories (
                story_key TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_feeds_enabled ON feeds(enabled);
            CREATE INDEX IF NOT EXISTS idx_items_status_due ON items(status, next_attempt_at);
            CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
            """
        )
        existing_items = conn.execute(
            "SELECT id, article_url, status, created_at FROM items ORDER BY id"
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for item in existing_items:
            story_key = canonical_story_key(item["article_url"])
            if not story_key:
                continue
            grouped.setdefault(story_key, []).append(item)
            conn.execute(
                "INSERT OR IGNORE INTO seen_stories(story_key, first_seen_at) VALUES(?, ?)",
                (story_key, item["created_at"]),
            )
        for rows in grouped.values():
            pending_rows = [row for row in rows if row["status"] == "pending"]
            has_seen_nonpending_copy = any(row["status"] != "pending" for row in rows)
            duplicates = pending_rows if has_seen_nonpending_copy else pending_rows[1:]
            for duplicate in duplicates:
                conn.execute(
                    "UPDATE items SET status = 'duplicate' WHERE id = ?",
                    (duplicate["id"],),
                )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('autopost_enabled', '0')"
        )


def setting(key: str, default: str = "") -> str:
    with connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def pause_pending_items() -> None:
    with connection() as conn:
        conn.execute("UPDATE items SET status = 'paused' WHERE status = 'pending'")


def add_event(level: str, message: str) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO events(level, message, created_at) VALUES(?, ?, ?)",
            (level, message[:500], utc_now()),
        )


def list_events(limit: int = 12) -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def add_source(name: str, logo_filename: str, logo_alt: str) -> int:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO sources(name, logo_filename, logo_alt, created_at) "
            "VALUES(?, ?, ?, ?)",
            (name, logo_filename, logo_alt, utc_now()),
        )
        return int(cursor.lastrowid)


def list_sources() -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            """SELECT s.*, COUNT(f.id) AS feed_count
               FROM sources s LEFT JOIN feeds f ON f.source_id = s.id
               GROUP BY s.id ORDER BY lower(s.name)"""
        ).fetchall()


def get_source(source_id: int) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()


def delete_source(source_id: int) -> str | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT logo_filename FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            return row["logo_filename"]
    return None


def add_feed(source_id: int, url: str) -> int:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO feeds(source_id, url, enabled, created_at) VALUES(?, ?, 0, ?)",
            (source_id, url, utc_now()),
        )
        return int(cursor.lastrowid)


def list_feeds() -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            """SELECT f.*, s.name AS source_name,
                      (SELECT headline FROM items i WHERE i.feed_id = f.id
                       ORDER BY i.id DESC LIMIT 1) AS latest_headline
               FROM feeds f JOIN sources s ON s.id = f.source_id
               ORDER BY f.id DESC"""
        ).fetchall()


def get_feed(feed_id: int) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute(
            """SELECT f.*, s.name AS source_name, s.logo_filename, s.logo_alt
               FROM feeds f JOIN sources s ON s.id = f.source_id WHERE f.id = ?""",
            (feed_id,),
        ).fetchone()


def enabled_feeds() -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            """SELECT f.*, s.name AS source_name, s.logo_filename, s.logo_alt
               FROM feeds f JOIN sources s ON s.id = f.source_id
               WHERE f.enabled = 1 ORDER BY f.id"""
        ).fetchall()


def update_feed_fetch(
    feed_id: int,
    *,
    etag: str | None,
    last_modified: str | None,
    error: str | None = None,
) -> None:
    with connection() as conn:
        conn.execute(
            """UPDATE feeds SET etag = ?, last_modified = ?, last_checked = ?, last_error = ?
               WHERE id = ?""",
            (etag, last_modified, utc_now(), error[:500] if error else None, feed_id),
        )


def enable_feed_and_seed(
    feed_id: int,
    *,
    etag: str | None,
    last_modified: str | None,
    items: list[dict],
) -> int:
    """Enable a feed and mark its current contents as baseline, never backfill them."""
    with connection() as conn:
        for item in items:
            seen = conn.execute(
                "INSERT OR IGNORE INTO seen_stories(story_key, first_seen_at) VALUES(?, ?)",
                (item["key"], utc_now()),
            )
            if seen.rowcount != 1:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO items
                   (feed_id, item_key, headline, article_url, published_at, status, created_at)
                   VALUES(?, ?, ?, ?, ?, 'baseline', ?)""",
                (
                    feed_id,
                    item["key"],
                    item["headline"],
                    item["url"],
                    item.get("published_at"),
                    utc_now(),
                ),
            )
        conn.execute(
            "UPDATE items SET status = 'baseline' WHERE feed_id = ? AND status IN ('pending', 'paused')",
            (feed_id,),
        )
        conn.execute(
            """UPDATE feeds SET enabled = 1, etag = ?, last_modified = ?,
               last_checked = ?, last_error = NULL WHERE id = ?""",
            (etag, last_modified, utc_now(), feed_id),
        )
    return len(items)


def disable_feed(feed_id: int) -> None:
    with connection() as conn:
        conn.execute("UPDATE feeds SET enabled = 0 WHERE id = ?", (feed_id,))
        conn.execute(
            "UPDATE items SET status = 'paused' WHERE feed_id = ? AND status = 'pending'",
            (feed_id,),
        )


def delete_feed(feed_id: int) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))


def store_item(feed_id: int, item: dict, status: str) -> bool:
    with connection() as conn:
        auto_post = conn.execute(
            "SELECT value FROM settings WHERE key = 'autopost_enabled'"
        ).fetchone()
        if status == "pending" and (not auto_post or auto_post["value"] != "1"):
            status = "paused"
        seen = conn.execute(
            "INSERT OR IGNORE INTO seen_stories(story_key, first_seen_at) VALUES(?, ?)",
            (item["key"], utc_now()),
        )
        if seen.rowcount != 1:
            return False
        cursor = conn.execute(
            """INSERT OR IGNORE INTO items
               (feed_id, item_key, headline, article_url, published_at, status, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (
                feed_id,
                item["key"],
                item["headline"],
                item["url"],
                item.get("published_at"),
                status,
                utc_now(),
            ),
        )
        return cursor.rowcount == 1


def pending_items(limit: int = 10) -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            """SELECT i.*, f.source_id, f.enabled, s.name AS source_name,
                      s.logo_filename, s.logo_alt
               FROM items i JOIN feeds f ON f.id = i.feed_id
               JOIN sources s ON s.id = f.source_id
               WHERE i.status = 'pending' AND f.enabled = 1
                 AND (i.next_attempt_at IS NULL OR i.next_attempt_at <= ?)
               ORDER BY COALESCE(i.published_at, i.created_at), i.id LIMIT ?""",
            (utc_now(), limit),
        ).fetchall()


def mark_posted(item_id: int, bsky_uri: str) -> None:
    with connection() as conn:
        conn.execute(
            """UPDATE items SET status = 'posted', bsky_uri = ?, last_error = NULL
               WHERE id = ?""",
            (bsky_uri, item_id),
        )


def mark_post_failed(item_id: int, attempts: int, error: str, retry_at: str | None) -> None:
    status = "failed" if retry_at is None else "pending"
    with connection() as conn:
        conn.execute(
            """UPDATE items SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?
               WHERE id = ?""",
            (status, attempts, retry_at, error[:500], item_id),
        )


def recent_posts(limit: int = 8) -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            """SELECT i.*, s.name AS source_name
               FROM items i JOIN feeds f ON f.id = i.feed_id
               JOIN sources s ON s.id = f.source_id
               WHERE i.status IN ('posted', 'failed')
               ORDER BY i.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()


def counts() -> dict[str, int]:
    with connection() as conn:
        return {
            "sources": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "feeds": conn.execute("SELECT COUNT(*) FROM feeds").fetchone()[0],
            "active_feeds": conn.execute("SELECT COUNT(*) FROM feeds WHERE enabled = 1").fetchone()[0],
            "posted": conn.execute("SELECT COUNT(*) FROM items WHERE status = 'posted'").fetchone()[0],
        }
