# Therac-25 Documentary — Exact Production Workflow

**Workspace:** `D:/OpenMontage2` (branch `v6-baseline`) · **Project:** `projects/therac-25-test/` · **Style:** hand-inked graphic novel, deep navy `#0a1428` + amber (`CHANNEL_STYLE=graphic-novel-disaster`).

This is the prescriptive recipe to produce a narrated documentary reliably, **from a blank topic to the final render**: deep research → vetted brief → scored script → real reference photos → entity sheets → stills + animatics → video → machine QC. Follow it top to bottom. Every command is run from the workspace root (`D:/OpenMontage2`). Do not improvise lanes, do not skip gates, do not run a Grok clip longer than 6 s. The trial-and-error this episode suffered is encoded below as RULES — obey them.

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
                  │
 STAGE 5   python -m lib.render_qc <pid> ─► machine QC report
              borders / freeze tails / holds / stray-shot flashes / seam-jumps /
              double-cuts / silence gaps / duration — run BEFORE every operator
              watch-through; per-take mode (--clip take.mp4 --slot s) BEFORE
              every promotion. Advisory by default; --strict to gate.

 [Preview lane] build_preview.py --yes --until seg_009  → renders/preview_3min.mp4
                (scoped ~3-min chunk; generate → review → spend on next chunk)

 [Stage -1]  python -m lib.entity_census <pid>  — BEFORE the bible/any generation:
             extract the story's recurring people/places/things from the script and
             build an approved reference sheet per entity (real photos as ground
             truth). Every identity defect this episode traced back to skipping this.

 [Stage -3]  DEEP RESEARCH → research/research_brief.json (Claude-assisted; schema
             schemas/artifacts/research_brief.schema.json). GATE:
             python -m lib.research_vetter <brief.json>  (deterministic lint +
             adversarial Gemini vet + claims map). "If you don't find it and cite
             it, it won't be in the video."

 [Stage -2]  SCRIPT → script_*/scored_script.yaml (Claude writes narration from the
             brief + the project's story_formula.md, emitting the Scored Script
             directly — there is no generator tool). GATES: script_validator
             (schema) + python -m lib.script_lint <yaml> (craft rules) +
             python -m lib.script_review <yaml> (fact/style/flow panel vs brief).

 [Stage -2b] REAL REFERENCE PHOTOS → assets/_reference/ via lib/image_search
             (whole-web search; curated repos are blind to press photos). These
             are the ground truth the census sheets get vetted AGAINST.
```

## 2. THE LANE DECISION TREE (decide this FIRST, per beat)

Every beat picks **exactly one** lane by **what the beat must DO**. Ask, in order:

```
Is the beat teaching a MECHANISM / labeled diagram / a precise count taught?
   └─ YES → MANIM / SKETCH lane  (visual.type=manim_animation; lib/sketch_diagrams.py)

Else: does ONE element change to a SPECIFIC new state the viewer must SEE change
      (glyph X→E, error code appears, needle to a reading, 6→3 figures)?
   └─ YES → FLF STATE-MORPH  (visual.flf; two pixel-matched keyframes; Kling interpolates)

Else: does the ENVIRONMENT change state at scale under a locked camera, with a
      precise WHERE (and where-NOT) the viewer must read (overflow starts over the
      far wall but the near walkway stays dry; a stain spreads; a room floods)?
   └─ YES → FLF ENVIRONMENT MORPH  (Lane B2 below — composite endpoint; do NOT
            burn Grok attempts first: Grok drifts the where/where-not contract)

Else: is this a rare "going cold / going dark" emotional FULL-STOP?
   └─ YES → FLF DRAIN  (visual.flf with a high drain; end = start darkened toward navy)
            ⚠ reads as a fade — use SPARINGLY, never two quiet beats back-to-back.

Else (a SUBJECT physically acts + a camera move stages the narration verb):
   └─→ GROK i2v  (visual.type=ai_video)  ◄── the default for the kept first 3 minutes
```

**ESCALATION RULE (operator directive, 2026-07-14, taum-sauk act 3):** if a beat fails
Grok **twice** (timeout, reversed physics, broken where/where-not), **stop iterating
Grok.** Re-route immediately: environment/state change → FLF with a composite endpoint
(Lane B/B2); an element to add/remove on a still → `lib.gemini_image` edit; counted
choreography → Omni Flash. Judge lanes on **$/LANDED-take, not $/attempt** — one $0.70
FLF that lands beats four $0.10 Groks that don't (plus an operator review round each).
The automatic push fallback is **banned** while narration continues — it is Ken Burns.

**Lane A — MANIM/SKETCH** — *technical mechanism, a count taught, a labeled schematic.* The diagram is authored, not interpolated, so labels and counts are exact. Route via `SKETCH_SCENE_MAP` / `SKETCH_SEGMENT_OVERRIDE` in `build_v6.py`; falls back to `lib/diagram_codegen.generate_diagram_scene`, then legacy `MANIM_ASSETS`.
- *Examples this episode:* `seg` with template `race_condition` (the concurrency bug schematic); `radiation_therapy`→`linac` (how the beam path works); `byte_overflow` (the counter rolling over). **Never** ask Grok to "show how the interlock works" — it cannot draw a correct labeled mechanism.

**Lane B — FLF STATE-MORPH** — *a precise content change you must watch happen.* Author two keyframes differing only in the one element; Kling 3.0 FLF interpolates between them (`vr.generate_flf_shot`). Counts, per-object state, locked camera, and object permanence hold **by construction**. This is what FLF is FOR.
- *Examples this episode:* the dose glyph `X→E` appearing on the console (carry the literal change in the keyframes, not in a Grok screen); a dial needle moving to a specific reading; "6 overdosed" figures where 3 then go dark (band drain — see Lane C mechanism, used here for a *count reveal*, `band=(0.50,0.62)` so the front 3 drain while the back 3 stay lit).

**Lane B2 — FLF ENVIRONMENT MORPH** — *a large environment state-change under a locked
camera with a where/where-NOT contract.* Proven on taum-sauk seg_010 b3 (2026-07-14): "water
flows over the far northwest wall; the near walkway — where the crew walks next shot — stays
dry." Landed FIRST TRY after two Grok timeouts and an operator-rejected texture-scroll. The recipe:
1. **START = an approved still of the composition.** If subjects must be absent, remove them
   with `lib.gemini_image` — removal/big-lever edits are reliable; small-lever edits
   (waterline nudges) regress (5 recorded failures).
2. **END = deterministic composite, never a generative edit.** Splice ONLY the changing
   element's pixels from another approved SAME-CAMERA still: HSV water mask (warm-white foam
   needs `V>140 & S<110`, not the blue-leaning mask), region polygon, geometric exclusion
   lines for the must-stay-dry zones, then **recolor by source luminance to the base's grade**
   (`140+112L / 150+103L / 163+90L` killed the warm cast) so the splice reads as water under
   the base's light.
3. **Pre-crop BOTH frames to 16:9** — Kling ignores `aspect_ratio` and honors the keyframe AR.
4. `lib.flf.flf_beat(start, prompt, span, out, derive=<copy of the composite>)` — the prompt
   describes the transition AND names what must not change ("near walkway stays dry and
   empty; the wall never moves or morphs").
- *Why not the alternatives:* Grok reverses flow direction / breaks the where-not contract on
  water; np.roll texture-scroll of a still reads as **flashing** (operator rejected 3 shots);
  a generative end-frame breaks the FLF pixel match.

**Lane C — FLF DRAIN** — *rare "going cold/dark" punctuation.* End frame = start blended toward navy `#0a1428` (`lib.flf.drain_endpoint`, `drain≈0.8`). **This reads as a fade (Ken Burns).** Use it once in a great while, never as a default, never on two consecutive quiet beats.
- *Examples this episode:* the seg_009 "removed safety fuse goes cold" (uniform drain); the final powered-down room settling to dead navy. If you find yourself reaching for it twice in a row, one of those beats wants Lane B or D instead.

**Lane E — OMNI FLASH (explicit only, $0.10/s)** — *counted events / precise multi-phase
choreography, and surgical EDITS of a near-approved clip.* First-party Google Interactions
API (`tools/video/omni_flash_video.py`, provider `omni-flash`); ≤10s clips, 720p, native
synced audio. Evaluated 2026-07-10: "fires EXACTLY TWICE" landed FIRST TRY (brightness-curve
verified) after Grok failed the same counted beat 4/4 and Veo 3× — judge this lane on
**$/LANDED-take**, not $/attempt. Sequential edits revise the prior clip surgically
(region-diff verified: only the named element changed) — the dashboard's **omni edit**
button on a take. RULES, all paid-for:
- **⚠ NO DRY RUNS — every call generates a FULL video and bills it.** A throwaway probe
  prompt produced a default-length 10s clip ($1.00). ALWAYS state a duration in the prompt.
- **⚠ Safety gate: no human reactions.** "He convulses" — blocked; even "flinches at each
  activation" — blocked. Keep people passive in Omni prompts; stage the reaction in
  another lane.
- **Timing drift on edits:** a burst moved 0.6s in eval — re-verify word-timed beats
  after any edit (the normal take QC applies).
- **Never auto-routed.** `supports` flags are all False; reachable only by explicit
  provider or the omni edit button.
- **KIE-OUTAGE PRIMARY LANE (proven taum acts 4–5, 2026-07-14→17).** When the KIE host is
  down, BOTH Grok and Kling 500/timeout for hours — Grok ran 0-for-~40 across a multi-day
  window. Omni is a different provider (Google) and stayed up throughout, landing ~14/14
  real takes including figure locomotion and drone moves. **Protocol:** probe KIE ONCE
  (one real Grok call); if it fails, route the WHOLE batch's paid beats to Omni instead of
  paying a 2-strike + retry-loop tax on every beat. Judge on $/landed-take: Omni's $0.10/s
  beats $0.10/attempt-that-never-lands.
- **STYLE-LOCK on establishing/landscape shots.** Omni drifts toward PHOTOREALISM on wide
  natural scenes (a valley drift went photographic mid-clip). Every Omni prompt for a
  stylized film MUST hard-lock the medium: *"FLAT 2D hand-drawn comic-book illustration,
  bold black outlines, halftone dot shading, <palette> duotone, the WHOLE time — do NOT make
  it photorealistic, do NOT make it a 3D render, do NOT add realistic lighting."* Verify the
  END frame matches the START's medium, not just the START.
- **Locomotion passes the safety gate; disaster IMAGES may not.** "Rescuers walk carefully
  carrying the children" landed (careful movement ≠ a reaction). But some aftermath IMAGES
  are input-blocked regardless of prompt — neutralize the wording ("a quiet winter morning
  by the water") or route that beat deterministic.

**Lane D — GROK i2v** — *gross / ambient motion that stages the narration verb.* Grok renders gross subject + camera motion well (walking, slamming, sweeping, a crane-back reveal, atmospheric drift). This is the workhorse — the kept seg_001–seg_009 are 100% Grok.
- *Examples this episode:* the dose needle "driven violently across the dial and slams the stop"; eyes "snap open"; code "scrolls and races" up a CRT (stage the CRT as a blown-out amber glow — see the Grok screen trap). Find the verb in the narration line and stage it as a SUBJECT physically acting + a camera move.

## 3. PHASE-BY-PHASE STEPS

### STAGE -3 — Deep research → the research brief
- **How:** Claude-assisted deep research on the topic, assembled into
  `projects/{pid}/research/research_brief.json` per `schemas/artifacts/research_brief.schema.json`
  (sources with citations, claims, timeline, people, the primary source deeply extracted).
  There is no generator tool — the brief is authored, then GATED.
- **Gate:** `python -m lib.research_vetter projects/{pid}/research/research_brief.json` —
  deterministic lint (counts, citations, source diversity, page-cite discipline for primary
  claims) + adversarial Gemini vet ("what would a deep extraction of the named primary source
  contain that is ABSENT?") + claims map for downstream fact-vetting.
- **Failure modes:** the Therac v1 brief shipped hand-assembled with the primary paper never
  deeply extracted — the gaps surfaced MONTHS later as fact-vetting false positives and missing
  story beats. Vet the brief at stage -3, where fixing it costs nothing. "If you don't find it
  and cite it, it won't be in the video."

### STAGE -2 — Script → scored_script.yaml
- **How:** Claude writes the narration from the vetted brief + the project's
  `story_formula.md` (the act formula; therac used the 5-act "Technology Disasters" shape),
  emitting the **Scored Script YAML directly** — narration + per-beat `visual` specs routed by
  the §2 lane tree + `silence_after_s` (default **1**; 2-3s only at act breaks).
- **Gates (all three, in order):** `lib.script_validator` (schema/structure) →
  `python -m lib.script_lint <yaml>` (deterministic craft rules — encodes
  `docs/research/Tech Disaster Documentary Scriptwriting.md`) →
  `python -m lib.script_review <yaml> --panel fact,style,flow` (adversarial panel; fact vetting
  runs AGAINST the research brief; exits 1 on any critical fact issue, fail-closed).
- **Then the operator reads it.** Narration edits after TTS force re-TTS + timeline shifts
  (the seg_016 doubled line survived to a watch-through) — catch script problems here.

### STAGE -2b — Real reference photos (ground truth for identity)
- **How:** `lib/image_search` (Google Programmable Search, whole-web — curated open-license
  repos are blind to press photos of specific real objects) → curate the real photos of every
  story-critical entity into `assets/_reference/`. These are what the census sheets get vetted
  AGAINST on the dashboard's Sheets page.
- **Failure modes:** skipping this is how the wrong machine shipped into 11 scenes — the sheet
  drifted because nothing anchored it to reality. Sheets are approved against REAL photos,
  by a human, before any generation.

### STAGE -1 — Entity census (see §6)
- `python -m lib.entity_census <pid>` — recurring people/places/THINGS from the scored script,
  gap-checked against the bible; every `needs_sheet` entity gets a reference sheet built and
  operator-vetted (dashboard Sheets page) BEFORE the bible or any paid generation.

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

### Stills-first workflow (dashboard v2, 2026-07-10)

The dashboard drives a NEW video in this order — each step is a gate before spend:

1. **Import**: the research→script flow (Claude-assisted: `research_brief.json` vetted by
   `lib/research_vetter`, narration per `story_formula.md`, QC via `script_lint`/
   `script_review`) delivers `scored_script.yaml` into `projects/{pid}/script_*/`. TTS runs
   via `generate_voice_v6.py` (CLI).
2. **Sheets page**: run the census (`POST /entities/census`), then vet every `needs_sheet`
   entity — its generated sheet renders SIDE BY SIDE with the real reference photos;
   approve (binds the exact sheet file to the entity) or reject with a hint. People,
   places, AND things — the machine was the identity that burned us, not a character.
3. **Stills per scene**: author keyframes (existing flow, ~$0.04/still), note/re-roll each,
   then build the **storyboard shot** (`POST /scenes/{sid}/animatic`) — narration + the
   planned stills cut on sentence starts from the word alignment ($0, `lib/animatic.py`).
   Watch it in the scene page, then **Approve stills** — this OPENS the video gate for
   that scene (`approve-and-dispatch` 409s while a scene with an animatic is unapproved;
   legacy scenes that never entered the stills flow pass through).
4. **Episode animatic** (`POST /projects/{pid}/animatic`): the whole story as narrated
   stills — the cheapest point to catch pacing/story problems. Watch before the first
   paid clip.
5. **Video**: the existing dispatch → takes → verdict flow, per scene, unchanged.

Stage state is derived from the append-only feedback log (event types
`sheet_approved/rejected`, `still_note`, `stills_approved/unapproved`, `animatic_built`) —
`GET /projects/{pid}/stages` renders the per-scene strip.

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
- **Two-strike Grok escalation.** DON'T iterate Grok past two failures on the same beat. DO re-route: FLF composite endpoint (Lane B/B2) for state changes, `lib.gemini_image` for still edits, Omni Flash for counted choreography. WHY: operator directive (2026-07-14) — $/landed-take, not $/attempt; every failed round costs a review cycle too.
- **No texture-scroll water.** DON'T animate water by wrap-scrolling a still's own texture (two-layer `np.roll` + periodic crossfade) — the period reset reads as FLASHING/gleam; the operator rejected 3 such shots in one review. DO stage flowing water as Lane B2 or a Grok verb-beat.
- **Sketch holds must boil.** DON'T let a sketch/manim scene hold a static frame — matplotlib's `path.sketch` wobble is deterministic per path, so a hold renders pixel-identical (a dead still). DO nudge path geometry every ⅓s (`_boil()` in taum_sketch.py), ride typewriter text with a blinking caret, and gate with the freeze-scan: no ≥3s stretch under 0.35 mean delta at 1fps.
- **Pixel delta is not motion.** DON'T certify a beat as animated because frames differ — a slow zoom on a still (+ grain) generates healthy deltas and sailed through the freeze scan twice (taum act-4; the operator's eyes caught it, the metric didn't). DO gate with `lib.frame_hygiene.zoom_still()` (zoom-compensated residual): if a centered 1.00–1.16x zoom+shift of the early frame explains away the "motion", it's a Ken Burns artifact. Wired into beat_exec's reuse guard.
- **Duration match is not identity.** DON'T let a resume/reuse guard accept a beat file on span-match alone — crashed fallbacks leave span-matching artifacts that impersonate finished takes. DO require the motion check on reuse, and after ANY batch crash purge fallback artifacts from a **grep of the logs** (every fallback line), never from memory — the two artifacts that shipped were created by a later run than the purge list covered.
- **Deterministic shimmer is not "animated" footage.** DON'T ship a subtle deterministic build (lake shimmer, glow-breath, a blinking beacon on a held frame — mean 1fps motion ≈ 0.1–3) on an ESTABLISHING / HERO / LANDSCAPE / CLOSING shot. The operator read every one of these as "just a still / too static / just blinking lights" (taum notes at 1:02, 1:16, 2:16, 1:51-end). WHY: low-amplitude local motion doesn't sell a wide/quiet shot — those need **real camera motion (drone push/drift) or strong water/subject motion** from Grok or Omni. Deterministic motion is correct ONLY for: hand-inked cards/diagrams (the boil + typewriter), close quiet INSERTS (a CRT flicker, a gauge needle), and as SECONDARY life layered on a beat that already carries primary motion. When in doubt on a footage beat, spend the paid take.
- **Motion floor gate (dead-still detector).** DON'T certify a footage beat as animated on the freeze/zoom scan alone — a beat can clear freeze (not frozen) and clear zoom_still (not a push) yet still be essentially a still. DO run `lib.frame_hygiene.motion_floor(clip)` → (frame_mean, span_delta, reads_dead). It flags dead ONLY when BOTH consecutive-frame delta (< 0.4) AND start→end span delta (< 1.5) are low — the span check rescues slow-but-real motion (a wall sagging scores frame≈0.2 but span≈5). Calibrated on taum: recalibrating from frame-only (flagged 42/82, mostly accepted) to the dual metric dropped it to 3 genuinely-dead beats. Cards, diagrams, and quiet inserts are exempt (declare them). This is the third leg with `zoom_still` (fake motion) and `freeze_tail` (dead tail). It is a COARSE aid: a LONG hero/establishing shot with tiny-but-nonzero motion can still read static (role/duration is a human call — the shimmer rule above).
- **Omni is the KIE-outage lane.** When Grok AND Kling 500 for hours (same host), Omni Flash (Google) is a working third lane: careful figure LOCOMOTION ("walk carefully carrying the children") passes its safety gate first-try — it blocks *reactions*, not walking/carrying. Some disaster-aftermath IMAGES are input-blocked regardless of prompt — those beats go deterministic.

### Polish-pass rules (codified from the therac-25 rounds 1-4, 2026-07)

Four operator watch-throughs kept surfacing the SAME defect classes. Run the machine QC
before EVERY operator review, and follow these craft rules when building/fixing scenes:

- **Machine QC before human QC.** After every render: `python -m lib.render_qc <pid>`
  (borders / freeze tails / mid-scene holds / stray-shot flashes / seam-jumps / silence gaps /
  duration vs manifest, timeline from the assembly manifest). Before promoting any take:
  `python -m lib.render_qc --clip <take.mp4> --slot <slot_s>`. Intentional panel borders
  live in `assets/ai_segments/_gold_refs/qc_keep_borders.json` (SCENE-relative windows).
- **Source-still hygiene.** A border/frame baked into ONE keyframe rides into every
  derivation (master frame → cascades → Veo keyframes → beat cuts: ~20 scenes on therac).
  Deborder/inspect a still BEFORE it becomes a gold ref, master, or FLF anchor
  (`lib/frame_hygiene.find_border_box`).
- **Sheets: bind entity→sheet EXPLICITLY, and mark props.** The census over-flags (document
  props like emails/offsets/gauges get `needs_sheet`), and fuzzy filename matching
  mis-binds entities that share a token (every "Toops X" grabbed one sheet). Author
  `artifacts/sheet_map.json`: `{"<entity>": {"sheet": "loc_x.png", "ref_dirs": [...],
  "role": "sheet"|"prop"|"covered"}}`. role prop/covered drops the entity from the sheets
  gate (handled by the diagram/deterministic lane or another sheet). The Sheets page reads
  it before fuzzy matching. Ground each generated sheet on the curated REAL photos
  (`lib/image_search` → `assets/_reference/<dir>/`), NB2 Lite ~$0.034/sheet.
- **Wobbling borders: overscan, never run-split.** Hand-drawn borders shift frame to
  frame; detect-and-crop flickers on playback. Cure = uniform overscan crop of the
  ORIGINAL (`frame_hygiene.overscan_vf`, 4.5-5.5%/edge) — never overscan an already
  part-cropped output (double-crop = zoom jumps).
- **Match cut by construction.** When generating a leg that continues an existing shot,
  the keyframe MUST be the literal boundary frame of the kept material (extract it, host
  it, anchor on it). Never prompt "same scene" and hope — that's how the 00:52 jump
  happened. Style-boundary joins get a ~0.5s xfade.
- **Replace to cut boundaries.** When restyling/replacing a sub-window of a take, extend
  the replacement to the take's own CUT points, not to arbitrary times — a mid-shot style
  or continuity pop reads instantly (the 6:11 painterly-Cox note).
- **Old takes carry stray frames.** Before reusing any window of an old take, flash-scan
  it (`frame_hygiene.flash_frames`) — two takes shipped 1-2-frame fragments of other
  scenes at splice points (male operator @1.8, vintage room @9.2).
- **No freeze tails.** A take shorter than its slot must get DELIBERATE motion to the
  slot end: slow the real motion (ffmpeg setpts — note `-t` is OUTPUT duration on slowmo),
  add a word-timed closer card, or an FLF exit. Never let conform freeze-pad visible body
  content while narration continues.
- **Scene-change silences: 1s.** `silence_after_s: 2`+ reads as dead air on playback
  (three separate operator notes). Default new scripts to 1; reserve 2-3s for act breaks.
  ⚠ silence files cache POSITIONALLY (`audio_v6/silence/`) — clear them when changing
  any `silence_after_s`.
- **TTS keys: probe the production request shape.** A scoped ElevenLabs key 401s on
  `/v1/user` while TTS works; tier-gating 403s only on the exact
  endpoint+output_format combo (192kbps needs Creator). Health-check with the real
  request, read the 403 JSON body. The voice builder now FAILS CLOSED if any segment
  mp3 is missing at concat time.
- **Promote by HUMAN approval only.** The takes index's "accepted" is the auto-gate's
  verdict; promoting latest-accepted ships wrong takes (019 t10 vs approved t12). The
  dashboard's "active clip" pick is ALSO the auto verdict — never promote from it blindly.
  Promote via the dashboard's per-take "Promote to canonical" button
  (`POST .../takes/{take}/promote` — human-keyed, backs up the old canonical, logs
  `take_promoted` so the board always shows which take is canonical).
- **Render + machine QC from the dashboard.** The Render tab runs build → render → QC as a
  job and renders the findings table — each finding seeks the render player to its
  timestamp. The full loop (script → sheets → stills → animatic → video → render → QC)
  needs no terminal beyond TTS.
- **Cut-invention on short close-up beats: deterministic push, not another Grok roll.**
  Grok invents a mid-clip cut to a phantom scene on extreme-close-up beats ≤~3s (4/4
  attempts on one beat; the settle clause does NOT cure it). Cure: a deterministic zoompan
  push-in on the APPROVED keyframe, concat with the paid beats. This is the one sanctioned
  exception to the Ken Burns ban — the ban forbids a bare drift as a beat's MAIN staging;
  a push on approved pixels as a cut-invention CURE is craft.
- **Excerpt diagrams from --raw renders only.** sketch_diagrams renders pre-graded by
  default; excerpting one into an assembly grades it AGAIN at finishing (visibly dim).
  `python -m lib.sketch_diagrams <template> <dur> <out> --raw` and cut from the `_raw`
  file — the timeline finishing pass is the single grade.
- **Chain-seam repair: dissolve a FREEZE, and never blend a timeline with itself.** To
  hide a redraw seam, dissolve a 0.5s freeze of the pre-seam frame into the moving
  post-seam content. An xfade whose second input re-reads the same timeline blends the
  seam WITH ITSELF — a no-op that looks right on sparse frame checks (shipped once;
  the seam-jump detector now catches it).
- **Crop boxes don't transfer between resolutions.** A border box detected on a
  NORMALIZED 1080p part does NOT apply to the raw beat clip — rescale the coordinates
  to the raw frame first (a 1080p box on a 1280x720 beat is an ffmpeg 'Invalid
  argument' at best, a mis-crop at worst).

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

## 6. PER-PROJECT CONSISTENCY SETUP (the universal recipe)

The pipeline CODE is project-agnostic; whether a NEW documentary's recurring machine /
character / room stays on-model is decided by per-project DATA authored to this recipe.
Everything below was proven on the Therac-25 (the C-arm drift fight) — do it ONCE per
project, per recurring entity, BEFORE bulk generation:

-1. **ENTITY CENSUS — run this before anything else, including the bible.**
   `python -m lib.entity_census <pid>` reads the scored script, uses Gemini to list every
   person/place/thing/organization that repeats or carries the story (importance 1-5,
   mention counts, segments), cross-checks the bible, and flags `** BUILD SHEET **` gaps →
   `artifacts/entity_census.json`. Every flagged entity gets a reference sheet grounded on
   REAL research photos and **operator-approved BEFORE segment one is generated**. The
   census checks sheet EXISTENCE, not fidelity — approval against the real photos is the
   fidelity gate. Lesson (2026-07-08): the Therac-25 itself — importance 5, on screen in
   26 of 36 segments — ran the whole episode with a sheet that had drifted from the real
   photos and a room canonical showing a DIFFERENT machine; eleven shots shipped wrong and
   were caught only in the episode watch-through. The title character must never be the
   last entity to get a faithful sheet.

   **Image edit/gen lane for sheets + masters: `lib.gemini_image` (first-party Google,
   refs as INLINE BYTES).** The KIE nano queue 500s intermittently, and its edit mode
   fetches refs from hosted URLs — a silent fetch failure degrades the task to unanchored
   text-to-image that re-imagines the scene. `python -m lib.gemini_image "<prompt>" out.png
   ref1.png ref2.jpg` (~$0.039/image, ledger-logged); `_nano_image` now auto-falls-back to
   it when KIE fails (`NANO_FALLBACK_GOOGLE=0` to disable).

0. **Shop the asset library BEFORE generating anything.** Every approved frame/clip is a
   paid, eyeball-passed artifact — reuse is $0 and on-model BY CONSTRUCTION; regeneration
   is a paid dice-roll. `python -m lib.asset_library index <pid>` catalogs gold plates,
   takes + per-beat clips, authored keyframe stills, Veo one-offs, and bible assets into
   `artifacts/asset_library.json`; `… find <pid> "<regex>" [--approved-only]` searches it.
   An approved still can be a beat's KEYFRAME directly (copy over
   `_keyframe_review/{sid}__{rid}/b{idx}/keyframe.png`, back up first) — narrative echoes
   ("same machine", "same hospital") should reuse the literal approved frame; deterministic
   derivations (last-frame extraction, `lib.flf.drain_endpoint`, stat cards, PIL composites)
   come before any paid lane. Works across projects: everything is keyed by project id
   (`--projects-dir` / `$OPENMONTAGE_PROJECTS_DIR` for other roots).

1. **Real photos on disk.** Curate 2-4 real photographs per key entity under
   `assets/_reference/` and list them in the bible's `reference_images`. Stored URLs
   expire (tmpfiles/CDNs die in hours) — the pipeline re-hosts from LOCAL files at use
   time, so the local file is the truth.
2. **Identity tokens = a police description, derived from the photos.** Run
   `python -m lib.identity_tokens <bible.json> <asset_id> [--apply]` — it produces the
   standard shape: silhouette clause, 2-4 distinctive PARTS with shape adjectives,
   surface/era, and **"NOT a <confusable>" negatives** (image models drift to the
   nearest look-alike in their prior; naming it is what stops the drift — "NOT a C-arm,
   NOT a CT donut"). Curate, don't just accept.
3. **A model sheet per recurring entity** (multi-view, in the house style) with the
   LOCAL path in `reference_sheet`. The figure drifts without one exactly like the
   machine does (Cox drifted until `subj_cox` existed).
4. **One location asset per real room** with a LOCAL `canonical_reference_image` —
   including secondary rooms (the control room / terminal). Per-beat locale grounding
   (`_beat_locale`) routes console beats to the terminal asset automatically, but only
   if the asset exists.
5. **Gold plates for hard scenes**: approved stills under
   `assets/ai_segments/_gold_refs/{sid}_b*.png` + a `{sid}.beats.json` sidecar keying
   each plate to its beat by CONTENT regex (ordinal keying misgrounds when the director
   re-plans the beat count).
6. **Hard shots → the Veo reference lane** (`hard_shot: true` on the visual spec, or
   `AI_VEO_HARD_SHOTS=1` + the scored detector): identity refs (sheet + photo + beat
   keyframe) pin the design by construction, ~$0.32/8s clip.
7. **Keep improving the tokens from operator signals.** Every dashboard still re-roll
   logs its pose/design hint in the feedback event log (`keyframes_authored` →
   `reroll.hint`). A hint that keeps repeating ("rounded head", "hospital gown") is an
   attribute MISSING from the tokens — promote it via `lib/identity_tokens`.
8. **Beat weights come from the AUDIO, not guesses.** The director-pass automatically
   receives NARRATION TIMING (real sentence spans from `assets/audio_v6/{sid}.alignment.json`
   via `scenes.sentence_spans`) and must mirror them — each action lands on its own line;
   a trailing non-action span (statistics, reflection) extends the LAST beat as a
   settle/hold. Sanity-check the drafted weights against the spans before approving.
9. **Counted events are HARD SHOTS.** A beat whose events are counted ("fires EXACTLY
   twice") or a continuous multi-phase arc must be `"hard_shot": true` (director emits it;
   operators can set it on a beat edit/regen): it renders as ONE Veo reference generation
   (KIE: 8s only, conformed by SPEED-FIT — never tail-trim, the climax lives at the end).
   Grok leg-chaining re-reads the prompt per leg and re-stages the events (2 fires became 4).
10. **Settle/hold beats end scenes safely.** Phrase the motion literally ("...then holds
   there, nearly still — no new events"): continuation legs then use a SETTLE clause instead
   of narration-derived prompts, so a stats tail can't spawn invented action (a second door,
   once). Chained-leg seams get an automatic ~4-frame crossfade (`AI_CHAIN_SEAM_BLEND`).

---

**Key file paths (all absolute):** `D:/OpenMontage2/projects/therac-25-test/script_v5/{build_asset_bible,generate_voice_v6,build_render_package,run_bulk_generation,build_v6,render_v6,build_preview}.py` · `D:/OpenMontage2/lib/{visual_router,flf,duration_map,narration_gate,quality_gate,asset_bible,cost_ledger,channel_style,sketch_diagrams,text_overlay,finishing,word_timing,beat_splitter,identity_tokens}.py` · artifacts under `D:/OpenMontage2/projects/therac-25-test/artifacts/`.

**Note on one verified discrepancy from the subsystem maps:** the maps claim `build_v6.py` instantiates `QualityGate` but never calls `evaluate()` (inert at STAGE 3) and that the narration/identity gates are "not implemented." That is true *for `build_v6.py`* — but the real PRE-spend gating lives in `build_render_package.py` (`narration_gate.validate_script_alignment`/`gate` + `asset_bible.validate_subject_identity`, both present and wired), and the POST quality gate runs inside `run_bulk_generation.py`/`generate_shot` via `generate_with_quality_gate`. Author against the gates where they actually fire (Stages 2A/2B), not Stage 3.