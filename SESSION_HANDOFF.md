# Session Handoff — scenes 19–22 SHIPPED; the consistency pipeline is generic; next: seg_018

**Date:** 2026-07-04 · **Branch:** `v6-baseline` (pushed) · **Project:** `projects/therac-25-test`

> Read the auto-loaded memories first — especially `openmontage-consistency-toolkit`,
> `temp-host-urls-expire`, `feedback-event-whitelist`, `amber-graphic-novel-house-style`,
> `stat-card-diagram-splice-technique`. Prior handoffs: `git show 218cbd1:SESSION_HANDOFF.md`
> (machine-drift era), `git log --follow SESSION_HANDOFF.md`.

## STATE: episode board

- **seg_019 ✅ APPROVED (take 12)** — the template chained scene: locked-camera console beat →
  Veo two-fire hard shot (each fire on its narration line) → pound + settle-hold through stats.
- **seg_020 ✅ APPROVED (take 1)** — $0 deterministic stat card ("25,000 RADS / 1 SECOND /
  1 CENTIMETER"), reveals cut on the word timings (seg_012 splice recipe).
- **seg_021 ✅ APPROVED (take 2)** — the "Same hospital. Same machine. Same operator." return:
  4 lanes in one scene (Grok echo beat / Veo Kidd hard shot / Grok Hager / Kling FLF
  "MALFUNCTION 54" morph). ~$1.43.
- **seg_022 ✅ APPROVED (take 3)** — the elegy montage: empty room light-death (Kidd), sealed-door
  light-death (Cox), Katie window hold. ~$1.18. Its drift is what motivated montage grounding.
- **NEXT: seg_018** (see bottom). Then: re-time remaining old-slot scenes; sheets for Katie
  (seg_001-003); Hager sheet (his face exists in approved seg_021 b3/take2 — curate a sheet
  from it via `lib/identity_tokens` + a Nano multi-view edit before seg_024/031/034 reuse him).

## THE PIPELINE (all generic, all committed `6327bce..aacabd2`)

**Grounding kit (every mixed beat, chained OR montage):** per-beat locale asset (terminal vs
room, `_beat_locale`), content-keyed gold plates (`_gold_refs/{sid}.beats.json` regex sidecar —
ordinal plates misground on re-plans), machine sheet+tokens+REAL photo whenever the beat's text
puts the machine on screen (`_machine_in_beat`), figure sheets by beat text, judge vet badge.
**Chain-carry (chained scenes only, >1 chainable beat):** beat N's keyframe seeds N+1, locale
boundaries reset the chain, narration-matched figure sheets, always-on room machine grounding,
semantic clip gate OFF (operator eyeballs). Montage keeps the gate ON, never inherits frames.
The console-operator prompt clause fires ONLY on terminal-locale beats.

**Lanes per beat:** Grok (default motion, ~$0.102/6s leg; seams crossfaded
`AI_CHAIN_SEAM_BLEND=0.12`) · **Veo `hard_shot: true`** (counted events / multi-phase arcs —
one 8s-ONLY REFERENCE_2_VIDEO generation on Kie, refs = beat still + machine sheet + character
sheet, conformed by SPEED-FIT never tail-trim, no-dialogue clause automatic, $0.32) · Kling FLF
(LEGIBLE state morphs — text composited deterministically; ~$0.084/s) · manim (placeholder) ·
$0 deterministic cards (PIL + ffmpeg + `use-clip`, the seg_020/012 recipe).

**Director (automatic):** receives NARRATION TIMING (real sentence spans via
`scenes.sentence_spans`) → beat weights mirror the audio; emits `hard_shot` + settle/hold
wording ("holds there, nearly still — no new events" → settle continuation legs, so stats
tails can't spawn invented action); one-frozen-moment beat prompts (transition wording causes
DIPTYCHS); chained-vs-montage via the focused classifier (`_chained_source` on the revision).

**Consistency tooling:** `lib/identity_tokens.py` (derive police-description tokens + "NOT a
<confusable>" from REAL photos; re-roll hints in the event log are the promotion signal) ·
`lib/reference_judge.py` (**gemini-2.5-pro ONLY** — flash ranked the C-arm frame FIRST; ranking
= gold-plate nomination CLI, checklist = advisory ⚠ badges; `selftest` must pass before
trusting changes) · per-project setup recipe in `docs/PRODUCTION_WORKFLOW.md` §6 (+ lessons
8-10).

## THE DASHBOARD WORKFLOW (proven on 4 scenes)

1. Scope the scene (narration + spans + gate) → pick the lane family FIRST: legible text/stats
   → $0 card / FLF morph; action → chained grok + Veo hard shots; montage/elegy → grounded
   montage beats; mechanism → manim.
2. Curate plates ($0) when continuity matters: copy approved stills / canonicals into
   `_gold_refs/{sid}_b*.png` + write `{sid}.beats.json` content regexes. Reuse APPROVED frames
   from earlier scenes for narrative echoes ("Same machine" grounded on seg_019's stills).
3. Director-pass with a steering note (beat structure, hard_shot, holds, character looks).
   Check the draft: weights vs spans, hard_shot flags, settle wording. Edit beats if needed.
4. 🖼 Preview stills (~$0.04/still; works for chained AND montage now) → eyeball + ⚠ badges →
   ↻ re-roll weak stills with pose hints (hint REPLACES the beat prompt; priors backed up as
   keyframe_rN.png; deterministic PIL healing is $0 — e.g. inset panels patched out).
5. ✓ approve → animate (beat-aware cost confirm) → watch WITH narration.
6. Per-beat regen for the one wrong beat: motion-only edits KEEP the approved still;
   `hard_shot: true` on the edit re-routes through Veo; prompts with "no text/plaques/signs"
   fight the garble trap. Repeat until the eyeball says done.
7. Verdict: Approve. ~$0.65-1.50/scene total is the observed range.

## Costs & guards
Grok $0.102/6s leg · Veo-ref $0.32 (8s only) · Nano $0.04/edit · Kling ~$0.084/s ·
Gemini/judge pennies. Ceiling `OPENMONTAGE_REGEN_MAX_USD` ($1, beat-aware, EXCLUDES Nano
authoring ~+$0.04-0.12/beat). Announce before ANY generation. Kill switch honored everywhere
incl. keyframe authoring. `projects/` is GITIGNORED (plates/sidecars/bible live on disk only).
Never `git gc`/`-delete` under `.git/`. Event types are whitelisted (`feedback._EVENT_TYPES`).

## NEXT TASK: seg_018 — the Malfunction-54 scene (the big one)

52.48s, NO takes yet, gate=partial. The full accident sequence; sentence spans:
`[0-3.5] spring/machines still treating · [3.5-10.2] Cox on the table, ninth treatment ·
[10.2-12.8] operator knows her console · [12.8-14] she's fast · [14-17.4] types X for E ·
[17.4-20.7] catches it/cursors up/fixes it · [20.7-27.4] BEAM FIRES + Cox jolted ·
[27.4-29] she can't see him · [29-33.4] monitor dead, intercom broken ·
[33.4-40.6] screen: Malfunction 54, no manual explains · [40.6-44.7] underdose reading ·
[44.7-49.6] stops happen all the time · [49.6-52.5] her finger moves to the P key.`

Suggested shape (verify with the director + operator):
- Terminal beats reuse the seg_019/021 console grounding (plates from approved b1 stills).
- **X→E typo = the textbook FLF content-morph** (box over the cursor line, start "X" end "E" —
  or two morphs: X appears, then fixed). LEGIBLE text never via Grok.
- **Beam fires + jolt = Veo hard_shot** (counted single fire; Cox on the table — reuse the
  seg_021 Kidd/seg_019 plates + Cox sheet).
- "Malfunction 54" screen = FLF morph (seg_021 b4 proved it; same terminal grounding).
- Finger to P key = the cliffhanger INTO approved seg_019 b1 — ground it on seg_019's approved
  console still for a seamless episode cut.
- Watch beat COUNT: ~6-8 beats over 52s; the director may over-split the typo micro-actions
  (catches/cursors/fixes = ONE beat with the morph). Budget: ~$1.5-2.5 → the $1 ceiling will
  block a single dispatch; either split the work (preview + per-beat regens) or raise
  `OPENMONTAGE_REGEN_MAX_USD` with the operator's say-so.

## How to run
Dashboard: `python -m uvicorn web.backend.app:app --port 8011` DETACHED (no auto-reload —
restart after backend edits; 127.0.0.1; rebuild UI: `npm run build --prefix web/ui`). System
python: `C:\Users\Soda\AppData\Local\Programs\Python\Python312\python.exe`. Drive via API:
director-pass → author-keyframes → keyframes/{idx}/reroll → approve-and-dispatch →
takes/{n}/regen-beats; poll `/jobs/{id}`.
