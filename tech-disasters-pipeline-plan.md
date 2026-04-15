# Technology Disasters Automated Video Pipeline — Implementation Plan

## Project overview

Fork OpenMontage and extend it into a semi-automated production pipeline for narrated documentary-style YouTube videos about technology disasters. The pipeline takes a finished script as input and produces a rough-cut video with voice-cloned narration, semantically-matched stock footage, music, transitions, and captions — requiring only a 10-15 minute human review pass before upload.

The core differentiator versus generic AI video tools is a three-layer footage scoring system that produces editorially intelligent clip selection, combined with pacing-aware assembly that varies visual rhythm by narrative mood. Stock footage is the primary visual source (not AI-generated video), which provides both YouTube policy safety and a competitive moat against automated channels.

**Market context:** YouTube began demonetizing low-quality faceless AI channels in early 2026, using Gemini to cross-check content quality. OpenAI shut down Sora in March 2026 after six months. The successful model is now semi-automated: AI handles production mechanics while humans provide editorial direction and original insight. This pipeline is built for that model — the script is human-written, the pipeline automates production, and a 10-15 minute human review pass catches what automation misses.

## Architecture overview

```
Script (manual input)
  │
  ├─ Stage 1: Script Segmentation (Claude API)
  │    → Scene array with narration, pacing types, search queries,
  │      SigLIP descriptions, AI fallback prompts
  │
  ├─ Stage 2: Voice Generation (ElevenLabs API)
  │    → Per-scene audio files with exact durations
  │    → Sentence-level timestamps via silence detection
  │
  ├─ Stage 3: Footage Sourcing & Scoring (Hybrid pipeline)
  │    → Priority: disaster corpus (CSB, NTSB, TV News Archive pre-indexed clips)
  │    → Fanout: 19 stock sources (16 existing + 3 new government/archival)
  │    → Layer 1: Metadata text matching (sentence-transformers, local)
  │    → Layer 2: SigLIP 2 visual scoring (local GPU)
  │    → Layer 3: Gemini video review (API, tough calls only)
  │    → Supplemental: motion analysis, quality filtering, color extraction
  │    → Fallback: Runway Gen-4.5 for gaps
  │    → Output: ranked manifest with trim points and confidence scores
  │
  ├─ Stage 4: Assembly & Render (Remotion, from OpenMontage)
  │    → Pacing-aware clip placement with L-cut/J-cut offsets
  │    → Transitions varied by scene type
  │    → Music bed with ducking (existing AudioMixer.duck)
  │    → Word-level captions (existing CaptionOverlay component)
  │    → 1080p render (stock footage caps at 1080p on free tiers;
  │      4K requires premium sources or upscale in asset stage)
  │
  ├─ Stage 5: Human Review (manual, 10-15 minutes)
  │    → Flag/swap weak footage, adjust pacing, approve
  │
  └─ Stage 6: Export & Upload
       → Final render with color grade
       → YouTube metadata (SEO title, description, tags, chapters)
       → Chapter markers from scene timestamps
```

## What OpenMontage provides (no new code needed)

These components exist in the fork and work out of the box. Reference: OpenMontage repo structure — `tools/`, `remotion-composer/`, `lib/`, `pipeline_defs/`.

**The `documentary-montage` pipeline** (`pipeline_defs/documentary-montage.yaml`). This is our primary fork target — not a greenfield build on the framework. It already does: thematic brief → scene slots with search queries → corpus building from stock sources → CLIP-based retrieval → edit arrangement → Remotion/FFmpeg render. Our pipeline extends it with three-layer scoring, pacing-aware assembly, and voice-cloned narration. Forking this pipeline (rather than building from scratch) inherits source-preference routing, corpus architecture, and director skill structure.

**Remotion composition and rendering** (`remotion-composer/`). The entire React-based video composition system including clip placement, transitions, text overlays, spring-physics animations, and word-level TikTok-style captions. Renders headlessly via `npx remotion render`. This alone saves 2-3 weeks of development. Supports `<OffthreadVideo>` for efficient clip handling with `startFrom` for in-point and `trimBefore`/`trimAfter` for precise trimming. Available compositions: `Explainer`, `CinematicRenderer`, `TalkingHead`, `TitledVideo`, `HeroTitle`, `ProductReveal`, `CaptionOverlay`, `EndTag`.

**16 stock footage source adapters** (`tools/video/stock_sources/`). Far more than just Pexels and Pixabay — the repo has adapters for Pexels, Pixabay, Archive.org (Prelinger Archives), NASA, JAXA, ESA, NOAA, Wikimedia, Unsplash, Coverr, Mixkit, Videvo, Pond5 (public domain), Dareful, Library of Congress, and NARA (U.S. National Archives). All implement the `StockSource` protocol with `search()`, `download()`, `is_available()`. For tech disaster content, **NARA and Archive.org are gold mines** for historical archival footage. LOC and Wikimedia provide additional historical material. All 16 sources auto-register via `all_sources()` and feed into `corpus_builder`.

**ElevenLabs TTS** (`tools/audio/elevenlabs_tts.py`). Voice synthesis integration exists with voice ID selection, SSML support, pronunciation control, multilingual (eleven_multilingual_v2), and voice parameter tuning (stability, similarity_boost, style). We add voice cloning configuration on top.

**FFmpeg toolchain** (`tools/`). Audio mixing with sidechain ducking (`AudioMixer.duck`), full narration+music+normalize in one call (`AudioMixer.full_mix`), segmented music mixing, subtitle burn-in, color grading, encoding, format conversion. Battle-tested wrappers around ffmpeg commands.

**Subtitle/caption generation** (`tools/subtitle/subtitle_gen.py`). Word-level timing from transcriber output, SRT/VTT/JSON generation, karaoke-style word-by-word highlighting. The Remotion `CaptionOverlay` component consumes this format directly.

**Scene detection and frame extraction** (`tools/analysis/`). `SceneDetect` with content/threshold/adaptive methods (PySceneDetect + FFmpeg fallback). `FrameSampler` with interval, count, timestamp, and scene-guided extraction strategies. We extend this with motion analysis.

**Video analysis pipeline** (`tools/analysis/video_analyzer.py`). The `VideoAnalyzer` tool chains download → transcribe → scene-detect → keyframe-extract → motion-classify → audio-energy analysis into a single call. This is the key enabler for the disaster corpus pre-build (§1B.4): feed it a CSB investigation video URL and it returns segmented scenes with transcripts, keyframes, and motion classification — ready for `VideoTrimmer` to extract clips and `CorpusBuilder` to index them.

**CLIP-based corpus search** (`lib/clip_embedder.py` + `lib/corpus.py`). The documentary montage pipeline's semantic search system. `clip_embedder.py` uses `openai/clip-vit-base-patch32` (512-d L2-normalized vectors) with lazy-loaded torch/transformers. `corpus.py` provides an append-only local clip index (JSONL + `.npy` embeddings) with `rank_by_text()`, `knn()`, `diversify()` (MMR). `corpus_builder` fans out across all stock sources, downloads, extracts thumbnails, embeds, and indexes. We replace CLIP with SigLIP 2 but keep the corpus architecture.

**Existing scoring infrastructure** (`lib/scoring.py`). Provider selection scoring already exists with `ProviderScore` (7 dimensions: task_fit, output_quality, control, reliability, cost_efficiency, latency, continuity) and `ProductionPathScore`. Also `lib/slideshow_risk.py` for quality assessment and `lib/variation_checker.py` for scene diversity. Our three-layer scoring pipeline is additive to this — it handles footage selection, while the existing scoring handles provider/tool selection.

**Checkpoint system with custom stage support** (`lib/checkpoint.py`). The checkpoint system now resolves canonical artifacts from the pipeline manifest's `produces` field for custom stages (not just the 9 built-in stages). This means our 11-stage pipeline validates cleanly — each custom stage just needs to declare `produces: [artifact_name]` in the YAML manifest. Stages with no artifact output can omit `produces` entirely.

**Project structure and config** (root). Python setup, requirements management (including `requirements-gpu.txt` for torch), env handling via `python-dotenv`, Makefile for setup/build. AGPL-3.0 license (fine for personal use, no distribution concerns).

## What we build new — ordered by implementation priority

### Phase 1: Foundation (Week 1)

#### 1.1 Fork, clone, validate baseline

Clone OpenMontage, run `make setup`, configure `.env` with Pexels API key and ElevenLabs key. Run the `documentary-montage` pipeline on a test topic ("the Therac-25 radiation incidents") to establish baseline output quality. Document what works, what breaks, what's missing. This pipeline is our fork target — understanding its actual behavior (16-source fanout, CLIP retrieval, edit arrangement) is prerequisite for extending it.

Deliverable: working OpenMontage instance producing baseline documentary montage videos via the existing `documentary-montage` pipeline.

#### 1.2 ElevenLabs voice clone setup

Upload 30+ minutes of clean voice audio to ElevenLabs. Configure the voice clone ID in `.env`. Test synthesis quality on technical narration — specifically test acronyms (SCADA, TCP/IP), version numbers, and proper nouns that are common in technology disaster content.

Build a domain-specific pronunciation dictionary as a JSON file mapping problem terms to SSML-expanded forms. Example: "Therac-25" → "Therak twenty-five", "TCP/IP" → "T C P I P". This dictionary gets applied as a pre-processing step before text hits the ElevenLabs API.

Deliverable: voice clone that sounds natural on technology disaster narration with correct pronunciation of technical terms.

#### 1.3 Swap CLIP for SigLIP 2

The target file is `lib/clip_embedder.py`, which uses `openai/clip-vit-base-patch32` (512-d vectors) with lazy-loaded torch/transformers. Replace with SigLIP 2 So400m from Hugging Face Transformers.

SigLIP 2 achieves 94.5% R@1 on Flickr30K versus CLIP's ~86% (source: research doc, arXiv 2502.14786 and Hugging Face blog). The embedding generation and cosine similarity scoring logic remains identical — both produce normalized vectors in the same pattern. The swap is model loading and tokenizer, not architecture.

**Critical: embedding dimension change.** CLIP ViT-B/32 produces 512-d vectors; SigLIP 2 So400m produces 1152-d vectors. This invalidates all existing corpus `.npy` files (`embeddings.npy` and `tag_embeddings.npy` in `lib/corpus.py` are stored as raw numpy arrays with no dimension metadata). Any cached corpus must be fully re-embedded after the swap. Mixing old 512-d and new 1152-d embeddings will silently corrupt similarity scores.

**Input resolution change.** SigLIP 2 uses 384×384 input vs CLIP's 224×224, so per-frame GPU memory is ~3× higher. The 800MB VRAM estimate for model weights is correct (400M params × 2 bytes at fp16), but batch inference activations add overhead. Test actual VRAM usage with 5-frame batches on the RTX 2080 before committing to batch sizes.

**Calibration first.** Run the scoring prototype validation (§5.1) in Week 1, not Week 5. The R@1 improvement claim is on Flickr30K natural image captioning — stock footage metadata matching may see less lift. If SigLIP doesn't meaningfully beat CLIP on stock footage retrieval, the three-layer design still works; just keep CLIP at Layer 2 and save the model swap work.

Test on the same clips used in baseline to confirm scoring improvement.

Deliverable: SigLIP 2-powered semantic search replacing CLIP throughout the pipeline, with re-embedded corpus.

### Phase 1B: Archival Footage Sources (Week 1, parallel with 1.1-1.3)

The single biggest quality differentiator for a tech disaster channel is **real footage of real events** — not generic stock clips of server rooms. US government works are public domain (17 U.S.C. § 105), and several agencies produce high-quality investigation footage directly relevant to technology and industrial disasters. OpenMontage's `StockSource` protocol makes adding new adapters trivial (3 methods: `is_available()`, `search()`, `download()`).

#### 1B.1 CSB source adapter

New file: `tools/video/stock_sources/csb.py`

The U.S. Chemical Safety Board (CSB) produces the highest-quality government investigation videos — real incident footage, facility walkthroughs, witness interviews, and animated reconstructions. Their YouTube channel has 200+ investigation videos covering major industrial disasters: Deepwater Horizon, Texas City refinery explosion, West Fertilizer, Imperial Sugar dust explosion, Chevron Richmond fire, and dozens more. All public domain as US government works.

Implementation pattern: Uses `VideoDownloader` (yt-dlp wrapper already in `tools/analysis/video_downloader.py`) to search and download from CSB's YouTube channel. The adapter wraps yt-dlp's playlist/channel search with the `StockSource` protocol:

- `is_available()` → always `True` (no API key needed, just yt-dlp binary)
- `search(query, filters)` → `yt-dlp --flat-playlist --match-filter` on `youtube.com/@CSaborad/videos` with query terms, returns `Candidate` objects with video metadata (title, duration, thumbnail)
- `download(candidate, out_path)` → `yt-dlp -o out_path` at analysis quality (720p)

Priority: 10 (highest — check CSB before any generic stock source for disaster content).

**What CSB gives you that Pexels can't:** Real explosion footage, actual control room footage from investigations, on-site facility walkthroughs with investigators, CCTV footage of incidents, and high-quality animated reconstructions of failure sequences. For a video about "the Bhopal disaster" or "what caused the Deepwater Horizon explosion," CSB footage is editorial-grade.

#### 1B.2 NTSB source adapter

New file: `tools/video/stock_sources/ntsb.py`

The National Transportation Safety Board publishes investigation videos, hearing footage, and animated reconstructions for aviation, rail, pipeline, and marine incidents. Relevant to tech disasters: Boeing 737 MAX MCAS software failure, aviation autopilot incidents, pipeline SCADA failures. YouTube channel + website.

Same implementation pattern as CSB (yt-dlp wrapper). Priority: 15.

#### 1B.3 Archive.org TV News Archive adapter

New file: `tools/video/stock_sources/archive_tv_news.py`

The Internet Archive's TV News Archive (`archive.org/details/tv`) has full-text searchable transcripts of US television news broadcasts going back decades. The API allows searching by keyword and borrowing clips up to 60 seconds — exactly the right length for B-roll.

This is distinct from the existing `archive_org.py` adapter (which searches Prelinger Archives, open-source movies, and home movies). The TV News Archive has a different API endpoint and returns different metadata (transcript snippets, broadcast date, network, show name).

- `is_available()` → always `True`
- `search(query, filters)` → `https://archive.org/details/tv?q=<query>&output=json` returns clips with transcript context
- `download(candidate, out_path)` → download clip MP4 from the Archive's clip server

Priority: 20. **What this gives you:** Actual news broadcast footage of Chernobyl, Three Mile Island, the 2003 Northeast blackout, Challenger, Columbia, Bhopal, Y2K coverage — real anchors reporting on real events. For establishing shots and "this is what it looked like when it happened" sequences, nothing beats actual news footage.

#### 1B.4 Disaster corpus pre-build

Before the first production video, run a one-time corpus building pass across the new government sources. This uses existing tools in a specific sequence:

```
Step 1: VideoAnalyzer (download + scene-detect + transcribe + keyframe)
    → Process each CSB/NTSB investigation video into segments
    
Step 2: VideoTrimmer (extract individual scenes as standalone clips)
    → Each scene boundary from step 1 becomes a separate clip file
    
Step 3: CorpusBuilder (SigLIP embed + index)
    → Each clip gets visual + tag embeddings in the corpus
    → Tags populated from transcript text + video title + agency metadata
    
Step 4: footage_db.py (tag with disaster-specific metadata)
    → Incident name, year, disaster type (nuclear, software, chemical, etc.)
    → Agency source, legal status (public domain)
```

**Target:** Process ~200 CSB videos + ~50 NTSB videos + ~100 TV News Archive clips = ~3,000-5,000 indexed clips of real disaster footage. This corpus becomes the **priority search source** — the scoring pipeline checks it before hitting generic Pexels/Pixabay.

**Estimated processing time:** ~4-6 hours on the RTX 2080 (download + transcribe + scene-detect + embed). One-time cost. The corpus grows over time as new CSB/NTSB investigations are published.

**Integration with scoring pipeline:** The decision engine (§2.6) adds a source priority bonus: clips from the disaster corpus score higher than equivalent-quality generic stock footage. A real photo of the Therac-25 machine from NARA beats a Pexels "medical equipment" clip even if SigLIP similarity is comparable — the provenance carries editorial weight.

New file: `tools/scoring/source_priority.py` — simple lookup table mapping source names to priority multipliers. CSB/NTSB/NARA/NASA clips get a 1.15x boost. Archive.org TV News clips get 1.10x. Generic stock gets 1.0x. This is a soft nudge, not a hard filter — if the disaster corpus has nothing relevant, generic stock still fills the gap.

### Phase 2: Three-Layer Scoring Pipeline (Weeks 2-3)

This is the core intelligence of the pipeline and the primary quality differentiator. Reference the detailed scoring pipeline design from our conversation.

#### 2.0 Important: Layer 1 source-quality weighting

Stock footage metadata quality varies dramatically across the 16 source adapters. Pexels and Pixabay have rich tags and descriptions — Layer 1 text matching will work well. Archive.org tags are often just collection names; NARA metadata is sparse government cataloging. Layer 1 confidence thresholds should be weighted by source quality: trust Layer 1 more for Pexels/Pixabay descriptions, escalate to Layer 2 more aggressively for archival sources (Archive.org, NARA, LOC). The 60-70% resolution estimate at Layer 1 is realistic for well-tagged sources but optimistic for archival ones.

#### 2.1 Script segmentation prompt engineering

Create the Claude API prompt that takes a finished script and returns structured JSON per scene. Each scene object contains:

- `scene_id` — sequential identifier
- `narration` — the text for this scene
- `duration_seconds` — estimated from word count at 150 WPM (refined later by actual TTS output)
- `pacing` — one of: establishing, escalation, crisis, resolution
- `search_queries` — 5 stock footage queries expanding from specific to generic (tier 1-3)
- `visual_description` — rich prose for SigLIP scoring (not keywords)
- `ai_fallback_prompt` — Runway-ready prompt if stock footage fails
- `min_duration` — minimum acceptable clip length
- `preferred_duration` — ideal clip length (narration + breathing room)
- `mood` — for music bed selection (tension, neutral, dramatic, resolution)

The search queries use a tiered strategy: Tier 1 specific ("server room fire damage"), Tier 2 broader ("server room warning lights"), Tier 3 atmospheric ("dark data center hallway"). If Tier 1 returns fewer than 3 results, cascade to Tier 2, then Tier 3. This prevents the pipeline from stalling on obscure topics.

The visual description is deliberately different from search queries — it's rich prose that SigLIP can match against frames: "Dramatic wide shot of electrical infrastructure failing — sparking power lines, city lights going dark, or aerial view of a blacked-out urban area at night."

Store as a new tool in `tools/script/segment.py`.

**Footage availability pre-check.** Before committing to a full production run, the segmentation stage should do a quick Pexels/Pixabay probe on the 2-3 hardest visual concepts in the script. If a topic has thin stock coverage (e.g., "Therac-25 control panel" returns 0 hits across all tiers), flag it early — the production plan can pre-allocate more Runway gap-fill budget or adjust the visual approach before spending time and API calls on scoring. This takes seconds and prevents wasted work.

#### 2.2 Layer 1 — Metadata text matching

New file: `tools/scoring/layer1_metadata.py`

Uses `sentence-transformers` (all-MiniLM-L6-v2 model, runs on CPU). For each candidate clip returned by stock APIs, concatenate its tags/description into a text string, encode both that and the scene's `visual_description`, compute cosine similarity.

Decision thresholds (starting points, calibrate during testing):
- Above 0.80 → high confidence, download full clip, skip to approved pile
- 0.45 to 0.80 → ambiguous, download preview only, escalate to Layer 2
- Below 0.45 → discard immediately

Volume: with 16 source adapters, expect ~200-400 candidates per scene (5 queries × 16 platforms × variable results per platform — many will return 0 for niche queries). Layer 1 processes hundreds in under a second on CPU. Expect ~60-70% resolution at this layer for well-tagged sources (Pexels, Pixabay), lower for archival sources with sparse metadata.

#### 2.3 Layer 2 — SigLIP 2 visual scoring

New file: `tools/scoring/layer2_siglip.py`

Frame extraction strategy: extract frames at 10%, 25%, 50%, 75%, and 90% of clip duration using ffmpeg seeks. Batch-encode all 5 frames in a single GPU forward pass. Encode scene `visual_description` through SigLIP's text encoder. Compute cosine similarity per frame.

Score aggregation uses weighted max-mean blend:
```
clip_score = (0.4 × max_score) + (0.4 × top3_mean) + (0.2 × overall_mean)
```

This rewards clips where the best content matches strongly, while penalizing clips where only a single frame is relevant. Individual frame scores also determine trim points — the highest-scoring frames indicate where the best visual content lives in the clip.

Decision thresholds:
- Above 0.75 composite → strong match, add to approved pile
- 0.55 to 0.75 → moderate, escalate top 3-5 to Layer 3
- Below 0.55 → discard

Performance on RTX 2080 (8GB VRAM): SigLIP 2 So400m uses ~800MB VRAM at fp16. Processes ~60-80 frames/second. Five frames per clip = ~12-16 clips scored per second. Full video scoring pass (~300 candidates) takes ~20-25 seconds.

#### 2.4 Layer 3 — Gemini video review

New file: `tools/scoring/layer3_gemini.py`

Handles scenes where Layer 2's best clip scored in the moderate range, and scenes requiring narrative/tonal judgment that pixel-level similarity can't capture. Uses Gemini Pro which accepts video natively — no frame extraction needed.

Batch the top 3-5 candidates for a scene in a single request. Comparative ranking is more reliable than absolute scoring. Prompt asks for relevance (visual match), tonal fit (mood match for disaster documentary), technical quality, and usability (watermarks, text overlays, recognizable faces).

Cost: ~$0.02-0.03 per scene at Gemini 2.0 Pro pricing. Maybe 6-8 scenes per video need Layer 3. Total: $0.15-0.25 per video.

**Fallback when Gemini is unavailable.** If the Gemini API is down or rate-limited, don't silently accept Layer 2's moderate-confidence pick. Instead, flag those scenes for human review at Stage 5. This prevents quality regression from going unnoticed. Also: prompt version-control the Layer 3 prompt — comparative ranking is sensitive to wording and clip ordering. Include A/B infrastructure for prompt iteration.

#### 2.5 Supplemental scoring modules

**Motion analysis** — New file: `tools/analysis/motion_classify.py`

Sample 10 frame pairs per clip (~1 second apart), compute Farneback optical flow via OpenCV, classify dominant motion: static, slow_pan, fast_pan, tilt, zoom_in, zoom_out, handheld. Store as clip metadata. Reference: OpenCV `calcOpticalFlowFarneback()` for flow vectors, classify by analyzing directional uniformity vs divergence/convergence patterns. GitHub reference: antiboredom/camera-motion-detector.

Assembly logic consumes this: static clips can be trimmed anywhere, pan/tilt clips should complete their motion, handheld clips add energy but can't be slowed.

**Quality filtering** — New file: `tools/analysis/quality_filter.py`

Run BRISQUE (via pyiqa library) on sampled frames to filter low-resolution or overly compressed clips. Run PySceneDetect (`detect-adaptive`) to flag clips with internal shot changes — either discard or split into sub-clips. Reference: pyiqa GitHub (chaofengc/IQA-PyTorch) for quality metrics.

**Color palette extraction** — New file: `tools/analysis/color_extract.py`

Sample 5 frames, K-means clustering (k=5) on pixel values via scikit-learn. Store dominant colors as hex values. Feeds into coherence scoring for adjacent scenes and helps the assembly stage decide about color grading.

#### 2.6 Decision engine

New file: `tools/scoring/decision_engine.py`

Combines all scoring data into final clip selection per scene. Weighted composite:
```
base_score = (visual_score × 0.40) + (tonal_score × 0.25) +
             (duration_fit × 0.15) + (motion_suitability × 0.10) +
             (color_coherence × 0.05) + (source_priority × 0.05)

final_score = base_score × source_priority_multiplier
```

Source priority multiplier (from `source_priority.py`): CSB/NTSB/NARA/NASA footage gets 1.15x, Archive.org TV News gets 1.10x, generic stock gets 1.0x. This soft-boosts real disaster footage over generic equivalents — a real CCTV clip of a plant explosion beats a Pexels "industrial fire" clip at similar visual similarity.

Hard filters applied first: minimum duration, quality threshold, no internal shot changes, not in used-clips database.

Duration fit: peaks at 1.0 when clip duration equals preferred_duration, decays as mismatch grows. Clips shorter than min_duration already filtered.

Motion suitability varies by pacing type: establishing scenes prefer static/slow_pan, crisis scenes prefer handheld/fast_pan. Simple lookup table.

Color coherence: compares clip's dominant colors against adjacent scenes' selections. Large color temperature shifts get penalized. **This requires a two-pass approach**: first pass selects top candidates per scene ignoring coherence (can run in parallel), second pass re-ranks considering cross-scene color flow (sequential). Make this two-pass design explicit in the implementation — single-pass won't work because you can't score color coherence against adjacent scenes that haven't been selected yet.

#### 2.7 Used-footage database

New file: `tools/scoring/footage_db.py`

SQLite database tracking: clip_id, source platform, SigLIP embedding (cached), usage history (which videos, which scenes), download path (for local cache), metadata scores. Prevents duplicate footage across videos. Also serves as a FAISS-indexed search cache — before hitting any API, check if we've already scored that clip.

Over time this becomes a private footage index that returns instant results for common visual concepts without network calls.

#### 2.8 AI gap-filling via Runway

New file: `tools/video/runway_genfill.py`

Triggered when the best candidate for a scene scores below the threshold after all three layers. Uses the `ai_fallback_prompt` from Stage 1. Runway Gen-4.5 API generates a 5-10 second clip at the specified duration.

Prompt template enforces: shot type, subject, action, camera movement, duration, color grade (matching brand dark aesthetic), things to avoid (text, faces, brand logos). Budget: ~$0.50-1.00 per generated clip, targeting ~3-5 per video maximum.

### Phase 3: Pacing-Aware Assembly (Week 3)

#### 3.1 Audio waveform analysis for sentence boundaries

New file: `tools/audio/sentence_detect.py`

Analyze TTS audio files using `pydub` silence detection to find inter-sentence gaps. ElevenLabs inserts natural pauses between sentences — detect silences with min_silence_len=300ms and silence_thresh=-40dB. Each silence gap becomes a potential visual cut point.

If ElevenLabs doesn't produce clean gaps, fall back to `whisperx` for word-level forced alignment timestamps.

Output: per-scene JSON mapping sentence text to exact start/end timestamps within the audio file.

#### 3.2 Pacing-aware assembly logic

New file: `tools/assembly/pacing_engine.py` (generates Remotion composition data)

Consumes the scene manifest (from scoring pipeline) and sentence timestamps (from audio analysis). Produces a Remotion-compatible JSON (matching the `Explainer` composition's props interface) that defines every clip placement, trim point, transition, and timing offset. **Read the `Explainer` component's props TypeScript interface before designing this output schema** — the composition consumes structured props and the pacing engine must emit exactly that shape.

**Default: mute source clip audio.** All stock and archival clips are placed with `volume={0}` on `<OffthreadVideo>` in the Remotion composition. CSB investigation footage has narrators, NTSB hearing footage has spoken testimony, even Pexels clips often have ambient audio — any of this bleeding under the voice clone is a production-killing bug. The default is silent video; the only exception is L-cut ambient audio (rain, machinery hum, traffic) which is explicitly opted-in via the `edit_decisions.metadata.l_cuts` schema with a named `channel`. The pacing engine must emit `volume: 0` for every clip and `volume: 0.5-0.7` only for L-cut ambient layers routed through `AudioMixer` as SFX entries.

**Establishing scenes**: single clip, full duration, slow Ken Burns drift via CSS transform keyframes. 0.5s pre-roll (video starts before voice), 0.3s post-roll (video lingers after voice).

**Escalation scenes**: multiple clips at sentence boundaries, cuts getting slightly faster. Each sentence gets its own clip from the runner-up pool. Speed factor decreases slightly per cut (1.0 → 0.95 → 0.90) to create subtle acceleration.

**Crisis scenes**: rapid cuts at 2-3 second intervals, potentially faster than sentence boundaries. Minimal pre/post roll (0.1s). Clips may change mid-sentence for urgency.

**Resolution scenes**: single clip with deliberate breathing room. Longer pre-roll (0.8s), longer post-roll (0.5s). Slow, calm footage.

L-cuts and J-cuts: video starts 0.5-1.0s before narration begins (L-cut into scene) and narration stops 0.3-0.5s before video cuts (J-cut into next scene). These tiny offsets dramatically improve perceived quality. **OpenMontage already supports this** — no new Remotion work needed:

- The `documentary-montage` edit-director skill defines L-cuts as outgoing clip's ambient audio carrying 0.5-1.5s under the incoming clip, stored in `edit_decisions.metadata.l_cuts` with `carry_seconds` and `channel` per transition.
- The `video-stitching.md` skill documents J-cuts at 0.3-0.5s audio lead from the next clip under the current clip's video, with outgoing audio fading over 0.3-0.5s.
- `AudioMixer` implements this via the `start_seconds` parameter per track (FFmpeg `adelay` filter).
- The compose-director mixes L-cut SFX layers at 0.5-0.7 volume under music.

The pacing engine (§3.2) should emit L/J-cut decisions in the `edit_decisions.metadata.l_cuts` format, targeting the 3-4 hardest transitions per video. The edit-director skill notes this yields ~50% improvement in perceived coherence at those cut points.

**Aspect ratio normalization.** The final output is 16:9 (1920×1080), but source clips come in mixed ratios: NARA/Archive.org archival footage is often 4:3, CSB videos are 16:9 but may embed 4:3 source material, Pexels is mixed. The pacing engine must detect each clip's aspect ratio (from `Candidate.width`/`Candidate.height` stored in the corpus) and emit a framing strategy per clip:

- **16:9 clips** → direct placement, no transform
- **4:3 clips** → blur-pad (blurred, scaled copy of the frame as background behind the 4:3 clip). This looks significantly better than black letterbox bars for documentary content. Remotion can implement this with a `<AbsoluteFill>` background layer using CSS `filter: blur(20px); transform: scale(1.3)` behind the sharp 4:3 source.
- **Vertical clips** (rare from stock) → center-crop to 16:9 with slight Ken Burns to add motion
- **Near-16:9** (e.g., 1.85:1 cinema) → slight crop, no visible bars

Store the framing decision in the assembly manifest per clip so the Remotion composition can apply the right treatment. This is a per-clip CSS transform, not a pre-processing FFmpeg step — keeps source files untouched and the decision reversible.

Trim logic respects motion analysis: static clips can be trimmed anywhere, pan/tilt clips favor using the tail end (where camera settles), zoom clips should complete their zoom. Default strategy: set in-point at `(clip_duration - needed_duration)` to keep the natural ending.

"Too short" fallback chain: (1) concatenate top 2 clips with crossfade at a sentence boundary, (2) speed ramp down to 0.85x (imperceptible on non-human footage), (3) freeze-and-drift on last frame for up to 3 seconds, (4) request AI-generated gap fill.

All pacing multipliers stored in a `pacing_config.json` for easy tuning without code changes.

**Ken Burns on still images.** When the pipeline falls back to still images (or for establishing shots that use a single strong frame), use the existing `AnimeScene` Remotion component which already implements Ken Burns, zoom-in, zoom-out, pan-left/right, drift-up/down, parallax, and static camera motions. No new code needed — the pacing engine just emits the `animation` field in the composition JSON. Match motion type to pacing: establishing → `"ken-burns"` or `"drift-down"`, crisis → `"zoom-in"`, resolution → `"static"` or `"parallax"`.

**Speed ramping.** The "too short" fallback uses `playbackRate` (Remotion's built-in prop on `<OffthreadVideo>`) to slow clips to 0.85x. This is imperceptible on non-human footage like server rooms, infrastructure, and environmental shots — the primary visual content of tech disaster videos.

#### 3.3 Music bed integration

New file: `tools/audio/music_select.py`

Select music from a tagged library (Epidemic Sound at $15/month, or YouTube Audio Library for free start). Scene mood tags from Stage 1 drive selection: tension, neutral, dramatic, resolution.

Assembly script handles volume ducking under narration (-12dB to -18dB reduction when voice is active) and crossfades between music segments at scene boundaries. OpenMontage's existing `AudioMixer` handles the actual processing — `duck` for sidechain compression, `full_mix` for narration + music + ducking + normalize in one call, `segmented_music` for volume expression building with fade in/out at segment boundaries.

#### 3.4 Color grading pass

No new code needed. The existing `ColorGrade` tool (`tools/enhancement/color_grade.py`) applies uniform LUT across the rendered video. This is critical for mixed-era footage: archival NARA clips, modern Pexels stock, and AI-generated Runway clips all have different color profiles. A single `moody_dark` or `cinematic_cool` LUT pass smooths these differences and establishes a consistent visual register.

The documentary-montage compose-director already documents this as a mandatory step ("uniform LUT applied across the timeline"). Inherit that practice. Apply after the main Remotion render, before the final export.

**Blend intensity matters.** For tech disaster content with archival footage, use 0.6-0.7 intensity — enough to unify color temperature without destroying the historical look of archival material.

#### 3.5 End-tag card

No new code needed. The existing `EndTag` Remotion component renders a philosophical closing line on black with animated underline and shimmer. Two palettes: `cool_offwhite_on_black` (default) and `warm_ivory_on_black`. Supports overlay mode (alpha channel for compositing on final scenes) or concat mode (appended after body).

For tech disaster videos, use a closing line that reframes the disaster as a lesson — this is the editorial voice that separates the channel from generic automation. Example: "Every system failure is a failure of imagination." The script segmentation stage (§2.1) should output this as a `closing_line` field.

### Phase 4: Pipeline Orchestration (Week 4)

#### 4.1 New pipeline definition

New file: `pipeline_defs/narrated-documentary.yaml` (kebab-case to match convention)

YAML manifest forked from `documentary-montage.yaml`, defining the full stage sequence, tool assignments, quality gates, and human review checkpoints. The checkpoint system now supports custom stage names — each stage declares `produces: [artifact_name]` and the checkpoint validator resolves canonical artifacts from the manifest at runtime (no hardcoded stage list limitation).

Stages and their canonical artifacts:

| # | Stage name | `produces` | Human approval | Notes |
|---|------------|-----------|----------------|-------|
| 1 | `segment` | `[segment_plan]` | true | Script → structured scene JSON via Claude API |
| 2 | `voice_gen` | `[voice_manifest]` | false | ElevenLabs synthesis, parallel with step 3 |
| 3 | `footage_search` | `[footage_candidates]` | false | Fanout across all 16 stock sources |
| 4 | `scoring` | `[scoring_manifest]` | false | Three-layer scoring (metadata → SigLIP → Gemini) |
| 5 | `gap_fill` | `[gap_fill_report]` | false | Runway Gen-4.5 for gaps, conditional |
| 6 | `audio_analysis` | `[sentence_map]` | false | Sentence boundary detection from TTS audio |
| 7 | `music_select` | `[music_manifest]` | true | Mood-based music selection |
| 8 | `assembly` | `[assembly_manifest]` | true | Pacing engine → Remotion-compatible JSON |
| 9 | `render` | `[render_report]` | false | Remotion headless render |
| 10 | `review` | `[review_report]` | true | Human review gate (10-15 min), optional OTIO export |
| 11 | `export` | `[publish_log]` | false | Final render + YouTube metadata + chapters |

Each artifact will need a JSON schema in `schemas/artifacts/` for full validation. During development, the checkpoint system will still track these stages and enforce their ordering — artifacts that don't have schemas yet are silently skipped during schema validation (existing behavior).

**OTIO export at review stage (Phase 5 enhancement).** The research identifies OpenTimelineIO as the industry-standard interchange format. When the human review step reveals clips that need swapping, an OTIO/EDL export would let the reviewer open the timeline in DaVinci Resolve, make precise cuts, and re-export — far better than flagging timestamps in a text file. This is a quality-of-life feature for after v1 is working, but worth designing the assembly manifest with OTIO export in mind from the start (store in/out points, clip paths, and transition types in a format that maps cleanly to OTIO's data model).

#### 4.2 Claude Code orchestration (initial)

During development and early production, use Claude Code as the orchestrator following the pipeline YAML. This matches OpenMontage's native execution model and gets videos produced fastest while iterating on scoring quality.

Advantage: natural language interaction for debugging ("that clip in scene 7 doesn't work, find a better one"), easy parameter adjustment, and rapid iteration.

#### 4.3 Unattended execution (production, later)

**Revised assessment:** n8n migration is significantly more complex than initially scoped and should be deferred past v1. The current architecture relies on agent judgment at every stage (reading director skills, making creative decisions, handling degraded paths). n8n nodes execute deterministic logic. Extracting this would require: (a) rewriting every stage as a deterministic function, (b) HTTP/CLI wrappers for every Python tool (tools are classes with `.execute(params_dict)`, not APIs), and (c) complex conditional branching for the three-layer scoring cascade.

**Recommended v1 path:** Wrap the pipeline in a single Python CLI script that calls stages sequentially, using the existing checkpoint system for resume-on-failure. This gets unattended execution without the n8n refactor overhead. For a one-person production pipeline, this is sufficient.

**When n8n makes sense later:** If the pipeline needs visual workflow editing, multi-team coordination, or parallel production runs with different topics. Reference: n8n template #3442 (automated video generation + multi-platform publishing) and #2971 (faceless YouTube via Leonardo AI + Creatomate) for workflow patterns.

#### 4.4 Export stage — YouTube metadata generation

The export stage produces three outputs: the final rendered video (from the render stage, possibly with color grade applied), a YouTube metadata package, and chapter markers. The metadata is generated by a lightweight Claude API call that takes the original script + scene timestamps and produces:

- **Title** — YouTube-optimized: front-loaded keyword, under 60 characters, curiosity gap. Example: "The Bug That Killed 6 People: Therac-25" not "A History of the Therac-25 Radiation Therapy Incidents". The segmentation stage (§2.1) should output a `youtube_title` field with 2-3 options ranked by keyword strength.
- **Description** — First 2 lines are the hook (visible before "Show more"). Then: chapter timestamps, one-paragraph summary, sources cited with links, standard channel boilerplate. Auto-generated from the script sections mapped to scene timestamps.
- **Tags** — 15-20 tags extracted from: disaster name, technology category, key technical terms, broader topic tags ("engineering failure", "software bug", "industrial disaster"), and the channel's recurring tags.
- **Chapters** — Formatted from the scene timestamps: `0:00 Introduction\n0:32 The System\n1:15 What Went Wrong\n...`. Each chapter title is the scene's narrative beat, not its technical ID. Minimum 10 seconds per chapter (YouTube requirement).

Store as `publish_log` artifact with `title`, `description`, `tags[]`, `chapters[]`, and `category` fields. The human review stage (Stage 10) can adjust these before final upload.

### Phase 5: Calibration & Quality Tuning (Ongoing)

#### 5.1 Scoring prototype validation

Before the full pipeline is connected, validate the scoring approach in isolation.

Step 1: Write 5 scene descriptions (mix of easy and hard technology disaster topics). Manually search Pexels and download the top 20 clips per scene — 100 clips total. Rank them 1-20 per scene. This is ground truth.

Step 2: Run Layer 1 metadata matching. Measure how many top-5 picks land in the high-confidence band, and how many discards you would have kept.

Step 3: Run SigLIP 2 scoring on all 100 clips. Critical test: if SigLIP's top 3 overlaps with your top 5 for at least 4 out of 5 scenes, the approach works.

Step 4: For divergent scenes, send candidates to Gemini. Measure whether Layer 3 aligns better with human judgment.

Step 5: Tune thresholds based on data. Adjust Layer 1 cutoffs, SigLIP blend weights, and composite weights.

#### 5.2 Pacing calibration

Produce 5-10 test videos. Watch each one and note where pacing feels wrong — too fast, too slow, jarring transitions, clips that overstay their welcome. Adjust `pacing_config.json` multipliers: pre/post roll durations, crisis cut speeds, speed ramp limits, Ken Burns zoom percentages.

Research benchmarks for reference (from visual_aid_research.md): visual changes every 15-25 seconds for educational content, more frequent in first 3 minutes (every 10-20 seconds). Pattern interrupts in first 5 seconds correlate with 23% higher retention. Videos under 6 minutes see highest engagement. Faster speaking with genuine enthusiasm increases engagement.

#### 5.3 Footage quality feedback loop

After each batch of videos, review which clips scored well on SigLIP but looked wrong to human eyes (false positives), and which clips were discarded but would have been good (false negatives). Use these cases to refine the visual description prompt templates in Stage 1 — SigLIP responds dramatically to description specificity.

Also track which topics consistently lack good stock footage (feeding the "footage availability estimate" pre-production step discussed earlier) and which topics have abundant options.

## Monthly operating costs

| Component | Service | Monthly cost |
|-----------|---------|-------------|
| Script segmentation | Claude API | ~$8 |
| Voice generation | ElevenLabs Creator | $22 |
| Stock footage | 19 sources (16 existing + CSB, NTSB, TV News Archive) | Free |
| Visual scoring | SigLIP 2 on local RTX 2080 | Free |
| Tough-call scoring | Gemini Pro API | ~$5 |
| AI gap fills | Runway Gen-4.5 API | ~$30 |
| Music | Epidemic Sound | $15 |
| Rendering | Remotion (solo use) | Free |
| Orchestration | Claude Code / CLI script | Free |
| **Total** | | **~$80/month** |

At 4 videos per week (16/month), that's approximately $5 per video.

Future additions: Adobe Stock (40 assets/month, ~$80/month) would increase per-video cost to ~$10 but significantly improve footage quality and library depth. Storyblocks (annual plan, ~$25/month) adds unlimited downloads as a secondary premium source. **AP Archive** (pay-per-clip licensing, variable cost) would unlock actual news footage of specific events — the highest editorial quality available for disaster content.

## Hardware requirements

Existing RTX 2080 (8GB VRAM) is sufficient for the full pipeline.

SigLIP 2 So400m: ~800MB VRAM at fp16, processes 60-80 frames/second. Full scoring pass for a 20-scene video with ~300 candidates takes ~20-25 seconds.

PySceneDetect and OpenCV optical flow run on CPU.

Remotion rendering is CPU-bound (Chrome engine) with NVENC hardware encoding assist for final export. Render at 1080p — free-tier stock footage caps at 1080p. 4K would require premium stock sources (Adobe Stock, Storyblocks) or an upscale pass via enhancement tools in the asset stage.

Total pipeline execution time per video (estimated): 15-25 minutes automated processing + 10-15 minutes human review.

## File structure of new/modified code

```
OpenMontage/                          # Forked repo
├── tools/
│   ├── video/
│   │   ├── stock_sources/
│   │   │   ├── csb.py               # NEW — CSB investigation videos (yt-dlp, public domain)
│   │   │   ├── ntsb.py              # NEW — NTSB investigation videos (yt-dlp, public domain)
│   │   │   └── archive_tv_news.py   # NEW — Archive.org TV News Archive (news broadcasts)
│   │   └── runway_genfill.py         # NEW — Runway API gap filling
│   ├── script/
│   │   └── segment.py               # NEW — Claude API script segmentation
│   ├── scoring/
│   │   ├── layer1_metadata.py        # NEW — Metadata text matching
│   │   ├── layer2_siglip.py          # NEW — SigLIP 2 visual scoring
│   │   ├── layer3_gemini.py          # NEW — Gemini video review
│   │   ├── decision_engine.py        # NEW — Final clip selection (two-pass: select then coherence)
│   │   ├── source_priority.py        # NEW — Source provenance priority multipliers
│   │   └── footage_db.py            # NEW — SQLite used-footage cache
│   ├── analysis/
│   │   ├── motion_classify.py        # NEW — Camera motion detection
│   │   ├── quality_filter.py         # NEW — BRISQUE + scene detection (defer to Phase 5)
│   │   └── color_extract.py          # NEW — Color palette extraction (defer to Phase 5)
│   ├── assembly/
│   │   └── pacing_engine.py          # NEW — Pacing-aware assembly logic
│   ├── audio/
│   │   ├── sentence_detect.py        # NEW — Audio waveform sentence detection
│   │   ├── music_select.py           # NEW — Mood-based music selection
│   │   └── pronunciation_dict.json   # NEW — Technical term pronunciations
├── pipeline_defs/
│   └── narrated-documentary.yaml     # NEW — Pipeline definition (forked from documentary-montage)
├── skills/
│   └── pipelines/
│       └── narrated-documentary/     # NEW — Director skills for each stage
│           ├── segment-director.md
│           ├── voice-gen-director.md
│           ├── footage-search-director.md
│           ├── scoring-director.md
│           ├── gap-fill-director.md
│           ├── audio-analysis-director.md
│           ├── music-select-director.md
│           ├── assembly-director.md
│           ├── render-director.md
│           ├── review-director.md
│           └── export-director.md
├── schemas/
│   └── artifacts/
│       ├── segment_plan.schema.json   # NEW — Schema for segment stage output
│       ├── voice_manifest.schema.json # NEW — Schema for voice gen output
│       ├── footage_candidates.schema.json # NEW — etc.
│       ├── scoring_manifest.schema.json
│       ├── gap_fill_report.schema.json
│       ├── sentence_map.schema.json
│       ├── music_manifest.schema.json
│       ├── assembly_manifest.schema.json
│       └── review_report.schema.json
├── config/
│   └── pacing_config.json            # NEW — Tunable pacing parameters
├── data/
│   └── footage.db                    # NEW — SQLite footage cache (generated)
└── lib/
    └── clip_embedder.py              # MODIFIED — Swap CLIP ViT-B/32 → SigLIP 2 So400m (512-d → 1152-d vectors)
```

Modified files: `lib/clip_embedder.py` (model swap), `lib/checkpoint.py` (already done — custom stage support). All other code lives in new files. `pipeline_defs/narrated-documentary.yaml` is forked from `documentary-montage.yaml` — inherits structure but redefines stages. This keeps the fork clean and allows pulling upstream changes without merge conflicts.

**New dependencies to add:**

| Package | Used by | Add to |
|---------|---------|--------|
| `sentence-transformers` | Layer 1 metadata matching | `requirements-gpu.txt` |
| `google-generativeai` | Layer 3 Gemini review | `requirements.txt` |
| `pyiqa` | BRISQUE quality filter (Phase 5) | `requirements-gpu.txt` |
| `opencv-python-headless` | Optical flow motion analysis | `requirements.txt` |
| `runway-python` | Runway Gen-4.5 gap filling | `requirements.txt` |
| `faiss-cpu` | FAISS indexing in footage DB | `requirements.txt` |

## Build order summary

| Week | Focus | Key deliverable | Risk notes |
|------|-------|-----------------|------------|
| 1 | Foundation + archival sources | Working fork with SigLIP 2, voice clone, baseline video, CSB/NTSB/TV News adapters, disaster corpus pre-build (runs overnight) | Run SigLIP calibration (§5.1) this week, not Week 5. Adapters are ~100 lines each following the existing pattern. |
| 2 | Scoring pipeline (Layers 1-3 + decision engine) | Three-layer scoring producing ranked clip manifests, with source priority boosting disaster corpus | Defer supplemental modules (motion, quality, color) to Phase 5. Layer 3 Gemini prompt needs iteration. |
| 3 | Assembly + pacing engine | Pacing-aware Remotion compositions with music, color grade, end-tag | L/J-cuts use existing AudioMixer + edit_decisions schema — no Remotion changes needed. Main risk is pacing config tuning. |
| 4 | Orchestration + pipeline YAML | Full pipeline running end-to-end via Claude Code | Realistic only if Weeks 2-3 don't slip. Pipeline YAML + director skills are straightforward given the documentary-montage template. |
| 5+ | Calibration + supplemental modules + corpus growth | Threshold tuning, motion/quality/color scoring, pacing refinement, expand disaster corpus with new CSB/NTSB releases | Add BRISQUE, optical flow, color extraction here — quality refinements, not blockers. AP Archive adapter as paid upgrade. |

**De-risk Week 1:** The main thing that can derail the timeline if discovered late: SigLIP not meaningfully beating CLIP on stock footage retrieval. Run the calibration protocol (§5.1) in Week 1, not Week 5. If SigLIP doesn't outperform CLIP on your test set, keep CLIP at Layer 2 and save the model swap work — the three-layer architecture still works either way. L/J-cuts were previously flagged as a risk but are already implemented in the documentary-montage pipeline's edit-director skill and AudioMixer tool.

First video target: end of Week 4. Production cadence target: 2-4 videos per week by Week 6, after calibration stabilizes scoring quality.
