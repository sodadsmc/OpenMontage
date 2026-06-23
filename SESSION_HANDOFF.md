# Session Handoff — Scene-Review Dashboard: regen flow hardening

**Date:** 2026-06-21 · **Branch:** `v6-baseline` · **Project worked on:** `projects/therac-25-test`

> For the earlier **depiction-first migration** + the dashboard's original design, see the previous handoff: `git show 0abb1b8:SESSION_HANDOFF.md`.

This session was almost entirely about the **scene-review dashboard** (`web/`, FastAPI + React/Vite, runs on `:8011` via `web/start_dashboard.bat`) and its **AI-video regeneration flow**. We took it from "regenerate one shot" to a full operator-driven, vetted, multi-beat editor — fixing a long string of bugs along the way. The clip being iterated is the Therac-25 documentary in the amber graphic-novel house style.

---

## Current state

**Commits this session (newest first):**
- `3f3fc46` feat(web): editable beat plan, per-beat regen, and the Gemini vet feedback loop
- `d90a67f` feat(web): mixed multi-beat generation + beat indicator, reliable job poll
- `321e5f1` feat(generation): make per-shot gate re-roll cap env-controllable (AI_SHOT_ATTEMPTS)
- `66e949f` feat(web): style-aware + spend-safe regeneration, take delete, graded previews
- `ea71bec` fix(web): dispatch_take 500 — return referenced undefined 'lane'

**Uncommitted (mine — NOT yet committed):** `web/backend/{takes,app,feedback}.py`, `web/ui/src/components/{SceneDetail,BeatFixer}.tsx`. These hold: the **"↺ start fresh" scene reset**, the **beat-fixer prefill/fallback fix**, and the **FLF `drain: null` crash fix** (all below). Worth committing as one chunk.

**Uncommitted (PRE-EXISTING, NOT mine — leave alone):** `diagram.png`, `lib/visual_router.py` (a leg-cap WIP: `AI_GROK_LEG_CAP`/`AI_CONTINUOUS_LEG_CAP`). Never bundle these. NOTE: my one committed `visual_router.py` change (the `AI_SHOT_ATTEMPTS` line in `321e5f1`) was isolated from the WIP via a checkout-dance; the WIP still sits uncommitted on top.

**Server:** runs detached via `web/start_dashboard.bat` (survives across turns). Restart after any backend change. Use `http://127.0.0.1:8011` (NOT `localhost` — uvicorn binds IPv4-only).

**Spend:** ~$10.63 of Grok/Kling regen this session (81 calls); a meaningful slice was wasted on the bugs below (runaways + crashes that spent then failed). Dispatch is LIVE (real KIE key). Guards now cap it.

---

## Issues hit — cause → fix

### 1. `dispatch_take` 500 (NameError) — masked a paid background job  *(fixed, `ea71bec`)*
"Fix failed: …/approve-and-dispatch → 500." `dispatch_take` queued the job, then `return {… "lane": lane}` — but `lane` is only defined inside `_run_take_job`. NameError fired AFTER `_POOL.submit`, so the HTTP call 500'd **while a paid generation ran in the background** (masked as failure). Fix: `"lane": revision.get("lane")`.

### 2. Regenerated takes were off-style  *(fixed, `66e949f`)*
take2/take3 of seg_009 came out bright/colorful/digital, not the amber house style. Three causes in the regen path:
- `takes.py` hardcoded `ai_style=None` → per-segment **mood** never reached the prompt builder.
- the director's `revised_prompt` had no style tags and the director was never shown the channel look.
- **biggest:** regen called `generate_ai_video(bible=None)` → no **canonical-reference anchor**; the bulk pipeline anchors every shot on the amber-graded canonicals in `assets/asset_bible/`.
Fixes: surface `ai_style` on the scene (`scenes.py`) + pass into the spec; make the director style-aware (`director.py`: channel look + mood + hard rules); anchor regen on the asset bible (`_load_bible_asset` → pass `bible`/`asset`).

### 3. "generator returned no clip" after the anchor fix — stale canonical URL  *(fixed, `66e949f`)*
The bible's `canonical_image_url` was an **expired tmpfiles.org link (HTTP 404)** (those last ~1h). Fix: `_refresh_canonical_url` re-hosts the LOCAL canonical to a fresh durable URL via `lib.image_host` (prefers premiumize when keyed). (A "non-fatal fallback" I added here was later **removed** — see #5.)

### 4. "Black + amber" — clips didn't match the strict look  *(fixed, `66e949f`)*
A 5-agent codebase investigation found the strict two-tone look comes from the **FINISHING GRADE** (`lib/finishing.py` / `channel_style.finish_filter` — desaturate→duotone-curves→grain), which only runs **once at final assembly**, never on per-clip takes. So **the dashboard was showing RAW, ungraded clips**. Also: shadows map to navy `#0a1428`, never pure black. Fix: **graded previews** — each take goes through `apply_finish` into a `*.graded.mp4` and the dashboard serves THAT (raw kept for assembly, no double-grade). Backfilled existing takes. Floor kept navy.

### 5. Spend runaway — $1.33 / 13 Grok calls on one scene  *(fixed, `66e949f` + `321e5f1`)*
A gate-FAILING mixed scene fanned out: `generate_shot` retries the gate **3× per leg** (was hardcoded) × ~3 legs = 9 calls, all failing — then my non-fatal fallback ran a SECOND full generation (4 more). Had to **kill the server** (the `OPENMONTAGE_DISABLE_DISPATCH` kill-switch only checks at job *start*). Fixes: removed the fallback; made the per-shot cap env-controllable (`AI_SHOT_ATTEMPTS`); added a **spend ceiling** (`OPENMONTAGE_REGEN_MAX_USD`, default $1.00) in `dispatch_take`.

### 6. Cost felt too high / scenes over-blocked  *(fixed)*
A ~30s scene blocked at worst-case $1.02 > $1.00. First fix — **single-pass (`AI_SHOT_ATTEMPTS=1`)** — was a **FALSE ECONOMY**: one gate-failing leg then fails the WHOLE take after paying for the good legs (seg_014: $0.51 spent, no clip). Reverted to **2 (one re-roll)** and changed the ceiling to block on **EXPECTED** cost (legs × one pass), not worst-case. Also fixed the cost model: **FLF is one Kling interpolation (≤15s), not 6s "legs."**

### 7. "Doesn't show up" — finished takes never displayed  *(fixed, `d90a67f`)*
Two frontend bugs: (a) the job poll **gave up after 6 min** but jobs take 8–14 min → finished take never refreshed; (b) partial/mixed takes are `needs_review`, not auto-promoted. Fix (`SceneDetail.pollJob`): poll up to ~15 min + auto-show the new take (even partial) when it lands.

### 8. "mixed" lane only made a PARTIAL take  *(fixed, `d90a67f`)*
A mixed scene generated only the *primary* beat → a partial take that didn't auto-promote (hit on seg_009/012/014). Fix: **full mixed-beat generation** — the director emits a `beats` array (lane/desc/prompt/weight/flf); dispatch generates EVERY dispatchable beat and concats into one take; a manim/failed beat becomes a **labeled placeholder**; per-beat status recorded; a **beat indicator** strip shows which beat needs fixing.

### 9. The dose diagram (seg_012) — grok garbles legible scales  *(solved, $0)*
Grok garbles legible numbers ("screen trap"); FLF can't sweep a needle. Solved with a **deterministic PIL stat-card** ("PRESCRIBED 200 / ADMINISTERED ~20,000 / 100× the prescribed dose") spliced ahead of the phone-call take — $0, legible, graded on-style. (Memory: stat-card-diagram-splice-technique.)

### 10. Editable beat plan + per-service cost  *(built, `3f3fc46`)*
Built: editable beat rows in the approval card (lane select + per-beat cost + editable prompt + total); an **edit endpoint** (`/revision/{rid}/edit` logs a new revision); mixed scenes no longer auto-dispatch on needs-work — they **draft the editable card first**.

### 11. Per-beat regeneration  *(built, `3f3fc46`)*
"No option to change the notes and generate only them." Built: **redo only selected beats** (edit prompt/lane), **reuse the rest's clips**, re-concat. Cost = only the redone beats; each beat's clip+prompt is recorded for reuse (`BeatFixer.tsx` + `regen_beats`/`_run_regen_beats_job`). Plus a **director rule**: grok needs a CONCRETE physical scene; abstract pattern/matching/diagram ideas → manim or rewrite.

### 12. Empty prompt → narration fallback (beat 2 placeholdered)  *(fixed, UNCOMMITTED)*
Redoing beats with **blank** prompt boxes (old takes had no stored per-beat prompts) made the backend fall back to the **whole narration** → generic shot → beat 2 failed → placeholder. Fix: prefill the redo box with `b.prompt || b.label`; backend falls back to the beat **label**, not the narration.

### 13. "Why aren't we using the Gemini vetting?" → wired it in  *(built, `3f3fc46`)*
The dashboard used only the **coarse `quality_gate` score** (it passed a bad take at 0.94). `lib/animation_vet.py` **WATCHES the clip vs the narration** and reports per-moment sync mismatches + fixes (it scored that same take **1/10**). Wired into every take (`_vet_take`), surfaced per-take, + a **"↺ re-plan from the vet"** button that feeds findings back to the director — the eyes the text-only director lacks.

### 14. seg_015 got messy → "start fresh"  *(built, UNCOMMITTED)*
Built a **scene reset**: a `scene_reset` event (`feedback.py`) clears the folded UI state (notes/verdict/revisions) so the next director pass isn't polluted; `reset_scene` (`takes.py`) drops the scene's takes from the index; files + log preserved. **"↺ start fresh"** button in `SceneDetail`. Ran it on seg_015, then drove a **clean single-shot regen** → a good on-style Yakima establishing shot (gate 0.99; vet sync-3/polish-4, expected for an atmospheric beat).

### 15. seg_019 crash — `float() argument … not 'NoneType'`  *(fixed, UNCOMMITTED)*
A director-planned **FLF dose-readout beat** had `"drain": null`. `float(f.get("drain", 0.85))` returns `None` when the key exists as null (not the default) → crash **after beats 1&2 had generated** (~$0.30 sunk). Fix: `float(f.get("drain") if … is not None else 0.85)` in BOTH FLF spots, and **wrapped `_gen_beat` in try/except → None** so one beat's error becomes a placeholder, never a take-killing crash.

---

## Recurring themes / still-open
- **Grok fails on small/abstract beats** ("operator presses P", "collimator matching pattern", "hip with striped burns") → placeholders. Mitigations: the director concrete-shot rule, the **per-beat regen** (redo just the bad one), and the **vet** that flags it. Grok is still a coin-flip on these — the human-in-the-loop beat editor is the real answer.
- **Gate score vs vet disagree by design** — gate = technical/style quality, vet = narration sync + motion. Use both.
- **Atmospheric vs literal:** "pattern repeats" beats (seg_015) work best as ONE clean establishing shot; literal multi-beat sequences (seg_019) are where the per-beat fragility lives.
- **Wasted spend on failures:** a beat crash/gate-fail still bills the beats that generated first. The `_gen_beat` wrap (#15) + spend ceiling reduce but don't eliminate this.

## Suggested next steps
1. **Commit the uncommitted chunk** (reset + prefill fix + FLF drain fix) — all bug/feature fixes.
2. **seg_019:** re-dispatch the (good) plan now the FLF crash is fixed — but make beat 1 ("operator presses P") concrete first or it'll placeholder again. The FLF dose-readout beat is the right call (clean legible "DOSE: 16,500–25,000 RADS").
3. Consider: vet score on the take chip; a per-beat vet (map findings → beat index) for one-click targeted fixes.
4. The pre-existing `visual_router.py` leg-cap WIP + `diagram.png` still need an owner decision.

## Key files
- `web/backend/takes.py` — the regen engine: dispatch, single/mixed/per-beat generation, beat plan, cost (`_beat_cost`/`_worst_case_usd`), graded previews, vet (`_vet_take`), reset.
- `web/backend/director.py` — the rule-applied planner (style-aware, beats schema, concrete-grok rule).
- `web/backend/{scenes,feedback,app}.py` — scene model / append-only feedback log / FastAPI routes.
- `web/ui/src/components/{SceneDetail,RevisionCard,BeatFixer}.tsx` — review UI / editable plan / per-beat fixer.
- `lib/{finishing,channel_style,animation_vet,flf,image_host,asset_bible}.py` — grade, style, vet, FLF, hosting, canonical anchors.
- `styles/channel_styles/graphic-novel-disaster.yaml` — the house style (navy `#0a1428` + amber `#e8a44c`, duotone+grain).
