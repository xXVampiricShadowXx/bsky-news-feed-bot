"""Project-wide identity settings shared by the dashboard, fetcher, and publisher."""

from __future__ import annotations

import os

VERSION = "1.0.0"
REPO_URL = "https://github.com/xXVampiricShadowXx/bsky-news-feed-bot"
USER_AGENT = f"BlueskyNewsFeedBot/{VERSION} (+{REPO_URL})"
DEFAULT_BOT_NAME = "News Feed Bot"


def bot_name() -> str:
    """Display name for the dashboard; set BOT_NAME in .env to brand your copy."""
    return " ".join(os.getenv("BOT_NAME", "").split())[:40] or DEFAULT_BOT_NAME


def bot_initials() -> str:
    words = [word for word in bot_name().replace("-", " ").split() if word[:1].isalnum()]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return (bot_name()[:2] or "NB").upper()
