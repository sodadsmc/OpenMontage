# Scene Director - Animation Pipeline

## When To Use

You are converting the script into a feasible animation plan. This is the stage that decides whether the project feels designed or chaotic.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["script"]["script"]`, `state.artifacts["proposal"]["proposal_packet"]` | Beat map and tool path |
| Playbook | Active style playbook | Palette, typography, motion consistency |

## Process

### 1. Make An Animatic-Minded Plan

For each scene, define:

- what appears first,
- what changes,
- what is held,
- how the scene exits.

Then tag the beat like a director. Beyond the cinematography pair already in `shot_language` (`shot_size` + `camera_movement`), set the Scene Library tags the beat earns: `rhythm`, `narration_mode`, `audio_transition`, `directors_move`, and `retention_beat`. Anything left blank is filled from the emotional-default mapping below — see the next subsection.

### 2. Limit Transition Families

Choose a small set of transition meanings:

- cut,
- fade,
- slide,
- transform.

### 3. Match Scene Type To Tool Path

Use:

- `diagram` scenes for structured explanation,
- `animation` scenes for motion-first sequences,
- `text_card` for clean high-impact copy moments,
- `generated` only where needed.

**For `image_animation` approach (anime/illustration style):**

Use `anime_scene` type for each scene. Plan:

- **Images per scene**: 2-3 images built from the same visual system and nearby seeds for crossfade effect
- **Camera motion**: choose from `zoom-in`, `zoom-out`, `pan-left`, `pan-right`, `ken-burns`, `drift-up`, `drift-down`, `parallax`, `static` — vary per scene to prevent monotony
- **Particle type**: choose from `fireflies`, `petals`, `sparkles`, `mist`, `light-rays` — match to scene mood
- **Lighting**: optional `lightingFrom`/`lightingTo` gradient for atmospheric shifts within the scene
- **Vignette**: `true` for cinematic framing (default), `false` for bright/open scenes
- **Scene duration**: 4-7 seconds per scene. Longer scenes need more images for crossfade variety.

**Scene variety rules for image_animation:**
- Don't use the same camera motion for consecutive scenes
- Alternate between warm and cool particle types
- Mix close-up and wide establishing shots
- Use overlays (`hero_title`, `section_title`) to add narrative structure

**JSON prop name mapping** (use these exact field names in the composition JSON):

| Concept | JSON Field | Example Values |
|---------|-----------|----------------|
| Camera motion | `animation` | `"zoom-in"`, `"pan-right"`, `"ken-burns"` |
| Particle effect | `particles` | `"fireflies"`, `"sparkles"`, `"mist"` |
| Particle color | `particleColor` | `"#FFE082"` |
| Particle density | `particleCount` | `20` (range: 1-50) |
| Particle brightness | `particleIntensity` | `0.5` (range: 0-1) |
| Lighting start | `lightingFrom` | `"rgba(255,200,100,0.15)"` or `"transparent"` |
| Lighting end | `lightingTo` | `"rgba(255,107,157,0.08)"` or `"transparent"` |
| Cinematic edge darken | `vignette` | `true` / `false` |
| Scene background | `backgroundColor` | theme-derived value such as `"#0A0A1A"` or `"#F6F1E8"` |

Reference: `remotion-composer/public/demo-props/mori-no-seishin.json` — 6 scenes using this pattern.
Reference: `remotion-composer/public/demo-props/deep-ocean.json` — 6 underwater scenes with different palette.

### 4. Scene Library — The Director's Touch

Tag every scene as a director would so the episode is layered and rhythmic, not a flat slideshow. These tags are all optional and additive — set what the beat earns, leave the rest blank. Full how/when reference: `skills/creative/scene-library.md`.

Per-scene tags this stage sets (in addition to `shot_language.shot_size` + `camera_movement`):

| Tag | Field | Vocabulary |
|-----|-------|------------|
| Rhythm | `rhythm` | `fast / medium / lingering / breath` (`breath` = held beat after a peak; pair with a silent beat) |
| Narration mode | `narration_mode` | `literal / evocative / none` — alternate them; never stay all-`literal` |
| Audio transition | `audio_transition` | `hard_cut / match_cut / match_on_action / j_cut / l_cut / sound_bridge / contrast_cut` |
| Director's move | `directors_move` | named reveal/withholding move (e.g. `visual_anchor_before_context`, `calibrated_cliffhanger`) |
| Retention beat | `retention_beat` | `cold_open / value_proposition / commitment_hook / pattern_interrupt / re_hook / cliffhanger / payoff / none` |

**Let emotion drive the shot.** When you leave `shot_size`/`camera_movement` blank, derive them from the scene's `narrative_role` / intent via the Scene Library mapping — camera moves are motivated by emotion, not decoration:

| Intent / role | shot_size | camera_movement | rhythm | narration_mode |
|---|---|---|---|---|
| tension / build_tension | `medium_close` | `dolly_in` (slow push) | fast | evocative |
| revelation / deliver_payload | `wide` | `dolly_out` (pull-back) | lingering | evocative |
| grief / emotional_beat | `close_up` | `static` (hold + drop to silence) | breath | evocative |
| teaching / evidence | `medium` | `static` | medium | literal |
| energy / escalation | `medium_close` | `handheld` (fast cuts) | fast | evocative |

Open the cold open with `retention_beat: cold_open → value_proposition → commitment_hook`, drop a `pattern_interrupt` every 30–90s, and make sure every planted tease fires (a `cliffhanger` earns its `payoff`). Respect the channel's HARD constraints — graphic-novel style, no legible on-screen text — when choosing moves.

### 5. Use Metadata For Timing Rules

Recommended metadata keys:

- `animatic_rules`
- `transition_rules`
- `hold_rules`
- `tool_path_map`
- `reusable_motifs`

### 6. Quality Gate

- every scene has a clear timing intent,
- the transition system is limited and meaningful,
- the tool path is explicit,
- the sequence feels like one designed system.

## Common Pitfalls

- Adding a new transition idea in every scene.
- Planning scenes that have no realistic production path.
- Overanimating text-heavy scenes.
