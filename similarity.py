"""Detect when a new headline is the same story the account already covered.

Outlets often publish one story several times: an article plus a video page, a
retitled update, or the same event reported by three broadcasters. URL keys can't
see that, so we compare headline wording. The threshold is deliberately strict:
genuinely new developments ("Bardella files lawsuit" after "Bardella denies
claims") share fewer words and still get posted.
"""

from __future__ import annotations

import re

WINDOW_HOURS = 18
MIN_SHARED = 4
MIN_JACCARD = 0.5

_STOP = frozenset(
    """a an the of in on at to for and or with as by from is are was were be been has have
    had after over into says say said its it this that than new amid about up out not will
    could would but who what how why live news latest watch wrap more his her their they he
    she we you our us video""".split()
)
_SUFFIXES = ("ing", "ed", "es", "s")


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def headline_tokens(headline: str) -> frozenset[str]:
    text = (headline or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"'s\b", "", text)
    return frozenset(
        _stem(word) for word in re.findall(r"[a-z0-9]+", text) if word not in _STOP and len(word) > 2
    )


def same_story(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    shared = len(a & b)
    return shared >= MIN_SHARED and shared / len(a | b) >= MIN_JACCARD
