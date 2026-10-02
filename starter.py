"""One-click import of the curated public-media source list in starter_sources.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import db
import logos

STARTER_FILE = Path(__file__).resolve().parent / "starter_sources.json"


@dataclass
class ImportResult:
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    generated_logos: list[str] = field(default_factory=list)


def load_starter_sources(path: Path = STARTER_FILE) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [entry for entry in data["sources"] if entry.get("name") and entry.get("feed")]


def _existing() -> tuple[set[str], set[str]]:
    with db.connection() as conn:
        names = {row[0].casefold() for row in conn.execute("SELECT name FROM sources")}
        urls = {row[0] for row in conn.execute("SELECT url FROM feeds")}
    return names, urls


def import_starter_sources(
    activate_feed: Callable[[int], int],
    entries: list[dict] | None = None,
    discover: Callable[[str], bytes | None] = logos.discover_logo,
) -> ImportResult:
    """Add each starter source + feed that isn't already present and switch it on.

    `activate_feed` is BotWorker.activate_feed: it fetches the feed and marks the
    stories already in it as the starting point, so nothing old gets posted.
    """
    result = ImportResult()
    names, urls = _existing()
    for entry in entries if entries is not None else load_starter_sources():
        name = entry["name"]
        if name.casefold() in names or entry["feed"] in urls:
            result.skipped.append(name)
            continue
        filename = None
        source_id = None
        try:
            raw = discover(entry["website"]) if entry.get("website") else None
            if raw is None:
                raw = logos.generated_logo(name)
                result.generated_logos.append(name)
            filename, alt = logos.store_logo_bytes(raw, name)
            source_id = db.add_source(name, filename, alt)
            feed_id = db.add_feed(source_id, entry["feed"])
            activate_feed(feed_id)
            result.added.append(name)
            names.add(name.casefold())
            urls.add(entry["feed"])
        except Exception as exc:
            if source_id is not None:
                db.delete_source(source_id)
            if filename:
                (db.LOGO_DIR / filename).unlink(missing_ok=True)
            result.failed.append(f"{name} ({exc})")
    return result
