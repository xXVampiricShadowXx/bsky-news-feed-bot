"""RSS and Atom fetching/normalization. Story images and blurbs are intentionally ignored."""

from __future__ import annotations

import calendar
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser


USER_AGENT = "BskyNewsFeedBot/0.1 (+local RSS reader)"
TIMEOUT_SECONDS = 25


@dataclass
class FeedSnapshot:
    title: str
    website_url: str
    entries: list[dict]
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


def _validate_url(url: str) -> str:
    clean = url.strip()
    parsed = urllib.parse.urlsplit(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Feed URL must start with http:// or https://.")
    return clean


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]*>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _published(entry) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        stamp = calendar.timegm(parsed)
        return datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return None


def _article_url(link: str, base_url: str) -> str:
    if not link or not link.strip():
        return ""
    joined = urllib.parse.urljoin(base_url, link.strip())
    parsed = urllib.parse.urlsplit(joined)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    # Fragments do not identify a distinct article. Keep query parameters intact.
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


_TRACKING_QUERY_KEYS = {
    "dclid",
    "fbclid",
    "gbraid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "wbraid",
}


def canonical_story_key(url: str) -> str:
    """Normalize article URLs for global duplicate detection, without changing post links."""
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return url.strip()

    hostname = parsed.hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        return url.strip()
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    if port and port != default_port:
        hostname = f"{hostname}:{port}"

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query_pairs = [
        (key, value)
        for key, value in query_pairs
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    query_pairs.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
    query = urllib.parse.urlencode(query_pairs, doseq=True)

    # Scheme, www, fragments, trailing slashes, query order, and common tracking
    # parameters do not make a distinct story for this bot's duplicate guard.
    return urllib.parse.urlunsplit(("https", hostname, path, query, ""))


def fetch_snapshot(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedSnapshot:
    url = _validate_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml, */*;q=0.8",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError("Feed is larger than 4 MB; refusing to load it.")
            response_url = response.geturl()
            response_etag = response.headers.get("ETag") or etag
            response_modified = response.headers.get("Last-Modified") or last_modified
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FeedSnapshot("", "", [], etag, last_modified, not_modified=True)
        raise ValueError(f"Feed server returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"Could not fetch feed: {exc}") from exc

    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        detail = str(parsed.bozo_exception or "invalid XML")
        raise ValueError(f"Feed could not be parsed: {detail[:180]}")

    feed_title = _clean_text(parsed.feed.get("title"))
    website_url = _article_url(parsed.feed.get("link", ""), response_url)
    entries: list[dict] = []
    for index, entry in enumerate(parsed.entries):
        headline = _clean_text(entry.get("title"))
        raw_link = entry.get("link", "")
        if not raw_link:
            possible_id = entry.get("id") or entry.get("guid") or ""
            if str(possible_id).startswith(("http://", "https://")):
                raw_link = str(possible_id)
        article_link = _article_url(raw_link, response_url)
        if not headline or not article_link:
            continue
        entries.append(
            {
                # Use a normalized key for deduplication, but preserve the original
                # article URL for the clickable link in the post.
                "key": canonical_story_key(article_link),
                "headline": headline[:1000],
                "url": article_link,
                "published_at": _published(entry),
                "_order": index,
            }
        )

    # If feed timestamps exist, publish a burst of new items oldest-first.
    entries.sort(key=lambda item: (item["published_at"] or "", item["_order"]))
    for entry in entries:
        entry.pop("_order", None)

    return FeedSnapshot(
        title=feed_title,
        website_url=website_url,
        entries=entries,
        etag=response_etag,
        last_modified=response_modified,
    )
