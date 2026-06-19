# Session Handoff — Scene-Review Dashboard + Depiction-First Migration

Date: 2026-06-18 · Branch: `v6-baseline` · Repo: `D:/OpenMontage2`

This session produced two things: (A) a **depiction-first migration** of the narrated-documentary
pipeline, and (B) a new **scene-review dashboard** (`web/`) with a director-driven regenerate loop.
Read this top-to-bottom before continuing.

---

## 1. TL;DR

- The narrated-documentary channel is now **generation-first / depict-the-narration**, not
  stock-footage / atmospheric. Gate, schemas, routing, and skills were updated to match.
- There's a working **review dashboard** (FastAPI + React/Vite) where you review each scene's clip,
  mark approve / needs-work / reject, add notes, and **regenerate a fix** — your notes go through a
  **director pass** (Gemini re-plans under the channel rules) and the new clip is generated as a
  **take** you review. Every action is logged to an append-only event log = **training data**.
- Regeneration auto-dispatches the **Grok** and **FLF (Kling)** lanes, incl. true **state-morph**
  (X→E / "error code appears") and a partial path for **mixed** beats. `manim`/diagram stays manual.

---

## 2. Git state

- **Branch:** `v6-baseline` (NOT main). HEAD = `9ffdff1`.
- **This session's commits** (oldest→newest):
  - `a371d45` fix: make narrated-documentary depiction-first (retire stock-era b-roll standard)
  - `32543c9` feat: scene-review dashboard (FastAPI + UI) with director-pass approval
  - `b41354e` feat: approve-and-dispatch scene regeneration (new takes) + configurable Gemini director
  - `4749064` feat: React + TypeScript + Vite port of the scene-review UI
  - `8692722` fix: manim scenes display + clip-swap picker + dashboard run docs   ← tag `scene-review-v1` is here
  - `25b8c6c` feat(web/ui): scene Next/Back nav + one-click "Fix in pipeline" + progress spinners
  - `cea92e9` feat(web): FLF auto-dispatch — full director path completes for the transition-frame lane
  - `50a5437` feat(flf): true content state-morph keyframe authoring (matched-frame text composite)
  - `d7f57ec` fix(director): stop silent fallback to note-appending — re-plan reliably from narration
  - `9ffdff1` feat(dispatch): bias director to a dispatchable lane + auto-dispatch mixed's primary shot
- **Tag** `scene-review-v1` points at `8692722` — now **5 commits behind HEAD**; re-tag at `9ffdff1`
  if you want a current checkpoint.
- **Uncommitted:**
  - `web/start_dashboard.bat` — NEW, untracked (a double-click launcher). Safe to commit.
  - `lib/visual_router.py` — modified, but this is **pre-existing WIP that predates this session**
    (an `AI_GROK_LEG_CAP` leg-cap change). It was deliberately NOT committed this session to avoid
    bundling someone else's WIP. Decide what to do with it.
  - `diagram.png` — pre-existing modification, not from this session.

---

## 3. Part A — Depiction-first migration

**The change:** the channel moved from stock footage (a visual only had to be *atmospherically
relevant*) to **custom AI video that must DEPICT what the narration describes**. The migration was
half-done; `a371d45` finished it. Key edits:

- `lib/narration_gate.py` — the pre-spend gate rubric rewritten to **depiction-first** and
  **mode-aware** (per-segment `narration_mode`: `literal` = depict the action [default], `evocative`
  = the looser atmospheric bar, `none` = minimal). `atmospheric_footage` dropped from the gated set.
- `schemas/artifacts/scored_script.schema.json` + `segment_plan.schema.json` — `visual.description`
  now "must DEPICT"; `search_queries` made optional/legacy; `ai_fallback_prompt` deprecated for
  `ai_prompt`; type enum reframed (ai_video primary, footage = deliberate fallback).
- Dispatch/authoring code (`lib/visual_router.py` docstrings, `tools/assembly/pacing_engine.py`,
  `tools/script/segment.py`, `template_applier.py`, `tools/video/runway_genfill.py`) reframed from
  "gap-fill / fallback-to-stock" to generation-first.
- Skills/docs: `skills/pipelines/narrated-documentary/{footage-search,scoring,gap-fill,ai-visual}-director.md`,
  `skills/creative/broll-planning.md`, `skills/meta/footage-research.md`, `README.md` got scope
  banners. **`documentary-montage` / `hybrid` / `source_led` were intentionally left retrieval-first.**
- Full audit + rationale: the change was driven by a deduped audit; the keystone is the gate rubric.

**Watch-out:** the tightened gate will fail atmospheric-only prompts on `literal` beats — that's
intended. Tag a beat `narration_mode: evocative` where atmosphere is genuinely right.

---

## 4. Part B — The scene-review dashboard (`web/`)

### 4.1 Purpose
Review the cut **scene-by-scene** and iterate: review clip → mark needs-work + add a note → the note
becomes **instructions** (director re-plans) → regenerate a new **take** → review → iterate. Every
action is logged as **training data** to teach the auto-gate/director later.

### 4.2 How to run
```bash
# from D:/OpenMontage2
pip install -r web/requirements.txt
npm install --prefix web/ui && npm run build --prefix web/ui   # build the React UI once
uvicorn web.backend.app:app --port 8011                        # -> http://localhost:8011
```
Or just double-click **`web/start_dashboard.bat`** (runs it in its own window, survives independently).
UI hot-reload dev: `npm run dev --prefix web/ui` (Vite :5173, proxies `/api` → :8011).

> **Important:** don't rely on an agent/editor-managed server — those get reaped between turns (this
> bit us repeatedly). Run it yourself in a terminal / via the .bat.

### 4.3 Env vars
| Var | Effect |
|---|---|
| `GOOGLE_API_KEY` | director pass (Gemini) + quality-gate scoring |
| `KIE_API_KEY` | generation (Grok i2v, Kling FLF, Nano keyframes) |
| `OPENMONTAGE_DIRECTOR_MODEL` | director model, default `gemini-2.5-flash` |
| `OPENMONTAGE_DISABLE_DISPATCH=1` | hard kill-switch — dispatch always blocks (use for safe testing) |

`GET /api/health` reports director + dispatch readiness.

### 4.4 Architecture
- `web/backend/scenes.py` — **read-only** scene model. Joins `duration_map_v6.json` (order, narration,
  slot, lane) + `ai_visual_assets_v6.json` (clip per ai_video seg; PRIMARY) + `visual_assets_v6.json`
  (fallback clip for manim/text-card) + `narration_alignment_report.json` (auto-gate) +
  `shot_manifest_v6.json` (sub-shots) + an accepted **take** if present. Prefers an accepted take >
  ai_visual_assets > visual_assets.
- `web/backend/feedback.py` — append-only **event log** `projects/<id>/feedback/events.jsonl`; per-scene
  state derived by folding it. Event types: `human_verdict`, `human_note`, `human_suggestion`,
  `regenerate_requested`, `revision_drafted` (director), `revision_approved`/`rejected`, `take_generated`.
- `web/backend/director.py` — the **director pass**: notes (intent) → rule-compliant revision
  (`described_action` + `revised_prompt` + `lane` + `rationale` + optional `flf`/`morph`/`primary_shot`)
  via Gemini. Retries + lenient JSON parse; falls back to a restatement only if all attempts fail
  (records `_error`). `max_output_tokens=8192` (2.5-flash thinking tokens were truncating the JSON).
- `web/backend/takes.py` — the **job runner / dispatcher**. `dispatch_take` validates + queues;
  `_run_take_job` (ThreadPool, 1 worker) generates and stores a take. `_gen_target` resolves what to
  generate. Also `clip_candidates`/`assign_clip` (the **⇄ swap-to-existing-clip** picker).
- `web/backend/app.py` — FastAPI: read endpoints, range-served media, capture endpoints,
  director-pass, approve-and-dispatch, jobs, clip-candidates/use-clip, `/api/health`, static UI mount.
- `web/ui/` — **React 18 + TS + Vite** (the served frontend; build → `web/ui/dist`). `web/frontend/` is
  a **vanilla-JS no-build fallback that LAGS** the React app (missing nav/swap/spinners) — FastAPI
  serves `web/ui/dist` when built, else `web/frontend/`.
- Writes: `projects/<id>/feedback/events.jsonl` + `projects/<id>/artifacts/ai_segments_takes.json`
  (the take index). Both are under the **gitignored** `projects/` dir.

### 4.5 The regenerate / dispatch model (lanes)
- "Needs work" + a note → records the verdict → **auto-runs the fix** (director pass → cost confirm →
  generate). Spinners during planning + generating. The director turns notes into instructions and
  **picks the lane**.
- Auto-dispatch supports **`grok`** (i2v, ~$0.017/s) and **`flf_state_morph`/`flf_drain`** (Kling
  first-last-frame, ~$0.084/s). The director is **biased to prefer a single grok/flf lane**.
- **`mixed`** (e.g. diagram + live-action): the director emits a `primary_shot`; the dispatcher
  generates that single shot as a **PARTIAL take** (verdict `needs_review`, flagged "diagram/other
  beats need a manual pass"). `mixed` without a primary_shot, and pure `manim`, return
  `not_yet_auto_dispatched` (manual pipeline).
- **FLF lanes:** `lib.flf.flf_segment` authors the start keyframe (Nano) and either **drains** the end
  (`drain_endpoint`, for going-dark/dim/"N of M go dark" via `band`) or, for a **content state-morph**,
  composites a glyph/code onto matched frames (`composite_text` — the X→E technique, new this session),
  then Kling interpolates. The `morph` field drives the latter: `{box, start_text, end_text, color}`.
- **Safety:** the prompt is fetched server-side from the logged revision (not the request body);
  KIE/GOOGLE/kill-switch gates; one job per scene; idempotent on repeat approval; cost confirmed before
  spend and logged to `cost_ledger.jsonl`.

### 4.6 therac-25 project state
- seg_006 → swapped to `assets/seg006_grok/seg006_explained_final.mp4` (the "explained" clip).
- seg_007 → swapped to `assets/visuals_synced/seg_007_sketch_final.mp4` (the newer animated manim).
  Both are accepted takes in `ai_segments_takes.json` (project-local, gitignored).
- Operator feedback (verdicts/notes/takes for seg_001–009) is in `projects/therac-25-test/feedback/events.jsonl`.
- ⚠ Duration mismatch on the swapped clips vs their slots (seg_006 clip 28.7s vs 11.2s slot; seg_007
  34.5s vs 45.6s) — fine for review, but at final render they'd be trimmed/freeze-padded. Re-time those
  beats before a final render if they're keepers.

---

## 5. Known issues / caveats
1. **Server reaping** — agent-started servers die between turns; use `web/start_dashboard.bat` or your own terminal.
2. **`lib/visual_router.py`** carries pre-existing uncommitted leg-cap WIP (not from this session) — resolve it.
3. **Tag** `scene-review-v1` is behind HEAD — re-tag at `9ffdff1` if desired.
4. **`google.generativeai` is deprecated** (FutureWarning) across the repo — eventual migration to `google.genai`.
5. **FLF state-morph box** is placed by the director in relative fractions (it never sees the keyframe), so
   placement is approximate — FLF is eyeball-reviewed; nudge the box via a note → re-plan.
6. **Non-text morphs** (a needle swinging to a reading) aren't handled by `composite_text` — use drain or a manual pass.
7. **Mixed = partial** (live-action only; the diagram half needs a manual/manim pass). Full mixed isn't wired.
8. **Job progress is polled**, not SSE.
9. Dashboard narration = the **duration_map** narration (what was actually voiced); `scored_script.yaml`
   narration may differ where it was rewritten post-audio.

## 6. Suggested next steps
- Commit `web/start_dashboard.bat`; decide on `lib/visual_router.py` WIP; re-tag a checkpoint.
- **Full mixed generation** — wire manim auto-dispatch + per-beat concat so mixed beats fully generate.
- **FLF non-text morphs** (needle/dial to a reading) — bespoke matched-frame author beyond text.
- **SSE live progress** + Kie balance-diff for true (not estimated) spend.
- Reuse `remotion-composer` components in the UI for in-browser composition preview.
- Consider exposing `narration_mode` per scene in the dashboard so reviewers can flip literal/atmospheric.

## 7. Key files
- Dashboard: `web/backend/{scenes,feedback,director,takes,app}.py`, `web/ui/src/{App.tsx,api.ts,components/*}`, `web/README.md`, `web/start_dashboard.bat`.
- FLF/morph: `lib/flf.py` (`composite_text`, `drain_endpoint`, `flf_segment`), `lib/scored_script.py` (`FLFSpec`).
- Gate/migration: `lib/narration_gate.py`, `schemas/artifacts/scored_script.schema.json`, `docs/PRODUCTION_WORKFLOW.md` (the "Scene-review dashboard" run section).
- Cost: `lib/cost_ledger.py`, `projects/therac-25-test/artifacts/cost_ledger.jsonl`.
