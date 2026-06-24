# OpenMontage

## ⚠️ Read this first — what actually runs (reality check)

This repo documents an elaborate "instruction-driven pipeline" — YAML manifests in
`pipeline_defs/`, 110+ stage-director skills in `skills/`, and a checkpoint state machine
(`lib/checkpoint.py`, `lib/pipeline_loader.py`). **That formal system is legacy: it is
imported only by tests, has produced zero checkpoints on disk, and drives no real output.**
Do not route production through it, and do not take `pipeline_defs/` / `skills/` at face value.

**The real, current production system** is the prescriptive recipe in
[`docs/PRODUCTION_WORKFLOW.md`](docs/PRODUCTION_WORKFLOW.md): TTS-first timing → per-beat lane
routing (Manim / FLF / Grok i2v) → fail-closed gates → conform → assemble + grade. The code is in
`lib/*.py` and `projects/therac-25-test/script_v5/*.py`, with a scene-review dashboard in `web/`
(run it per `docs/PRODUCTION_WORKFLOW.md` §3). **For any work on the documentary, follow that file.**

## Then read AGENT_GUIDE.md — for protocol, not pipeline routing

[`AGENT_GUIDE.md`](AGENT_GUIDE.md) holds genuinely useful cross-cutting protocol: announcing paid
generation before spending, the decision-communication contract, cost discipline, and the capability
registry. Read it for those. **But treat its "Rule Zero / all production goes through
`pipeline_defs/`" routing as superseded** by `docs/PRODUCTION_WORKFLOW.md` — see the reality banner
at the top of AGENT_GUIDE.md.

> Cleanup status (2026-06): the dead formal-pipeline layer (`pipeline_defs/`, `skills/pipelines/`,
> `lib/checkpoint.py`, `lib/pipeline_loader.py`) is slated for removal once the documentary ships.
> Until then, these reality banners are the source of truth over the legacy text they sit above.
