"""Classify geopolitical stories from article text, independent of source metadata.

The score is diagnostic only; acceptance requires a specific international subject signal.
"""

from __future__ import annotations

import re

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
Guterres Rubio Lavrov Assad Sharaa Lecornu Hasina
""".split()

# Countries, regions, and demonyms. One mention is a supporting signal.
_PLACES = r"""
Afghanistan Afghan Albania Algeria Angola Argentina Armenia Australia Australian Austria
Azerbaijan Bahrain Bangladesh Belarus Belgium Bolivia Bosnia Brazil Brazilian Britain British
Bulgaria Myanmar Burma Cambodia Cameroon Canada Canadian Chad Chile China Chinese
Colombia Congo Croatia Cuba Cuban Cyprus Czech Denmark Danish Djibouti Ecuador Egypt Egyptian Europe European
Eritrea Estonia Ethiopia Ethiopian Finland France French Gabon Gaza Gazan Georgia Germany German
Ghana Greece Greek Greenland Guatemala Guinea Haiti Haitian Honduras Hungary Hungarian India Indian
Indonesia Iran Iranian Iraq Iraqi Ireland Irish Israel Israeli Israelis Italy Italian Japan Japanese
Jordan Kazakhstan Kenya Kosovo Kuwait Kyrgyzstan Laos Latvia Lebanon Lebanese Libya Libyan
Lithuania Mali Mexico Mexican Moldova Mongolia Montenegro Morocco Mozambique Namibia Nepal
Netherlands Dutch Nicaragua Niger Nigeria Nigerian Norway Oman Pakistan Pakistani Palestine
Palestinian Palestinians Panama Paraguay Paraguayan Peru Peruvian Philippines Philippine Filipino Poland Polish Portugal Portuguese Qatar Qatari
Romania Romanian Russia Russian Russians Rwanda Rwandan Saudi Senegalese Senegal Serbia Serbian Singapore Singaporean Slovakia Slovak Slovenia Slovenian
Somalia Somali Somaliland Spain Spanish Sudan Sudanese Sweden Swedish Switzerland Syria Syrian
Taiwan Taiwanese Tajikistan Tanzania Thailand Thai Tibet Tunisia Turkey Turkish Türkiye
Turkmenistan Uganda Ukraine Ukrainian Ukrainians UAE Emirates Uruguay Uruguayan Uzbekistan Uzbek Venezuela
Venezuelan Vietnam Vietnamese Yemen Yemeni Zambia Zambian Zimbabwe Zimbabwean Kashmir Crimea Donbas Kaliningrad
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
    "el salvador", "costa rica", "dominican republic", "strait of hormuz", "taiwan strait", "gulf of aden",
]

_SUPPORT = r"""
elected vote votes voters ballot parliament parliamentary
president presidential government governments opposition protest protests protesters
crackdown minister ministers lawmakers legislature constitution constitutional independence
border borders migrants migration deport deportation deportations exports imports pipeline
weapons arms aid attack attacks killed casualties violence clashes unrest crisis famine
international leaders
""".split()

_OFF_TOPIC = r"""
sport sports football soccer rugby cricket tennis golf afl nrl nba nfl mlb nhl olympic
olympics paralympics marathon premiership tournament championship celebrity celebrities
actor actress singer rapper album concert concerts tour touring film movie oscars grammys emmys netflix recipe recipes
fashion horoscope lottery rainfall heatwave cyclone bushfire bushfires murder murdered
stabbing burglary theft jury sentenced inquest coroner
""".split()

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
_OFF_TOPIC_RE = _word_pattern(
    _OFF_TOPIC + ["box office", "grand final", "royal baby", "weather forecast", "tv series",
                  "premier league", "man city"]
)


_NOT_MILITARY_RE = re.compile(r"\bhome[- ]invasions?\b|\bpest invasions?\b", re.IGNORECASE)

# Keep phrases intact: never put multi-word entries in a whitespace-split block.
_WAR_CORE_RE = _word_pattern([
    "war", "wars", "warfare", "ceasefire", "cease-fire", "truce", "armistice",
    "invasion", "invade", "invaded", "invades", "occupation", "occupied",
    "annexation", "annexed", "bombardment", "shelling", "frontline", "coup",
    "coups", "junta", "juntas", "insurgency", "armed conflict", "front line",
])
_ATTACK_RE = _word_pattern([
    "attack", "attacks", "attacked", "airstrike", "airstrikes", "strike", "strikes",
    "struck", "missile", "missiles", "drone", "drones", "shelling", "targeted",
    "targeting", "bomb threat", "bomb threats",
])
_THREAT_RE = _word_pattern("threat threats threaten threatens threatened threatening".split())
_DIPLOMACY_CORE_RE = _word_pattern(
    "diplomacy diplomatic diplomats diplomat treaty treaties accord accords negotiation "
    "negotiations negotiators ceasefire truce armistice".split()
)
_DIPLOMACY_PHRASE_RE = _word_pattern(
    ["peace talks", "peace deal", "diplomatic ties", "foreign policy", "foreign affairs",
     "bilateral talks", "trade talks", "climate talks"]
)
_SANCTIONS_RE = _word_pattern(
    ["sanction", "sanctions", "embargo", "embargoes", "blockade", "blockades",
     "tariff", "tariffs"]
)
_ELECTION_RE = _word_pattern(
    "election elections electoral referendum president-elect".split()
)
_LEADERSHIP_CHANGE_RE = _word_pattern([
    "resign", "resigns", "resigned", "resigning", "resignation", "resignations",
    "step down", "steps down", "stepped down", "stepping down",
    "ousted", "ousts", "ousting", "dismissed", "dismissal", "impeached",
    "impeachment", "replaced", "replacement", "appoint", "appoints",
    "appointed", "appointment", "coalition", "coalitions", "coalition-building", "picked", "picks", "pick", "chosen", "chooses", "selected", "selects", "named", "names", "tapped", "taps", "mandated",
])
_GOVERNMENT_FORMATION_RE = _word_pattern([
    "form a government", "form government", "form a coalition", "form coalition",
    "form a cabinet", "form cabinet", "government formation",
])
_CROSS_BORDER_LEGAL_RE = _word_pattern([
    "extradition", "extradited", "extradite", "exile", "exiled", "return from exile",
    "escape from custody", "escaped custody", "fled abroad", "fleeing abroad",
    "cross-border arrest", "cross-border legal", "international arrest warrant",
])
_SECURITY_TARGET_RE = _word_pattern([
    "military base", "military bases", "air base", "airbase", "airfield",
    "airport", "airports", "flights", "flight", "airlines", "aircraft",
    "aviation", "transit system", "rail network", "train station", "subway",
    "shipping", "commercial vessel", "cargo ship",
])
_DISASTER_IMPACT_RE = _word_pattern([
    "strong", "powerful", "major", "severe", "deadly", "devastating",
    "magnitude", "kills", "killed", "injures", "injured", "damages", "damage",
    "strikes", "struck", "hits", "hit", "destroys", "destroyed",
])
_MARITIME_SECURITY_RE = _word_pattern([
    "piracy", "pirates", "pirate", "hijacking", "hijacked", "hijack",
    "shipping lane", "shipping lanes", "commercial shipping", "merchant vessel",
    "merchant vessels", "cargo ship", "cargo ships",
])
_POLITICAL_CANDIDACY_RE = _word_pattern([
    "bid for the presidency", "bid for the French presidency", "bid for presidency", "presidential bid",
    "run for president", "running for president", "candidate for president",
    "presidential candidate", "seeks the presidency", "seeking the presidency",
    "presidential race", "presidential campaign",
])
_DIPLOMATIC_MEETING_RE = _word_pattern([
    "talks", "nuclear talks", "iran talks", "diplomatic talks", "bilateral talks",
    "negotiations", "diplomacy", "foreign policy", "peace process",
])
_MEETING_ACTION_RE = _word_pattern([
    "meet", "meets", "meeting", "met", "visit", "visits", "visited",
    "welcomed", "hosts", "hosted", "held talks",
])
_STATE_FAMILY_RE = re.compile(
    r"\b(?:leader|president|dictator|ruler|king|supreme leader)\b.{0,70}"
    r"\b(?:daughter|son|child|children|heir)\b"
    r"|\b(?:daughter|son|child|children|heir)\b.{0,70}"
    r"\b(?:leader|president|dictator|ruler|king|supreme leader)\b",
    re.IGNORECASE,
)
_SECURITY_RAID_RE = re.compile(
    r"\bsecurity forces?\b.{0,140}\b(?:killed|kill|killing|slain|eliminated)\b"
    r".{0,100}\b(?:militants?|insurgents?|fighters?|rebels?|gunmen)\b"
    r"|\b(?:killed|kill|killing|slain|eliminated)\b.{0,100}"
    r"\b(?:militants?|insurgents?|fighters?|rebels?|gunmen)\b.{0,100}\b(?:raids?|operations?)\b",
    re.IGNORECASE,
)
_HEALTH_CRISIS_RE = re.compile(
    r"\b(?:ebola|cholera|mpox|polio|measles|malaria|avian flu|bird flu|"
    r"coronavirus|covid-?19|plague|dengue|outbreak|epidemic|pandemic)\b"
    r".{0,100}\b(?:cases?|outbreak|epidemic|spreads?|spread|crossed|surge|surges|"
    r"deaths?|infections?)\b"
    r"|\b(?:cases?|outbreak|epidemic|pandemic|infections?)\b.{0,100}"
    r"\b(?:ebola|cholera|mpox|polio|measles|malaria|avian flu|bird flu|"
    r"coronavirus|covid-?19|plague|dengue)\b",
    re.IGNORECASE,
)
_LARGE_SCALE_RE = re.compile(
    r"\b(?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{3,}|thousands?|tens of thousands|hundreds of deaths|"
    r"large[- ]scale|nationwide|across the country|spreading across)\b",
    re.IGNORECASE,
)
_TRADE_ECONOMY_RE = _word_pattern([
    "trade surplus", "trade deficit", "trade balance", "trade volume",
    "exports", "imports", "tariffs", "trade barriers", "supply chains",
    "currency crisis", "currency devaluation", "foreign investment",
    "cross-border trade", "global trade", "international trade",
])
_INTERNATIONAL_PLOT_RE = re.compile(
    r"\b(?:plotting|planned|planning|conspiring|conspiracy|foiled)\b"
    r".{0,100}\b(?:attacks?|bombings?|assassinations?)\b"
    r"|\b(?:attacks?|bombings?|assassinations?)\b.{0,100}"
    r"\b(?:plotting|planned|planning|conspiring|conspiracy|foiled)\b",
    re.IGNORECASE,
)
_DOMESTIC_PLACES = {
    "australia", "australian", "canberra",
    "us", "u.s.", "united states", "american", "americans", "washington",
}
_SUMMIT_RE = re.compile(
    r"\b(?:summits?|UN|NATO|EU|G[- ]?7|G[- ]?20|BRICS|ASEAN|COP\d+|"
    r"climate talks|general assembly|security council|european union|african union)\b",
    re.IGNORECASE,
)
_SUMMIT_ACTION_RE = _word_pattern(
    "attend attending attends attended skip skips skipped skipping boycott boycotts "
    "boycotted negotiate negotiates negotiated negotiation negotiations deal agreement "
    "accord resolution deadlock deadlocked dispute disputes walkout meet meets meeting met "
    "open opens opened opening urge urges urged urging call calls called calling address "
    "addresses addressed addressing warn warns warned warning pledge pledges pledged "
    "announce announces announced announcing".split()
)
_STATE_ACTOR_RE = _word_pattern(
    ["president", "presidents", "prime minister", "minister", "ministers", "trade ministers",
     "foreign leaders", "foreign minister", "foreign ministers", "world leaders",
     "heads of state", "government", "governments", "member states", "delegates", "countries"]
)
_DIPLOMATIC_OUTCOME_RE = _word_pattern(
    "deal agreement accord treaty resolution compromise negotiated adopted signed".split()
)
_HISTORICAL_CONTEXT_RE = _word_pattern(
    ["historical", "historian", "historians", "century-old", "decades ago", "years ago",
     "world war i", "world war ii", "wwi", "wwii", "archive", "archival"]
)
_CURRENT_DEVELOPMENT_RE = _word_pattern(
    "currently ongoing today now renewed resumes resumed reignites reignited escalates "
    "escalated continues continued".split()
)
_STATE_SECURITY_RE = _word_pattern(
    "espionage spying spy spies".split()
    + ["intelligence service", "foreign agent", "state-sponsored",
       "government-backed", "national security"]
)
_TERRORISM_RE = _word_pattern(
    "terror terrorism terrorist terrorists hijacking".split()
)
_CROSS_BORDER_RE = _word_pattern(
    ["cross-border", "cross border", "international response", "international aid"]
)
_INCIDENT_RE = _word_pattern(
    "earthquake tsunami disaster capsized capsizes capsizing sank sinking shipwreck "
    "migrant boat migrant boats".split()
)
_MIGRANT_BOAT_DISASTER_RE = re.compile(
    r"\bmigrant boats?\b.{0,100}\b(?:capsiz\w*|sank|sinking|shipwreck\w*|disaster|"
    r"dead|killed|missing|drown\w*|recover\w*|bodies)\b|\b(?:capsiz\w*|sank|shipwreck\w*|disaster|"
    r"drown\w*|recover\w*|bodies|dead|killed|missing)\b.{0,100}\bmigrant boats?\b",
    re.IGNORECASE,
)
_PERSONAL_SUMMIT_RE = re.compile(
    r"\b(?:activist|activists|celebrity|celebrities|actor|actress|singer)\b.{0,100}"
    r"\b(?:arriv\w*|appear\w*|attend\w*|join\w*|remarks?|speech|spoke|commentary|activism|advocacy)\b"
    r".{0,100}\b(?:summit|COP\d*|G[- ]?7|G20|UN|NATO)\b"
    r"|\b(?:summit|COP\d*|G[- ]?7|G20|UN|NATO)\b.{0,100}"
    r"\b(?:activist|activists|celebrity|celebrities|actor|actress|singer)\b.{0,100}"
    r"\b(?:arriv\w*|appear\w*|attend\w*|join\w*|remarks?|speech|spoke|commentary|activism|advocacy)\b",
    re.IGNORECASE,
)
_CORPORATE_SCANDAL_RE = _word_pattern(
    "CEO CEOs company corporate corporation stock stocks shares shareholder scandal affair "
    "earnings".split()
)
_PERSONAL_OR_MARKET_RE = _word_pattern(
    "affair affairs stock stocks shares shareholder scandal".split()
)
_GOVERNMENT_INVESTIGATION_RE = re.compile(
    r"\b(?:government|state|national security|intelligence|police|prosecutors?)\b"
    r".{0,100}\b(?:investigat\w*|probe|probes|probing|inquir\w*)\b"
    r"|\b(?:investigat\w*|probe|probes|probing|inquir\w*)\b.{0,100}"
    r"\b(?:government|state|national security|intelligence|police|prosecutors?)\b",
    re.IGNORECASE,
)


def _distinct(pattern, text):
    return {m.group(0).lower() for m in pattern.finditer(text)}


def assess(headline: str, summary: str = "", tags=(), url: str = "") -> tuple[bool, int, str]:
    """Classify from article text only; tags and URL are accepted for API compatibility."""
    headline = _NOT_MILITARY_RE.sub(" ", headline)
    summary = _NOT_MILITARY_RE.sub(" ", summary)
    text = f"{headline}\n{summary}"

    strong = _distinct(_STRONG_RE, text) | _distinct(_ORG_RE, text) | _distinct(_ORG_NAME_RE, text)
    leaders = _distinct(_LEADER_RE, text)
    places = _distinct(_PLACE_RE, text) | _distinct(_PLACE_PHRASE_RE, text)
    support = _distinct(_SUPPORT_RE, text)

    # Headline hits count double: the headline is what a reader sees.
    headline_strong = _distinct(_STRONG_RE, headline) | _distinct(_ORG_RE, headline) \
        | _distinct(_LEADER_RE, headline) | _distinct(_ORG_NAME_RE, headline)

    score = (
        2 * len(strong | leaders)
        + 2 * len(headline_strong)
        + min(len(places), 3)
        + min(len(support), 2)
    )

    headline_places = _distinct(_PLACE_RE, headline) | _distinct(_PLACE_PHRASE_RE, headline)
    foreign_places = places - _DOMESTIC_PLACES
    two_countries = len(headline_places) >= 2
    conflict = bool(_WAR_CORE_RE.search(text)) or (
        bool(_ATTACK_RE.search(text)) and (two_countries or bool(_ORG_RE.search(text))
        or (bool(foreign_places) and bool(_SECURITY_TARGET_RE.search(text))))
    ) or (
        bool(_THREAT_RE.search(text))
        and bool(_word_pattern(["weapons", "military", "nuclear", "missile"]).search(text))
        and bool(places | leaders)
    )
    diplomacy = bool(_DIPLOMACY_CORE_RE.search(text) or _DIPLOMACY_PHRASE_RE.search(text))
    sanctions = bool(_SANCTIONS_RE.search(text)) and (
        bool(foreign_places) or bool(re.search(r"\binternational sanctions\b", text, re.I))
    )
    foreign_election = bool(_ELECTION_RE.search(text)) and bool(foreign_places)
    summit_negotiation = (
        bool(_SUMMIT_RE.search(text))
        and bool(_SUMMIT_ACTION_RE.search(text))
        and bool(foreign_places or leaders or _STATE_ACTOR_RE.search(text))
    )
    foreign_leadership_change = (
        bool(foreign_places)
        and bool(_STATE_ACTOR_RE.search(text))
        and (bool(_LEADERSHIP_CHANGE_RE.search(text)) or bool(_GOVERNMENT_FORMATION_RE.search(text)))
    )
    foreign_legal_political = (
        bool(foreign_places)
        and bool(_CROSS_BORDER_LEGAL_RE.search(text))
        and bool(_STATE_ACTOR_RE.search(text) or _LEADER_RE.search(text)
                 or re.search(r"\b(?:former|exiled|ousted|opposition|president|minister)\b", text, re.I))
    )
    foreign_disaster = (
        bool(foreign_places)
        and bool(_INCIDENT_RE.search(text))
        and bool(_DISASTER_IMPACT_RE.search(text))
        and bool(re.search(r"\b(?:earthquake|tsunami|volcanic eruption|typhoon|hurricane)\b", text, re.I))
    )
    maritime_security = (
        bool(foreign_places)
        and bool(_MARITIME_SECURITY_RE.search(text))
        and bool(re.search(r"\b(?:Gulf of Aden|Red Sea|strait|shipping lane|vessel|ship|piracy|pirates)\b", text, re.I))
    )
    foreign_political_candidacy = (
        bool(foreign_places)
        and bool(_POLITICAL_CANDIDACY_RE.search(text))
        and bool(_word_pattern(["president", "presidency", "presidential"]).search(text))
    )
    diplomatic_meeting = (
        bool(foreign_places)
        and bool(_STATE_ACTOR_RE.search(text))
        and bool(_MEETING_ACTION_RE.search(text))
        and bool(_DIPLOMATIC_MEETING_RE.search(text))
    )
    foreign_state_family = (
        bool(foreign_places)
        and bool(_STATE_FAMILY_RE.search(text))
        and bool(re.search(r"\b(?:state event|state events|military parade|state banquet|state visit|official event)\b", text, re.I))
    )
    security_operation = (
        bool(foreign_places)
        and bool(_SECURITY_RAID_RE.search(text))
    )
    international_health_crisis = (
        bool(foreign_places)
        and bool(_HEALTH_CRISIS_RE.search(text))
        and bool(_LARGE_SCALE_RE.search(text))
    )
    international_trade = (
        bool(foreign_places)
        and bool(_TRADE_ECONOMY_RE.search(text))
    )
    international_plot = (
        bool(_INTERNATIONAL_PLOT_RE.search(text))
        and (two_countries or bool(_ORG_RE.search(text))
             or bool(re.search(r"\b(?:foreign|British|U\.S\.|European)\s+(?:military )?base\b", text, re.I)))
    )
    un_leadership = (
        bool(_ORG_RE.search(text))
        and bool(_word_pattern(["secretary-general"]).search(text))
        and bool(_ELECTION_RE.search(text) or re.search(r"\bwho\b.{0,30}\bnext\b", text, re.I))
    )
    state_espionage = bool(_STATE_SECURITY_RE.search(text)) and bool(
        foreign_places or leaders or _GOVERNMENT_INVESTIGATION_RE.search(text)
    )
    government_security_investigation = (
        bool(_GOVERNMENT_INVESTIGATION_RE.search(text))
        and bool(_STATE_SECURITY_RE.search(text))
    )
    international_terrorism = bool(_TERRORISM_RE.search(text)) and (
        bool(re.search(r"\binternational|transnational|global\b", text, re.I))
        or two_countries
        or bool(_ORG_RE.search(text))
    )
    cross_border_crisis = (
        bool(_CROSS_BORDER_RE.search(text))
        and (bool(_INCIDENT_RE.search(text)) or bool(_ATTACK_RE.search(text)))
    ) or (
        bool(_INCIDENT_RE.search(text))
        and bool(re.search(r"\b(?:international|foreign)\s+(?:rescue|relief|response|aid)\b", text, re.I))
    ) or bool(_MIGRANT_BOAT_DISASTER_RE.search(text))
    core = any((
        conflict, diplomacy, sanctions, foreign_election, summit_negotiation,
        un_leadership, state_espionage, international_terrorism, cross_border_crisis,
        foreign_leadership_change, security_operation, international_health_crisis,
        international_trade, international_plot, foreign_legal_political, foreign_disaster,
        maritime_security, foreign_political_candidacy, diplomatic_meeting, foreign_state_family,
        espionage_plot_context,
    ))

    personal_summit = bool(_PERSONAL_SUMMIT_RE.search(text)) and not bool(
        _DIPLOMATIC_OUTCOME_RE.search(text)
    )
    historical_without_current_angle = (
        bool(_HISTORICAL_CONTEXT_RE.search(text))
        and not bool(_CURRENT_DEVELOPMENT_RE.search(text))
    )
    government_investigation_match = _GOVERNMENT_INVESTIGATION_RE.search(headline)
    corporate_or_market_match = _CORPORATE_SCANDAL_RE.search(headline)
    corporate_fallout_leads = (
        corporate_or_market_match is not None
        and (
            government_investigation_match is None
            or corporate_or_market_match.start() < government_investigation_match.start()
        )
    )
    espionage_plot_context = (
        bool(re.search(r"\b(?:spy|spying|espionage)\b.{0,70}\b(?:plot|ring|scheme|network|allegations?)\b"
                       r"|\b(?:plot|ring|scheme|network|allegations?)\b.{0,70}\b(?:spy|spying|espionage)\b",
                       text, re.I))
        and bool(foreign_places or leaders or _GOVERNMENT_INVESTIGATION_RE.search(text)
                 or re.search(r"\b(?:foreign intelligence|national security|state-sponsored)\b", text, re.I))
    )
    corporate_fallout = (
        bool(_CORPORATE_SCANDAL_RE.search(headline))
        and bool(_PERSONAL_OR_MARKET_RE.search(headline) or _STATE_SECURITY_RE.search(text))
        and (not government_security_investigation or corporate_fallout_leads)
        and not espionage_plot_context
    )
    if personal_summit:
        return False, score, "not geopolitical (personal summit appearance)"
    if historical_without_current_angle:
        return False, score, "not geopolitical (historical story without a current angle)"
    if corporate_fallout and not core:
        return False, score, "not geopolitical (corporate or personal fallout)"

    off_topic = _distinct(_OFF_TOPIC_RE, headline)
    signals = sorted(strong | leaders)[:4] or sorted(places | support)[:3]
    if off_topic and not core:
        return False, score, "off-topic: " + ", ".join(sorted(off_topic)[:3])
    if core:
        return True, score, "geopolitics: " + ", ".join(signals)
    return False, score, "not geopolitical" + (f" (only: {', '.join(signals)})" if signals else "")
