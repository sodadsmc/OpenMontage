# Session Handoff — scenes 18, 23–26 SHIPPED (27 pending verdict); reuse library live

**Date:** 2026-07-07 · **Branch:** `v6-baseline` (keep it pushed — origin is the backup) ·
**Project:** `projects/therac-25-test`

> Read the auto-loaded memories first — especially `reuse-before-generate`,
> `openmontage-consistency-toolkit`, `temp-host-urls-expire`, `feedback-event-whitelist`,
> `amber-graphic-novel-house-style`, `stat-card-diagram-splice-technique`. Prior handoffs:
> `git log --follow SESSION_HANDOFF.md`.

## STATE: episode board

- **seg_018 ✅ APPROVED (take 2, ~$3.50)** — the Malfunction-54 accident scene, 52s, 8 beats:
  console beats grounded on seg_019/021 stills, X→E FLF morph, beam+jolt Veo hard_shot,
  M54 FLF, finger-to-P cliffhanger on the seg_019 console still. Ceiling raised to $2.50
  (operator sign-off) for one dispatch. Lesson: a "failed" Veo poll timeout still charged
  ($0.32, ledger entry 7 min late) — budget hard shots at ~3× sticker, check the ledger.
- **seg_023 ✅ APPROVED (take 7, $0.93)** — $0 stat card + Grok beats + $0 deterministic
  drain (PIL blend toward NAVY from beat 2's last frame) spliced via `use-clip`. Lessons:
  approved-still-vs-prompt contradictions fail the montage gate (re-roll the still first);
  settle-clause motion ("then holds there, nearly still — no new events") stops leg-2
  reinvention; b1 fixed by copying the approved seg_022 still directly as the keyframe.
- **seg_024 ✅ APPROVED (take 3, $0.68)** — first keyframes-reviewed-before-video scene (now
  the default: author stills → operator eyeballs → dispatch).
- **seg_025 ✅ APPROVED (take 2, $1.62)** — Hager sheet debut. `subj_hager` reference sheet
  built for $0.04 (Nano multi-view edit from approved seg_021 b3 keyframe) →
  `assets/asset_bible/subj_hager_reference_sheet.png`, registered in `asset_bible_v6.json`
  (forward-slash paths — backslash escapes get mangled). Split dispatch ($0.70 + $0.52) to
  stay under ceiling: placeholder-lane the deferred beats, then regen — COPY
  `_keyframe_review/{sid}__{old_rid}` → new rid first or preauthored stills are lost.
  Person-leak fix: person-free `seg_025_screen.png` plate inserted FIRST in the sidecar.
- **seg_026 ✅ APPROVED (take 2, $0)** — `race_condition` sketch diagram rewritten word-timed
  to narration (character-level times in `assets/audio_v6/{sid}.alignment.json`); in-place
  X-RAY→ELECTRON crossfade on two boxes, third stays X-RAY ("it didn't update").
- **seg_027 ⏳ take 1 registered, VERDICT PENDING** — `beam_fires` diagram rewritten the same
  way ($0): safeguards struck-through to 5.7, beam fires 5.9, dose-chamber gauge (climb →
  pegged → collapses to near-zero after saturation 11.9), closing line 15.4 (footer fades to
  yield the bottom strip). Committed `47e7cda`. On approve: log verdict + note, declutter.
- **NEXT after 27:** seg_028+ (check the board); Katie sheets (seg_001-003); episode re-time
  of remaining old-slot scenes. Hager returns in seg_031/034 — his sheet now exists.

## TODAY'S FIXES (all committed + pushed except where noted)

- `ea1b049` director.py: `max_output_tokens` 8192→16384 — thinking tokens truncated
  big-scene JSON (`_source:"fallback"` + `_error:"Unterminated string"` is the tell).
- `06ae16a` takes.py reroll: failed URL→disk mirror now FAILS the job (was silently
  succeeding with the stale still).
- `46ee73c` `lib/asset_library.py` — cross-project reuse catalog. **Shop it BEFORE any
  generation** (PRODUCTION_WORKFLOW §6 step 0):
  `python -m lib.asset_library {index,find,list} <pid> [query] [--approved-only]`.
  Approved stills can be copied straight over `_keyframe_review/.../keyframe.png` (backup
  first) — dispatch reuses the local file.
- `73d6748` image_host.py: content-addressed premiumize names — same-named `keyframe.png`
  uploads got renamed server-side, name+size verify failed, refs silently dropped →
  unanchored Grok tasks failed twice. Root-caused only because the server now logs to disk.
- `47e7cda` sketch_diagrams.py: beam_fires rewrite (word-timed + dose-chamber gauge).
  race_condition rewrite committed earlier in the day.

## OPERATING PATTERNS (proven today)

- **Launch the dashboard with log redirect** (three failures vanished with the console
  before this): `-RedirectStandardOutput logs/dashboard_8011.out.log -RedirectStandardError
  logs/dashboard_8011.err.log` on the detached uvicorn launch.
- **Reuse before generate:** approved stills as direct keyframes; $0 derivations
  (last-frame extraction, PIL drains, stat cards, PIL patches from CLEAN source regions).
- **Chained classifier is unreliable both ways** → operator-override by appending a
  `revision_drafted` event with `chained` flipped.
- **Ceiling raises:** `OPENMONTAGE_REGEN_MAX_USD` is read from the server process env —
  relaunch the server with the var set, with operator sign-off.
- **Diagram lane:** `python -m lib.sketch_diagrams <template> <duration> <out>`; word timing
  from `scenes.sentence_spans` + the alignment JSON; frame-grid QC at key beats before
  registering via `use-clip`.
- Temp-host URLs die in hours — never store-and-reuse; local file is truth, re-host at use.

## THE PIPELINE (generic; see docs/PRODUCTION_WORKFLOW.md §6)

**Grounding kit (every mixed beat):** per-beat locale (`_beat_locale`), content-keyed gold
plates (`_gold_refs/{sid}.beats.json` regex sidecar, FIRST match wins), machine sheet+tokens
when the beat shows the machine, figure sheets by beat text (montage) / narration (chained).
**Chain-carry (chained only):** beat N's keyframe seeds N+1, locale boundaries reset, gate
OFF (operator eyeballs); montage keeps the gate ON, never inherits frames.

**Lanes per beat:** Grok (~$0.102/6s leg) · Veo `hard_shot` (8s-only REFERENCE_2_VIDEO,
$0.32 sticker — budget 3×, speed-fit conform) · Kling FLF morphs/drains (~$0.084/s, legible
text composited deterministically) · manim/sketch diagrams ($0) · $0 deterministic cards +
splices via `use-clip`.

**Dashboard flow:** director-pass (steering note) → check draft (weights vs spans,
hard_shot, settle wording, beat count — typo micro-actions are ONE beat) → author keyframes
($0.04/still) → operator reviews stills → re-roll with pose hints (hint REPLACES beat
prompt) → approve-and-dispatch (ceiling check) → per-beat regen for the one wrong beat →
verdict. One job per scene at a time.

## Costs & guards
Announce before ANY generation; small batches; the operator's eyeball is the bar (gates
advisory). `projects/` is GITIGNORED (plates/sidecars/bible/takes live on disk only).
Never `git gc`/`-delete` under `.git/`. Event types whitelisted (`feedback._EVENT_TYPES`).
Repo on exFAT D:; push v6-baseline after each work block — origin is the only backup.

## How to run
Dashboard: `python -m uvicorn web.backend.app:app --port 8011` DETACHED with the log
redirect above (no auto-reload — restart after backend edits; 127.0.0.1; rebuild UI:
`npm run build --prefix web/ui`). System python:
`C:\Users\Soda\AppData\Local\Programs\Python\Python312\python.exe`. Drive via API:
director-pass → author-keyframes → keyframes/{idx}/reroll → approve-and-dispatch →
takes/{n}/regen-beats → use-clip → verdict; poll `/jobs/{id}`.
