# The Director's Touch — Codified Scene Library

> Source: `docs/research/The Directors Touch - A Codified Scene Library for an Animated Documentary AI Agent.md`
> Machine half: `lib/scene_library.py` (the vocabularies + the emotional→shot mapping live there as data).
> This skill is the **how/when**; the module is the **what**. Schemas enforce the enums; `script_validator.py` blocks contradictions; `script_review.py` (`story` panel) grades the architecture.

Acclaimed-documentary technique reduces to a finite set of repeatable **moves** — and nearly all of them map cleanly onto graphic-novel animation, because "virtual camera over static art" *is* the Ken Burns technique. Tag every scene like a director would, so the episode is a layered, rhythmic piece — not a flat slideshow.

## The per-scene tagging contract

For every segment/scene, decide and record:

| Tag | Field | Vocabulary |
|---|---|---|
| Shot size | `shot_language.shot_size` (scene_plan) | Taxonomy 2 — `establishing…extreme_close_up` |
| Camera move | `ai_motion` (scored_script) / `shot_language.camera_movement` (scene_plan) | Taxonomy 3 — push-in, pull-back, Ken Burns, tilt-up, arc, static hold |
| Pacing | `pacing` | `establishing / escalation / crisis / resolution` |
| Rhythm | `rhythm` | `fast / medium / lingering / breath` |
| Narration mode | `narration_mode` | `literal / evocative / none` |
| Music cue | `music.state` | `playing / continue / change / stop / fade_out` |
| Transition out | `audio_transition` | `hard_cut / match_cut / match_on_action / j_cut / l_cut / sound_bridge / contrast_cut` |
| Director's move | `directors_move` | Taxonomy 5 (named, optional) |
| Retention role | `retention_beat` | `cold_open / value_proposition / commitment_hook / pattern_interrupt / re_hook / cliffhanger / payoff` |

You do **not** have to set every tag on every scene. Set what the beat earns; leave the rest blank and the planner fills a sensible default from the emotional mapping (it never overrides a value you set).

## Stage 2 — emotional-trigger defaults (the deterministic core)

Each `editorial_intent` has a default shot grammar (see `EMOTIONAL_DEFAULTS` in `lib/scene_library.py`). When you leave `ai_motion` blank, `visual_router` derives the camera move from this table:

| editorial_intent | shot | camera | rhythm | narration | music |
|---|---|---|---|---|---|
| establishing_atmosphere | establishing | slow push (dolly_in) | lingering | evocative | playing |
| building_trust | medium | slow push | medium | literal | continue |
| technical_explanation | medium | static* | medium | literal | continue |
| escalation | medium_close | push-in | fast | evocative | change |
| crisis_moment | close_up | handheld | fast | evocative | change |
| pattern_recognition | insert | push-in | medium | literal | continue |
| **emotional_impact** | close_up | **static hold** | **breath** | evocative | **drop to silence** |
| institutional_critique | medium | slow push | medium | literal | continue |
| **resolution** | wide | **pull-back (dolly_out)** | lingering | evocative | change |
| call_to_action | medium | push-in | medium | literal | change |
| chapter_transition | establishing | pull-back | breath | none | change |

Read the mapping as: **tension → slow push-in + lingering + score build; revelation → pull-back + cue change; grief/gravity → static hold + prepared silence; teaching → medium + literal + steady; energy → fast cuts + match-on-action.** Camera moves must be *motivated by emotion, not decoration* — but even subtle motion beats a dead-still frame for retention.

> *`technical_explanation` is **content-aware**: it defaults to a static hold for an inert subject (a diagram, a still label), but a beat that shows a **mechanism actuating** (a cam rotating, a lever snapping, gears turning) is seeded a slow macro push to the moving contact point instead — the actuation is the point. See `is_mechanism_action` in `lib/scene_library.py`.*

## Taxonomy 5 — named director's moves (the engine of propulsion)

The reveal is what pulls viewers forward. Apply these where the script supports them (all defined in `DIRECTORS_MOVES`):

- **genre_bait_and_switch** — establish one tone, modulate to another (*Three Identical Strangers*).
- **acclimatize_dont_ambush** — plant foreboding teases so a dark turn is *earned*, never "you won't believe what happens next."
- **recontextualized_replay** — reuse an earlier image after a reveal so its meaning inverts (planner pulls the camera back to re-frame it).
- **chronological_reveal_ladder** — A→Z, each escalation depending on the prior one.
- **withhold_judgment** — opposing accounts at equal weight, a cue per side. **For educational content, use this only to *sequence true information*, never to distort it.**
- **acts_within_acts** — every act AND every segment gets its own opening + cliffhanger.
- **calibrated_cliffhanger** — end on escalating threat without prematurely resolving (planner leans the camera in).
- **escalating_weirdness** — each segment out-escalates the last, anchored to a concrete end point.
- **delayed_antagonist_reveal** — build a figure through others' accounts before they appear.
- **cold_open_inversion** — open near the end, then rewind.
- **visual_anchor_before_context** — Johnny Harris: show the striking image first, *then* explain it.

## Stage 3 — episode structural rules (enforced)

- **Cold open ≤15s.** 0–5s attention grab → 5–15s value proposition (what the viewer gets) → 15–30s commitment hook (open the loop the episode resolves). Tag the opening segments `retention_beat: cold_open` / `value_proposition` / `commitment_hook`. The 15-second cliff is real — <45% of viewers pass the first minute.
- **Pattern interrupt every 30–90s.** Change *something* — angle, graphic, sound, narration pace, POV. Tag those segments `retention_beat: pattern_interrupt`.
- **Mid-video re-hook past ~7–8 min** for a 15-min target ("the part most people get wrong"). Tag `retention_beat: re_hook`.
- **Per-segment cliffhangers.** Close one loop, open the next.
- **Every planted tease fires.** A forward-tease with no payoff is a broken promise — zero unfired loops is the bar (`script_validator` blocks unfired `open_loop` plants).
- **Tight, dead-zone-free ending.** Cut when content is done.

## Narration ↔ visual (Taxonomy 7)

Alternate `literal` (visual shows the words — clarity/teaching) and `evocative` (visual evokes the feeling — emotion). A script that never leaves `literal` reads like captioned stock footage. Use `none` for observed/direct-cinema beats with no narration.

## Caveats (do not skip)

- **Accuracy over manufactured suspense.** `withhold_judgment` and villain-framing drew real criticism (and a defamation suit over *Making a Murderer*) for false equivalence. Use these tools to *sequence true information*, not to distort it.
- **Herzog's "ecstatic truth" is anti-factual.** Borrow his authored *voice and framing*, never his license to fabricate.
- **Retention numbers are directional.** The 15s cliff / 40–55% band / 30–90s cadence are starting hypotheses to validate against the channel's own YouTube Studio data, not laws.
