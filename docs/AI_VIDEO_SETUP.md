# AI-Generated Video Setup (narrated-documentary)

This fork generates documentary visuals with **AI video as the primary source**
(stock/archival is fallback). Recurring locations/subjects stay consistent via an
**Asset Bible**: one canonical reference image per asset, then image-to-video for
every shot of it.

## Providers

| Role | Provider | Env var | Notes |
|------|----------|---------|-------|
| Reference images + keyframes | **Nano Banana 2** via Kie.ai (`nano_banana_image`) | `KIE_API_KEY` (same key as hero video) | overrides: `NANO_BANANA_MODEL` (default `nano-banana-2`), `KIE_BASE_URL` |
| Default video (image-to-video) | **Wan** (`wan_video`, local GPU) | `VIDEO_GEN_LOCAL_ENABLED=true` | run on the **rented GPU box** (e.g. vast.ai); needs Wan weights + `requirements-gpu.txt` |
| Hero shots | **Kie.ai** (`kie_video`, Veo/Runway) | `KIE_API_KEY` | only used for shots marked `hero: true` |

> **Keyframe anchoring (Kie.ai note):** Kie.ai takes reference images as public
> URLs only (no file upload). By default each AI shot is anchored to the Asset
> Bible's **local** canonical reference image directly (Wan i2v accepts a local
> path; maximizes consistency). For **per-shot keyframe variety** or **hero i2v**
> (Kie video needs a URL), `lib/image_host.py` hosts the image via keyless
> **catbox.moe** by default (or fal.ai with `IMAGE_HOST=fal`). Enable per-shot
> keyframe edits with `AI_PER_SHOT_KEYFRAMES=1`.

Add new providers by dropping a tool in `tools/video/` or `tools/graphics/` —
the selectors auto-discover them (the registry now skips any tool whose optional
dependency is missing instead of failing).

## Mode flag

```
AI_VIDEO_PRIMARY=1   # footage segments generate AI video first, stock only on failure
```
Leave unset to keep the legacy stock-first behavior. Segments authored with
`type: ai_video` always generate AI video regardless of this flag.

## Rented GPU box

The default video generator is the existing local `wan_video.py` running on a
rented GPU box (no Modal/serverless). Recommended topology: **run the whole
generation pipeline on the box** (Nano Banana + Kie.ai are plain API calls).

```bash
# On the box (Linux + CUDA, >=24 GB VRAM for Wan 2.1-14B, ~8 GB for 1.3B):
git clone <your-fork>  &&  cd OpenMontage
pip install -r requirements.txt -r requirements-gpu.txt
export VIDEO_GEN_LOCAL_ENABLED=true KIE_API_KEY=... AI_VIDEO_PRIMARY=1
# KIE_API_KEY covers Nano Banana images AND hero video. GOOGLE_API_KEY optional (Gemini quality checks).
# (Wan weights download on first use; ffmpeg must be on PATH)
```

## Run order (Therac-25)

```bash
python projects/therac-25-test/script_v5/build_asset_bible.py      # canonical refs (Nano Banana)
python projects/therac-25-test/script_v5/generate_voice_v6.py      # TTS (existing)
python projects/therac-25-test/script_v5/build_v6.py               # visuals (AI video) + sync gates
python projects/therac-25-test/script_v5/render_v6.py              # trim/concat/mux + post-render sync
```

`build_asset_bible.py --dry-run` extracts the assets and writes
`artifacts/asset_bible_v6.json` without generating images (useful to review the
asset list and edit `locked_attributes` / `period_constraints` before spending).

## Authoring `ai_video` segments

In `scored_script.yaml`, a segment's `visual` block can be:

```yaml
visual:
  description: "Slow walk down a 1985 hospital corridor, fluorescent lights"
  type: ai_video
  asset_ref: loc_kennestone_marietta   # FK into the Asset Bible (or rely on location_id)
  ai_motion: "slow dolly forward"
  ai_style: "1985 documentary, 16mm grain, muted institutional palette"
  shots:                                # optional; auto-split by length if omitted
    - { shot_id: shot_01, ai_prompt: "wide empty corridor", duration_weight: 1 }
    - { shot_id: shot_02, ai_prompt: "closer on a treatment-room door", duration_weight: 2, hero: true }
```

Long segments are split into multiple shots (<=8s each) and concatenated to the
exact narration duration — never a single looped clip. Each shot is anchored to
the asset's canonical reference image so the location stays consistent.

## Ethics

Real historical incidents (named victims, actual CSB/NTSB footage) must use
`type: archival_footage` (real footage). AI video is for atmospheric/illustrative
shots only — do not AI-fabricate real people or real events.
