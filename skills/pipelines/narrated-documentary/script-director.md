# Script Director - Narrated Documentary Pipeline

## When To Use

The research brief exists and it's time to author the **scored script** — the
single source of truth that binds narration, visuals, timing, and music for
every segment. This skill governs the conversion from research/raw script into
`scored_script.yaml`. It exists because this step had no guardrails: alignment
between what the narrator says and what the screen shows used to ride entirely
on the author's discipline, and the narration style drifted into evidence
recitation. Both are now codified and gated.

## Prerequisites

| Resource | Purpose |
|----------|---------|
| Prior artifact `research_brief` | Facts, data points, sources — every claim traces here |
| Schema `schemas/artifacts/scored_script.schema.json` | Structure validation |
| Lib `lib/scored_script.py` | Loader / data model |
| Lib `lib/script_validator.py` | Structural checks (ids, bindings, weights) |
| Lib `lib/narration_gate.py` | Narration↔visual alignment gate (HARD, pre-spend) |
| Lib `lib/script_review.py` | Adversarial panel: fact vetting, style critic, flow critic |
| Tool dict `tools/audio/pronunciation_dict.json` | Seed pronunciations for technical terms |

## The Artifact

One YAML, one contract. Every segment carries:

- `narration` — the words the voice actually reads (this text goes to TTS verbatim)
- `visual` — type + description + `ai_prompt`/`ai_motion`/`ai_style` (+ explicit `shots` for authored cuts)
- `silence_after_s` — breathing room after the segment
- `act`, `editorial_intent`, `pacing`, `music` — structure and mood

The narration is the spine: TTS audio is measured and every visual slot is
sized to it. Write the words first; design the pictures against the words.

## Narration Style Rules (the channel voice)

**Tell a story. Do not read evidence.**

1. **No scene-opening slates.** Never open a segment with a date/location
   recitation ("June third, nineteen eighty-five. Marietta, Georgia. ...").
   That is a caption read aloud, and a run of segments doing it reads like a
   court filing. The flow critic counts these; the target is ~zero.
2. **Anchors are earned, sparse, and mid-flow.** A handful of dates/places per
   EPISODE, placed where they heighten the story ("It's a June morning in 1985
   when..." / the chilling gap before a recurrence). When location matters, let
   a concrete detail carry it — the Texas heat, the Pacific-Northwest overcast —
   not a place-name statement.
3. **Dramatize the load-bearing specifics.** Dose numbers, error codes, and
   timings are the story's teeth — keep them exact, but land them as moments,
   not as data points in a list.
4. **Spoken-word rhythm.** Present tense. Short breath-sized clauses. This text
   is performed, not read — no constructions that only work on paper. Vary
   sentence shapes BETWEEN segments; neighboring segments must not open the
   same way.
5. **Numbers as words** ("sixteen thousand five hundred", "nineteen
   eighty-five") — the TTS reads exactly what is written.
6. **Pronunciation-managed terms keep their canonical written form**
   (Therac-25, AECL, rads, PDP-11). New technical terms get an entry in the
   pronunciation dictionary BEFORE voice generation, not after a bad take.

## Shot Design — stage the beat, don't illustrate the setting

The alignment gate only checks that the visual MATCHES the narration. A shot
can match and still waste the beat: the narration lands a devastating irony
("the machine's display? It read: treatment delivered normally") and the
visual is... a portrait of a terminal. Every segment's visual must stage the
beat's **dramatic core** — its conflict, irony, turn, or consequence — inside
the frame.

The reference example (seg_005): the weak shot is the calm terminal alone.
The strong shot holds both truths in one frame — **foreground**: the terminal
serene, cursor blinking, untroubled; **background, small, through the
doorway**: the patient flinching on the table under the machine. The frame
itself makes the argument; a viewer with the sound off still feels it.

Staging devices (pick the one the beat demands):

- **Foreground/background juxtaposition** — two truths in one frame
- **The prop that lies** — frame the object whose message contradicts reality
- **Consequence in frame** — the damage visible WITH its cause
- **Scale contrast** — the small human against the huge machine
- **Isolation** — the subject alone in oversized negative space
- **Point of view** — the camera as participant (the patient's view up at the
  beam head; the operator's view of only the screen)
- **The turn** — stage the instant the narration pivots on

Channel-specific mechanics:

- **Words belong to the overlay layer.** No legible text in generated frames
  (i2v warps it) — stage the silent prop (a calm glowing screen) and put the
  words in `text_overlay` ("TREATMENT DELIVERED NORMALLY"), where they render
  deterministically.
- **Cross-asset staging**: when a frame needs TWO anchored identities (the
  terminal up front, the machine behind), set `support_asset_refs` on the
  visual — the support assets' reference sheets/canonicals ride into the
  keyframe edit and their identity tokens into the prompt, so neither subject
  drifts off-model.
- **The shot doctor enforces this**: `python -m lib.script_review <script>
  --panel shots` adversarially scores every AI segment's staging (1-10),
  names the missed device, and proposes a staged replacement prompt. Run it
  before the narration gate; apply or consciously reject each suggestion.

## Visual Binding Rules

1. **The visual must depict what the narration says.** `visual.description` /
   `ai_prompt` are checked against the narration by `lib/narration_gate.py` —
   a mismatch blocks Phase A. Do not author a prompt that forbids what the
   narration demands (real example: a prompt said "No people, no hands" while
   the narration said "The operator pressed P").
2. **Multi-beat narration needs multi-beat visuals.** A segment whose narration
   moves through distinct beats (error appears → ignored → habituation) cannot
   sit on one static prompt. Either author explicit `shots` with their own
   prompts, or rely on the chain-leg beat splitter — but know that authored
   shots are better than derived ones.
3. **People in the narration means people in the frame** — the planner will
   build a populated keyframe (subject ON surfaces, gated) from the empty
   canonical. Keep the faceless-channel rules: anonymous figures, hands and
   silhouettes; never a real person's likeness, never faces as the subject.
4. **Segment length drives clip count.** At the current provider cap (~30s) a
   segment at or under the cap is ONE continuous clip; longer segments become
   chained continuations. Prefer segments ≤ the cap unless the beat genuinely
   needs the length.

## Workflow

1. Draft narration segment-by-segment from the research brief (style rules above).
2. Author each segment's visual against its narration (binding rules above).
3. Validate structure: `lib/script_validator.py`.
4. Run the adversarial panel: `python -m lib.script_review <script.yaml> --report ...`
   - **fact vetting**: every checkable claim vs the research brief — fix
     critical findings, never ship an unsupported claim;
   - **style critic**: evidence-reading/data-dump/repetition flags with
     concrete fixes — apply or consciously reject each;
   - **flow critic**: hook, tension curve, transitions, opener repetition.
5. Run the alignment gate: `python -m lib.narration_gate <script.yaml>` — fix
   mismatches in the SCRIPT (that is the cheap place; after generation it costs
   regenerations).
6. Human approval (creative stage), then voice generation.

## Quality Bar

- Zero scene-opening date/location slates; surviving anchors individually justified
- Every factual claim traces to the research brief; zero critical fact findings
- Narration gate: zero mismatches
- Every multi-beat segment has authored shots or an explicit decision to chain
- Pronunciation dictionary covers every technical term before voice gen

## Common Mistakes

- **Evidence reading.** Opening segments with dates, locations, institutions.
  The story carries time and place; the slate does not.
- **Authoring prompts against the narration** (the "no hands" vs "pressed P"
  class). The gate catches it, but write it right the first time.
- **One static beat under a moving arc.** If the narration escalates, the
  visual must move with it.
- **Editing narration after voice generation.** The audio is the timeline;
  narration edits after TTS mean regenerating audio AND re-fitting visuals.
  Finish the words first.
- **Polishing on paper.** Read it aloud. If a sentence needs a second pass by
  eye, the voice will stumble on it.
