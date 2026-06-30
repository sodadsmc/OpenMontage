# Session Handoff — Build FLF visual cohesion for action sequences

**Date:** 2026-06-29 · **Branch:** `v6-baseline` · **Project:** `projects/therac-25-test`

> Previous handoff (scene-review regen hardening): `git show f3a0513:SESSION_HANDOFF.md`.

## TL;DR

This session hardened the **narration-following** and **first-run shot quality** of the Therac-25
documentary, then hit the next wall: **multi-beat action shots have no visual cohesion** (the figure
appears/disappears, faces backwards, teleports, the door floats with no walls). **The next session's
job is to build chained-keyframe-set + Kling-FLF cohesion** (~2–3 days). The full design is below and
in memory (`flf-cohesion-action-sequences.md`).

## What this session did (state you're inheriting)

All on `v6-baseline`, committed and (should be) pushed to `origin = github.com/sodadsmc/OpenMontage`:

1. **Recovered a near-fatal git loss.** The object store was accidentally deleted and recovered via
   `winfr /extensive` (D: is **exFAT**). `v6-baseline` is now pushed to the user's own repo as a real
   backup. Auto-gc disabled (`gc.cruftPacks=false`, `maintenance.auto=false`) — a Windows cruft-pack
   bug. **Don't run `git gc`/`repack` or `-delete` under `.git/`.** (memory: `git-object-store-safety`)
2. **De-confused the docs.** `CLAUDE.md`/`AGENT_GUIDE.md`/`PROJECT_CONTEXT.md` now point to the REAL
   pipeline (`docs/PRODUCTION_WORKFLOW.md`); the legacy `pipeline_defs/`+`skills/` system is flagged dead.
   ~108 scratch `_*.py` files moved to `scratch/` (gitignored).
3. **Depiction-first prompt rewrites** (commit history; scored_script is gitignored): 13 atmospheric
   segments rewritten to depict the narration; `narration_mode` dial set (seg_037 evocative). The
   **narration gate is caption-blind and noisy** — do NOT hard-enforce `--fail-on partial`; it's an
   advisory smell-test, the dashboard eyeball is the real bar.
4. **Machine consistency:** the real-Therac-25 reference sheet (`therac25_reference_sheet.png`) is now
   propagated to ALL machine-bearing assets in `asset_bible_v6.json` (was only on kennestone). Injected
   at generation via `visual_router.py:309-330`.
5. **First-run shot fixes — commit `c69c176`** `feat(generation): action-aware shot-list + verb-locked
   motion + full-body keyframe gate`:
   - Director decomposes narration into discrete actions (`_decompose_actions` pre-pass in
     `web/backend/director.py`) → ONE beat per action (shot list) + server-side re-ask. Verified on
     seg_019: 2 collapsed beats → **4 action beats**, each with a `motion` verb.
   - `beat["motion"]` now flows director → `takes.py` (`_beat_plan`/`_gen_beat`, the hard-coded
     `ai_motion=None` is gone) → the prompt builder (no more default Ken-Burns zoom).
   - Full-body person keyframe on every person leg + a `full_figure` keyframe gate
     (`quality_gate.py KEYFRAME_FULL_FIGURE_MIN`) + non-"waist-up" framing (`shot_prompt_builder.py`).

## THE TASK: chained-keyframe-set + FLF cohesion

**Problem.** A multi-action scene now splits into the right beats, but each beat is an INDEPENDENT
Grok i2v clip in the `mixed` lane (`takes.py:_store_mixed_take` loops beats sharing nothing). So
seg_019 (Cox rises → struck → stumbles → pounds) renders as 4 disconnected clips: Cox materializes,
faces backwards, runs out from behind the machine, pounds a wall-less door.

**Key facts (verified this session):**
- Today's FLF (`lib/flf.py`) only DERIVES the end from the start — `drain_endpoint` (darken) or
  `composite_text` (stamp a glyph). It **cannot move a body** and **does not chain**. seg_006/009 FLF
  were small same-frame morphs. So action-continuity FLF is **net-new**.
- BUT `lib/visual_router.py:generate_flf_shot` (~:523) ALREADY takes a real `start_url` AND `end_url`
  → Kling can interpolate two different authored frames. The gap is purely upstream authoring.
- Grok-chaining (`resolve_chain_anchor`, visual_router.py:~922) exists and is what's ALREADY failing —
  Grok invents the motion in the middle.

**The fix = HYBRID (not pure-FLF, not grok-chaining):** author a CONSISTENT posed keyframe SET
(same figure/room/camera, via the `asset_bible` reference sheet, each Kᵢ grounded on Kᵢ₋₁ + the sheet),
then Kling-FLF the SHORT interpolation between adjacent keyframes. Beat N's END frame IS beat N+1's
START → figure/room can't jump; the door has walls by construction.

**FLF limit:** it interpolates, not biomechanics. A big jump (lying→standing) morphs → subdivide with
an intermediate keyframe. A big cross-room translation (the stumble) may need to stay a Grok leg
seeded from a keyframe.

### Net-new to build (reuse everything else)
1. `start_image` / `end_image` params on `lib/flf.py` `flf_segment`(:201) + `flf_beat`(:165): when set,
   skip the fresh-Nano START (flf.py:226) and accept a real authored END (not drain/derive). Add the
   fields to `FLFSpec` in `lib/scored_script.py:52-82`. **(start = small; end-authoring = medium)**
2. **Director** (`web/backend/director.py:148-187`): when `described_action` is one subject in one
   setting across the actions, mark a CHAINED run and emit an ordered keyframe SET (K0..Kn, one per
   action boundary) + the FLF pair prompts, instead of N islands. Add an end-keyframe authoring option
   to the `flf` schema block (today only drain/band/morph). **(medium)**
3. **Keyframe authoring:** reuse `_populated_keyframe` (visual_router.py:~851) — author K0 from the
   room canonical + Cox reference sheet; author each Kᵢ as a Nano edit whose PRIMARY ref is Kᵢ₋₁ + the
   reference sheet, prompt = the pose delta only. Grounding each frame on its predecessor locks
   figure+room identity across the set.
4. **Dispatch** (`web/backend/takes.py:_store_mixed_take` ~554-584): thread a `prev_end_frame` across
   the loop → `_gen_beat`(:480) → `flf_segment(start_image=...)` so END(N)=START(N+1). For a Grok
   travel beat inside the run, reuse `resolve_chain_anchor` on the prior clip. **(medium)**

**Reuse (no rebuild):** `generate_flf_shot` (two endpoints), `resolve_chain_anchor` (last-frame
extract+host), `_populated_keyframe`, the reference sheet (`asset_bible.py:75-82`), the action
decomposer + mixed-beat concat scaffold.

### seg_019 test plan (the golden case)
6 keyframes, all SAME locked low-3/4 camera + room + Cox figure (amber-on-navy):
`K0 lying flat → K1 propped on elbows → K2 on his feet → K3 flinch-in-place (2nd beam) → K4 mid-lurch
toward door → K5 fist on the door (walls visible)`. 5 FLF pairs, each pair's start = prior pair's end.
P4 (K3→K4, the cross-room travel) is the risky one — try FLF with the K4 mid-point first; fall back to
a Grok leg seeded from K3 if it slides.

**Honest expectation:** structurally coherent on run 1 (no teleporting, door has walls), but plan to
re-author 1–3 keyframes (Nano identity drift — mitigated by the reference sheet) and re-roll the 1
big-motion pair. Per-beat regen already exists in the dashboard.

## How to run / test
- **Dashboard:** `web/start_dashboard.bat` (uvicorn, port 8011, its own window — survives across turns,
  NO auto-reload → **restart after any backend edit**). Use `http://127.0.0.1:8011` (NOT localhost).
- **Drive a regen via API:** POST `…/scenes/seg_019/director-pass` (no notes) → `…/revision/{rid}/
  approve-and-dispatch` → poll `…/jobs/{job_id}`. (See this session's transcript for the exact python.)
- **Dispatch is LIVE/paid** (KIE + GOOGLE keys in `.env`). seg_019 ≈ $0.40 for 4 beats; the FLF version
  adds Nano keyframes (~$0.04 each) — **announce cost before spend**. Spend ceiling
  `OPENMONTAGE_REGEN_MAX_USD` (default $1.00) in `takes.py:dispatch_take`.
- **"Needs work" is only a verdict** — it does NOT regenerate. Regenerate = director-pass → approve-and-dispatch.

## Key files
- `lib/flf.py` — FLF/Kling first-last-frame (the file to extend with start_image/end_image).
- `lib/visual_router.py` — `generate_flf_shot` (two-endpoint Kling), `resolve_chain_anchor`,
  `_populated_keyframe`, `plan_ai_video`.
- `web/backend/director.py` — `_decompose_actions`, the lane/shot-list rules, `_SCHEMA_HINT` (add end-auth).
- `web/backend/takes.py` — `_store_mixed_take` / `_gen_beat` (thread prev_end_frame), `dispatch_take`.
- `lib/scored_script.py` — `FLFSpec` (add start_image/end_image).
- `lib/asset_bible.py` — reference sheet (figure identity across keyframes).
- `docs/PRODUCTION_WORKFLOW.md` — the real pipeline recipe + lane decision tree.
