"""Keep the bot focused on geopolitics: international relations, conflict, and power.

Scoring is deliberately transparent (keyword lists, no model) so a skipped or
accepted story can always be explained. Strong signals are worth 2 points,
supporting signals 1; a story needs THRESHOLD points from its headline,
summary, and feed categories. Off-topic markers (sport, celebrity, weather,
etc.) veto a story unless its geopolitical signal is overwhelming.
"""

from __future__ import annotations

import re

THRESHOLD = 3
VETO_OVERRIDE = 6

_STRONG = r"""
war wars warfare ceasefire cease-fire truce armistice invasion invade invaded invades
troops soldiers military militaries army navy airstrike airstrikes missile missiles drone
drones shelling bombardment frontline nuclear sanction sanctions embargo tariff tariffs
blockade diplomat diplomats diplomacy diplomatic embassy embassies ambassador envoy envoys
summit treaty accord negotiators coup junta regime dictator authoritarian annex annexation
annexed occupied occupation sovereignty territorial separatist separatists insurgent
insurgents insurgency rebels militia militias jihadist jihadists hostage hostages genocide
humanitarian refugee refugees asylum displaced geopolitical geopolitics secretary-general
president-elect espionage spy spies cyberattack superpower alliance allies bilateral
multilateral conflict conflicts election elections referendum terror terrorism terrorist terrorists hijacking
""".split()

_STRONG_PHRASES = [
    "prime minister", "martial law", "world leaders", "kim jong un",
    "air strike", "trade war", "peace talks", "peace deal", "war crimes", "ethnic cleansing",
    "foreign minister", "foreign ministers", "foreign policy", "defence minister",
    "defense minister", "secretary of state", "head of state", "heads of state",
    "security council", "general assembly", "front line", "human rights",
]

_ORGS = r"""
UN UNHCR UNICEF UNRWA UNSC NATO EU G7 G20 BRICS ASEAN OPEC IAEA ICC ICJ WTO IMF WHO
AU ECOWAS OSCE AUKUS Kremlin Pentagon Hamas Hezbollah Houthi Houthis Taliban IDF IRGC
""".split()

_ORG_NAMES = [
    "united nations", "european union", "african union", "security council",
    "world bank", "international criminal court", "white house", "downing street",
    "european commission", "arab league", "islamic state",
]

_LEADERS = r"""
Putin Trump Xi Zelensky Zelenskyy Netanyahu Modi Erdogan Erdo?an Macron Starmer Merz
Khamenei Pezeshkian Lukashenko Orb?n Orban Meloni Carney Albanese Lula Milei Ramaphosa
Guterres Rubio Lavrov Assad Sharaa Lecornu
""".split()

# Countries, regions, and demonyms. One mention is a supporting signal.
_PLACES = r"""
Afghanistan Afghan Albania Algeria Angola Argentina Armenia Australia Australian Austria
Azerbaijan Bahrain Bangladesh Belarus Belgium Bolivia Bosnia Brazil Brazilian Britain British
Bulgaria Myanmar Burma Cambodia Cameroon Canada Canadian Chad Chile China Chinese
Colombia Congo Croatia Cuba Cuban Cyprus Czech Denmark Danish Djibouti Ecuador Egypt Egyptian
Eritrea Estonia Ethiopia Ethiopian Finland France French Gabon Gaza Gazan Georgia Germany German
Ghana Greece Greek Greenland Guatemala Guinea Haiti Haitian Honduras Hungary Hungarian India Indian
Indonesia Iran Iranian Iraq Iraqi Ireland Irish Israel Israeli Israelis Italy Italian Japan Japanese
Jordan Kazakhstan Kenya Kosovo Kuwait Kyrgyzstan Laos Latvia Lebanon Lebanese Libya Libyan
Lithuania Mali Mexico Mexican Moldova Mongolia Montenegro Morocco Mozambique Namibia Nepal
Netherlands Dutch Nicaragua Niger Nigeria Nigerian Norway Oman Pakistan Pakistani Palestine
Palestinian Palestinians Panama Paraguay Peru Philippines Philippine Poland Polish Portugal Qatar
Romania Russia Russian Russians Rwanda Saudi Senegal Serbia Serbian Singapore Slovakia Slovenia
Somalia Somali Somaliland Spain Spanish Sudan Sudanese Sweden Swedish Switzerland Syria Syrian
Taiwan Taiwanese Tajikistan Tanzania Thailand Thai Tibet Tunisia Turkey Turkish Türkiye
Turkmenistan Uganda Ukraine Ukrainian Ukrainians UAE Emirates Uruguay Uzbekistan Venezuela
Venezuelan Vietnam Vietnamese Yemen Yemeni Zambia Zimbabwe Kashmir Crimea Donbas Kaliningrad
Crimean Kurdish Kurds Uyghur Uyghurs Rohingya Balkans Caucasus Sahel Kyiv Moscow Beijing
Tehran Jerusalem Washington Brussels Pyongyang Seoul Tokyo Taipei Damascus Beirut Baghdad
Kabul Khartoum Caracas Havana Minsk Ankara Riyadh Doha Cairo Islamabad Delhi
Arctic Baltic Balkan Hormuz US U.S. UK U.K.
""".split()

_PLACE_PHRASES = [
    "united states", "united kingdom", "north korea", "north korean", "south korea",
    "south korean", "south africa", "south sudan", "new zealand", "west bank", "middle east",
    "latin america", "south china sea", "red sea", "black sea", "hong kong", "sri lanka",
    "ivory coast", "burkina faso", "kim jong un", "wang yi", "central african republic", "democratic republic of congo", "saudi arabia",
    "el salvador", "costa rica", "dominican republic", "strait of hormuz", "taiwan strait",
]

_SUPPORT = r"""
elected vote votes voters ballot parliament parliamentary
president presidential government governments opposition protest protests protesters
crackdown minister ministers lawmakers legislature constitution constitutional independence
border borders migrants migration deport deportation deportations exports imports pipeline
weapons arms aid attack attacks killed casualties violence clashes unrest crisis famine
international leaders
""".split()

_GEO_TAGS = [
    "world politics", "unrest, conflict and war", "conflict", "war", "diplomacy",
    "foreign affairs", "foreign policy", "geopolitics", "international relations", "terrorism",
    "defence", "defense", "nuclear issues", "territorial disputes", "refugees", "human rights",
    "elections", "tariffs", "sanctions", "peace and security", "migrants and refugees",
]

# Region tags say where a story happened, not that it is geopolitical.
_REGION_TAGS = [
    "international", "middle east", "europe", "africa", "asia", "americas", "asia pacific",
    "ukraine", "russia", "israel", "gaza", "iran", "china", "trade",
]

_OFF_TOPIC = r"""
sport sports football soccer rugby cricket tennis golf afl nrl nba nfl mlb nhl olympic
olympics paralympics marathon premiership tournament championship celebrity celebrities
actor actress singer rapper album film movie oscars grammys emmys netflix recipe recipes
fashion horoscope lottery rainfall heatwave cyclone bushfire bushfires murder murdered
stabbing burglary theft jury sentenced inquest coroner
""".split()

_OFF_TOPIC_TAGS = [
    "sport", "sports", "entertainment", "arts and culture", "arts", "music", "film", "celebrity",
    "lifestyle", "food", "weather", "crime", "courts", "law, crime and justice", "health",
    "science", "technology", "business", "markets",
]


def _word_pattern(words, *, case_sensitive=False):
    escaped = sorted({re.escape(w) for w in words}, key=len, reverse=True)
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<![\w-])(?:" + "|".join(escaped) + r")(?![\w-])", flags)


_STRONG_RE = _word_pattern(_STRONG + _STRONG_PHRASES)
_ORG_RE = _word_pattern(_ORGS, case_sensitive=True)
_ORG_NAME_RE = _word_pattern(_ORG_NAMES)
_LEADER_RE = _word_pattern(_LEADERS, case_sensitive=True)
_PLACE_RE = _word_pattern(_PLACES, case_sensitive=True)
_PLACE_PHRASE_RE = _word_pattern(_PLACE_PHRASES)
_SUPPORT_RE = _word_pattern(_SUPPORT)
_OFF_TOPIC_RE = _word_pattern(_OFF_TOPIC + ["box office", "grand final", "royal baby", "weather forecast", "tv series"])


_NOT_MILITARY_RE = re.compile(r"\bhome[- ]invasions?\b|\bpest invasions?\b", re.IGNORECASE)


def _distinct(pattern, text):
    return {m.group(0).lower() for m in pattern.finditer(text)}


def assess(headline: str, summary: str = "", tags=(), url: str = "") -> tuple[bool, int, str]:
    """Return (is_geopolitical, score, short human-readable reason)."""
    headline = _NOT_MILITARY_RE.sub(" ", headline)
    summary = _NOT_MILITARY_RE.sub(" ", summary)
    text = f"{headline}\n{summary}"
    tag_terms = {str(t).strip().lower() for t in tags if t}

    strong = _distinct(_STRONG_RE, text) | _distinct(_ORG_RE, text) | _distinct(_ORG_NAME_RE, text)
    leaders = _distinct(_LEADER_RE, text)
    places = _distinct(_PLACE_RE, text) | _distinct(_PLACE_PHRASE_RE, text)
    support = _distinct(_SUPPORT_RE, text)
    geo_tags = tag_terms & set(_GEO_TAGS)
    places |= tag_terms & set(_REGION_TAGS)

    # Headline hits count double: the headline is what a reader sees.
    headline_strong = _distinct(_STRONG_RE, headline) | _distinct(_ORG_RE, headline) \
        | _distinct(_LEADER_RE, headline) | _distinct(_ORG_NAME_RE, headline)

    score = (
        2 * len(strong | leaders)
        + 2 * len(headline_strong)
        + min(len(places), 3)
        + min(len(support), 2)
        + 2 * min(len(geo_tags), 2)
    )

    off_topic = _distinct(_OFF_TOPIC_RE, headline) | (tag_terms & set(_OFF_TOPIC_TAGS))
    path = url.lower()
    if any(seg in path for seg in ("/sport/", "/sports/", "/football/", "/entertainment/",
                                   "/lifestyle/", "/culture/")):
        off_topic.add("section")

    signals = sorted(strong | leaders | geo_tags)[:4] or sorted(places | support)[:3]
    if off_topic and score < VETO_OVERRIDE:
        return False, score, "off-topic: " + ", ".join(sorted(off_topic)[:3])
    # Two countries in one headline usually means relations between states.
    headline_places = _distinct(_PLACE_RE, headline) | _distinct(_PLACE_PHRASE_RE, headline)
    core = strong or leaders or geo_tags or len(headline_places) >= 2
    if core and score >= THRESHOLD:
        return True, score, "geopolitics: " + ", ".join(signals)
    return False, score, "not geopolitical" + (f" (only: {', '.join(signals)})" if signals else "")
