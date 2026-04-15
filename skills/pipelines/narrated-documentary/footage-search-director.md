# Footage Search Director - Narrated Documentary Pipeline

## When To Use

The segment plan is locked with tiered search queries per scene. You
now need to source real-world footage candidates from disaster
investigation archives and stock providers. The output feeds the
three-layer scoring stage.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/footage_candidates.schema.json` | Artifact validation |
| Prior artifact | segment_plan | Scene search queries and visual descriptions |
| Tool | `corpus_builder` | Multi-provider corpus building with CLIP embeddings |
| Tool | `direct_clip_search` | Lightweight multi-provider search and download |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

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

### 1. Establish Source Priority

Disaster footage has a quality hierarchy. Search in this order:

**Tier A — Investigation archives (highest value):**
- CSB (Chemical Safety Board) investigation videos
- NTSB (National Transportation Safety Board) footage
- Archive.org disaster investigation collections
- NARA (National Archives) incident records
- Government agency releases (OSHA, EPA, BSEE)

**Tier B — Specialized documentary sources:**
- NASA (for aerospace disasters)
- Library of Congress
- Wikimedia Commons
- Pond5 Public Domain

**Tier C — General stock providers:**
- Pexels, Pixabay Video, Coverr, Mixkit, Videvo, Dareful
- Unsplash (images only — use as stills if needed)

**Tier D — Premium sources (cost-bearing):**
- Getty, Shutterstock (only if budget allows and Tiers A-C fail)

Always exhaust Tier A before moving to Tier B. Investigation footage
carries authenticity that no stock clip can match.

### 2. Run The Tiered Query Cascade

For each scene, execute queries in the order specified by the segment
plan (specific to general). The cascade rule:

1. Run Tier 1 query against Tier A sources
2. If >= 3 candidates found, stop for this scene
3. If < 3 candidates, run Tier 2 query against Tier A + B sources
4. If still < 3 candidates, run Tier 3 query against all sources
5. Continue until at least 3 candidates per scene or all tiers exhausted

Use `direct_clip_search` for fast iteration:

```python
direct_clip_search.execute({
    "output_dir": "projects/<name>/assets/video/candidates",
    "queries": [
        {"query": "CSB Texas City refinery explosion", "slot_id": "scene_01"},
        {"query": "oil refinery explosion aftermath investigation", "slot_id": "scene_01"},
    ],
    "sources": ["archive_org", "wikimedia", "pexels"],
    "clips_per_query": 3,
    "filters": {
        "min_duration": 3,
        "max_duration": 60,
        "orientation": "landscape",
        "min_width": 1280,
    },
})
```

For large productions or when CLIP scoring is needed, use
`corpus_builder` to build an indexed corpus first.

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
