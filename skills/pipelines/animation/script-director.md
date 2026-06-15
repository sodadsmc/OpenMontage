# Script Director — Animation Pipeline

## When to Use

This stage turns the approved proposal into animation-ready beats. The script must leave room for motion, staging, and hold time — and must integrate the research findings and respect the selected animation mode.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |
| Prior artifact | `proposal_packet` from Proposal Director | Selected concept, animation mode, target duration, reuse strategy |
| Optional artifact | `research_brief` from Research Director | Data points, audience insights, accuracy constraints |
| Tools | `transcriber` | Optional source transcript support |

## Process

### 1. Absorb the Proposal

Read the `proposal_packet.selected_concept` thoroughly. Extract:

- **Title and hook** — the opening must deliver on this promise
- **Animation mode** — `manim`, `remotion`, `ai_video`, `diagram_stills`, or `mixed`. This constrains how you write.
- **Narrative structure** — `progressive_build`, `myth_busting`, `journey`, etc. Follow it.
- **Target duration** — word budget = target_seconds × 2.5 words/sec (at 150 WPM)
- **Key points** — from `selected_concept.key_points`
- **Reuse strategy** — recurring motifs mean recurring script structures

If `research_brief` is available, also extract:
- **Data points** — weave specific, sourced facts into the narration (not vague claims)
- **Audience misconceptions** — address them directly in the script
- **Mathematical accuracy notes** — constraints on what can and cannot be simplified

### 2. Write in Animation Beats

Each section should express ONE clear visual idea:

- **Statement** — introduce a concept (entrance animation)
- **Demonstration** — show it working (the main animation)
- **Transformation** — morph from one state to another (transition)
- **Comparison** — show two things side by side (split screen or sequential)
- **Conclusion** — land the insight (hold + emphasis)

**Animation mode affects writing style:**

| Mode | Writing Style |
|------|---------------|
| Manim | Precise, mathematical. Each beat maps to a specific geometric transformation. Write narration that describes what's being shown. |
| Remotion | Data-driven, punchy. Each beat maps to a chart/component animation. Narration complements the visual data. |
| AI Video | Descriptive, evocative. Each beat describes a scene the AI should generate. Narration adds context the visual can't convey. |
| Diagram Stills | Explanatory, progressive. Each beat adds a layer to a building diagram. Narration walks through the build. |
| Mixed | Varies per section — tag each section's mode in metadata. |

### 3. Keep On-Screen Text Tight

Animation-heavy pieces fail when the viewer has to read too much while motion is happening:

- **Max 8 words** for on-screen titles
- **Max 15 words** for on-screen descriptions
- Prefer phrases over sentences
- Prefer numbers and labels over paragraphs
- Mathematical notation is fine — it IS the content in math-animation mode

### 4. Leave Room for Visual Holds

Do NOT fill every second with new information. The scene plan will need time for:

- **Entrances** (0.5-1s): objects appearing on screen
- **Reveals** (1-2s): progressive disclosure of complexity
- **Holds** (1-3s): letting the viewer absorb what they see
- **Exits** (0.5s): clearing the stage for the next beat

**Rule of thumb:** For every 10 seconds of narration, budget 3-4 seconds of visual breathing room. A 90-second video should have ~60-65 seconds of narration and ~25-30 seconds of visual holds.

### 5. Use Metadata for Motion Intent

Recommended metadata keys per section:

- `beat_type`: statement / demonstration / transformation / comparison / conclusion
- `animation_mode`: which mode this section uses (important for mixed mode)
- `text_constraints`: max words for on-screen text in this section
- `narration_plan`: how narration relates to visual (describes / complements / silent)
- `visual_priority`: what the viewer should focus on (the animation, the text, the data)
- `hold_time_seconds`: minimum visual hold time after this section's content
- `data_source`: if this section uses a research data point, reference it
- `rhythm`: shot-duration nuance — `fast` / `medium` / `lingering` / `breath` (`breath` = held near-silent beat after a peak; pair with `silence_after_s`)
- `narration_mode`: `literal` (visual shows the words) / `evocative` (visual evokes the feeling) / `none` — **alternate** them
- `audio_transition`: how this beat hands off — `hard_cut` / `match_cut` / `match_on_action` / `j_cut` / `l_cut` / `sound_bridge` / `contrast_cut`
- `directors_move`: named reveal move when one applies (see the Scene Library subsection below)
- `retention_beat`: episode-structure role — `cold_open` / `value_proposition` / `commitment_hook` / `pattern_interrupt` / `re_hook` / `cliffhanger` / `payoff` / `none`
- `open_loop`: `{ action: plant|payoff, id, note }` — tag a tease and where it fires (every `plant` needs a `payoff`)

### 5a. Scene Library — The Director's Touch

These per-segment tags (above) turn a flat beat list into a layered, rhythmic piece. They are all **optional and additive** — set what the beat earns; leave the rest blank and the planner fills a sensible default from the emotional mapping (it never overrides a value you set). Full how/when reference: `skills/creative/scene-library.md`.

**Emotional defaults.** When you leave camera/motion blank, the planner derives it from `editorial_intent`: tension → slow push-in + lingering; revelation → pull-back + cue change; grief/gravity → static hold + drop-to-silence (`breath` + `silence_after_s`); teaching → medium + `literal` + steady; energy → fast cuts + `match_on_action`. Motion is *motivated by emotion, not decoration*.

**Named director's moves** (`directors_move`, apply where the script supports them):

| Move | Use |
|------|-----|
| `acclimatize_dont_ambush` | Plant foreboding teases so a dark turn is *earned*. |
| `recontextualized_replay` | Reuse an earlier image after a reveal so its meaning inverts. |
| `chronological_reveal_ladder` | A→Z, each escalation depending on the prior one. |
| `calibrated_cliffhanger` | End on escalating threat without resolving it yet. |
| `visual_anchor_before_context` | Show the striking image first, *then* explain it. |

**Structural rules** (enforced by the validator):

- **Cold open ≤15s**: 0–5s grab → value proposition → commitment hook. Tag those opening segments `cold_open` / `value_proposition` / `commitment_hook`.
- **Pattern interrupt every 30–90s** — change angle, graphic, sound, or narration pace; tag `pattern_interrupt`.
- **Mid-video `re_hook`** past ~7–8 min on a 15-min target.
- **Every plant fires.** A forward-tease with no payoff is a broken promise — pair every `open_loop` `plant` with a `payoff` (zero unfired loops is the bar).
- **Accuracy over manufactured suspense.** Use `withhold_judgment` to *sequence true information*, never to distort it.

> HARD constraint reminder (step 3): keep on-screen text tight regardless of these tags — phrases over paragraphs, never wall-to-wall legible copy.

### 6. Research Integration

If a `research_brief` is available:

- Use at least 2 data points from the research in the narration
- Ground the hook in the research's most surprising finding
- Address at least 1 audience misconception if the narrative structure supports it
- Cite sources naturally ("According to [source]..." or "A [year] study found...")
- Do NOT invent statistics — only use what the research found

### 7. Quality Gate

Before submitting the script, verify:

- [ ] Every section supports ONE strong visual idea
- [ ] On-screen text is concise (phrases, not paragraphs)
- [ ] Timing is animation-friendly (holds budgeted)
- [ ] Word count is within ±10% of target duration
- [ ] Animation mode is respected in writing style
- [ ] Research data points are integrated (if research_brief available)
- [ ] Mathematical accuracy is maintained (if applicable)
- [ ] Later stages can map scenes cleanly from this script

### Mid-Production Fact Verification

If you encounter uncertainty during script writing:
- Use `web_search` to verify factual claims before committing them to the script
- Use `web_search` to find reference images for visual accuracy
- Log verification in the decision log: `category="visual_accuracy_check"`

Every factual claim in the script should be traceable to the `research_brief`.
If you make a claim that isn't in the research, do additional research and
add the source. Do not invent statistics, dates, or attributions.

## Common Pitfalls

- **Writing too many ideas into one section.** One beat = one visual idea.
- **Treating captions and on-screen text as the same thing.** Subtitles are narration transcribed. On-screen text is designed content that's part of the animation.
- **Forgetting that motion needs pause and emphasis.** Budget hold times.
- **Ignoring the animation mode.** A Manim script reads differently than an AI video script.
- **Writing research-less scripts when a research_brief exists.** If the research found surprising data, use it. Generic scripts waste the research investment.
- **Oversimplifying math to the point of being wrong.** Check the research brief's accuracy notes.
