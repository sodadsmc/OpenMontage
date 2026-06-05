# Segment Director - Narrated Documentary Pipeline

## When To Use

You are parsing a completed narration script into discrete scenes that
drive every downstream stage. Each scene becomes the atomic unit of
footage search, scoring, voice generation, and assembly. Get the
segmentation wrong and every stage after this one inherits bad cuts.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/segment_plan.schema.json` | Artifact validation |
| User input | Script text or conversation history | The narration to segment |
| Tool | `script_segment` | Automated segmentation helper |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Takes a full narration script about a tech disaster and splits it into
ordered scenes. Each scene gets narration text, a target duration, a
pacing type, tiered search queries for footage sourcing, a rich visual
description, and a mood tag. The output is the segment_plan artifact
that every downstream stage reads.

## AI-Primary Authoring

This pipeline generates AI video as the default visual. When authoring scenes:

- **Default to `type: ai_video`** for atmospheric/illustrative beats. Give each an
  `ai_motion` (camera move) and `ai_style` (look/era/grade). Break long beats
  (20-30s) into explicit `shots` so they become a cut sequence, not one looped clip.
- **Tie recurring places/subjects to the Asset Bible.** Give every recurring
  location a stable `location_id` (or an explicit `asset_ref`); the asset_bible
  stage turns these into canonical reference images for cross-shot consistency.
- **Reserve `archival_footage` for genuine historical events** — real incidents,
  named victims, actual investigation footage. Never use `ai_video` to fabricate a
  real, identifiable person or a real event.
- **Mark a few shots `hero: true`** to have them generated up front (Kie.ai) for
  review before the bulk GPU batch.
- The rich `visual_description` still matters — it seeds the AI prompt (and the
  archival search queries when a scene falls back to real footage).

## Inputs

- **User script or topic** from conversation history
- **Research notes** if available (disaster timeline, key events, sources)

## Outputs

- **segment_plan** artifact: version, ordered scenes array

## Workflow

### 1. Identify Natural Scene Breaks

Read the script end to end. Mark breaks where:

- The subject changes (e.g., from "the explosion" to "the investigation")
- The timeframe shifts (e.g., from "that morning" to "three months later")
- The emotional register changes (e.g., from routine operations to crisis)
- A new visual context is needed (e.g., from control room to field site)

Use the `script_segment` tool for an initial pass, then refine manually.
The tool catches obvious paragraph breaks but misses register shifts.

### 2. Write Rich Visual Descriptions

For each scene, write a visual_description that is **rich prose, not
keywords**. The scoring layers use this text to evaluate footage
candidates. Thin descriptions produce bad scores.

Good: "Aerial view of a chemical plant at dawn, vapor rising from
cooling towers, a network of silver pipes catching the first light,
workers in hard hats crossing the gravel lot toward the main facility"

Bad: "chemical plant exterior"

The description should paint what the IDEAL footage looks like. Include:
- Camera angle (aerial, ground level, close-up)
- Lighting conditions (dawn, harsh fluorescent, emergency strobe)
- Key objects and their state (ruptured pipe, intact control panel)
- Human presence and activity (investigators measuring, workers fleeing)
- Atmosphere (smoke, dust, calm before the storm)

### 3. Build Tiered Search Queries

Each scene needs 3-5 search queries ordered from most specific to most
general. The footage search stage runs them in order and stops when it
has enough candidates.

Tier 1 (specific): "CSB Bhopal gas leak investigation footage"
Tier 2 (related): "chemical plant gas leak emergency response"
Tier 3 (general): "industrial plant exterior aerial dawn"
Tier 4 (abstract): "factory smoke atmospheric"

Always start with disaster-specific terms (CSB, NTSB, the actual
incident name) because investigation footage is the highest value.

### 4. Assign Pacing Types

Each scene gets one pacing type from the fixed vocabulary:

- **establishing** — wide shots, slow holds (4-8s), sets the context
- **escalation** — medium shots, moderate cuts (3-5s), builds tension
- **crisis** — tight shots, fast cuts (1.5-3s), peak intensity
- **resolution** — mixed shots, breathing room (4-6s), aftermath and lessons

A typical disaster documentary follows: establishing (context) ->
escalation (warning signs) -> crisis (the event) -> resolution
(aftermath and investigation). But the arc can repeat for multi-incident
stories.

### 5. Estimate Scene Durations

Duration is driven by the narration text length. Rough formula:
- Average speaking rate: 150 words per minute
- Scene duration = (word_count / 150) * 60 seconds
- Add 1-2 seconds for breathing room at scene boundaries

Cross-check: the sum of all scene durations should match the target
video length within 10%.

### 6. Assign Mood Tags

Each scene gets a mood from: tense, calm, urgent, somber, technical,
hopeful, ominous, reflective. The music selection stage uses these to
match tracks to sections.

### 7. Pre-Check Footage Availability

Before finalizing, do a quick mental check: can the Tier 1 queries
plausibly return footage? If a scene describes a very specific internal
event (e.g., "the exact moment the valve failed"), that footage almost
certainly does not exist. Flag it now and provide a realistic Tier 2
alternative rather than letting the search stage fail silently.

### 8. Emit The Segment Plan

```json
{
  "version": "1.0",
  "scenes": [
    {
      "scene_id": "scene_01",
      "narration": "On the morning of March 23rd, 2005...",
      "duration_seconds": 12.5,
      "pacing": "establishing",
      "search_queries": [
        "BP Texas City refinery CSB investigation",
        "oil refinery exterior Texas morning",
        "industrial refinery aerial wide shot"
      ],
      "visual_description": "Wide aerial shot of a sprawling oil refinery at dawn...",
      "mood": "calm"
    }
  ]
}
```

## Quality Bar

- Every scene has all seven fields populated
- Visual descriptions are at least 20 words of concrete prose
- Search queries are ordered specific-to-general with at least 3 per scene
- Pacing types follow the disaster arc (not random assignment)
- Durations sum to target length within 10%
- No placeholder text ("TBD", "insert footage here")

## Common Mistakes

- **Keyword-only visual descriptions.** "explosion factory fire" gives
  the scoring layers nothing to work with. Write a sentence.
- **All queries at the same specificity.** If every query is general
  ("factory explosion"), the search returns the same generic clips for
  every scene. Tier your queries.
- **Ignoring pacing arc.** Assigning "crisis" to every scene makes the
  video exhausting. Use the full vocabulary.
- **Scenes that are too long.** A 45-second scene with one visual
  description means 45 seconds of the same clip. Break long narration
  passages into sub-scenes.
- **Scenes that are too short.** A 2-second scene is not enough for a
  establishing shot to register. Minimum practical duration is 3 seconds.
- **Forgetting the resolution phase.** Disaster stories need closure —
  the investigation, the lessons learned, the reforms. Do not end on
  the crisis.

## Tools Available

- `script_segment` — Automated script segmentation. Produces initial
  scene breaks from paragraph structure and topic shifts. Always refine
  its output manually.
