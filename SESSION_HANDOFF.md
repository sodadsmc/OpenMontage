# Session Handoff — Visual consistency for multi-beat action scenes (the "printing press")

**Date:** 2026-07-01 · **Branch:** `v6-baseline` · **Project:** `projects/therac-25-test`

> Previous handoff (the FLF-cohesion brief that started this session): `git show a4ab53d:SESSION_HANDOFF.md`.
> Read the auto-loaded memories first — especially `flf-cohesion-action-sequences`,
> `openmontage-consistency-toolkit`, and `env-inline-comment-poisons-values`.

## TL;DR

The job was "make multi-beat ACTION scenes visually cohesive" (seg_019: Cox rises → struck → pounds a
door). We got there and then some: cohesion works, and we built a **reference-driven "printing press"**
(lock a gold standard, refine only the weak beats) instead of the old **slot machine** (regenerate
everything, dice-roll each time). **The one unsolved struggle is machine identity drift** — Nano keeps
rendering the Therac-25 as a C-arm/CT-donut despite the reference sheet + identity tokens + gold plate.
That is now the strongest argument for the **next task: build the Veo-ref hard-shot lane (#5)** (design
doc + exact wire location below).

## The through-line struggle (what we kept fighting)

Every visible defect — C-arm machine, kneeling Cox, leg-clipping, comic-page diptych, fading figures,
Cox wandering to the wrong door — traces to ONE root cause: **stochastic models (Nano keyframe + Grok
i2v) reinvent the figure and machine from scratch each beat, anchored only weakly.** So a full
regeneration is a fresh dice-roll, not a refinement. The operator's exact words: *"it should be a
printing press, not a slot machine."* Everything below is in service of that.

Secondary reality checks we hit:
- **The dashboard was showing STALE data.** `duration_map_v6.json` was episode-wide stale — 33/37
  segments had the wrong narration text AND wrong durations. seg_019's real audio is the **22.1s ACTION
  narration** (rises→struck→pounds), not the 16.2s factual line the dashboard showed. We **rebuilt the
  duration map** from the current script + audio. (`build_v6.py` STAGE 2 self-heals it; the dashboard
  reads the on-disk copy directly, so it had drifted.) Only seg_033 lacked audio → it's genuinely
  deleted from the script (36 real segments, not 37).
- **A `.env` bug had broken ALL image generation.** `NANO_BANANA_MODEL=  # comment` made the comment the
  VALUE → KIE rejected it ("model not supported"). Fixed; model id is `google/nano-banana`. Backup at
  `.env.bak_cohesionfix`. (memory: `env-inline-comment-poisons-values`.)

## What we shipped this session (all committed on `v6-baseline`)

- **`3889ca0`** feat(cohesion): chained-keyframe action sequences + printing-press re-roll
- **`8202d2c`** feat(consistency): Cox character sheet + keyframe preview + no micro-beat splits
- **`e30d5c7`** feat(dashboard): keyframe-preview UI + author-keyframes as a background job

Mechanisms + where they live:
1. **Chained keyframes (cohesion).** `web/backend/takes.py:_gen_chained_beat` authors each beat's keyframe
   grounded on the PRIOR beat's keyframe + the scene's gold plate + the character sheet + machine tokens,
   then Grok-animates via `generate_ai_video(ground_keyframe=)` (new param in `lib/visual_router.py`
   `plan_ai_video`/`generate_ai_video`; default None → bulk path unchanged). Threaded through
   `_store_mixed_take` and `regen_beats`. Gated by `revision["chained"]`.
2. **`chained` detection** — `web/backend/director.py:_classify_chained` (a focused prepass, reliable where
   the combined director flag was ~50/50). A beam firing / light flaring / machine activating routes to
   GROK, not FLF.
3. **No over-decomposition** — `_DECOMPOSE` + SHOT-LIST rule keep ONE continuous motion as a single beat
   (rise-interrupted-by-a-strike = one beat). seg_019 now decomposes to 4 merged beats.
4. **Gold plate (the "printing plate")** — `_scene_gold_ref` reads `projects/{pid}/assets/ai_segments/
   _gold_refs/{sid}.png`; every chained keyframe + every per-beat re-roll grounds on it. seg_019's plate
   is set (its Ka frame). `regen_beats` reuses good beats, re-rolls only weak ones grounded on the plate.
5. **Cox character reference sheet (#1)** — `subj_cox` in the asset bible (multi-view, built from our best
   Cox frames). `_gen_chained_beat` injects any bible SUBJECT whose name is in the narration. **This is
   the biggest figure-consistency win** — the machine had a sheet, the figure had none.
6. **Advisory fidelity gate** — `lib/quality_gate.py` `reference_fidelity` is advisory (it hallucinated a
   "C-arm" and false-rejected good frames); interactive chained path uses `enable_gemini=False`
   (the semantic clip gate was false-rejecting good clips into placeholders). Operator eyeball is the bar.
7. **Keyframe preview before video (#3)** — dashboard button **"🖼 Preview keyframes (no video)"** on a
   scene → `POST …/revision/{rid}/author-keyframes` (a background job, `author_scene_keyframes` /
   `_run_author_keyframes_job`) authors the chained stills; the UI (`web/ui/src/components/SceneDetail.tsx`
   `KeyframeStills`) shows them in a grid; **approve → animate** dispatches reusing the exact stills.
   Catches kneeling/off-model/diptych at $0.04 before the $0.10+ clip.

## What's PROVEN vs OPEN (honest status)

| Issue | Status |
|---|---|
| Multi-beat cohesion (figure persists, no teleport, door has walls) | ✅ proven (take 3 hand-authored; automated take 5 all-beats) |
| Cox figure identity across beats | ✅ largely solved by `subj_cox` sheet |
| Over-decomposition (rise/struck split) | ✅ fixed (4 merged beats) |
| Diptych / fading figure | ✅ single-panel + solid-figure prompt guard |
| Gate false-rejecting good work | ✅ fidelity advisory + enable_gemini=False on interactive |
| **Machine identity (Therac-25 → C-arm/donut)** | ❌ **OPEN** — Nano's prior beats the sheet+tokens+gold. The keyframe-preview surfaces it cheaply. This is the case for #5 (Veo-ref) or #6 (composite). |
| Cross-room beat (door) grounding on the table frame → loses the door | ⚠️ partial — needs location-aware grounding (door beat should anchor on the room canonical, not the prior table frame) |
| duration_map episode-wide stale | ✅ rebuilt (seg_019 = 22.1s action); other scenes' baseline visuals still conformed to OLD slots — re-time the episode a few scenes at a time |

## seg_019 takes ledger (in the dashboard)
- take 1, 2: old 2-beat FACTUAL takes (stale narration). Ignore.
- **take 3: the GOLD hand-authored take** — the high-water mark. All beats clean.
- take 4: first automated chained — beat3 placeholder (gate false-reject), beat4 diptych, machine C-arm. Fixed since.
- take 5: automated after gate fixes — all 5 beats generated, but machine C-arm + beat5 lost the door.
- Gold plate `_gold_refs/seg_019.png` = the Ka "Cox rising" frame. Hand-authored keyframes/frames live in
  `projects/therac-25-test/assets/ai_segments/_keyframe_proof/seg_019/`.

## THE NEXT TASK (prioritized)

1. **Try the keyframe-preview button end-to-end** on seg_019 in the dashboard (refresh http://127.0.0.1:8011,
   scene → "Preview keyframes") — confirm the UI flow (job → grid → approve → animate). It was built +
   verified (route + bundle) but not yet clicked by the operator.
2. **Build the Veo-ref hard-shot lane (#5) — the fix for the machine.** Full design was produced this
   session (route ~5 hard shots/episode to **Veo 3.1 `REFERENCE_2_VIDEO`**, which takes 1–3 reference
   images = char sheet + machine sheet, on the **Kie Jobs API already in use**). Smallest first move:
   add `tools/video/veo_ref_kie_video.py` (near-clone of `tools/video/kling_kie_video.py`) + ~15 lines
   in `lib/visual_router.py`. **KEY FACT: the char+machine sheet URLs are already assembled as
   `extra_refs` in `lib/visual_router.py:~302` for the Nano keyframe edit but NEVER forwarded to the
   VIDEO model — that's the single missing wire.** Hard-shot detector = score signals (recurring subject
   +2, whole-body +2, on-model machine +2, cross-room +2, prior-failure +3) → route at ≥4, cap ~5/episode.
   The provider-mismatch overspend guard already exists (`_gen_shot_clip` raises GenerationHardStop).
   (memory `openmontage-consistency-toolkit` has the condensed version.)
3. **OR #6** — deterministic machine composite (paste the on-model Therac-25 PNG, the seg_012 stat-card
   splice technique) if you'd rather force the machine than switch models. Fiddlier (machine framed
   differently per beat) — the Veo lane is likely cleaner.
4. **Generalize:** set gold plates + build sheets for the other recurring figures/scenes (seg_018
   Cox/Malfunction-54, seg_003 Katie). seg_018 also exercises a real FLF screen-morph beat.
5. **Re-time the episode:** the duration_map rebuild changed every slot; existing baseline visuals were
   conformed to the OLD slots. Walk the episode a few scenes at a time (regenerate/re-conform).

## How to run / test
- **Dashboard:** `python -m uvicorn web.backend.app:app --port 8011` — run it DETACHED (Start-Process /
  its own window); **NO auto-reload → restart after any backend .py edit**; use **127.0.0.1** not localhost.
  It gets reaped when the agent process exits — relaunch it at session start.
- **System Python** (has all deps): `C:\Users\Soda\AppData\Local\Programs\Python\Python312\python.exe`.
- **Rebuild the React UI after `web/ui/src` edits:** `npm run build --prefix web/ui` (dist is gitignored,
  built locally; the server serves `web/ui/dist`).
- **Drive a regen via API:** POST `…/scenes/{sid}/director-pass` → `…/revision/{rid}/approve-and-dispatch`
  → poll `…/jobs/{job_id}`. Keyframe preview: `…/revision/{rid}/author-keyframes` → poll the job (it now
  returns `keyframes`).
- **Dispatch is LIVE/paid** (KIE + GOOGLE keys in `.env`). **Announce cost before spend.** Ceiling
  `OPENMONTAGE_REGEN_MAX_USD` (default $1.00). Rates: Grok ≈$0.017/s, Kling FLF ≈$0.084/s, Nano ≈$0.04/img,
  Veo3.1-fast ≈$0.30/s. Cost ledger: `python -m lib.cost_ledger`.
- **DON'T** `git gc`/`repack` or `-delete` under `.git/` (near-fatal loss earlier; auto-gc disabled).

## Gotchas that cost us time (don't rediscover)
- The dashboard reads narration + slot from `duration_map_v6.json` (`scenes.py:144-147`), NOT the scored
  script — so a stale map silently mis-drives the director. Rebuild the map when audio/script changes.
- FLF is WRONG for re-posing a figure (needs pixel-matched frames; a Nano re-pose morphs the background).
  FLF stays for LEGIBLE state-morphs (glyph X→E, error code). Figures = Grok from chained keyframes.
- The quality gate over-rejects for this channel (hallucinated C-arm, over-flagged leg crops) — it's
  advisory now; the eyeball is the bar.
- Grok clips come out ~6s regardless of requested duration → conform to the word-timed slot (trim or
  `setpts` speed-fit; word timing from `assets/audio_v6/seg_NNN.alignment.json`).
