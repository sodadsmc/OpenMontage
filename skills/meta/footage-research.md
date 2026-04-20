# Footage Research — Meta Skill

## When to Use

Read this before any footage search stage. It defines the four-bucket
methodology and the tools that make sourcing targeted rather than blind.

## The Problem

Sending generic queries to all sources wastes bandwidth downloading
irrelevant clips. A query like "radiation therapy medical equipment"
sent to Pexels returns random hospital b-roll; sent to JAXA returns
nothing. Each source needs queries tailored to what it indexes.

## The Four Buckets

### Bucket 1: Discovery
**Purpose:** Find WHERE footage EXISTS before trying to download it.
**Sources:** `youtube_search`, web search
**Strategy:**
- YouTube CC search: `"Therac-25" radiation therapy` → finds documentaries, lectures, news segments
- Long-form results (`extra.long_form = true`) route to corpus pre-build (VideoAnalyzer → VideoTrimmer → CorpusBuilder), not direct use
- Short clips go directly to the candidate pool

**Licensing note:** CC YouTube content can be reused with attribution.
For long-form CC documentaries, the safest approaches are:
1. Extract individual frames as stills → Ken Burns (minimal use)
2. Use only brief clips (3-5s) with attribution
3. Use them as a MAP TO PRIMARY SOURCES — identify what archives and papers they drew from, then source those originals directly
4. Recreate diagrams/animations inspired by (not copied from) the documentary

### Bucket 2: Open-Use
**Purpose:** Find freely reusable CC/PD-licensed content.
**Sources:** `openverse`, `wikimedia`, `wikimedia_categories`
**Strategy:**
- Entity-name searches: exact topic name for curated collections
- Category tree navigation: `wikimedia_categories` finds hand-catalogued files that text search misses
- Openverse aggregates CC content from Flickr, museums, government archives
**Licensing:** CC-BY (attribute creator), CC-BY-SA (attribute + share-alike), CC0/PDM (no restrictions)

### Bucket 3: Historic / Official
**Purpose:** Find authoritative archival and government footage.
**Sources:** `archive_org`, `loc`, `nara`, `nasa`, `doe`, `csb`, `ntsb`, `esa`, `jaxa`, `noaa`
**Strategy:**
- Technical terminology, not visual descriptions
- Incident-specific keywords for CSB/NTSB
- Entity names and era terms for Archive.org/LOC
**Licensing:** US government works are public domain. Other archives vary — check per-clip.

### Bucket 4: Licensed B-Roll
**Purpose:** Fill visual gaps with atmospheric/establishing footage.
**Sources:** `pexels`, `pixabay_video`, `coverr`, `mixkit`, `videvo`, `dareful`, `unsplash`
**Strategy:**
- Visual descriptors extracted from the scene's `visual_description`
- Mood/atmosphere words from the `mood` field
- Drop proper nouns, dates, and technical jargon — stock sources don't index these
**Licensing:** Free commercial use, no attribution required (Pexels/Pixabay/Coverr/Mixkit licenses).

## Tools

### Source Classifier (`lib/source_classifier.py`)
Before searching, classify which sources are relevant to your topic:

```python
from lib.source_classifier import classify_sources

relevance = classify_sources(
    topic_keywords=["radiation", "medical", "computer", "safety"],
    available_sources=[s.name for s in available_sources()],
)
# Returns sources sorted by tier: high → medium → low
# Search high-tier sources first, use low-tier only to fill gaps
```

### Query Router (`lib/query_router.py`)
Generate source-specific queries from scene metadata:

```python
from lib.query_router import route_queries

routed = route_queries(
    search_queries=scene["search_queries"],
    visual_description=scene["visual_description"],
    narration=scene["narration"],
    mood=scene.get("mood", ""),
    pacing=scene["pacing"],
)
# Each RoutedQuery has: source_name, query, priority, rationale
# Wikimedia gets "Therac-25", Pexels gets "dimly lit hospital room"
```

### Pre-Download Relevance Filter
Always pass `relevance_query` to `direct_clip_search` to filter before downloading:

```python
direct_clip_search.execute({
    "output_dir": "projects/<name>/assets/video/candidates",
    "queries": [{"query": routed_query.query, "slot_id": "scene_01"}],
    "sources": [routed_query.source_name],
    "relevance_query": scene["visual_description"],
    "relevance_threshold": 0.20,
    "clips_per_query": 5,
})
```

## Recommended Workflow

For each scene in the segment plan:

1. **Classify sources** for the project topic (once per project, not per scene)
2. **Route queries** — generate per-source queries from scene metadata
3. **Search Bucket 1** (discovery) — YouTube for topic-specific content
4. **Search Bucket 2** (open-use) — Wikimedia, Openverse for CC images/video
5. **Search Bucket 3** (historic) — government archives for authoritative footage
6. **Search Bucket 4** (b-roll) — stock sources for visual gap-fill
7. **Apply relevance filter** — only download candidates above threshold
8. **Check candidate count** — if < 3 per scene, broaden queries or lower threshold
9. **Flag gaps** for AI generation in the gap_fill stage

## Image Sources

Still images with Ken Burns animation are a first-class source type.
The render pipeline (CinematicRenderer) automatically applies Ken Burns
when it detects image files. Prioritize images for:
- Diagrams and schematics (Wikimedia Categories)
- Historical photographs (LOC, NARA, Archive.org)
- CC-licensed reference photos (Openverse)
- Equipment/device photos (NASA, DOE)

SVG files are automatically filtered out by the adapters. Only raster
images (JPEG, PNG) are usable.

## Attribution

Track attribution through the pipeline:
- `Candidate.creator` — who made it
- `Candidate.license` — under what terms
- `Candidate.source_url` — where to verify

The export stage generates a YouTube description credits section from
this metadata. Never discard attribution during assembly.
