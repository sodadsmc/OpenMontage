# OpenMontage — Scene Review Dashboard (scaffold)

A read-only scene-by-scene review surface over an existing project's artifacts,
plus an append-only **feedback event log** that captures every human action
(verdict / note / suggestion / regenerate-request) as future training data.

A "scene" = one scored-script **segment** (one narration beat). Each scene shows
its assembled clip with a **number badge** in the corner, the narration, the
**auto-gate** verdict/score (from `narration_alignment_report.json`), the
sub-shot legs (drill-down), and the review controls.

## Run

From the repo root:

```bash
pip install -r web/requirements.txt
uvicorn web.backend.app:app --reload --port 8000
# open http://localhost:8000   (defaults to the therac-25-test project)
```

## What it reads (read-only)

| Source | Used for |
|---|---|
| `artifacts/duration_map_v6.json` | scene order, narration, slot duration, lane |
| `artifacts/ai_visual_assets_v6.json` | the assembled clip per segment (the player) |
| `artifacts/shot_manifest_v6.json` | sub-shot legs (drill-down) |
| `artifacts/narration_alignment_report.json` | the AUTO gate verdict/score |
| `artifacts/cost_ledger.jsonl` | spend summary |
| `script_v5/scored_script.yaml` | `narration_mode` (depiction mode) + lane |

## What it writes (append-only)

Human feedback → `projects/<id>/feedback/events.jsonl`, one immutable line per
action. Per-scene UI state is **derived** by folding the log. The chain
`regenerate_requested → (director revision) → take → verdict` is the training
record: it lets you later teach the auto-gate and the director from human calls.

`regenerate` only **captures intent** — the actual re-generation runs through the
director pass (rules applied), not raw note-injection.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/projects` | list projects |
| GET | `/api/projects/{id}/scenes` | all scenes (the dashboard payload) |
| GET | `/api/projects/{id}/scenes/{sid}` | one scene |
| GET | `/api/projects/{id}/cost` | cost-ledger summary |
| GET | `/api/projects/{id}/events` | raw feedback event log (export) |
| GET | `/api/projects/{id}/media/{path}` | serve a project file (range-enabled) |
| GET | `/api/health` | director + dispatch availability |
| POST | `/api/projects/{id}/scenes/{sid}/verdict` | `{verdict: approve\|needs_work\|reject}` |
| POST | `/api/projects/{id}/scenes/{sid}/note` | `{text}` |
| POST | `/api/projects/{id}/scenes/{sid}/suggestion` | `{text, change_type}` |
| POST | `/api/projects/{id}/scenes/{sid}/regenerate` | `{notes[], target}` — log intent |
| POST | `/api/projects/{id}/scenes/{sid}/director-pass` | run the director → drafted revision |
| POST | `…/scenes/{sid}/revision/{rid}/approve` | approve a revision (no spend) |
| POST | `…/scenes/{sid}/revision/{rid}/reject` | reject a revision |
| POST | `…/scenes/{sid}/revision/{rid}/approve-and-dispatch` | approve + **generate a new take (spends)** |
| GET | `/api/projects/{id}/jobs/{job_id}` | dispatch job status |
| GET | `/api/projects/{id}/jobs` | dispatch jobs for the project |

## Director pass + approve-and-dispatch

`director-pass` turns the human's notes (intent) into a rule-compliant revision
(`described_action` + revised prompt + lane + rationale) via Gemini — see
`director.py`. `approve-and-dispatch` then regenerates that ONE scene as a **new
take** through `lib.visual_router.generate_ai_video`, conforms it to the slot,
re-scores it with the quality gate, and stores it as
`assets/ai_segments/<sid>__take<N>.mp4` (the original is never clobbered; an
accepted take is preferred by the scene loader). The take + cost are logged
(`take_generated` event + `cost_ledger.jsonl`).

**Spend safety:** dispatch needs `KIE_API_KEY` (generation) + `GOOGLE_API_KEY`
(gate) and network; it hard-blocks gracefully without them. v1 auto-dispatches
the **grok** lane only (FLF/manim stay on the manual pipeline). Set
`OPENMONTAGE_DISABLE_DISPATCH=1` as a hard kill-switch (no job can spend).

## Config / env

| Var | Effect |
|---|---|
| `OPENMONTAGE_DIRECTOR_MODEL` | director Gemini model (default `gemini-2.5-flash`) |
| `GOOGLE_API_KEY` | director pass + quality-gate scoring |
| `KIE_API_KEY` | take generation (grok i2v via Kie) |
| `OPENMONTAGE_DISABLE_DISPATCH=1` | kill-switch: dispatch always blocks |

## Not yet built (next steps)

- Port the vanilla-JS frontend to React/Vite + reuse `remotion-composer`.
- Auto-dispatch the FLF / manim lanes (need authored keyframes / scene defs).
- SSE live progress (currently polled) and balance-diff cost truthing.
