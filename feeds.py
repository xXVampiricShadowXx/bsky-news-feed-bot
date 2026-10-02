"""RSS and Atom fetching/normalization. Story images and blurbs are intentionally ignored."""

from __future__ import annotations

import calendar
import html
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser

import topics


from config import USER_AGENT
from netsafe import _public_url_opener
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


# Wire/news agencies whose copy public broadcasters often republish. Matched only
# against the byline (author) field: summaries are full of photo captions like
# "(AP Photo/...)" or "AFP via Getty Images" that say nothing about who wrote the text.
_WIRE_AGENCIES: tuple[tuple[re.Pattern[str], str], ...] = (
    # AAP first, and removed before the rest, so it is never also reported as AP.
    (re.compile(r"\bAustralian\s+Associated\s+Press\b|\bAAP\b", re.I), "AAP"),
    (re.compile(r"\bassociated\s+press\b|(?:^|,|\bby\s|\bvia\s|\bwith\s)\s*AP\s*$", re.I), "Associated Press"),
    (re.compile(r"\breuters\b", re.I), "Reuters"),
    (re.compile(r"\bagence\s+france[- ]presse\b|\bAFP\b", re.I), "AFP"),
    (re.compile(r"\bcanadian\s+press\b", re.I), "The Canadian Press"),
    (re.compile(r"\bPA\s+Media\b|\bPress\s+Association\b", re.I), "PA Media"),
    (re.compile(r"\bDeutsche\s+Presse-Agentur\b|\bdpa\b"), "dpa"),
    (re.compile(r"\bKyodo\b", re.I), "Kyodo News"),
    (re.compile(r"\bYonhap\b", re.I), "Yonhap"),
)


def _byline(entry) -> str:
    names = [entry.get("author") or ""]
    for author in entry.get("authors") or []:
        if isinstance(author, dict):
            names.append(author.get("name") or "")
    for key in ("dc_creator", "creator"):
        names.append(str(entry.get(key) or ""))
    return " ; ".join(_clean_text(name) for name in names if name)


def wire_credit(byline: str) -> str | None:
    """Return the wire agency credited in a byline, e.g. "Jane Doe, Associated Press"."""
    found: list[str] = []
    for name in (byline or "").split(";"):
        remaining = name.strip()
        for pattern, label in _WIRE_AGENCIES:
            if pattern.search(remaining):
                remaining = pattern.sub(" ", remaining)
                if label not in found:
                    found.append(label)
    return " and ".join(found[:2]) or None


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


_STABLE_ID_PATHS: dict[str, tuple[re.Pattern[str], str]] = {
    "dw.com": (re.compile(r"/a-(\d+)$"), "/a-{}"),
    "abc.net.au": (re.compile(r"^/news/(?:[^/]+/)*(\d{6,})$"), "/news/{}"),
    "rte.ie": (re.compile(r"^/news/(?:[^/]+/)*(\d{6,})-[^/]*$"), "/news/{}"),
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

    # Some publishers rewrite a story's headline slug while keeping its numeric id
    # (DW: /en/<slug>/a-79401752, ABC: /news/2026-10-02/<slug>/107221348). Key on
    # the id alone so a retitled story is not treated as a brand-new one.
    stable_id = _STABLE_ID_PATHS.get(hostname)
    if stable_id:
        match = stable_id[0].search(path)
        if match:
            return f"https://{hostname}{stable_id[1].format(match.group(1))}"

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


_SPORT_TERMS = {"sport", "sports"}
_SPORT_PATH_SEGMENTS = {"sport", "sports", "football"}


def is_sport(entry, article_url: str) -> bool:
    """Sport coverage falls outside the bot's global/breaking news remit."""
    for tag in entry.get("tags") or []:
        if str(tag.get("term") or "").strip().lower() in _SPORT_TERMS:
            return True
    segments = urllib.parse.urlsplit(article_url).path.lower().split("/")
    return any(segment in _SPORT_PATH_SEGMENTS for segment in segments)


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
        "Accept-Encoding": "gzip",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(url, headers=headers)

    try:
        with _public_url_opener().open(request, timeout=TIMEOUT_SECONDS) as response:
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

    # Some servers (UN News) gzip the body even without a Content-Encoding header.
    if raw[:2] == b"\x1f\x8b":
        try:
            inflater = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
            raw = inflater.decompress(raw, 4_000_001)
        except zlib.error as exc:
            raise ValueError(f"Feed could not be decompressed: {exc}") from exc
        if len(raw) > 4_000_000:
            raise ValueError("Feed is larger than 4 MB; refusing to load it.")

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
        if is_sport(entry, article_link):
            continue
        summary = _clean_text(entry.get("summary"))[:600]
        tags = [str(t.get("term") or "") for t in entry.get("tags") or []]
        relevant, _score, topic_reason = topics.assess(headline, summary, tags, article_link)
        entries.append(
            {
                # Use a normalized key for deduplication, but preserve the original
                # article URL for the clickable link in the post.
                "key": canonical_story_key(article_link),
                "headline": headline[:1000],
                "url": article_link,
                "published_at": _published(entry),
                "credit": wire_credit(_byline(entry)),
                "geopolitical": relevant,
                "topic_reason": topic_reason,
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
