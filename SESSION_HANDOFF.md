# Session Handoff — EPISODE BOARD COMPLETE (36/36 approved) — next: full conform + assemble

**Date:** 2026-07-08 · **Branch:** `v6-baseline` (pushed — origin is the backup) ·
**Project:** `projects/therac-25-test`

> Read the auto-loaded memories first — especially `reuse-before-generate`,
> `openmontage-consistency-toolkit`, `stat-card-diagram-splice-technique`,
> `amber-graphic-novel-house-style`. Prior handoffs: `git log --follow SESSION_HANDOFF.md`.

## STATE: the board

**Every scene on the board has an approved take (36/36).** This session shipped positions
27–37 (seg_033 doesn't exist — cut earlier; 13 early-era scenes are approved via
`conformed_v6` directly, predating the takes system).

- **seg_027 ✅ ($0)** — beam_fires diagram retimed word-level + dose-chamber gauge (47e7cda).
- **seg_028 ✅ ($0)** — byte_overflow retimed + carry-ripple rollover (d02b2d7).
- **seg_029 ✅ ($0)** — false_safe full rewrite: zero-opener handoff from 028, 1/256,
  vanishing turntable check, SET-press coincidence chain, THE BEAM FIRED, quiet
  Yakima/Glen Dodd beat, AECL X-outs on the words (ebcd9a2).
- **seg_030 ✅ ($2.68 — the day's only paid scene)** — Chicago fuse story. Grok/Veo fought
  us (see LESSONS); final take: b1+b5 are deterministic camera moves on the APPROVED
  seg_022 machine frame, b2 arc + pull-back onto the smoking panel, b4 deterministic
  push-in on the approved breaker still.
- **seg_031 ✅ ($0)** — full-reuse assembly: 025 code CRT, 018 typing/M54/routine-hands,
  021 console, 019 press-P, 40-stops stat card, 022 machine closer.
- **seg_032 ✅ ($0)** — DEFECTIVE document stamp card, 5/14/23+ stat cards, Katie window,
  NEW `safeguard_restored` diagram quoting seg_029's X-outs lifting on "put back" (dc0edf5).
- **seg_034 ✅ ($0)** — machine light-drain on "out of service", MEMORANDUM card with
  red-oxide ink stroke, code punch, drained-black closer.
- **seg_035 ✅ ($0)** — the Leveson & Turner payoff: IEEE paper title card word-timed,
  race/overflow diagram excerpts as "the case study", THE FIRST OF MANY warning card.
- **seg_036 ✅ ($0)** — five-charge indictment recap, hard cuts on sentence starts, code
  fades to black on "at all".
- **seg_037 ✅ ($0)** — the finale: technology cuts → accelerating human cuts → CRT
  switch-off on "to fail" (collapse → burning line → phosphor dot → black).

## FIRST FULL CUT RENDERED — `renders/therac25_v6.mp4` (827.4s, 351MB, sync 3/3)

What it took (all disk-only except the schema — `projects/` is gitignored):
- **Take promotion bridge:** build_v6/render_v6 predate the takes system and read canonical
  `assets/ai_segments/{sid}.mp4`. Promoted every scene's HUMAN-approved take over the stale
  June canonicals (backups in `_canonical_backup_20260708/`). ⚠ The take index's
  "accepted" verdict is the AUTO-gate's, not the operator's — promoting latest-accepted
  blindly ships the wrong takes (019 t10 vs approved t12; 018/024 skipped entirely).
  Cross-check `feedback/events.jsonl` human notes ("APPROVED take N"). A `promote_takes`
  step keyed on human verdicts belongs in the backend eventually.
- **Diagram scenes (007/026-029) NOT promoted** — build re-renders the committed templates
  raw; the timeline finishing pass is the single grade.
- **Schema drift fixed** (committed): `flf` + `support_asset_refs` added to visual_spec.
- **Removed three stale `flf:` blocks** from scored_script.yaml (seg_034/036/037 — YAML
  backed up as `.bak_preflf_removal_20260708`): they would have routed PAID Kling
  generation over the approved $0 takes.
- **render_v6 conform now frame-CEILS each slot** (was flooring via `-t` → 36 segments
  accumulated a 0.18s shortfall and the sync gate refused to ship). Video now covers the
  narration by construction.
- build_v6/render_v6 need `.env` loaded into the process env (the dashboard loads it
  itself; the build scripts don't).

## NEXT

1. **Operator watch-through of `renders/therac25_v6.mp4`** end-to-end. Known judgment
   calls: burned `text_overlay` captions (seg_018 "MALFUNCTION 54 / DOSE: UNDERDOSE",
   seg_030 "FUSE BLOWS → BEAM STOPS", seg_031 "OPERATOR PRESSED P", seg_036
   '"Overdoses impossible"…') now sit over scenes that carry their own composited text —
   keep, trim, or drop per scene.
2. From the watch-through fix-list: episode re-time of old-slot scenes, Katie sheets
   (seg_001-003) if her early scenes read off-model next to the grounded era.
3. Music/SFX/mix — narration-only today; assess after picture lock.

## LESSONS THAT CHANGED THE PIPELINE (this session)

- **Reuse-before-generate became the dominant mode.** After seg_030, six consecutive
  scenes shipped for $0 by quoting approved pixels: beat-clip excerpts from
  `_takes_scratch`, gold plates as motion sources (deterministic zoompan/PIL moves),
  diagram excerpts, stat/document cards. The library (`python -m lib.asset_library`)
  turned every approved scene into capital.
- **Machine-identity beats: reuse first, generate only when the sheet+tokens actually
  ride.** seg_030 proved the mechanism: no bible locale → machine sheet/tokens never
  injected → Nano invents CT-shaped machines no matter how good the hint. Either add the
  locale to the bible or copy approved pixels. (Chicago-clinic locale still NOT in the
  bible — add it if that room returns.)
- **Retries bill at EVERY layer — announce 2-3× sticker, reconcile the ledger.** seg_030:
  announced ~$1.11, ledger $2.25 (12 nano edits billed for 7 requested; 3 rejected Veo
  attempts; 4 gate-rejected Grok legs). `worst_usd` bounds the estimate, not runtime.
- **Grok invents cuts on short close-up beats** (4/4 attempts on a 2.8s breaker close-up
  dissolved to a phantom scene mid-leg; settle clause did NOT cure it). Cure: deterministic
  push-in on the approved keyframe.
- **Double-grade trap:** sketch_diagrams renders are pre-graded by default; excerpting one
  into a use-clip assembly grades it AGAIN (dim). Always excerpt from `--raw` renders; only
  ever let the take preview apply the grade once.
- **Amber-on-cream vanishes under the duotone grade** — page accents (underlines, stamps)
  must be dark ink (red-oxide reads as dark ink post-grade).
- **Word-timed everything.** All diagram templates now animate on the character-level
  alignment (`assets/audio_v6/{sid}.alignment.json`); assemblies cut on word times
  (sentence_spans + word lookups). "When the narrator says words the animation should
  follow" is the house rule.
- **Deterministic $0 toolkit now covers:** stat cards, document/memo/stamp cards, light
  drains (PIL blend), freeze-extends, zoompan pushes/pans, seamless still→motion handoffs
  (reversed pull-back landing on a beat's first frame), CRT switch-off, ffmpeg fades.

## Costs & guards
This session: ~$2.68 total (ten scenes). Announce before ANY generation; hard shots at
2-3× sticker; check `artifacts/cost_ledger.jsonl` after every paid job. Ceiling
`OPENMONTAGE_REGEN_MAX_USD` ($1) reads from server env. `projects/` is GITIGNORED.
Never `git gc`/`-delete` under `.git/`.

## How to run
Dashboard: `python -m uvicorn web.backend.app:app --port 8011` DETACHED with log redirect
(`-RedirectStandardOutput logs/dashboard_8011.out.log -RedirectStandardError
logs/dashboard_8011.err.log`, hidden window — it dies with its console otherwise);
no auto-reload — restart after backend edits; 127.0.0.1. System python:
`C:\Users\Soda\AppData\Local\Programs\Python\Python312\python.exe`. Rebuild UI:
`npm run build --prefix web/ui`. API: director-pass → author-keyframes → reroll →
approve-and-dispatch → regen-beats → use-clip → verdict; poll `/jobs/{id}`.
Diagram lane: `python -m lib.sketch_diagrams <template> <dur> <out> [--raw]`.
