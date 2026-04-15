# Audio Analysis Director - Narrated Documentary Pipeline

## When To Use

Voice-over audio files are generated. You now detect sentence
boundaries within each audio file so the pacing engine knows exactly
where to place cuts, L/J-cuts, and breathing room. This is the bridge
between voice generation and assembly.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/sentence_map.schema.json` | Artifact validation |
| Prior artifact | voice_manifest | Audio files per scene with durations |
| Tool | `sentence_detect` | Primary sentence boundary detection |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Takes each voice-over audio file and detects sentence boundaries with
sub-second timestamps. The sentence map tells the assembly stage
exactly when each sentence starts and ends, enabling precise cut
timing aligned to natural speech rhythms.

## Inputs

- **voice_manifest** artifact: segments array with scene_id and
  audio_path for each scene

## Outputs

- **sentence_map** artifact: version, scenes array each containing
  sentences with text, start_seconds, and end_seconds

## Workflow

### 1. Run Primary Sentence Detection

For each audio file in the voice manifest, run `sentence_detect`:

```python
sentence_detect.execute({
    "audio_path": "projects/<name>/assets/audio/scene_01.mp3",
    "scene_id": "scene_01",
    "method": "energy_vad",
    "min_silence_ms": 200,
    "min_sentence_ms": 500,
})
```

The tool detects sentence boundaries by analyzing:
- Energy-based Voice Activity Detection (VAD) for pause detection
- Silence gaps >= 200ms as sentence boundaries
- Pitch contour drops that indicate statement endings
- Rising intonation for questions

### 2. Whisperx Fallback

If primary detection produces ambiguous results (e.g., fewer
sentences than expected, or boundaries that fall mid-word), fall
back to Whisperx alignment:

1. Run Whisperx transcription on the audio file
2. Align the transcribed words with the known narration text from
   the segment plan
3. Use word-level timestamps to reconstruct sentence boundaries
4. Cross-reference with punctuation in the original narration text

Whisperx is slower but produces word-level timestamps that make
sentence boundary detection more reliable for complex narration.

### 3. Validate Timestamp Quality

For each scene's sentence list, verify:

- **Monotonicity:** Each sentence's start_seconds > previous
  sentence's end_seconds (no overlaps)
- **Coverage:** First sentence starts within 0.5s of audio start,
  last sentence ends within 0.5s of audio end
- **Gap tolerance:** Gaps between sentences should be 0.1-1.0s
  (natural breathing pauses). Gaps > 2.0s suggest a missed sentence
  boundary
- **Accuracy:** Timestamps within 50ms of actual speech onset/offset

Flag any violations. The assembly stage trusts these timestamps for
cut placement — inaccurate boundaries produce jarring cuts.

### 4. Handle Edge Cases

- **Very short scenes (< 3s):** May contain only one sentence. This
  is valid — emit a single sentence spanning the full duration.
- **Long compound sentences:** The narration may have sentences that
  run 8-10 seconds. Do not split these artificially — the pacing
  engine handles long sentences by holding the footage clip.
- **Enumeration patterns:** "First... Second... Third..." patterns
  may not have full sentence pauses between items. Detect sub-sentence
  boundaries if the pause is >= 150ms.

### 5. Emit The Sentence Map

```json
{
  "version": "1.0",
  "scenes": [
    {
      "scene_id": "scene_01",
      "sentences": [
        {
          "text": "On the morning of March 23rd, 2005, workers arrived at the BP Texas City refinery.",
          "start_seconds": 0.0,
          "end_seconds": 5.2
        },
        {
          "text": "None of them knew that the isomerization unit had been running dangerously hot for days.",
          "start_seconds": 5.5,
          "end_seconds": 11.8
        }
      ]
    }
  ]
}
```

## Quality Bar

- Every scene has at least one sentence boundary
- Timestamps are monotonically increasing within each scene
- Gaps between sentences are 0.1-2.0s (no missing boundaries)
- First sentence starts within 0.5s of audio start
- Last sentence ends within 0.5s of audio end
- Sentence text matches the original narration (not re-transcribed)

## Common Mistakes

- **Using transcribed text instead of original narration.** The
  sentence text field should come from the segment plan, not from
  re-transcribing the audio. Re-transcription introduces errors.
- **Splitting on every pause.** Not every 200ms pause is a sentence
  boundary. Speaker hesitations and breathing pauses are normal.
  Use the original narration punctuation as ground truth.
- **Ignoring Whisperx fallback.** If primary detection misses obvious
  sentence breaks, the assembly will place cuts in the middle of
  sentences. Always check and use the fallback when needed.
- **Artificial splitting of long sentences.** A 10-second sentence is
  fine. The pacing engine holds the clip for the duration. Do not
  split it into fragments.
- **Not validating coverage.** If the sentence map covers only 80% of
  the audio duration, the remaining 20% has no cut guidance and the
  assembly will guess.

## Tools Available

- `sentence_detect` — Primary sentence boundary detection using
  energy-based VAD, pitch analysis, and silence detection. Produces
  sentence-level timestamps from audio files.
