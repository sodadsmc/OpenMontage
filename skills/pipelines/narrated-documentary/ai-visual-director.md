# AI Visual Director - Narrated Documentary Pipeline

## When To Use

The Asset Bible and voice-over exist. This is the PRIMARY visual stage:
generate AI video for every AI segment, kept consistent by anchoring each shot
to its asset's canonical reference image. Real archival footage is handled
separately (footage_search/scoring) and is the fallback, not the default.

This stage is **two-phase** so the only GPU-bound work runs as one bulk batch:

- **Phase A (local, cheap, API only):** build the render package — per-shot
  keyframes (Nano Banana), the Shot Manifest, and the HERO clips (Kie.ai) for
  review.
- **Phase B (rented GPU box):** run every Wan image-to-video shot in one batch,
  then concat each segment's shots into one clip.

## Prerequisites

| Resource | Purpose |
|----------|---------|
| Schema `schemas/artifacts/shot_manifest.schema.json` | Bulk job spec validation |
| Prior artifact `segment_plan` | Which segments are `ai_video`, their motion/style/shots |
| Prior artifact `asset_bible` | Canonical reference image per recurring asset |
| Prior artifact `voice_manifest` | Narration durations (the timing contract) |
| Tool `image_selector` (-> `nano_banana_image`) | Per-shot keyframes (edit-mode from canonical ref) |
| Tool `video_selector` (-> `wan_video` / `kie_video`) | Image-to-video generation |
| Script `build_render_package.py` | Phase A: keyframes + manifest + hero clips |
| Script `run_bulk_generation.py` | Phase B: bulk Wan i2v on the box |
| Lib `lib/visual_router.py` | `plan_ai_video` (plan) + `generate_shot` (execute) |
| Doc `docs/AI_VIDEO_SETUP.md` | Env vars, GPU box setup, run order |

## Provider strategy

- **Keyframes + canonical references:** Nano Banana 2 (Gemini image) — API, runs in prep.
- **Default video:** Wan image-to-video on a rented GPU box (`VIDEO_GEN_LOCAL_ENABLED=true`).
- **Hero shots:** Kie.ai premium (Veo/Runway) — API, generated in prep for review.

## Workflow

### 1. Confirm the Asset Bible is ready

Every recurring location/subject must have a canonical reference image
(`build_asset_bible.py` was run). Each shot is anchored to it so the same place
looks the same across the documentary.

### 2. Phase A — build the render package (local)

```
python projects/<project>/script_v5/build_render_package.py
```

This splits each AI segment into shots (<= 8s each; long segments become a cut
sequence, never a looped clip), generates a keyframe per shot via Nano Banana
(edit-mode from the canonical reference so the look is locked), and writes
`shot_manifest_v6.json`. **Hero shots are generated now** (Kie.ai) so you can see
the actual look before paying for the box.

### 3. Hero review — the approval gate

Review the hero clips. Judge: does the AI look right? Is the location/subject
consistent with its canonical reference? Is the period accurate? Only approve the
box batch once the hero look is right. Regenerate hero shots (new seed / edited
`ai_prompt`/`ai_style`) until they pass.

### 4. Phase B — bulk generation (rented GPU box)

On the box (`VIDEO_GEN_LOCAL_ENABLED=true`, Wan weights, ffmpeg):

```
python projects/<project>/script_v5/run_bulk_generation.py
```

Runs every pending Wan i2v shot (resumable — re-run after interruptions), passes
each through the quality gate, then concatenates each segment's shots to exactly
the narration duration. Emits `ai_visual_assets_v6.json` (segment -> clip).

### 5. Hand off

`build_v6.py` picks up the per-segment AI clips automatically (from
`assets/ai_segments/`); segments with no usable AI clip fall through to the
stock/archival fallback (gap_fill stage).

## Quality Bar

- Every shot is anchored to its asset's canonical reference image (consistency)
- Hero clips reviewed and approved before the box batch is paid for
- Long segments are multi-shot cut sequences at the exact narration duration
- Each AI clip passes the quality gate (integrity, motion, duration +/-150ms)
- Cost within the per-video budget (Wan local = $0 marginal; Kie only for hero)

## Ethics

Real historical incidents (named victims, actual events) must use
`archival_footage` (real footage), never `ai_video`. AI video is for atmospheric
and illustrative shots only — never fabricate a real, identifiable person or a
real event.

## Common Mistakes

- **Skipping the hero gate.** Generating the whole box batch before reviewing the
  look wastes GPU time. Always review hero clips first.
- **One looped clip for a long segment.** Long beats must be several distinct
  shots cut together, not one short clip stretched.
- **Re-generating keyframes per shot from scratch.** Keyframes are edited FROM the
  single canonical reference so the asset stays consistent — don't bypass it.
- **Routing everything to Kie.ai.** Kie is for hero shots only; the bulk default
  is Wan on the box (free marginal cost).
