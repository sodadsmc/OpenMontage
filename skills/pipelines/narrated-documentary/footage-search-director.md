# Footage Search Director - Narrated Documentary Pipeline

> **SCOPE:** The narrated-documentary channel is generation-first — ~99% of segments are AI-generated to DEPICT the narration (see `ai-visual-director.md`). This stage runs ONLY for `archival_footage` segments (genuine historical footage) and for segments where AI generation hard-failed. If you're here for a normal scene, you're in the wrong stage.

## When To Use

**Archival / fallback path only.** AI-generated video is the primary visual (see
`ai-visual-director.md`). This stage sources REAL footage candidates only for
segments explicitly marked `archival_footage` (genuine historical events) or as a
fallback when AI generation failed. Prioritize disaster-investigation archives
(CSB, NTSB, Archive.org) — real footage is reserved for real events. The output
feeds the scoring stage, which ranks these real candidates.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/footage_candidates.schema.json` | Artifact validation |
| Prior artifact | segment_plan | Scene search queries and visual descriptions |
| Tool | `corpus_builder` | Multi-provider corpus building with CLIP embeddings |
| Tool | `direct_clip_search` | Lightweight multi-provider search and download |
| Meta | `skills/meta/footage-research.md` | **Four-bucket methodology and smart routing** |
| Meta | `skills/meta/reviewer.md` | Self-review pass |
| Lib | `lib/source_classifier.py` | Topic → source relevance classification |
| Lib | `lib/query_router.py` | Source-aware query generation |
| Lib | `lib/relevance_filter.py` | Pre-download text similarity filtering |

## What This Stage Does

Takes the tiered search queries from each scene in the segment plan
and fans them out across up to 19 source providers. Prioritizes
disaster-specific archives (CSB, NTSB, Archive.org investigation
collections) over general stock footage. Returns a ranked candidate
list per scene for the scoring stage.

## Inputs

- **segment_plan** artifact: scenes with search_queries and
  visual_description per scene

## Outputs

- **footage_candidates** artifact: version, scenes array each
  containing an array of candidate clips with clip_id, source, path,
  and preliminary score

## Workflow

### 0. Load Research Methodology

**Read `skills/meta/footage-research.md` first.** It defines the
four-bucket methodology, the smart routing tools, and the licensing
rules for each source type.

### 1. Classify Sources for This Topic

Before searching, classify which sources are relevant. This prevents
wasting API calls on sources with no chance of matching.

```python
from lib.source_classifier import classify_sources

relevance = classify_sources(
    topic_keywords=["radiation", "medical", "computer", "software", "safety"],
    available_sources=[s.name for s in available_sources()],
)
# high_sources = [r.source_name for r in relevance if r.tier == "high"]
# medium_sources = [r.source_name for r in relevance if r.tier == "medium"]
```

Search high-tier sources first. Only fall back to medium/low-tier
if high-tier sources don't fill the slot.

### 2. Generate Routed Queries

For each scene, generate source-specific queries instead of sending
one generic query everywhere:

```python
from lib.query_router import route_queries

for scene in segment_plan["scenes"]:
    routed = route_queries(
        search_queries=scene["search_queries"],
        visual_description=scene["visual_description"],
        narration=scene["narration"],
        mood=scene.get("mood", ""),
        pacing=scene["pacing"],
    )
    # Wikimedia gets "Therac-25"
    # Pexels gets "dimly lit hospital treatment room"
    # DOE gets "linear accelerator radiation therapy"
```

### 3. Run The Four-Bucket Search Cascade

For each scene, search buckets in order. Always pass `relevance_query`
to filter before downloading.

**Bucket 1 — Discovery (YouTube):**
```python
direct_clip_search.execute({
    "output_dir": "projects/<name>/assets/video/candidates",
    "queries": [{"query": "<entity name>", "slot_id": "scene_01"}],
    "sources": ["youtube_search"],
    "relevance_query": scene["visual_description"],
    "relevance_threshold": 0.20,
    "clips_per_query": 5,
})
```
Long-form results (`extra.long_form=true`) go to corpus pre-build.

**Bucket 2 — Open-use (CC images + video):**
```python
direct_clip_search.execute({
    "queries": [
        {"query": "<entity name>", "slot_id": "scene_01", "kind": "image"},
        {"query": "<entity name>", "slot_id": "scene_01", "kind": "video"},
    ],
    "sources": ["wikimedia_categories", "wikimedia", "openverse"],
    "relevance_query": scene["visual_description"],
    "clips_per_query": 5,
})
```

**Bucket 3 — Historic/official archives:**
```python
direct_clip_search.execute({
    "queries": [{"query": "<technical terms>", "slot_id": "scene_01"}],
    "sources": high_sources,  # from classifier
    "relevance_query": scene["visual_description"],
    "clips_per_query": 3,
    "filters": {"min_duration": 3, "max_duration": 60},
})
```

**Bucket 4 — Licensed b-roll (fill gaps):**
```python
direct_clip_search.execute({
    "queries": [{"query": "<visual descriptors>", "slot_id": "scene_01"}],
    "sources": ["pexels", "pixabay_video", "coverr", "mixkit"],
    "relevance_query": scene["visual_description"],
    "clips_per_query": 3,
    "filters": {"min_duration": 3, "orientation": "landscape", "min_width": 1280},
})
```

**Stop rule:** If a scene has >= 3 candidates after any bucket, skip
remaining buckets for that scene. Flag scenes with 0 candidates for
AI generation in the gap_fill stage.

### 3. Deduplicate Across Scenes

The same clip often matches queries for multiple scenes. Before
passing candidates downstream:

- Track every clip_id in a global set
- If a clip appears as a candidate for multiple scenes, keep it
  only for the scene where it scores highest
- Flag scenes that lose candidates to deduplication for an
  additional search pass

### 4. Record Provenance

Every candidate clip must carry:
- `clip_id` — unique identifier from the source
- `source` — provider name (e.g., "archive_org", "pexels", "csb")
- `path` — local path where the clip was downloaded
- `score` — preliminary relevance score (0-1) from search API
- `license` — license type from the source
- `original_url` — source URL for audit trail

### 5. Handle Search Failures

If a scene has fewer than 2 candidates after exhausting all tiers:

1. Rewrite the search queries using different vocabulary
2. Run one more search pass with the rewritten queries
3. If still < 2 candidates, flag the scene in the output and note
   that gap_fill may need to generate footage for this scene

Do not silently drop scenes with no candidates. The scoring stage
needs to know about gaps.

### 6. Emit The Footage Candidates

```json
{
  "version": "1.0",
  "scenes": [
    {
      "scene_id": "scene_01",
      "candidates": [
        {
          "clip_id": "archive_org_csb_2005_0323_clip04",
          "source": "archive_org",
          "path": "projects/<name>/assets/video/candidates/clip04.mp4",
          "score": 0.82
        },
        {
          "clip_id": "pexels_refinery_28441",
          "source": "pexels",
          "path": "projects/<name>/assets/video/candidates/pexels_28441.mp4",
          "score": 0.61
        }
      ]
    }
  ]
}
```

## Quality Bar

- Every scene has at least 2 candidate clips (3+ preferred)
- Disaster-specific sources searched before general stock
- Tiered query cascade followed (not all queries blasted at once)
- No clip_id appears as candidate for more than one scene
- Provenance complete on every candidate (source, license, URL)
- Scenes with < 2 candidates explicitly flagged

## Common Mistakes

- **Skipping Tier A sources.** General stock footage of "factory
  exterior" cannot replace actual CSB investigation footage. Always
  search investigation archives first.
- **Blasting all queries at once.** The cascade exists to save time
  and API calls. If Tier 1 returns enough candidates, skip Tier 3.
- **Ignoring deduplication.** The same dramatic explosion clip will
  match 5 different scenes. Allowing it in all 5 means the scoring
  stage picks it for 5 scenes and the video repeats the same clip.
- **Downloading too many candidates.** 3-5 per scene is sufficient.
  Downloading 20 per scene wastes bandwidth and storage without
  improving scoring quality.
- **Not recording provenance.** A clip without source and license
  information is unusable for YouTube publishing. Record it now.

## Tools Available

- `corpus_builder` — Multi-provider search with CLIP embedding
  indexing. Best for large productions needing automated ranking.
- `direct_clip_search` — Lightweight multi-provider fanout search
  and download. Best for fast iteration and act-by-act production.
