"""Topic-based source relevance classifier.

Given a set of topic keywords, classifies each available stock source
adapter by how likely it is to have relevant content.  This prevents
wasting API calls on sources with no chance of matching — querying JAXA
for Therac-25 radiation therapy footage is pure waste.

Rule-based (no ML).  Topic → source mappings are encoded as data.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SourceRelevance:
    """Relevance classification for a single source."""
    source_name: str
    tier: str       # "high", "medium", "low"
    reason: str


# ---------------------------------------------------------------------------
# Topic keyword → source relevance mappings
# ---------------------------------------------------------------------------
# Each keyword maps to sources that are strong for that topic.
# Sources not listed for any matched keyword fall to defaults.

_TOPIC_MAP: dict[str, dict[str, str]] = {
    # --- Medical / health ---
    "radiation": {"doe": "high", "nara": "high", "archive_org": "high", "nasa": "medium", "wikimedia": "high"},
    "medical": {"wikimedia": "high", "openverse": "high", "archive_org": "medium", "loc": "medium"},
    "hospital": {"openverse": "high", "wikimedia": "medium"},
    "therapy": {"wikimedia": "high", "openverse": "high"},
    "pharmaceutical": {"wikimedia": "medium", "archive_org": "medium"},

    # --- Nuclear / energy ---
    "nuclear": {"doe": "high", "nara": "high", "archive_org": "high", "nasa": "medium"},
    "energy": {"doe": "high", "archive_org": "medium"},
    "reactor": {"doe": "high", "nara": "high"},

    # --- Computing / software ---
    "computer": {"archive_org": "high", "wikimedia": "high", "loc": "medium"},
    "software": {"archive_org": "high", "wikimedia": "high", "nasa": "medium"},
    "programming": {"archive_org": "high", "wikimedia": "high"},
    "terminal": {"archive_org": "high", "wikimedia": "high"},
    "mainframe": {"archive_org": "high", "wikimedia": "high", "nasa": "medium"},

    # --- Safety / accidents ---
    "accident": {"csb": "high", "ntsb": "high", "nara": "medium", "archive_org": "medium"},
    "safety": {"csb": "high", "ntsb": "high", "doe": "medium", "nasa": "medium"},
    "investigation": {"csb": "high", "ntsb": "high", "nara": "medium"},
    "disaster": {"csb": "high", "ntsb": "high", "archive_org": "high", "nara": "medium"},
    "malfunction": {"csb": "high", "ntsb": "high"},
    "recall": {"nara": "high", "archive_org": "medium"},

    # --- Aviation / transportation ---
    "aviation": {"ntsb": "high", "nasa": "high", "archive_org": "medium"},
    "aircraft": {"ntsb": "high", "nasa": "high"},
    "flight": {"ntsb": "high", "nasa": "high"},
    "transportation": {"ntsb": "high", "loc": "medium"},
    "railroad": {"ntsb": "high", "loc": "high", "archive_org": "high"},
    "maritime": {"ntsb": "high", "noaa": "medium"},

    # --- Space / aerospace ---
    "space": {"nasa": "high", "esa": "high", "jaxa": "high"},
    "rocket": {"nasa": "high", "esa": "medium", "archive_org": "medium"},
    "satellite": {"nasa": "high", "esa": "high", "noaa": "medium"},
    "astronaut": {"nasa": "high", "esa": "medium", "jaxa": "medium"},
    "orbit": {"nasa": "high", "esa": "high"},

    # --- Earth / environment ---
    "ocean": {"noaa": "high", "nasa": "medium"},
    "weather": {"noaa": "high", "nasa": "medium"},
    "climate": {"noaa": "high", "nasa": "high", "esa": "medium"},
    "earthquake": {"noaa": "medium", "archive_org": "medium"},

    # --- Chemical / industrial ---
    "chemical": {"csb": "high", "archive_org": "medium"},
    "refinery": {"csb": "high", "doe": "medium"},
    "pipeline": {"csb": "high", "ntsb": "medium", "doe": "medium"},
    "explosion": {"csb": "high", "ntsb": "medium", "archive_org": "medium"},
    "factory": {"csb": "high", "archive_org": "high", "loc": "medium"},

    # --- Historical / government ---
    "historical": {"archive_org": "high", "loc": "high", "nara": "high"},
    "government": {"nara": "high", "loc": "high", "archive_org": "medium"},
    "military": {"nara": "high", "archive_org": "high", "loc": "medium"},
    "war": {"nara": "high", "archive_org": "high", "loc": "high"},
    "president": {"nara": "high", "loc": "high"},
}

# Sources that are always at least "medium" regardless of topic.
# Knowledge bases have broad coverage; stock sources provide b-roll.
_UNIVERSAL_KNOWLEDGE: frozenset[str] = frozenset({
    "wikimedia", "wikimedia_categories", "archive_org", "openverse",
})

_UNIVERSAL_BROLL: frozenset[str] = frozenset({
    "pexels", "pixabay_video", "coverr", "mixkit",
})


def classify_sources(
    topic_keywords: list[str],
    available_sources: list[str],
) -> list[SourceRelevance]:
    """Classify sources by relevance to the given topic.

    Parameters
    ----------
    topic_keywords
        Words describing the video topic.  Extracted from the project
        brief, segment plan title, or scene narrations.
    available_sources
        Names of currently available source adapters.

    Returns
    -------
    List of SourceRelevance, sorted by tier (high → medium → low).
    Every source in *available_sources* appears exactly once.
    No source is ever "skip" — low-priority sources still get searched
    if higher-priority sources don't fill the slot.
    """
    # Accumulate the highest tier each source achieves across all keywords
    best_tier: dict[str, str] = {}
    reasons: dict[str, list[str]] = {}

    normalized_kw = [k.lower().strip() for k in topic_keywords if k.strip()]

    for kw in normalized_kw:
        mappings = _TOPIC_MAP.get(kw, {})
        for source_name, tier in mappings.items():
            if source_name not in available_sources:
                continue
            current = best_tier.get(source_name, "low")
            if _TIER_RANK[tier] < _TIER_RANK.get(current, 3):
                best_tier[source_name] = tier
            if source_name not in reasons:
                reasons[source_name] = []
            reasons[source_name].append(kw)

    # Apply universal floors
    for src in available_sources:
        if src not in best_tier:
            if src in _UNIVERSAL_KNOWLEDGE:
                best_tier[src] = "medium"
                reasons.setdefault(src, []).append("universal knowledge source")
            elif src in _UNIVERSAL_BROLL:
                best_tier[src] = "medium"
                reasons.setdefault(src, []).append("universal b-roll source")
            else:
                best_tier[src] = "low"
                reasons.setdefault(src, []).append("no keyword match")

    # Build result sorted by tier
    result = [
        SourceRelevance(
            source_name=src,
            tier=best_tier.get(src, "low"),
            reason=", ".join(reasons.get(src, ["no match"])),
        )
        for src in available_sources
    ]
    result.sort(key=lambda sr: _TIER_RANK.get(sr.tier, 3))
    return result


_TIER_RANK = {"high": 1, "medium": 2, "low": 3}
