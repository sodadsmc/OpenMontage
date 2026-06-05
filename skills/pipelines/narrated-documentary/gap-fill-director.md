# Gap Fill Director - Narrated Documentary Pipeline

## When To Use

**Inverted in the AI-primary pipeline.** AI video is now the default (see
`ai-visual-director.md`); this stage fills the few remaining GAPS — segments where
AI generation failed the quality gate, or segments deliberately left for real
footage. Fill those gaps from stock/archival via `direct_clip_search`, falling
back to a one-off AI clip (`runway_gapfill`) only if no suitable real clip exists.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/gap_fill_report.schema.json` | Artifact validation |
| Prior artifact | ai_visual_assets | AI segment clips (a missing entry = a gap) |
| Tool | `direct_clip_search` | Stock/archival fallback sourcing |
| Tool | `runway_gapfill` | Last-resort one-off AI clip for a gap |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Reviews `ai_visual_assets` for segments with no usable AI clip (generation failed,
or the segment is deliberately archival). For those segments only, sources a
fallback clip from stock/archival via `direct_clip_search`, matching the visual
register of the AI-generated material. As a last resort (no suitable real clip),
generates a single AI clip via `runway_gapfill`.

## Inputs

- **ai_visual_assets** artifact: AI segment clips (a missing segment = a gap to fill)

## Outputs

- **gap_fill_report** artifact: version, generated_clips array
  (may be empty if no gaps exist)

## Workflow

### 1. Identify Gap Scenes

Scan the scoring manifest for scenes where:
- `selected_clip.score < 0.45` (weak match)
- No selected_clip exists (search returned nothing usable)
- The scene was flagged during footage search as having < 2 candidates

A scene qualifies for gap fill ONLY when its best available real
footage is genuinely unsuitable. Do not generate AI clips just because
the score is slightly below average.

### 2. Prioritize Gaps

If more than 5 scenes need gap fill, prioritize:

1. Scenes with NO footage at all (critical gaps)
2. Crisis-pacing scenes where weak footage ruins tension
3. Hero scenes that carry the narrative weight
4. Establishing scenes (where a generic wide shot suffices)

Cap at 5 generated clips per video. If more than 5 scenes are
genuinely unfillable, escalate to the user — the footage search
queries may need rewriting rather than generating AI clips.

### 3. Craft Generation Prompts

For each gap scene, derive the Runway prompt from the visual
description in the segment plan. Adapt for AI generation:

**From visual_description:** "Aerial view of a chemical plant at dawn,
vapor rising from cooling towers, workers crossing the lot"

**Runway prompt:** "Cinematic aerial shot of an industrial chemical
plant at dawn, wisps of steam rising from metal cooling towers,
golden morning light, documentary footage style, 4K, steady drone
movement"

Key prompt rules:
- Add "documentary footage style" to match the rest of the video
- Specify camera movement (steady, slow pan, static)
- Include lighting and time of day
- Do NOT include text, logos, or human faces (AI struggles with these)
- Keep prompts under 200 words

### 4. Generate With Budget Tracking

For each gap scene:

```python
runway_gapfill.execute({
    "prompt": "...",
    "duration_seconds": scene_target_duration,
    "aspect_ratio": "16:9",
    "output_path": "projects/<name>/assets/video/generated/scene_<id>.mp4",
})
```

Track cost_usd per generation. Typical Runway costs: $0.05-0.50 per
clip depending on duration and quality settings.

### 5. Verify Generated Output

After generation:
- Check that the output file exists and plays
- Verify duration matches the requested duration (within 10%)
- Visually inspect: does it look plausibly documentary? If it looks
  obviously AI-generated (melting objects, impossible physics), flag
  it and consider whether the gap is better left with weak real footage

### 6. Emit The Gap Fill Report

```json
{
  "version": "1.0",
  "generated_clips": [
    {
      "scene_id": "scene_07",
      "output_path": "projects/<name>/assets/video/generated/scene_07.mp4",
      "prompt": "Cinematic aerial shot of an industrial plant...",
      "cost_usd": 0.25
    }
  ]
}
```

If no gaps exist, emit an empty array:

```json
{
  "version": "1.0",
  "generated_clips": []
}
```

## Quality Bar

- AI generation only triggered for scenes below score threshold
- Maximum 5 generated clips per video
- Each generated clip has cost_usd recorded
- Generated clips match documentary visual register
- Total gap fill cost within budget allocation

## Common Mistakes

- **Generating clips for every scene.** Gap fill is a last resort, not
  a default strategy. If you are generating more than 5 clips, the
  footage search stage needs fixing.
- **Using AI footage for scenes where real footage exists.** Even
  imperfect real footage (grainy CSB video) is more authentic than
  polished AI generation for a documentary.
- **Prompts that produce obviously fake footage.** Avoid complex human
  actions, readable text, or specific branded equipment. AI generation
  works best for wide establishing shots and atmospheric footage.
- **Not tracking costs.** Runway generation is not free. Every clip's
  cost must be recorded for budget governance.
- **Replacing instead of supplementing.** Generated clips should fill
  gaps, not replace existing footage decisions made by the scoring
  stage.

## Tools Available

- `runway_gapfill` — AI video generation via Runway. Supports text-to-
  video with duration control, aspect ratio settings, and style
  parameters.
