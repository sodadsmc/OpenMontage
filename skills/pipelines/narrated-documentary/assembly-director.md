# Assembly Director - Narrated Documentary Pipeline

## When To Use

Scoring is complete, gaps are filled, sentence boundaries are mapped,
and music is selected. You now assemble the pacing-aware timeline that
stitches everything into a coherent documentary edit. This is where
footage becomes a film.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/assembly_manifest.schema.json` | Artifact validation |
| Prior artifact | scoring_manifest | Selected clips with trim points per scene |
| Prior artifact | gap_fill_report | AI-generated clips for gap scenes |
| Prior artifact | sentence_map | Sentence boundaries per scene |
| Prior artifact | music_manifest | Selected music track(s) |
| Tool | `pacing_engine` | Pacing-aware cut timing and transition selection |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Takes all upstream artifacts and produces a complete cut list with
precise in/out points, volume levels, animations, and framing for
every clip in the timeline. The pacing engine drives cut timing based
on the establishing/escalation/crisis/resolution arc from the segment
plan, aligned to sentence boundaries from the audio analysis.

## Inputs

- **scoring_manifest** — selected clips and trim points per scene
- **gap_fill_report** — AI-generated clips (may be empty)
- **sentence_map** — sentence start/end timestamps per scene
- **music_manifest** — selected music track

## Outputs

- **assembly_manifest** — version, cuts array defining the complete
  timeline

## Workflow

### 1. Merge Footage Sources

Build the master clip list by merging:

1. Selected clips from scoring_manifest (primary source for most scenes)
2. Generated clips from gap_fill_report (for gap scenes only)

For each scene, resolve to exactly one clip. If a scene has both a
scored clip and a generated clip, prefer the scored clip unless its
score is below the gap fill threshold.

### 2. Apply Pacing Rules

The pacing engine uses the segment plan's pacing types to determine
cut timing and hold duration:

| Pacing Type | Hold Duration | Cut Style | Transition |
|-------------|---------------|-----------|------------|
| establishing | 4-8s | Wide shot, slow | Dissolve or fade (1-2s) |
| escalation | 3-5s | Medium shot, building | Cut or quick dissolve (0.5s) |
| crisis | 1.5-3s | Tight/close, rapid | Hard cut (0s) |
| resolution | 4-6s | Mixed, breathing room | Dissolve or fade (1-1.5s) |

The pacing engine aligns cuts to sentence boundaries from the
sentence map. The rule: **cuts happen between sentences, never mid-
sentence.** If the pacing type calls for a 3-second hold but the
current sentence runs for 5 seconds, hold for 5 seconds.

#### Scene Library — The Director's Touch

When the scored script carries per-scene Scene Library tags (see
`skills/creative/scene-library.md`), the assembly reads them to refine
the cut — it never overrides a value the script set. At this stage you
consume, not author:

| Tag | What it drives in assembly |
|-----|----------------------------|
| `rhythm` | Tightens/loosens the hold inside the pacing type — `fast` trims toward the low end, `lingering` toward the high; `breath` = hold the frame and insert the `silence_after_s` beat. |
| `audio_transition` | The audio handoff to the next cut (l_cut / j_cut / sound_bridge / hard_cut / match_* / contrast_cut) — see step 4. |
| `narration_mode` | `none` signals an observed beat: let the image and music carry it, no VO ducking. |

`directors_move` and `retention_beat` are upstream/editorial concerns
and do not change the cut math here — leave them untouched.

```python
pacing_engine.execute({
    "scoring_manifest": scoring_manifest,
    "sentence_map": sentence_map,
    "segment_plan": segment_plan,
    "mode": "narrated_documentary",
})
```

### 3. Mute Source Audio

**Critical rule: set volume=0 on ALL footage clips.** The only audio
tracks in the final video are:
- Voice-over narration
- Music bed

Source audio from stock clips (ambient noise, on-scene audio from
investigation footage) must be muted. If the user specifically
requests preserving source audio for a scene, that is a special case
that needs explicit approval.

### 4. Apply Audio Transitions (L/J-Cuts and beyond)

Audio transitions create smoother handoffs by offsetting the audio and
video cut points. Because all audio is built in post, these are fully
available to you here. Honor the per-scene `audio_transition` tag from
the scored script when present:

- **l_cut (prior audio trails):** Let the outgoing scene's narration or
  SFX run 0.5-1.0s *over* the new image. The viewer sees the new
  footage while still hearing the previous topic.
- **j_cut (next audio leads):** Start segment N+1's narration/SFX 0.5-
  1.0s *before* its visual appears. The viewer hears the new topic
  while still seeing the previous footage.
- **sound_bridge:** Carry one continuous sound or VO line across the
  cut so a shared audio thread glues two shots together.
- **hard_cut / match_cut / match_on_action / contrast_cut:** Cut audio
  and video together; the named visual-match variants govern framing,
  not the audio offset.

Apply these at major scene transitions (not between every cut). When no
tag is set, fall back to defaults: l_cuts for escalation -> crisis
transitions, j_cuts for establishing -> escalation transitions.

### 5. Normalize Aspect Ratios

All clips must be normalized to the target canvas. Record the framing
decision per cut:

- **16:9 clips on 16:9 canvas:** No adjustment needed. Framing: "native"
- **4:3 clips on 16:9 canvas:** Letterbox with pillar bars OR center-
  crop. Framing: "letterbox" or "center_crop"
- **Vertical clips on 16:9 canvas:** Center with heavy pillar bars.
  Framing: "pillar_box"
- **Non-standard ratios:** Crop to fill, preserving center of frame.
  Framing: "crop_to_fill"

For documentary, prefer letterboxing over cropping — the viewer should
see the full original frame, especially for investigation footage.

### 6. Select Ken Burns Animation

For clips that are longer than the scene's hold duration (common with
still images or long stock clips), apply Ken Burns motion to prevent
the image from feeling static:

- **establishing:** Slow zoom out (105% -> 100%), 0.5% per second
- **escalation:** Slow pan left-to-right, tracking motion in frame
- **crisis:** Static (fast cuts make Ken Burns disorienting)
- **resolution:** Slow zoom in (100% -> 103%), contemplative feel

Record the animation type per cut.

### 7. Build The Cuts Array

For each scene, emit one cut entry:

```json
{
  "cut_id": "cut_01",
  "scene_id": "scene_01",
  "source": "projects/<name>/assets/video/candidates/clip04.mp4",
  "in_seconds": 2.0,
  "out_seconds": 14.5,
  "volume": 0,
  "animation": "slow_zoom_out",
  "framing": "native"
}
```

### 8. Verify Timeline Integrity

Before emitting the manifest:

- Sum all cut durations: should match total voice-over duration
  (from voice_manifest) within 2 seconds
- Check for gaps: no dead time between cuts unless intentional
  (e.g., a black frame beat)
- Check for overlaps: no two cuts occupy the same timeline position
- Verify music track duration covers the full timeline

### 9. Emit The Assembly Manifest

```json
{
  "version": "1.0",
  "cuts": [
    {
      "cut_id": "cut_01",
      "scene_id": "scene_01",
      "source": "projects/<name>/assets/video/candidates/clip04.mp4",
      "in_seconds": 2.0,
      "out_seconds": 14.5,
      "volume": 0,
      "animation": "slow_zoom_out",
      "framing": "native"
    }
  ]
}
```

## Quality Bar

- Every scene in the segment plan has exactly one cut in the assembly
- Pacing types followed (crisis = fast cuts, establishing = slow holds)
- Source audio muted (volume=0) on all footage clips
- L/J-cuts present at major scene transitions
- Aspect ratios normalized with framing recorded per cut
- Timeline duration matches voice-over total within 2 seconds
- No timeline gaps or overlaps

## Common Mistakes

- **Leaving source audio unmuted.** Stock footage ambient noise will
  clash with the narration and music. Mute everything.
- **Cutting mid-sentence.** Cuts aligned to sentence boundaries from
  the sentence map. Never cut while the narrator is mid-word.
- **Ignoring pacing arc.** Using the same hold duration for every
  scene produces a monotone video. Follow the establishing/escalation/
  crisis/resolution timing table.
- **Cropping investigation footage.** CSB/NTSB footage should be shown
  in its original framing. Letterbox rather than crop — the original
  composition is part of the authenticity.
- **Applying Ken Burns to crisis scenes.** Fast cuts with moving
  camera is disorienting. Crisis cuts should be static.
- **Not verifying timeline continuity.** A 2-second gap between scene
  5 and scene 6 means 2 seconds of black screen. Always check.

## Tools Available

- `pacing_engine` — Pacing-aware cut timing engine. Takes scoring
  manifest, sentence map, and segment plan to produce cut timing
  aligned to speech boundaries and pacing arc.
