# Therac-25 Documentary — Exact Production Workflow

**Workspace:** `D:/OpenMontage2` (branch `v6-baseline`) · **Project:** `projects/therac-25-test/` · **Style:** hand-inked graphic novel, deep navy `#0a1428` + amber (`CHANNEL_STYLE=graphic-novel-disaster`).

This is the prescriptive recipe to produce the narrated documentary reliably. Follow it top to bottom. Every command is run from the workspace root (`D:/OpenMontage2`). Do not improvise lanes, do not skip gates, do not run a Grok clip longer than 6 s. The trial-and-error this episode suffered is encoded below as RULES — obey them.

## 1. Overview + pipeline diagram

The pipeline is **TTS-first**: narration audio is generated and measured first, producing the **Duration Map** (`duration_map_v6.json`) that is the single source of truth for every downstream slot. Visuals are then planned per beat, routed to one of four lanes (Manim / FLF state-morph / FLF drain / Grok i2v), generated under fail-closed gates, conformed to their exact narration slot, and assembled into a constant-frame-rate timeline muxed under the continuous narration track and graded once at the end.

```
 STAGE 0A  build_asset_bible.py ───► asset_bible_v6.json + assets/asset_bible/*.png
              (canonical anchors; Nano Banana)
                  │
 STAGE 1   generate_voice_v6.py ───► audio_v6/seg_*.mp3 + *.alignment.json + narration_v6.mp3
              (ElevenLabs Daniel; TTS-FIRST)
                  │
 STAGE 1b  duration_map (ffprobe) ─► duration_map_v6.json   ◄── SINGLE SOURCE OF TRUTH
                  │
 STAGE 2A  build_render_package.py ─► shot_manifest_v6.json + HERO clips
              GATE: narration-alignment (PRE) + subject-identity (PRE)
                  │
                  ├──► REVIEW hero clips ◄── (pre-spend look check)
                  │
 STAGE 2B  run_bulk_generation.py --yes ─► assets/ai_segments/seg_*.mp4 + ai_visual_assets_v6.json
              GATE: Gemini quality_gate (POST, per clip, 3 retries)   [FLF beats run out-of-band, UNGATED]
                  │
 STAGE 3   build_v6.py (stages 0-5) ─► visuals conformed to slot, asset_registry_v6.json
              GATE: structural (PRE) + pre-assembly sync (HARD) ─► visual_assets_v6.json
                  │
 STAGE 4   render_v6.py ───► renders/therac25_v6.mp4
              conform→concat→mux→finish(duotone+grain)
              GATE: post-render sync (HARD: video_dur >= audio_dur)

 [Preview lane] build_preview.py --yes --until seg_009  → renders/preview_3min.mp4
                (scoped ~3-min chunk; generate → review → spend on next chunk)
```

## 2. THE LANE DECISION TREE (decide this FIRST, per beat)

Every beat picks **exactly one** lane by **what the beat must DO**. Ask, in order:

```
Is the beat teaching a MECHANISM / labeled diagram / a precise count taught?
   └─ YES → MANIM / SKETCH lane  (visual.type=manim_animation; lib/sketch_diagrams.py)

Else: does ONE element change to a SPECIFIC new state the viewer must SEE change
      (glyph X→E, error code appears, needle to a reading, 6→3 figures)?
   └─ YES → FLF STATE-MORPH  (visual.flf; two pixel-matched keyframes; Kling interpolates)

Else: is this a rare "going cold / going dark" emotional FULL-STOP?
   └─ YES → FLF DRAIN  (visual.flf with a high drain; end = start darkened toward navy)
            ⚠ reads as a fade — use SPARINGLY, never two quiet beats back-to-back.

Else (a SUBJECT physically acts + a camera move stages the narration verb):
   └─→ GROK i2v  (visual.type=ai_video)  ◄── the default for the kept first 3 minutes
```

**Lane A — MANIM/SKETCH** — *technical mechanism, a count taught, a labeled schematic.* The diagram is authored, not interpolated, so labels and counts are exact. Route via `SKETCH_SCENE_MAP` / `SKETCH_SEGMENT_OVERRIDE` in `build_v6.py`; falls back to `lib/diagram_codegen.generate_diagram_scene`, then legacy `MANIM_ASSETS`.
- *Examples this episode:* `seg` with template `race_condition` (the concurrency bug schematic); `radiation_therapy`→`linac` (how the beam path works); `byte_overflow` (the counter rolling over). **Never** ask Grok to "show how the interlock works" — it cannot draw a correct labeled mechanism.

**Lane B — FLF STATE-MORPH** — *a precise content change you must watch happen.* Author two keyframes differing only in the one element; Kling 3.0 FLF interpolates between them (`vr.generate_flf_shot`). Counts, per-object state, locked camera, and object permanence hold **by construction**. This is what FLF is FOR.
- *Examples this episode:* the dose glyph `X→E` appearing on the console (carry the literal change in the keyframes, not in a Grok screen); a dial needle moving to a specific reading; "6 overdosed" figures where 3 then go dark (band drain — see Lane C mechanism, used here for a *count reveal*, `band=(0.50,0.62)` so the front 3 drain while the back 3 stay lit).

**Lane C — FLF DRAIN** — *rare "going cold/dark" punctuation.* End frame = start blended toward navy `#0a1428` (`lib.flf.drain_endpoint`, `drain≈0.8`). **This reads as a fade (Ken Burns).** Use it once in a great while, never as a default, never on two consecutive quiet beats.
- *Examples this episode:* the seg_009 "removed safety fuse goes cold" (uniform drain); the final powered-down room settling to dead navy. If you find yourself reaching for it twice in a row, one of those beats wants Lane B or D instead.

**Lane D — GROK i2v** — *gross / ambient motion that stages the narration verb.* Grok renders gross subject + camera motion well (walking, slamming, sweeping, a crane-back reveal, atmospheric drift). This is the workhorse — the kept seg_001–seg_009 are 100% Grok.
- *Examples this episode:* the dose needle "driven violently across the dial and slams the stop"; eyes "snap open"; code "scrolls and races" up a CRT (stage the CRT as a blown-out amber glow — see the Grok screen trap). Find the verb in the narration line and stage it as a SUBJECT physically acting + a camera move.

## 3. PHASE-BY-PHASE STEPS

### STAGE 0A — Asset Bible (canonical anchors)
- **Command:** `python projects/therac-25-test/script_v5/build_asset_bible.py` (`--dry-run` plan only; `--force` regen all after a style change; `--only ASSET_ID …` selective).
- **In:** `script_v5/scored_script.yaml`. **Out:** `artifacts/asset_bible_v6.json` + `assets/asset_bible/<asset_id>.png` (+ `canonical_image_url` Kie host-free URL).
- **Gate guarding it:** none upstream; it gates everything below (build_render_package errors if the bible is absent).
- **Cost:** Nano Banana ≈ **$0.04/img** (~$0.32 for the 8-asset bible). Needs `KIE_API_KEY`.
- **Failure modes to avoid:** Location canonicals are authored **empty** (no people, period-accurate mid-1980s) so Grok never bakes a person into the anchor. Run this BEFORE everything else. If you change `CHANNEL_STYLE`, re-run with `--force` or the anchors drift off-look.

### STAGE 1 — Voice / TTS + alignment
- **Command:** `python projects/therac-25-test/script_v5/generate_voice_v6.py` (no flags; resume-safe, skips existing `.mp3 >1KB`).
- **In:** `scored_script.yaml` narration + `silence_after_s`. **Out:** `assets/audio_v6/seg_NNN.mp3`, `seg_NNN.alignment.json` (ElevenLabs word-level), `narration_v6.mp3` (concatenated with silence beats).
- **Config (in the script):** `VOICE_ID='onwK4e9ZLuTAKqWW03F9'` (Daniel), `model_id='eleven_multilingual_v2'`, `STABILITY_DEFAULT=0.5`, per-segment `STABILITY_OVERRIDES` → 0.6 (seg_006/009/017/020/024/028/029/030) to kill hallucinated emphasis on fragment-heavy beats, `similarity_boost=0.8`, `style=0.15`, `output_format='mp3_44100_192'`, pronunciation dictionary `PDICT_ID='YiawY4G8SCQv8kvJZqZA'` ver `'kiZq7TPX1DLFM4DGMV36'`.
- **Failure modes:** Build/attach the pronunciation dictionary BEFORE generating (acronyms mangle TTS). Don't regenerate just to get an alignment file — it's resume-safe.

### STAGE 1b — Duration Map (timing contract)
- **Entrypoint:** `lib.duration_map.build_duration_map_from_paths(script, audio_paths)` (written by `build_v6.py` STAGE 2; `save_duration_map` → `artifacts/duration_map_v6.json`).
- **In:** `scored_script.yaml` + `audio_v6/*.mp3` (measured with ffprobe). **Out:** `duration_map_v6.json` — `TimedSegment` per segment with `audio_duration_s`, `total_duration_s`, `timeline_start_s/end_s`.
- **Failure mode:** This is THE source of truth. If audio changes, rebuild it before any visual generation — every slot length downstream reads from it.

### STAGE 2A — Render Package (plan + hero + PRE gates)
- **Command:** `python projects/therac-25-test/script_v5/build_render_package.py` (`--dry-run` plans prompts/anchors but skips paid hero clips; `--skip-narration-gate` to force past).
- **In:** `scored_script.yaml`, `asset_bible_v6.json`, `audio_v6/` (duration map + `seg_NNN.alignment.json` for chain-seam leg boundaries via `lib.word_timing.sentence_ends`). **Out:** `artifacts/shot_manifest_v6.json` (every `ShotJob`: prompt, keyframe, `duration_s`, `chain_from`, `hero`, status), HERO clips → `assets/shots/`, `narration_alignment_report.json`. Backs up a prior manifest that had `done` shots to `shot_manifest_v6_<ts>.bak.json`.
- **Gates (PRE-spend, fail-closed):**
  - **Narration alignment** (`lib.narration_gate.validate_script_alignment` + `gate`): Gemini scores each segment `match`|`partial`|`mismatch`|`error`; `gate()` exits 1 if any `mismatch`/`error`. Fix the scored script's `ai_prompt`/`description` (the report's `suggested_prompt` guides you). Override only with `--skip-narration-gate` / `SKIP_NARRATION_GATE=1`.
  - **Subject identity** (`lib.asset_bible.validate_subject_identity`, deterministic/free): blocks a prompt that calls a grounded subject by a generic term ("a beige linear accelerator" licenses Grok to redesign the Therac-25). NAME the subject. Override only with `SKIP_IDENTITY_GATE=1`.
- **Key behavior:** `--dry-run` still writes **real** prompts/anchors (only hero clip gen is skipped) — never a placeholder manifest. `plan_ai_video` is free in default-anchor mode (canonical URL anchor, no per-shot keyframe unless `AI_PER_SHOT_KEYFRAMES=1`). FLF beats (`visual.flf`) are **excluded** from this paid manifest.
- **Then:** REVIEW the hero clips in `assets/shots/` before the bulk spend.

### STAGE 2B — Bulk generation (the cloud spend)
- **Command (preview, no spend):** `python projects/therac-25-test/script_v5/run_bulk_generation.py`
  **Command (spend):** `… run_bulk_generation.py --yes` · safe first run: add `--stop-on-fail` · `--provider grok-kie` · `--no-gemini` (skip quality gate) · `--max-attempts 3`.
- **In:** `shot_manifest_v6.json`, `KIE_API_KEY`, ffmpeg/ffprobe. **Out:** `assets/ai_segments/seg_*.mp4` (per-segment concat), `artifacts/ai_visual_assets_v6.json` (complete segments only; partials → `_PARTIAL.mp4`, excluded). Manifest checkpointed after **each** shot (resume-safe). Kie balance diffed before/after = authoritative spend.
- **Gate (POST, per clip):** `lib.quality_gate` via `generate_with_quality_gate` — Layer 1 (file integrity, static-frame, duration ±0.5s) always; Layer 2 Gemini semantic (`content_match`, `artifact_free`, `visual_quality`, each **≥5/10**) when `GOOGLE_API_KEY` set and `--no-gemini` not passed. Up to 3 attempts; the rejected take's issues ride into the next attempt's prompt as corrective feedback (seed bumps too).
- **Generation rules baked in:** legs ≤6 s (`AI_GROK_LEG_CAP=6`, default-on); each chained leg anchored to the prior leg's final frame (`vr.resolve_chain_anchor` → final-frame PNG hosted via `lib.image_host`); per-leg prompts from the beat splitter (`AI_BEAT_PROMPTS=1`); `MOTION_DISCIPLINE` clause appended to every prompt (`AI_MOTION_DISCIPLINE=1`).
- **Cost:** grok-kie ≈ **$0.017/s** (~2.9 Kie credits/s; worst case ×3 if every shot retries). **Announce the estimate before `--yes`.**
- **Failure modes:** A `GenerationHardStop` (out of credits / daily limit / provider mismatch) aborts gracefully and preserves done state — fix and resume. A provider mismatch refuses to silently spend on the wrong (premium Veo/Runway) model.

#### Mixed-lane segment (FLF shots inside an otherwise-Grok segment)
FLF is **segment-granular** (one lane per segment via `visual.flf`). To mix FLF shots and Grok shots in one segment: **pre-generate the FLF shots out-of-band** with `lib.flf.flf_beat` / `vr.generate_flf_shot` into the shots' output paths, mark them `done` in the manifest, and let `concat_segment_shots` stitch them with the Grok legs.

#### FLF beats (batch)
- **Entrypoint:** `lib.flf.generate_flf_segments(script, dm, FLF_DIR, bible=…)` (idempotent; reuses existing clips). Per beat: `flf_segment` authors START via `vr._nano_image(channel_style.apply_to_prompt(start_prompt))` (grounded on a bible canonical when `anchor` is an asset_id), derives END via `drain_endpoint`, interpolates via Kling 3.0 FLF, conforms to slot.
- **Cost:** Kling FLF std ≈ **$0.084/s** (~14 Kie credits/s). **FLF beats are UNGATED — eyeball every one.**

#### Keyframe authoring (the load-bearing techniques)
- **FLF state-morph (matched-frame technique):** Author the base frame ONCE with Nano, then **PIL-composite** the changed element onto a copy (draw `X`, then draw `E` in the same box; draw the needle at rest, then pinned). Pass them as start/end to `vr.generate_flf_shot`. A generative Nano "edit" of the base to make frame 2 **FAILS** — it redraws the whole composition and adds a mouse pointer + garbled text. The deterministic darken-toward-navy author (`drain_endpoint`, with optional `band=(lo,hi)` for a vertical region) is the only sanctioned generative-free endpoint.
- **Grok screens (glow-screen rule):** Grok paints a mouse cursor + garbled legible text onto any front-facing CRT/UI regardless of "no cursor" prompts. Stage the screen as a **blown-out amber GLOW** (no readable surface) and carry literal text on a `text_overlay` card; OR author the screen content via FLF (the `X→E` morph). Never rely on Grok for a clean legible screen.
- **People shots:** Canonicals are deliberately empty rooms; `plan_ai_video` auto-routes people prompts (`_PEOPLE_RE`) through `_populated_keyframe` (Nano edit places the subject ON surfaces with correct anatomy, gated, `AI_KEYFRAME_ATTEMPTS=3`) so Grok never melts a body out of furniture (`AI_POPULATED_KEYFRAMES=1`).

### STAGE 3 — Integrated build (parse → validate → conform → sync gate → manifest)
- **Command:** `python projects/therac-25-test/script_v5/build_v6.py` (`--dry-run`; `--stage N` to restart at stage 0–5).
- **In:** `scored_script.yaml`, `ai_visual_assets_v6.json` (picks up pre-gen AI segments), `asset_bible_v6.json`, sketch diagrams, `GENERATED_MAP`/stock pool (`pexels_*.mp4`, first 50). **Out:** `asset_registry_v6.json`, `assembly_manifest_v6.json`, and **`visual_assets_v6.json` written ONLY after the STAGE 4 sync gate passes**.
- **Gates:** STAGE 1 structural (`validate_scored_script_file` schema + `validate_structure` binding — blocking). STAGE 4 pre-assembly sync (`lib.sync_validator.validate_pre_assembly`, **±0.15 s** per asset, contiguous timeline — HARD, exits 1).
- **Conform step:** every real asset is `_trim_to_duration`'d to its exact slot at `RENDER_FPS=30` CFR (freeze-pad if short — **never loop**), then `text_overlay` captions are burned over the conformed clip.
- **Failure mode to avoid:** a missing `ai_video` asset is left MISSING (not silently swapped to photoreal stock) so the sync gate surfaces it loudly. `ALLOW_AI_STOCK_FALLBACK=1` only for a throwaway draft.

### STAGE 4 — Final render + POST gate
- **Command:** `python projects/therac-25-test/script_v5/render_v6.py` (`RENDER_FPS=30`, `FINISH=1`).
- **In:** `duration_map_v6.json`, `visual_assets_v6.json`, `audio_v6/narration_v6.mp3`. **Out:** `renders/therac25_v6.mp4`; intermediates in `assets/trimmed_v6/`.
- **Steps:** conform each visual to 1920×1080 @ 30fps CFR + exact slot (freeze-pad if short, trim if over; black placeholder if missing) → stream-copy concat (re-encode fallback) → mux video + narration (`-c:a aac`, **no `-shortest`**) → channel finishing pass (`lib.finishing.apply_finish` = duotone + grain, the single uniform grade; `FINISH=0` skips for debug) → post-render sync.
- **Gate (POST, HARD):** `validate_post_render` — `video_duration >= audio_duration` else exit 1 (won't ship a desynced render); warns if `|video − expected| > 0.5s`.

### Staged chunked review + cost ledger
- **Preview lane:** `python projects/therac-25-test/script_v5/build_preview.py` (no `--yes` = cost preview + FLF beat list, then exits) → `… --yes --until seg_009` (default scope ~3 min; `--from seg_010` for the tail; `--out-name <basename>`). Anchor-refreshes keyframes to a fresh host (`IMAGE_HOST=tmpfiles`), chain-aware, fully gated, renders in-scope sketch diagrams, assembles with real narration + finishing → `renders/preview_3min.mp4`.
- **Staged-chunk driver:** `python staged_chunk.py --from seg_010 --until seg_019 --name chunk1` (no `--yes` = cost preview only) → add `--yes` to generate. It wraps `build_preview.py`, then reports the cost-ledger delta for the chunk, builds a contact sheet (`renders/<name>_contact.png`), and STOPS for review before the next chunk. (`build_preview.py --until/--from` alone also works; `staged_chunk.py` adds the cost-delta + contact-sheet + stop-for-review wrapper.)
- **Discipline:** generate in **~3-min chunks**, review the chunk (contact sheet + cost preview), THEN spend on the next chunk.
- **Cost ledger (persistent, append-only):** every paid call (grok-kie, kling-kie, nano) logs to `artifacts/cost_ledger.jsonl`. Read the running tally: `python -m lib.cost_ledger` (or `--since 2026-06-16`). **Announce paid generation cost before spending.**

### Scene-review dashboard (run the server)

A FastAPI + React app under `web/` for reviewing the cut scene-by-scene (number-badged clips, approve/reject + notes/suggestions, regenerate through the director pass, or swap in an existing clip). All commands run from the workspace root (`D:/OpenMontage2`).

- **First-time setup:** `pip install -r web/requirements.txt`, then build the UI once: `npm install --prefix web/ui && npm run build --prefix web/ui`. (Without the build, the server still works — it serves the no-build fallback in `web/frontend/`.)
- **Start the server:** `uvicorn web.backend.app:app --port 8011` → open **`http://localhost:8011`**. It defaults to the `therac-25-test` project and reads the live artifacts, so a browser refresh always shows current state.
- **UI dev (hot reload):** run the API as above, and in another terminal `npm run dev --prefix web/ui` (Vite on `:5173`, proxies `/api` → `:8011`).
- **Optional env:** `GOOGLE_API_KEY` enables the real director pass; `KIE_API_KEY` enables paid regenerate/dispatch; `OPENMONTAGE_DISABLE_DISPATCH=1` is a hard kill-switch for paid regeneration. `GET /api/health` reports what's wired.
- **RUN IT YOURSELF in a terminal.** DON'T rely on an editor/agent-managed preview server — those get reaped between turns and the dashboard vanishes. If the port is busy, use `--port 8012`.

## 4. RULES & ANTI-PATTERNS (one-line DO/DON'T)

- **Follow the narration verb.** DO find the verb in each line and stage it as a subject physically acting + a camera move. DON'T leave nothing in frame acting — that beat gets rejected.
- **Ken Burns ban.** DON'T use a bare "slow push"/"slow drift"/fade over a still image as motion. WHY: the director rejected it twice; an FLF drain reads the same — it's a fade, not action.
- **Keyframe carries content.** DO put the error screen / person-on-table / 3 folders IN the keyframe (Nano, anchored to the bible canonical). DON'T expect the prompt to make Grok invent specific content — the generator only adds MOTION.
- **Grok screen trap.** DON'T rely on Grok for a clean legible CRT/UI — it paints a cursor + garbled text. DO stage it as a blown-out amber glow + carry text on an overlay card, OR author it via FLF.
- **Extension-500.** DON'T request a single Grok clip >6 s — the internal extension call 500s on the full style prompt. DO chain ≤6 s legs (`AI_GROK_LEG_CAP=6`, default-on) anchored to the prior leg's final frame.
- **FLF-drain-is-a-fade.** DON'T default to FLF drain or place two quiet drains back-to-back. DO reserve it for rare "going cold/dark" full-stops; a precise change is Lane B, not a drain.
- **Nano-edit-drifts-use-PIL.** DON'T make FLF frame 2 by a generative Nano edit of frame 1 — it redraws everything + adds a pointer/garbled text. DO PIL-composite the single changed element onto a copy (or `drain_endpoint`) for a pixel-matched pair.
- **Gates are fail-closed.** DON'T pass `--skip-narration-gate`/`SKIP_IDENTITY_GATE=1`/`ALLOW_AI_STOCK_FALLBACK=1` for a real render — fix the scored script instead. WHY: misalignment is cheap to fix pre-spend, expensive after the bulk batch.
- **Duration Map is law.** DON'T hand-edit `visual_assets_v6.json` or change audio without rebuilding the duration map. WHY: every slot reads from it; stale = silent desync caught only at the hard post-render gate.
- **Chunk before you spend.** DON'T fire the full bulk batch unreviewed. DO `build_preview.py --until …`, review, then proceed; announce cost and check `python -m lib.cost_ledger`.

## 5. PER-BEAT AUTHORING CHECKLIST (fill in before generation)

For each segment, fill this in (in the scored-script comment or a planning note) before any paid call:

```
seg_id: ____________   act: ________
narration: "________________________________________________"
  narration VERB (the on-screen action): ____________________

LANE  (circle one, with the reason):
  [ ] MANIM/sketch   — because the beat teaches a mechanism/count   → visual.type=manim_animation, template=______
  [ ] FLF state-morph — because ONE element changes to state ______ → visual.flf{start_prompt, transition, anchor=______}
  [ ] FLF drain (RARE) — because the beat goes cold/dark            → visual.flf{drain≈0.8, band=____}
  [ ] GROK i2v        — because a subject ACTS + camera moves        → visual.type=ai_video

KEYFRAME PLAN (content lives HERE, not the prompt):
  anchor asset_id: __________   support_asset_refs: [__________]
  content present in frame: ___________________________________
  screen handling (if any): [ ] amber GLOW + overlay card  [ ] FLF morph   (never raw Grok screen)
  people in shot? [ ] yes → populated keyframe (auto)   [ ] no
  FLF pair authored by: [ ] PIL-composite of the one element   [ ] drain_endpoint(band=____)

MOTION = the narration verb:  ai_motion="____________________"  (blank → Scene Library default)

DURATION → LEGS:
  slot (from duration_map) = ______ s
  Grok legs = ceil(slot / 6) = ______   |  continuous-cam (orbit/dolly)? cap 10s
  chain seams snap to sentence-ends (alignment present? [ ] yes)

GATE EXPECTATIONS:
  PRE: narration-alignment verdict expected = [ ] match  [ ] partial   |  subject NAMED in prompt? [ ] yes
  POST: Gemini ≥5/10 on content_match / artifact_free / visual_quality  (FLF = UNGATED, eyeball it)

EST COST:
  Grok i2v:  ____ s × $0.017/s = $______
  FLF (Kling std): ____ s × $0.084/s = $______
  keyframes: ____ imgs × $0.04 = $______
  → beat total ≈ $______   (announce before --yes; log lands in cost_ledger.jsonl)
```

---

**Key file paths (all absolute):** `D:/OpenMontage2/projects/therac-25-test/script_v5/{build_asset_bible,generate_voice_v6,build_render_package,run_bulk_generation,build_v6,render_v6,build_preview}.py` · `D:/OpenMontage2/lib/{visual_router,flf,duration_map,narration_gate,quality_gate,asset_bible,cost_ledger,channel_style,sketch_diagrams,text_overlay,finishing,word_timing,beat_splitter}.py` · artifacts under `D:/OpenMontage2/projects/therac-25-test/artifacts/`.

**Note on one verified discrepancy from the subsystem maps:** the maps claim `build_v6.py` instantiates `QualityGate` but never calls `evaluate()` (inert at STAGE 3) and that the narration/identity gates are "not implemented." That is true *for `build_v6.py`* — but the real PRE-spend gating lives in `build_render_package.py` (`narration_gate.validate_script_alignment`/`gate` + `asset_bible.validate_subject_identity`, both present and wired), and the POST quality gate runs inside `run_bulk_generation.py`/`generate_shot` via `generate_with_quality_gate`. Author against the gates where they actually fire (Stages 2A/2B), not Stage 3.