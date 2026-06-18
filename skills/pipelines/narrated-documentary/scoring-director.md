# Scoring Director - Narrated Documentary Pipeline

> **SCOPE:** The narrated-documentary channel is generation-first — ~99% of segments are AI-generated to DEPICT the narration (see `ai-visual-director.md`) and are NOT scored here. This three-layer scoring workflow applies ONLY to the rare archival/fallback case: `archival_footage` segments (genuine historical footage) or segments where AI generation hard-failed. If you're here for a normal scene, you're in the wrong stage.

## When To Use

**Archival / fallback path only.** In the AI-primary pipeline, AI-generated video
is the default visual for every segment (see `ai-visual-director.md`), generated
separately and NOT scored here. This stage runs only for segments explicitly
marked `archival_footage` (genuine historical events) or to pick a fallback clip
when AI generation failed. Footage candidates are downloaded; you score every
real candidate through three layers to select the best REAL clip per
archival/fallback scene.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scoring_manifest.schema.json` | Artifact validation |
| Prior artifact | segment_plan | Visual descriptions and pacing types |
| Prior artifact | footage_candidates | Candidate clips per scene |
| Tool | `layer1_metadata` | Metadata-based scoring (resolution, duration, source) |
| Tool | `layer2_siglip` | SigLIP visual-semantic similarity scoring |
| Tool | `layer3_gemini` | Gemini multimodal evaluation |
| Tool | `decision_engine` | Composite score aggregation |
| Tool | `source_priority` | Source quality weighting |
| Tool | `footage_db` | Footage database for deduplication and history |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Runs every candidate clip through a three-layer scoring pipeline.
Layer 1 checks metadata (resolution, duration, format). Layer 2 runs
SigLIP visual-semantic matching against the scene's visual description.
Layer 3 uses Gemini multimodal to evaluate narrative fit, visual
quality, and emotional register. A decision engine aggregates the
three scores with source quality weighting to select one winner per
scene.

## Inputs

- **segment_plan** artifact: visual descriptions, pacing types, moods
- **footage_candidates** artifact: candidate clips per scene

## Outputs

- **scoring_manifest** artifact: version, scenes array each with
  selected_clip and runner_ups

## Workflow

### 1. Layer 1 — Metadata Scoring

For each candidate, run `layer1_metadata` to check:

- **Resolution:** >= 1280x720 scores 1.0, 640x480 scores 0.5, below scores 0.2
- **Duration:** clip must be at least as long as scene duration target.
  Shorter clips get penalized proportionally.
- **Format:** MP4/H.264 scores 1.0, other containers score 0.8
  (may need transcoding)
- **Source tier:** Investigation footage (CSB/NTSB) gets a 1.5x
  multiplier. General stock gets 1.0x.

Layer 1 is fast and cheap. It eliminates obviously unfit candidates
before spending compute on visual scoring.

### 2. Layer 2 — SigLIP Visual-Semantic Scoring

For candidates that pass Layer 1 (score >= 0.3), run `layer2_siglip`:

- Extract 3-5 representative frames from the clip
- Encode the scene's visual_description as text
- Compute cosine similarity between frame embeddings and text embedding
- Take the maximum similarity across frames as the clip's Layer 2 score

SigLIP is better than CLIP for this use case because it handles longer
descriptive text and produces more calibrated similarity scores.

Key parameters:
- Use the full visual_description, not the search queries
- Extract frames at 25%, 50%, 75% of clip duration (avoid black
  frames at start/end)
- Threshold: Layer 2 score >= 0.20 to proceed to Layer 3

### 3. Layer 3 — Gemini Multimodal Evaluation

For the top 3-5 candidates per scene (by Layer 2 score), run
`layer3_gemini`:

Send the clip (or representative frames) along with:
- The scene's visual_description
- The scene's narration text
- The scene's pacing type and mood

Ask Gemini to evaluate:
- **Narrative fit** (0-1): Does this clip illustrate what the
  narration describes?
- **Visual quality** (0-1): Is the footage sharp, well-exposed,
  stable?
- **Emotional register** (0-1): Does the clip's mood match the
  scene's mood tag?
- **Authenticity** (0-1): Does this look like real disaster/
  investigation footage or generic stock?

Layer 3 is the most expensive layer. Run it only on shortlisted
candidates, never on the full candidate pool.

### 4. Composite Scoring With Source Weighting

Run `decision_engine` to aggregate scores:

```
composite = (layer1 * 0.15) + (layer2 * 0.35) + (layer3 * 0.50)
composite *= source_multiplier
```

Source multipliers via `source_priority` (real footage only — AI video is the
primary source, generated separately by ai_visual_gen, and is never scored here):
- CSB/NTSB investigation footage: 1.3x (authenticity premium for real events)
- Government archives (NARA, NASA, LOC): 1.15x
- Wikimedia/Archive.org: 1.05x
- General stock (Pexels, Pixabay, etc.): 1.0x

### 5. Two-Pass Color Coherence Check

After selecting winners, check color coherence across the full timeline:

**Pass 1:** Extract a representative frame from each selected clip.
Compute the average hue and saturation.

**Pass 2:** Flag any adjacent scenes where the hue difference exceeds
30 degrees or saturation difference exceeds 0.3. These pairs will need
extra attention in the color grade render stage.

Record color coherence flags in the manifest so the render stage knows
where to apply targeted correction.

### 6. Record Runner-Ups

For every scene, keep the top 2-3 candidates that were NOT selected.
The review stage uses these for swap suggestions when the human
reviewer flags a weak clip.

### 7. Emit The Scoring Manifest

```json
{
  "version": "1.0",
  "scenes": [
    {
      "scene_id": "scene_01",
      "selected_clip": {
        "clip_id": "archive_org_csb_2005_0323_clip04",
        "source": "archive_org",
        "score": 0.87,
        "trim_points": { "in_seconds": 2.0, "out_seconds": 14.5 }
      },
      "runner_ups": [
        {
          "clip_id": "pexels_refinery_28441",
          "source": "pexels",
          "score": 0.61
        }
      ]
    }
  ]
}
```

## Quality Bar

- All three layers ran for every candidate that passed Layer 1
- Source quality weighting applied (investigation > stock)
- Color coherence check completed on the selected timeline
- Runner-ups recorded for every scene (at least 1 per scene)
- No clip selected for more than one scene

## Common Mistakes

- **Running Layer 3 on all candidates.** Gemini calls are expensive.
  Filter through Layer 1 and Layer 2 first. Top 3-5 per scene max.
- **Ignoring source weighting.** A stock clip with a 0.85 composite
  should lose to investigation footage at 0.75. The source multiplier
  exists for this reason.
- **Skipping color coherence.** The render stage cannot fix drastic
  color mismatches that span the whole timeline. Flag them now.
- **Picking by composite score alone.** A technically perfect stock
  clip of a different refinery may score higher than grainy CSB footage
  of the actual incident. For disaster documentaries, authenticity
  matters more than visual polish.
- **Forgetting trim points.** Most clips have dead space at the start
  and end. Set in/out trim points to the usable portion.

## Tools Available

- `layer1_metadata` — Metadata extraction and scoring (resolution,
  duration, format, source tier)
- `layer2_siglip` — SigLIP visual-semantic similarity scoring against
  scene descriptions
- `layer3_gemini` — Gemini multimodal evaluation for narrative fit,
  quality, register, and authenticity
- `decision_engine` — Composite score aggregation with configurable
  layer weights
- `source_priority` — Source quality multiplier lookup
- `footage_db` — Footage database for deduplication history and
  cross-project reuse
