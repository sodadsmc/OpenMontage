# AI Visual Director - Narrated Documentary Pipeline

## When To Use

The Asset Bible and voice-over exist. This is the PRIMARY visual stage:
generate AI video for every AI segment, kept consistent by anchoring each shot
to its asset's canonical reference image. Real archival footage is handled
separately (footage_search/scoring) and is the fallback, not the default.

This stage is **two-phase** so the look is approved before the bulk spend:

- **Phase A (prep, cheap):** build the render package — per-shot keyframes
  (Nano Banana), the Shot Manifest, and the HERO clips (Grok i2v) for review.
- **Phase B (bulk, cloud):** generate every Grok image-to-video shot via Kie.ai,
  then concat each segment's shots into one clip. No GPU box required.

## Prerequisites

| Resource | Purpose |
|----------|---------|
| Schema `schemas/artifacts/shot_manifest.schema.json` | Bulk job spec validation |
| Prior artifact `segment_plan` | Which segments are `ai_video`, their motion/style/shots |
| Prior artifact `asset_bible` | Canonical reference image per recurring asset |
| Prior artifact `voice_manifest` | Narration durations (the timing contract) |
| Tool `image_selector` (-> `nano_banana_image`) | Per-shot keyframes (edit-mode from canonical ref) |
| Tool `video_selector` (-> `grok_kie_video` / `kie_video`) | Image-to-video generation |
| Script `build_render_package.py` | Phase A: keyframes + manifest + hero clips |
| Script `run_bulk_generation.py` | Phase B: bulk Grok i2v via Kie.ai (cloud) |
| Lib `lib/visual_router.py` | `plan_ai_video` (plan) + `generate_shot` (execute) |
| Doc `docs/AI_VIDEO_SETUP.md` | Env vars, GPU box setup, run order |

## Provider strategy

- **Images (canonical refs + keyframes):** Nano Banana via Kie.ai (`KIE_API_KEY`, model `google/nano-banana`). Kie returns a hosted URL that becomes the i2v anchor (no third-party host).
- **Default video:** **Grok Imagine image-to-video via Kie.ai — cloud, no GPU box**; 6–30s clips (Grok video-1.5 via Kie; `GROK_KIE_MAX_SECONDS` rolls back the cap), #1 i2v Arena, ~$0.017/s.
- **Hero shots:** the same Grok model, generated in prep for review; premium Veo/Runway is an opt-in escalation.
- **Channel style:** the graphic-novel look (`styles/channel_styles/`, `CHANNEL_STYLE` env) is injected into every image + video prompt by `lib/channel_style.py`; `lib/finishing.py` applies the duotone + grain finishing pass at render. Per-segment `ai_style` carries **mood**, the channel style carries the **medium**.
- **Shot length is provider-aware** (Grok 30s, Wan 8s): segments split into the fewest, longest shots the model handles well — segments at/under the cap are a single clip (no concat).
- **Multi-clip segments CHAIN, never duplicate.** When a segment still needs more than one
  clip, leg N+1 is anchored to leg N's extracted final frame (`resolve_chain_anchor`) with a
  **beat-progressed prompt** derived from the narration (`lib/beat_splitter.py`) — each leg
  describes what happens NEXT in the same continuous scene. Two parallel takes of the same
  keyframe (the old behavior) is a bug, not a cut.
- **People shots animate from POPULATED keyframes.** Canonical anchors are deliberately
  empty rooms; a shot whose prompt puts a person in frame gets a Nano Banana edit of the
  canonical (subject placed ON surfaces with correct anatomy, gated by `validate_keyframe`)
  before the i2v call. Animating a person out of an empty frame is how bodies melt into
  furniture. Opt out per run with `AI_POPULATED_KEYFRAMES=0`.
- **Narration is enforced, not assumed.** Phase A runs the narration↔visual alignment gate
  (`lib/narration_gate.py`) before any spend, and the post-generation Gemini gate checks the
  finished clip against BOTH the shot description and the narration it plays under.

## Workflow

### 1. Confirm the Asset Bible is ready

Every recurring location/subject must have a canonical reference image
(`build_asset_bible.py` was run). Each shot is anchored to it so the same place
looks the same across the documentary.

### 2. Phase A — build the render package (local)

```
python projects/<project>/script_v5/build_render_package.py
```

This first runs the **narration alignment gate** (every AI segment's prompt is
checked against its narration — mismatches block until the scored script is fixed
or `--skip-narration-gate`), then splits each AI segment into shots (provider-aware
cap — Grok ≤30s; longer segments become a CHAINED continuation, never a looped or
duplicated clip), generates a keyframe per shot via Nano Banana (edit-mode from the
canonical reference so the look is locked; people shots get a populated keyframe),
and writes `shot_manifest_v6.json`. **Hero shots are generated now** (Grok i2v) so
you can see the actual look before the bulk spend.

### 3. Hero review — the approval gate

Review the hero clips. Judge: does the AI look right? Is the location/subject
consistent with its canonical reference? Is the period accurate? Only approve the
bulk batch once the hero look is right. Regenerate hero shots (new seed / edited
`ai_prompt`/`ai_style`) until they pass.

### 4. Phase B — bulk generation (cloud, runs anywhere)

```
python projects/<project>/script_v5/run_bulk_generation.py
```

Generates every pending Grok i2v shot via Kie.ai — **cloud, no GPU box** — passes
each through the quality gate, then concatenates each segment's shots to exactly
the narration duration. Resumable (re-run after interruptions). Emits
`ai_visual_assets_v6.json` (segment -> clip). *Free-compute alternative:* set
`DEFAULT_VIDEO_PROVIDER="wan"` and run on a GPU box.

### 5. Hand off

`build_v6.py` picks up the per-segment AI clips automatically (from
`assets/ai_segments/`); segments with no usable AI clip fall through to the
stock/archival fallback (gap_fill stage).

## Quality Bar

- Every shot is anchored to its asset's canonical reference image (consistency)
- Hero clips reviewed and approved before the box batch is paid for
- Long segments are CHAINED continuations (leg N+1 starts from leg N's final frame,
  beat-progressed prompt) at the exact narration duration — never two takes of the
  same keyframe, never a looped clip
- People shots animate from populated, gated keyframes — never from an empty room
- Each AI clip passes the quality gate: integrity, motion, duration ±150ms, AND the
  Gemini video review (content vs description+narration, artifact check: bodies
  merging into surfaces, geometry that grows/stretches, morphing limbs — fail-closed)
- Cost within the per-video budget (Grok i2v ~$0.017/s; a full doc ≈ $5–6)

## Ethics

Real historical incidents (named victims, actual events) must use
`archival_footage` (real footage), never `ai_video`. AI video is for atmospheric
and illustrative shots only — never fabricate a real, identifiable person or a
real event.

## Common Mistakes

- **Skipping the hero gate.** Generating the whole bulk batch before reviewing the
  look wastes generation budget. Always review hero clips first.
- **One looped clip for a long segment.** Long beats must be several distinct
  shots cut together, not one short clip stretched.
- **Defaulting to a static "Ken Burns" zoom.** A near-static `ai_motion` on a
  still anchor yields a boring slow zoom. The default must be **dynamic but
  grounded** motion — animate what's IN the scene: subjects acting (a figure
  walking, a hand striking a key, the machine head swinging down, a beam firing),
  the environment alive (scrolling/flickering screens, drifting mist, falling
  snow, dust, dying light), plus a purposeful camera move. Engagement is the goal.
- **Aimless camera reveals that hallucinate.** The way to avoid i2v inventing
  content (people, signage) is NOT to go static — it's to make the motion
  *purposeful*: drive subject/environmental motion and keep camera moves
  motivated, rather than long pans/dollies into empty off-screen space the model
  must fill. Review hero clips for hallucinated junk, but don't trade away motion
  to prevent it.
- **Re-generating keyframes per shot from scratch.** Keyframes are edited FROM the
  single canonical reference so the asset stays consistent — don't bypass it.
- **Assuming you need a GPU box.** The bulk default is Grok cloud (no box). Wan on
  a rented box is an optional free-compute alternative, not a requirement.
