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
| POST | `/api/projects/{id}/scenes/{sid}/verdict` | `{verdict: approve\|needs_work\|reject}` |
| POST | `/api/projects/{id}/scenes/{sid}/note` | `{text}` |
| POST | `/api/projects/{id}/scenes/{sid}/suggestion` | `{text, change_type}` |
| POST | `/api/projects/{id}/scenes/{sid}/regenerate` | `{notes[], target}` |

## Not yet built (next steps)

- Trigger the director-pass + pre-spend approval card from the UI (currently the
  regenerate request is captured for the agent/script to act on).
- Port the vanilla-JS frontend to React/Vite + reuse `remotion-composer` for
  in-browser composition preview.
- Per-scene cost attribution and live job progress (when generation is driven
  from the UI).
