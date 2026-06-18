"""Source-aware query router for the RETRIEVAL/archival fallback path.

Scope: this serves the deliberate fallback of retrieving REAL footage/imagery
(stock_footage / archival_footage and the knowledge/technical archives below).
It is no longer the default visual acquisition for the AI channel — the primary
path generates AI video that depicts the narration. Use this router only when a
beat calls for genuine real-world/historical material.

Transforms a scene's metadata into optimized queries per source adapter.
Different sources respond to different query styles:

- Stock sources (Pexels, Coverr) need visual descriptors: "dark hospital room"
- Knowledge sources (Wikimedia, Archive.org) need entity names: "Therac-25"
- Investigation sources (CSB, NTSB) need incident keywords: "radiation accident"
- Technical sources (NASA, DOE, ESA) need engineering terms: "linear accelerator"

This is rule-based (no LLM) — source profiles are encoded as data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoutedQuery:
    """A query optimized for a specific source."""
    source_name: str
    query: str
    priority: int = 1  # 1=primary, 2=secondary, 3=fallback
    rationale: str = ""


# ---------------------------------------------------------------------------
# Source profiles — how each source should be queried
# ---------------------------------------------------------------------------

@dataclass
class _SourceProfile:
    strategy: str  # query generation strategy name
    strengths: list[str] = field(default_factory=list)

# Strategy types:
#   entity_search  — keep proper nouns, technical terms, exact phrases
#   visual_desc    — extract visual/mood adjectives, drop proper nouns
#   incident_kw    — extract incident type, hazard, consequences
#   technical      — keep engineering/science vocabulary
#   category_nav   — use exact entity names for category lookup

SOURCE_PROFILES: dict[str, _SourceProfile] = {
    # --- Entity/knowledge sources ---
    "wikimedia": _SourceProfile("entity_search", ["encyclopedic", "diagrams", "historical"]),
    "wikimedia_categories": _SourceProfile("category_nav", ["curated collections", "exact topics"]),
    "archive_org": _SourceProfile("entity_search", ["historical footage", "public domain films"]),
    "openverse": _SourceProfile("entity_search", ["CC-licensed images", "museum collections"]),
    "loc": _SourceProfile("entity_search", ["historical", "government", "americana"]),

    # --- Investigation sources ---
    "csb": _SourceProfile("incident_kw", ["chemical accidents", "industrial safety"]),
    "ntsb": _SourceProfile("incident_kw", ["transportation accidents", "aviation"]),

    # --- Technical/government sources ---
    "nasa": _SourceProfile("technical", ["space", "engineering", "computing"]),
    "doe": _SourceProfile("technical", ["nuclear", "radiation", "energy"]),
    "esa": _SourceProfile("technical", ["space", "testing", "engineering"]),
    "jaxa": _SourceProfile("technical", ["space", "aerospace"]),
    "nara": _SourceProfile("entity_search", ["government records", "historical"]),
    "noaa": _SourceProfile("technical", ["weather", "ocean", "environmental"]),

    # --- Stock b-roll sources ---
    "pexels": _SourceProfile("visual_desc", ["modern b-roll", "lifestyle", "abstract"]),
    "pixabay_video": _SourceProfile("visual_desc", ["stock footage", "nature"]),
    "coverr": _SourceProfile("visual_desc", ["short loops", "atmospheric"]),
    "mixkit": _SourceProfile("visual_desc", ["stock", "motion graphics"]),
    "videvo": _SourceProfile("visual_desc", ["stock footage"]),
    "dareful": _SourceProfile("visual_desc", ["stock footage", "4K"]),
    "unsplash": _SourceProfile("visual_desc", ["photography", "atmospheric"]),

    # --- YouTube ---
    "youtube_search": _SourceProfile("entity_search", ["documentaries", "lectures", "news"]),

    # --- Investigation YouTube ---
    "doe": _SourceProfile("technical", ["nuclear", "radiation", "energy infrastructure"]),
}


# Words to strip from visual-descriptor queries (too specific for stock)
_ENTITY_WORDS = re.compile(
    r"\b(therac|thera[-c]|AECL|FDA|atomic energy|leveson|"
    r"kennestone|east texas|malfunction \d+)\b",
    re.IGNORECASE,
)

_YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}s?\b")

_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "was", "were", "are",
    "its", "their", "this", "that", "it", "as", "be", "has", "had",
})


def route_queries(
    search_queries: list[str],
    visual_description: str = "",
    narration: str = "",
    mood: str = "",
    pacing: str = "",
    sources: list[str] | None = None,
) -> list[RoutedQuery]:
    """Generate source-optimized queries from scene metadata.

    Parameters
    ----------
    search_queries
        Pre-curated query list from the segment plan (tiered).
    visual_description
        Prose description of the intended visual.
    narration
        The scene's narration text (for extracting key entities).
    mood
        Emotional register tag (e.g., "tension", "dramatic").
    pacing
        Narrative function (e.g., "establishing", "crisis").
    sources
        Limit to these source names. If None, generate for all known sources.

    Returns
    -------
    List of RoutedQuery objects, one per source, sorted by priority.
    """
    target_sources = sources or list(SOURCE_PROFILES.keys())
    result: list[RoutedQuery] = []

    # Extract components from the scene metadata
    entities = _extract_entities(search_queries, narration)
    visual_terms = _extract_visual_terms(visual_description, mood, pacing)
    incident_terms = _extract_incident_terms(search_queries, narration)
    technical_terms = _extract_technical_terms(search_queries, narration)

    for source_name in target_sources:
        profile = SOURCE_PROFILES.get(source_name)
        if not profile:
            # Unknown source — send the first search query as-is
            if search_queries:
                result.append(RoutedQuery(
                    source_name=source_name,
                    query=search_queries[0],
                    priority=3,
                    rationale="unknown source, using first query verbatim",
                ))
            continue

        if profile.strategy == "entity_search":
            query = _build_entity_query(entities, search_queries)
            result.append(RoutedQuery(
                source_name=source_name,
                query=query,
                priority=1,
                rationale="entity names and technical terms for knowledge source",
            ))

        elif profile.strategy == "category_nav":
            # For category navigation, use the primary entity name from
            # the FIRST search query (most specific).  Never use visual
            # descriptors — categories are indexed by topic, not appearance.
            query = ""
            if entities:
                query = entities[0]
            elif search_queries:
                # Take first non-trivial tokens from the first query
                tokens = [t for t in search_queries[0].split()
                          if t.lower() not in _STOP_WORDS and len(t) >= 3]
                query = " ".join(tokens[:2])
            result.append(RoutedQuery(
                source_name=source_name,
                query=query,
                priority=1,
                rationale="exact entity name for category tree navigation",
            ))

        elif profile.strategy == "visual_desc":
            query = _build_visual_query(visual_terms)
            result.append(RoutedQuery(
                source_name=source_name,
                query=query,
                priority=2,
                rationale="visual descriptors for stock b-roll",
            ))

        elif profile.strategy == "incident_kw":
            query = _build_incident_query(incident_terms, search_queries)
            result.append(RoutedQuery(
                source_name=source_name,
                query=query,
                priority=2,
                rationale="incident keywords for investigation archives",
            ))

        elif profile.strategy == "technical":
            query = _build_technical_query(technical_terms, profile.strengths, search_queries)
            result.append(RoutedQuery(
                source_name=source_name,
                query=query,
                priority=2,
                rationale="technical/engineering terms for government source",
            ))

    # Sort by priority (1 first)
    result.sort(key=lambda rq: rq.priority)
    return result


# ---------------------------------------------------------------------------
# Term extraction helpers
# ---------------------------------------------------------------------------

def _extract_entities(search_queries: list[str], narration: str) -> list[str]:
    """Extract proper nouns and named entities from queries and narration."""
    entities: list[str] = []

    # Look for quoted phrases in search queries
    for q in search_queries:
        # Proper noun patterns: capitalized multi-word terms
        for match in re.finditer(r"[A-Z][a-z]+(?:[-\s][A-Z][a-z]+)+", q):
            entities.append(match.group())
        # Also look for specific technical terms
        for match in re.finditer(r"Therac-\d+|AECL|FDA|PDP[-\s]\d+|VT-?\d+", q + " " + narration):
            entities.append(match.group())

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for e in entities:
        lower = e.lower()
        if lower not in seen:
            seen.add(lower)
            unique.append(e)

    # If no entities found, use the longest non-stop-word tokens from first query
    if not unique and search_queries:
        tokens = [t for t in search_queries[0].split()
                  if t.lower() not in _STOP_WORDS and len(t) >= 4]
        tokens.sort(key=lambda t: -len(t))
        unique = tokens[:3]

    return unique


def _extract_visual_terms(
    visual_description: str, mood: str, pacing: str,
) -> list[str]:
    """Extract visual/mood descriptors suitable for stock footage search."""
    if not visual_description:
        return []

    # Strip entity names and years — stock sources don't index these
    cleaned = _ENTITY_WORDS.sub("", visual_description)
    cleaned = _YEAR_PATTERN.sub("", cleaned)

    # Keep adjectives and visual nouns, drop function words
    tokens = cleaned.split()
    visual = [
        t.strip(".,;:!?\"'()") for t in tokens
        if len(t) >= 3
        and t.lower().strip(".,;:!?\"'()") not in _STOP_WORDS
        and not t[0].isupper()  # drop remaining proper nouns
    ]

    # Add mood/pacing as visual keywords
    mood_map = {
        "tension": ["dark", "moody", "clinical"],
        "dramatic": ["intense", "contrast", "shadows"],
        "crisis": ["urgent", "alarm", "emergency"],
        "resolution": ["calm", "reflective", "dawn"],
        "neutral": ["clean", "professional"],
    }
    visual.extend(mood_map.get(mood, []))

    # Deduplicate, keep top 8
    seen: set[str] = set()
    unique: list[str] = []
    for v in visual:
        if v.lower() not in seen:
            seen.add(v.lower())
            unique.append(v)
    return unique[:8]


def _extract_incident_terms(
    search_queries: list[str], narration: str,
) -> list[str]:
    """Extract incident-type keywords for investigation sources."""
    incident_words = {
        "accident", "incident", "failure", "malfunction", "disaster",
        "overdose", "radiation", "explosion", "crash", "collision",
        "death", "injury", "investigation", "recall", "defect",
        "error", "bug", "safety", "hazard", "toxic", "chemical",
    }
    text = " ".join(search_queries + [narration]).lower()
    found = [w for w in incident_words if w in text]
    return found or ["safety", "investigation"]


def _extract_technical_terms(
    search_queries: list[str], narration: str,
) -> list[str]:
    """Extract technical/engineering terms for government sources."""
    tech_patterns = [
        r"linear accelerator", r"radiation therapy", r"nuclear",
        r"software", r"computer", r"terminal", r"accelerator",
        r"engineering", r"safety.critical", r"control system",
        r"medical device", r"race condition", r"interlock",
    ]
    text = " ".join(search_queries + [narration]).lower()
    found: list[str] = []
    for pat in tech_patterns:
        if re.search(pat, text):
            found.append(pat.replace(r"\b", "").replace(".", " "))
    return found or ["engineering", "safety"]


# ---------------------------------------------------------------------------
# Query builders per strategy
# ---------------------------------------------------------------------------

def _build_entity_query(entities: list[str], search_queries: list[str]) -> str:
    """Build query for entity/knowledge sources — proper nouns first."""
    if entities:
        return " ".join(entities[:3])
    return search_queries[0] if search_queries else ""


def _build_visual_query(visual_terms: list[str]) -> str:
    """Build query for stock sources — visual descriptors only."""
    if visual_terms:
        return " ".join(visual_terms[:5])
    return "cinematic establishing shot"


def _build_incident_query(
    incident_terms: list[str], search_queries: list[str],
) -> str:
    """Build query for investigation sources — incident keywords."""
    if incident_terms:
        return " ".join(incident_terms[:4])
    return search_queries[0] if search_queries else "safety investigation"


def _build_technical_query(
    technical_terms: list[str],
    source_strengths: list[str],
    search_queries: list[str],
) -> str:
    """Build query for technical/government sources."""
    # Intersect technical terms with source strengths where possible
    terms = technical_terms[:3] if technical_terms else []
    if not terms:
        return search_queries[0] if search_queries else "engineering safety"
    return " ".join(terms)
