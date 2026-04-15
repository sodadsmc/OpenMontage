# Voice Generation Director - Narrated Documentary Pipeline

## When To Use

The segment plan is locked. You now generate voice-over audio for every
scene. The output feeds sentence boundary detection downstream and
becomes the backbone timing track for the entire assembly.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/voice_manifest.schema.json` | Artifact validation |
| Prior artifact | segment_plan | Scene narration text and durations |
| Tool | `elevenlabs_tts` | Primary TTS provider |
| Tool | `tts_selector` | Provider routing fallback |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Takes the narration text from each scene in the segment plan and
generates high-quality voice-over audio files. Handles pronunciation
dictionaries for technical terms, acronyms, and proper nouns common
in disaster documentation.

## Inputs

- **segment_plan** artifact: scenes array with narration text per scene

## Outputs

- **voice_manifest** artifact: version, segments array mapping scene_id
  to audio file path and measured duration

## Workflow

### 1. Build The Pronunciation Dictionary

Tech disaster narration is dense with acronyms and proper nouns that
TTS engines mangle. Before generating any audio, build a pronunciation
dictionary covering:

**Acronyms:** CSB, NTSB, OSHA, EPA, BSEE, FAA, NFPA, PSM, MOC, PHA,
SIS, ESD, DCS, PLC, HAZOP, LOPA, P&ID

**Organizations:** Bhopal, Piper Alpha, Deepwater Horizon, Macondo,
Texas City, Lac-Megantic, Chernobyl, Fukushima, Three Mile Island

**Technical terms:** blowout preventer, catalytic cracker, isomerization
unit, hydrofluoric acid, methyl isocyanate, casing centralizer

Map each to its spoken form:
- "CSB" -> "C-S-B" (spell it out, do not say "cuhsb")
- "NTSB" -> "N-T-S-B"
- "P&ID" -> "P and I D"
- "HAZOP" -> "HAZ-op" (two syllables)
- "Lac-Megantic" -> "Lack Meg-AN-tick"

Use ElevenLabs pronunciation dictionary features if available, otherwise
inject SSML hints or phonetic spelling into the narration text.

### 2. Select Voice And Settings

For disaster documentary narration, use a voice with:
- Male or female, authoritative but not dramatic
- Moderate pace (not rushed, not plodding)
- Neutral accent (General American or BBC English)
- Low emotional coloring (the facts carry the weight)

Recommended ElevenLabs settings:
- stability: 0.65-0.75 (enough variation to sound human, not enough
  to wander into dramatic reads)
- similarity_boost: 0.75-0.85
- style: 0.0-0.2 (minimal style — documentary, not audiobook)

### 3. Generate Audio Per Scene

For each scene in the segment plan:

1. Extract the narration text
2. Apply pronunciation dictionary corrections
3. Call `elevenlabs_tts` (or `tts_selector` if ElevenLabs is unavailable)
4. Save output to `projects/<name>/assets/audio/scene_<id>.mp3`
5. Measure actual duration via ffprobe
6. Compare actual duration to segment_plan estimated duration

If actual duration differs from estimated by more than 20%, flag it.
The assembly stage needs accurate durations for pacing.

### 4. Post-Generation Quality Check

Listen to (or analyze) each generated audio file for:

- **Mispronunciations:** Acronyms read as words instead of spelled out,
  place names with wrong stress, chemical names garbled
- **Artifacts:** Clicks, pops, unnatural pauses, repeated words
- **Pacing:** Rushing through complex technical passages, dragging on
  simple connective text
- **Consistency:** Voice character should not shift between scenes

If problems are found, regenerate the affected scene with adjusted
pronunciation hints or stability settings. Maximum two regeneration
attempts per scene before flagging to the user.

### 5. Emit The Voice Manifest

```json
{
  "version": "1.0",
  "segments": [
    {
      "scene_id": "scene_01",
      "audio_path": "projects/<name>/assets/audio/scene_01.mp3",
      "duration_seconds": 12.3
    }
  ]
}
```

## Quality Bar

- One audio file per scene, all files exist and play cleanly
- Actual durations measured (not estimated) and recorded
- Pronunciation dictionary applied before generation
- No audible mispronunciations of disaster-specific terms
- Total audio duration within 15% of segment_plan total

## Common Mistakes

- **Skipping the pronunciation dictionary.** "CSB" will be read as
  "cuhsb" by every TTS engine. Build the dictionary first.
- **Using dramatic voice settings.** Disaster documentaries need
  restraint. High style values produce audiobook drama that clashes
  with investigative footage.
- **Not measuring actual duration.** The segment_plan estimates are
  based on word count. Actual TTS pacing varies. The assembly stage
  needs real durations, not estimates.
- **Generating all scenes in one call.** Generate per-scene so you can
  replace individual scenes without re-doing the whole narration.
- **Ignoring cross-scene consistency.** If scene 3 sounds different
  from scene 4, the viewer notices. Use the same voice and settings
  throughout.

## Tools Available

- `elevenlabs_tts` — Primary TTS provider. Supports pronunciation
  dictionaries, voice selection, and stability/similarity controls.
- `tts_selector` — Routes to the best available TTS provider if
  ElevenLabs is unavailable. Adapts parameters transparently.
