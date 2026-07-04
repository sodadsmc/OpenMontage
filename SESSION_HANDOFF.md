# Session Handoff — seg_019 SHIPPED (take 12); the consistency pipeline is now generic

**Date:** 2026-07-04 · **Branch:** `v6-baseline` (pushed) · **Project:** `projects/therac-25-test`

> Previous handoff (printing-press/machine-drift): `git show 218cbd1:SESSION_HANDOFF.md`.
> Read the auto-loaded memories — especially `openmontage-consistency-toolkit`,
> `temp-host-urls-expire`, `feedback-event-whitelist`.

## TL;DR

**seg_019 is done** — take 12 is the keeper (operator: "we finally got it"), pending only the
Approve verdict click. Machine identity drift is SOLVED (per-beat location grounding + content-keyed
gold plates + the Veo reference lane). Every fix from four operator review rounds is now GENERIC
pipeline behavior + documented process (`docs/PRODUCTION_WORKFLOW.md` §6 is the per-project recipe).
**Next scene: seg_020** — a 7.3s stats hammer ("Twenty-five thousand rads. One second. One
centimeter."), which is a LEGIBLE-TEXT beat (stat-card/FLF/overlay territory), NOT chained action.

## What shipped this stretch (all committed + pushed, `6327bce..ad47cf9`)

1. **Keyframe-preview reuse made real** (`6327bce`, `0151002`): approved stills are reused from
   durable LOCAL files (stored temp-host URLs die in hours — never trust them); failed beats can't
   masquerade as authored; authored sets render under their revision card (zero clicks, zero spend);
   the preview button reuses the displayed draft (re-click of an authored rid is FREE — idempotent).
2. **Veo reference lane** (`c20eb4a`, `031c327`, `2cff550`): `tools/video/veo_ref_kie_video.py` on
   KIE's DEDICATED endpoints (`POST /api/v1/veo/generate` → poll `/api/v1/veo/record-info`).
   **REFERENCE_2_VIDEO is 8s-ONLY** (docs say 4/6/8 — reality 500s anything but 8). Clips conform by
   **SPEED-FIT, never tail-trim** (Veo paces the arc across all 8s; trimming amputated the climax
   once). Every veo prompt carries the no-dialogue clause (v1 had Cox SAY "stop please it burns").
   ~$0.32/clip, 3 refs (beat still + machine sheet + character sheet), all re-hosted from local.
3. **Per-beat location grounding + content-keyed plates** (`cbd29f2`, `79a2e3f`): each beat
   classifies terminal-vs-room from its own text and grounds on ITS locale's bible asset; the chain
   resets at the room boundary; machine sheet/tokens/real-photo ride room beats only; console beats
   get the operator-identity clause. Gold plates are per-beat, keyed by CONTENT regex
   (`_gold_refs/{sid}.beats.json`) because re-plans renumber beats.
4. **Per-still re-roll** (`a8444ea`): "↻ re-roll this still" on every grid cell (~$0.04) — the hint
   box doubles as cost-confirm AND pose language; hints are logged as drift telemetry
   (`keyframes_authored → reroll.hint`) for token promotion.
5. **Universal consistency tooling** (`09f4a99`, `1d2edc8`):
   - `lib/identity_tokens.py` — derive police-description identity tokens (incl. "NOT a <confusable>")
     from an asset's REAL photos: `python -m lib.identity_tokens <bible.json> <asset_id> [--apply]`.
   - `lib/reference_judge.py` — reference-anchored vision review. **gemini-2.5-pro ONLY** (flash
     ranked the C-arm frame FIRST on the labeled set — that's why the old fidelity gate hallucinated).
     Ranking → gold-plate nomination (`python -m lib.reference_judge nominate <pid> <sid> --apply`);
     token-derived checklist → advisory "⚠ mismatch" badges on the stills grid. `selftest` = the
     labeled regression harness; must PASS before trusting any model/prompt change. Never blocking.
6. **Motion truth in the pipeline** (`4923600`, `694696f`, `ad47cf9`):
   - Chained-leg seams crossfade ~4 frames (`AI_CHAIN_SEAM_BLEND=0.12`) — hard cuts on
     near-identical frames read as a hitch.
   - **`"hard_shot": true` on a BEAT** routes it through the Veo lane — required for COUNTED events
     (leg-chaining re-stages events: 2 narrated fires rendered as 4).
   - Settle/hold beats ("holds there, nearly still — no new events" in the motion) use a SETTLE
     continuation for legs 2+ — narration-derived leg prompts over a stats tail invented a second door.
   - Motion-only regens REUSE the approved still + keep the motion field.
7. **Narration-locked beat weights** (this commit): director-pass automatically receives NARRATION
   TIMING (real sentence spans via `scenes.sentence_spans` from the ElevenLabs alignment) and must
   mirror them; trailing stats extend the last beat as its settle/hold.

## seg_019 ledger (dashboard)
- **Take 12 = THE ONE**: locked-camera press-P (0–1.8s) → Veo two-fire arc, each fire on its line
  (1.8–7.4s) → pound ON the door line, slump + hold through the stats (7.4–22.1s). Needs the
  operator's Approve verdict.
- Takes 6–11 = the iteration trail (each fixed one operator note); takes 1–5 = pre-rebuild history.
- Approved stills: rid `738eba26581b` (b1 console, b2 lying/firing, b3 door) + plates
  `_gold_refs/seg_019_b1..b4.png` + `seg_019.beats.json` (from take-3 hand frames).

## THE DASHBOARD WORKFLOW (per scene, going forward)

0. **Once per project/entity**: recipe in `docs/PRODUCTION_WORKFLOW.md` §6 (real photos → derived
   tokens → sheets → location assets incl. secondary rooms → plates+sidecar for hard scenes).
1. **Open the scene** → read narration + AUTO-GATE. Decide the lane family first: legible
   text/stats → stat-card/FLF/overlay (see seg_012 splice); physical action → chained grok
   (+ hard_shot beats); mechanism → manim placeholder.
2. **Fix in pipeline** (with a note) or bare director-pass → review the drafted beat card:
   weights should mirror the narration spans (shown to the director automatically), counted-event
   beats should carry hard_shot, settle beats should SAY the hold. Edit beats if not.
3. **🖼 Preview keyframes (no video)** (~$0.04/still) → eyeball the grid (judge badges flag design
   mismatches) → **↻ re-roll** weak stills with a pose hint (~$0.04) until the set is right.
4. **✓ approve keyframes → animate** (beat-aware cost confirm) → watch the take WITH narration.
5. Wrong beat? **Per-beat regen** from the take's beat strip — motion-only edits keep the approved
   still; pass `hard_shot: true` to re-route a beat through Veo. Repeat until the eyeball says done.
6. **Verdict: Approve.** Then next scene.

Timing note: baseline visuals across the episode were conformed to OLD slots (duration map was
rebuilt) — as each scene is approved at its true slot, the episode re-times a few scenes at a time.

## NEXT: seg_020 (then 18, 21, …)
- **seg_020**: 7.32s, "Twenty-five thousand rads. One second. One centimeter." — spans
  [0–1.6][1.6–2.7][2.7–4.3] + 3s tail. NO takes, NO revisions yet. This is a LEGIBLE-TEXT stats
  beat — the auto-gate already suggests the visual (terminal screen, the three figures appearing).
  Route: text overlay / stat-card splice (memory: seg_012 technique) or FLF content-morph —
  NOT chained grok (Grok can't render clean text). Cheap scene: likely $0.10–0.25 total.
- **seg_018** (Cox/Malfunction-54): the full chained recipe again + a REAL FLF screen-morph beat;
  curate its plates from its best frames first. **seg_003/others**: build sheets for Katie etc.

## How to run / costs / gotchas
- Dashboard: `python -m uvicorn web.backend.app:app --port 8011` DETACHED (no auto-reload —
  RESTART after backend edits; 127.0.0.1; rebuild UI after web/ui/src edits:
  `npm run build --prefix web/ui`). `.claude/launch.json` has the same config for preview tooling.
- Rates: Grok ≈$0.102/6s leg · Veo-ref $0.32/8s clip (8s ONLY) · Nano $0.04/edit · Kling FLF
  ≈$0.084/s. Announce cost before ANY generation; `OPENMONTAGE_REGEN_MAX_USD` ceiling ($1) is
  beat-aware but EXCLUDES Nano authoring (~+$0.04-0.12/authored beat).
- `projects/` is GITIGNORED — plates, sidecars, bible edits, script_v5 changes live on disk only.
- Never `git gc`/`-delete` under `.git/` (near-fatal loss once; auto-gc disabled).
- Event log types are WHITELISTED (`feedback.py _EVENT_TYPES`) — register new types; never let a
  log append fail a paid job.
- The reference judge and any vision QC: pro-tier only, reference-anchored, advisory. Run
  `python -m lib.reference_judge selftest` after any judge change.
