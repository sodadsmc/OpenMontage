# Pre-Production Pipeline Automation for "Technology Disasters": Existing Tools, Schemas, and Automation Gaps

## TL;DR
- **No single existing system covers your 6-stage VO-first, asset-consistency-driven documentary pipeline end-to-end — but roughly two-thirds of the components exist as adoptable open-source parts, and the right architecture is a thin n8n + Claude orchestration spine over four mature primitives: Fountain (script), WhisperX/ElevenLabs Scribe (timing spine), a custom asset-bible JSON, and OpenTimelineIO (shot manifest).** The genuinely novel work is concentrated in exactly two stages: the Asset Bible (G4) and the timing-spine→shot-duration propagation.
- **The closest end-to-end analog is ViMax (HKUDS, MIT license, 8.7k stars / 1.3k forks per the GitHub HKUDS org page updated June 1, 2026), which already implements your exact agent chain (Character Extractor → Reference Image Selector → Consistency/Continuity → Storyboard → shot planning) — adopt it as the reference architecture and inspiration, not as-is, because it is generation-coupled and its internal schemas are undocumented.** OpenMontage (your existing fork's upstream) is the best structural match to your Claude Code + contracts/ + JSON-schema build pattern.
- **The gates are partially automatable today: script-quality (LLM-as-judge) and manifest-completeness (JSON-schema validation) can be fully automated now; dossier fact-checking and asset-bible drift-prevention validation need a human-in-the-loop for at least the first dozen episodes.** Visual reference sourcing and "does this spec actually prevent drift" remain the weakest tooling areas and your highest custom-build cost.

## Key Findings

1. **End-to-end systems exist but none fit.** Commercial AI-filmmaking suites (Studiovity, Shai, Boords, StoryboardCanvas, mStudio, DeepFiction) cover script→storyboard→shot-list but are closed SaaS, generation-coupled, and lack the verified-dossier and asset-bible stages. Open-source production trackers (Kitsu/Zou, OpenTimelineIO) provide the data backbone. Agentic research frameworks (ViMax, VideoAgent, OpenMontage) provide the orchestration pattern.
2. **Per-stage best-of-breed is mostly open-source and API-friendly**, and maps cleanly onto your n8n + Claude stack.
3. **You should not invent your own schemas for 3 of 6 artifacts.** Adopt Fountain (script), OpenTimelineIO (shot manifest/timing), and lean on USD concepts (asset identity) — invent custom JSON only for the dossier, sequence map, and asset bible.
4. **The timing-spine pattern is well-supported.** WhisperX (faster-whisper + wav2vec2 forced alignment) and ElevenLabs Scribe v2 both give word-level timestamps; this is a solved input.
5. **Gate automation is realistic for script and manifest stages; weakest at dossier and asset-bible.**
6. **The two true gaps: programmatic historical-reference sourcing, and machine-validation that an asset spec prevents drift.**

## Details

### Research Goal 1 — End-to-End Systems

**ViMax (github.com/HKUDS/ViMax) — your single most important reference.** Open-source (MIT), Python 3.12, **8.7k stars / 1.3k forks** (per the GitHub HKUDS org repositories page, updated June 1, 2026), from the University of Hong Kong data-science lab. It is a multi-agent framework whose pipeline mirrors yours almost exactly: a Script Understanding layer (Character/Environment Extraction, Scene Boundaries, Style Intent) → Scene & Shot Planning (Storyboard Steps, Shot List, Key Frames & Beats) → Visual Asset Planning (Reference Image Selection, Look/Style Guidance) → Asset Indexing (Frames/Refs Catalog, Embeddings, Retrieval for Reuse) → Consistency & Continuity (Character/Environment Tracking, Ref Matching) → Visual Synthesis. Per PyShine's write-up, "ViMax orchestrates 12 specialized agents that collaborate like a real film production team: a Screenwriter, Storyboard Artist, Character Extractor, Reference Image Selector, Best Image Selector, and more — all coordinated by a central orchestration layer." It is configured by YAML (`configs/idea2video.yaml`, `configs/script2video.yaml`) using a `class_path` + `init_args` plugin pattern, writes artifacts to a `working_dir` (default `.working_dir/idea2video`), and is invoked via `main_idea2video.py` / `main_script2video.py` with `idea`/`script`, `user_requirement`, and `style` string inputs. **Assessment: inspiration-and-fork.** It proves your architecture is sound and gives you a working agent decomposition to copy. Caveat from primary-source verification: ViMax's *internal* data structures (the exact fields of its character/asset records and shot manifest) are **not documented** and could not be confirmed from the repo's rendered pages — you would need to `git clone` and read `agents/`, `interfaces/`, and `pipelines/` directly. Also be warned that several third-party blogs (atomixweb, pixel4it) publish **fabricated** ViMax APIs (e.g., a `ViMaxOrchestrator.produce_video()` call and a "visual profile vector" consistency module) that do not match the real repo; do not design against them.

**OpenMontage (github.com/calesthio/OpenMontage) — closest match to your build pattern.** Since you already run an OpenMontage fork (Remotion-based), this is high-leverage: it is explicitly architected as "your AI coding assistant IS the orchestrator," with YAML pipeline manifests (the playbook), Markdown stage-director skills, Python tools, **15 JSON Schemas for contract validation**, a reviewer skill that does schema validation + playbook compliance + quality checks, JSON checkpointing (resumable with a decision log), and human approval gates at every creative decision. This is essentially your "/ultraplan + managed-agents from contracts/" pattern already implemented for video. **Assessment: fork-and-extend** — its schema/skill/checkpoint trichotomy is the template for your contracts/ folder.

**VideoAgent (github.com/HKUDS/VideoAgent)** — a graph-powered agentic framework with a Storyboard Agent that decomposes input into "fine-grained sub-queries that are both visually and semantically aligned," plus self-reflective evaluation loops; reports 87–98% task success and uses an "agent registry" producing "JSON schema formatted registration tables." **Assessment: inspiration** for the gate/self-reflection loop.

**"The Script is All You Need" (arXiv 2601.17737)** — an agentic dialogue-to-cinematic framework with a **CriticAgent** (powered by Gemini 2.5 Pro) that scores generated scripts on Format Compliance (strict adherence to required JSON with fields like "Shot Type", "Camera Movement", "Description"), plus a Frame-Anchoring mechanism for cross-scene consistency. **Assessment: inspiration** for your G2/G5 gates and JSON shot fields.

**Production trackers (adopt as backbone, optional):**
- **Kitsu / Zou (github.com/cgwire/kitsu)** — open-source (AGPL/LGPL), self-hostable production tracker for animation/VFX. Backend "Zou" exposes a full HTTP REST API with JWT auth, WebSocket real-time streaming on port 5001, bulk import/export, and an official Python client **Gazu**. It models projects→sequences→shots→assets and "lets you list which asset belongs to which shot" — i.e., it natively stores your asset-to-scene mapping. Chat integrations to Discord/Slack/Mattermost exist. **Assessment: adopt-as-is if you want a UI/database for human review gates; otherwise inspiration.** The self-hosted core is free and open-source; CGWire's paid plans are for managed hosting.
- Commercial trackers (ftrack, Autodesk Flow/ShotGrid) are the proprietary equivalents — **inspiration-only** given your self-host preference.

**Commercial AI-filmmaking SaaS (inspiration-only — closed, generation-coupled):** Studiovity (30+ shot parameters, script→shot-list sync, Excel/PDF export), Shai Creative (script→storyboard→shotlist, accepts FDX), Boords (script import, **REST API + signed webhooks**, shot-list/CSV/XLSX/DOCX export, After Effects animatic export), StoryboardCanvas (20-app suite, launching 2026), mStudio, DeepFiction. Boords is the only one with a documented API + webhooks worth integration testing; the rest are reference designs for field structures.

### Research Goal 2 — Per-Stage Best-of-Breed

**Stage 0 / G1a — Research dossier (factual spine):** Your existing n8n content engine + Claude Opus already generates research briefs; this is your most mature stage. For the verification half, the academic state of the art is RAG + in-context-learning fact-checkers: **OpenFactCheck (github.com/yuxiaw/openfactcheck)** is a unified, customizable open-source framework (CustChecker for verifying free-form documents/claims, LLMEval, CheckerEval). **LLM-Cite** (URL-generation + entailment, no external KB) is a cheap attribution method. Claude Sonnet with web-search tool use + structured claim-extraction is the pragmatic implementation. **Assessment: adopt OpenFactCheck as inspiration/library; build the dossier verifier as a Claude-Sonnet claim-extraction→search→entailment loop in n8n.**

**Stage 0 / G1b — Visual reference sourcing (WEAKEST TOOLING — high custom-build):**
- **Open archive APIs:** Smithsonian Open Access API (api.data.gov key) provides "easier access to more than **5.1 million 2D and 3D digital items** from our collections… across the Smithsonian's 21 museums, nine research centers, libraries, archives, and the National Zoo" as CC0, with image URLs in JSON metadata and a weekly-refreshed GitHub data repo (the AWS Open Data registry lists "Over 11 million metadata records" as a superset including items without media) — directly relevant for US tech/engineering artifacts. Library of Congress Prints & Photographs (PPOC), Europeana, NYPL Digital Collections, and Wikimedia Commons all expose programmatic access. For Therac-25-class subjects, these cover period medical/industrial equipment.
- **Reverse image / provenance:** TinEye API (paid, ~$200/5,000 searches, no free tier, best for exact-match/provenance); Google Cloud Vision (official, no true "Lens" API); unofficial Google Lens via Apify actors (visual match, exact match, OCR; ~$5/mo free credit; callable via REST/Python/MCP with webhook support).
- **Organization/tagging:** PureRef (free, .pur files embed images, infinite canvas, but **no API and no headless mode** — a human-facing tool, not automatable).
- **Assessment:** No turnkey "source historical references for every physical thing in the episode" system exists. **Build custom:** an n8n flow that takes asset descriptions from the dossier, queries Smithsonian/LoC/Europeana/Wikimedia APIs, runs candidates through your existing **SigLIP 2 visual scorer** for relevance ranking, and writes ranked reference URLs into the dossier JSON. This reuses an asset you already own and is the single highest-value custom module.

**Stage 1 / G2 — Scriptwriting + voice/style:** Claude (your stack) writing to your `writing_style_profile.md` + `example_bank.md` with banned-word/-pattern constraints is the correct approach. Adopt **Fountain** (fountain.io) as the script format — plain-text, Markdown-like, with mature parsers: **screenplay-tools (github.com/wildwinter/screenplay-tools)** is multi-language (Python/JS/C#/C++) and supports **both Fountain and Final Draft FDX**; **Jouvence** (pip install jouvence) is pure-Python. **Assessment: adopt Fountain + screenplay-tools as-is.**

**Stage 2 / G3 — Script→scene segmentation:** No specialized tool needed; this is a Claude structured-extraction task over the Fountain script (segment into numbered scenes with beat/setting/constant-assets annotations). Fountain parsers give you scene-heading boundaries for free as a deterministic pre-pass; Claude does the beat/asset annotation. **Assessment: build with Claude + screenplay-tools; inspiration from ViMax's "Scene Boundaries" agent and its RAG-based long-script segmentation engine.**

**Stage 3 / G4 — Asset Bible (THE CRITICAL GATE; custom-build):**
- **Human-side conventions to adopt:** the animation "show bible" / "model sheet" / "character sheet" tradition (StudioBinder, No Film School, Screen Australia transmedia-bible template) gives you the field vocabulary — but these are pitch/prose documents, not machine contracts.
- **AI-consistency mechanisms (specification-level, not generation):** The field has converged on a layered model — (1) a **canonical reference image** per asset, (2) **locked attribute lists** (silhouette/proportions/color/signature details), (3) identity encoded via reference-conditioning (IP-Adapter) or weights (LoRA/IC-LoRA). Your asset bible operates at the *specification* layer that feeds these. The most rigorous published schema is **VideoGen-of-Thought (VGoT, arXiv:2412.02259, code github.com/DuNGEOnmassster/VideoGen-of-Thought)**, which (per its abstract) generates "detailed, cinematic specifications across five domains (character dynamics, background continuity, relationship evolution, camera movements, HDR lighting)" — the tuple `p_cha`, `p_b`, `p_r`, `p_cam`, `p_h` — with a self-validation function gating each shot on a narrative-coherence similarity threshold (τ_c = 0.85) and a rule-based constraint-completeness check (τ_k = 1), plus Identity-Preserving Portrait (IPP) tokens that "keep character identity while allowing controlled trait changes." VGoT reports that it "surpasses strong baselines by **20.4% in within-shot face consistency and 17.4% in style consistency**, while requiring 10x fewer manual adjustments… than alternatives like MovieDreamer and DreamFactory," and defines the drift metrics WS-FC/CS-FC (within-/cross-shot face consistency) and WS-SC/CS-SC (style consistency).
- **Assessment: build a custom "Asset Bible JSON" schema** (Asset ID, description, factual_anchor {reference_image_url, period_constraints}, locked_attributes[], canonical_reference_image, appears_in[scene_ids]). Borrow VGoT's threshold-gated self-validation idea (τ_c/τ_k) for the gate, and borrow the WS/CS consistency-metric framing for drift measurement. This is the artifact with the least off-the-shelf support and where your architecture-first, contracts/-folder approach pays off most.

**Stage 4 / G5 — Storyboard:** AI storyboard generators with structured export: **Boords** (REST API + webhooks, shot-list export, character-reference consistency), Studiovity, Shai, Storyboard Pro (Toon Boom — CSV export/import with a per-panel 16-hex-char Object ID, round-trippable). **Assessment:** for an automated faceless channel, you do not need a drawing tool — generate storyboard panels as structured JSON (one entry per shot: composition, assets_in_frame[Asset IDs], motion intent, narration_line) and optionally render thumbnails in **Excalidraw** (which you already use). Borrow Storyboard Pro's stable-panel-ID convention. **Build custom; Boords as integration fallback / field-structure reference.**

**Stage 5 / G6 — Shot manifest (adopt OpenTimelineIO):** **OpenTimelineIO (OTIO, github.com/AcademySoftwareFoundation/OpenTimelineIO)** — Academy Software Foundation, Apache-2.0, C++ core with idiomatic Python bindings (pip install OpenTimelineIO; current release **0.18.1, published Nov 9, 2025**, requires Python >3.9.0; note PyPI still lists it as "Development Status: 4 - Beta," not "Production/Stable"). It is the industry interchange for editorial cut/timeline data: a clip carries name, source_frame_range, and an **arbitrary nested `metadata` dict** (the docs explicitly show `clip.metadata["mystudio"] = {"shotID":..., "takeNumber":..., "department":..., "artist":...}`) — meaning you can store every field of your shot manifest (Shot ID, parent scene, VO line, in/out timecode, duration, setting, Asset IDs, composition, motion start/end, mood/lighting flags) as namespaced metadata while getting real timeline semantics for free. ".otio" JSON is the lossless native format; adapters exist for FCP XML, AAF, CMX3600 EDL. **Assessment: adopt OTIO as-is for G6.** It is JSON-native, integrates with your Remotion/OpenMontage layer, and is the correct "canonical handoff" container. Your "showrunner JSON" pattern can be the namespaced metadata payload.

### Research Goal 3 — Schemas & Interchange Formats (what to adopt vs. invent)

| Artifact | Adopt | Verdict |
|---|---|---|
| Narration script (G2) | **Fountain** (.fountain) + screenplay-tools/Jouvence parsers | Adopt-as-is |
| Script ↔ FDX interop | **FDX** via screenplay-tools | Adopt if needed |
| Shot manifest / timing (G6) | **OpenTimelineIO** (.otio JSON, namespaced metadata) | Adopt-as-is |
| Asset identity concepts (G4) | **USD** (OpenUSD) — asset referencing, variants, stable identity, override layering | Inspiration-only (USD is a 3D scene-graph; too heavy, but its *referencing/variant/stable-ID model* is the right mental model) |
| Research dossier (G1) | none standard | Invent custom JSON |
| Asset bible (G4) | none standard (VGoT tuple as inspiration) | Invent custom JSON |
| Sequence map (G3) | none standard | Invent custom JSON (or scene-list inside OTIO) |

USD (Pixar/AOUSD, openusd.org, github.com/PixarAnimationStudios/OpenUSD) is worth understanding conceptually: its core problem — "the same asset, referenced into many scenes, edited non-destructively via override layers, with stable identity" — is *exactly* your "same machine in scene 1 and scene 5" problem, solved at the specification level for 3D. You will not adopt USD itself (it's a 3D scene-graph with composition arcs), but its **asset-referencing + variant + stable-namespace-path model** is the design pattern your Asset Bible should imitate: define each asset once, reference it by stable Asset ID everywhere, never redefine.

### Research Goal 4 — The Timing-Spine Pattern (solved)

Word-level timestamps from recorded VO are a solved input:
- **ElevenLabs Scribe v2** (your stack already has Scribe) — API with `timestamps_granularity: word` (or `character`), batch up to 10 hours with webhook notifications, keyterm prompting (up to 1000 terms — useful to lock technical terms like "Therac-25"), JSON output with per-word start/end. **Adopt — you already pay for it.**
- **WhisperX (github.com/m-bain/whisperX, BSD)** — faster-whisper + wav2vec2 forced alignment → ±50ms word timestamps (vs Whisper's ~500ms drift), JSON/SRT/VTT, ~70× real-time on a 4090. Self-hostable, free. **Adopt as the self-hosted alternative/fallback to Scribe.** (Newer **easytranscriber** from KBLab claims 35–102% speedup over WhisperX with GPU-accelerated forced alignment.)
- **Pipeline pattern:** lock script (G2) → record VO with ElevenLabs voice clone → run Scribe/WhisperX → per-line start/end timecodes become the authoritative durations written into the OTIO shot manifest (G6). No tool derives shot *lengths* from VO automatically — **this propagation logic (VO line → shot duration → OTIO clip range) is a small but custom n8n/Python module.** It is the second of your two genuinely novel build items.

### Research Goal 5 — Automating the Gates

Rubric-based **LLM-as-judge** is mature (G-Eval, Prometheus, MT-Bench lineage) and directly applicable, but research flags systematic **position bias** and **self-preference bias** in rubric scoring — mitigate with balanced-permutation scoring and by using a different model family as judge than as author (e.g., author with Claude, judge with a second model, or vice-versa).

| Gate | Automatable now? | How |
|---|---|---|
| G1 dossier (facts) | Partial — human-in-loop | Claude claim-extraction → web-search entailment (OpenFactCheck pattern); humans confirm for first ~12 episodes; auto-flag unsupported claims |
| G1 dossier (refs) | Partial | SigLIP 2 relevance score threshold + human spot-check |
| G2 script | **Yes** | LLM-as-judge rubric (voice-profile adherence, banned-word/pattern regex = deterministic hard-fail, pacing, factual-alignment to dossier) |
| G3 sequence map | **Yes** | Schema validation + LLM check that every scene maps to script lines and lists constant assets |
| G4 asset bible | Partial — **hardest** | Schema completeness = automatable; "does this spec prevent drift?" = needs human or a generate-test-loop (render 2 candidates, score consistency with SigLIP 2 / face-consistency metric à la VGoT WS-FC/CS-FC) |
| G5 storyboard | **Yes** | Schema validation: every shot has composition + Asset IDs (resolvable against bible) + narration line + motion intent |
| G6 shot manifest | **Yes** | OTIO schema validation + completeness: every shot has VO timecode, duration, ≥1 Asset ID, motion start/end for moving shots |

**Bottom line on gates:** deterministic + schema checks and LLM-as-judge can fully automate G2, G3, G5, G6 today. G1 and G4 should keep a human reviewer initially, with the explicit goal of replacing them once you have ~12 episodes of labeled gate decisions to calibrate the judges against.

### Research Goal 6 — Automation Gaps (synthesis)

**Mature / adopt-as-is (low risk):**
- Timing spine (Scribe/WhisperX) — solved.
- Script format (Fountain) and shot-manifest container (OTIO) — solved, JSON-native, integrate with your stack.
- Script-quality and manifest-completeness gates — solved (LLM-judge + schema).
- Orchestration spine (n8n + Claude via HTTP node, structured JSON output) — solved and idiomatic; n8n's HTTP Request node + Execute Code node for parsing is the documented Claude pattern.

**Requires custom building (your real work):**
1. **Visual reference sourcing (G1b)** — no turnkey tool. Build the archive-API-query + SigLIP-2-ranking module. Highest-value custom work.
2. **Asset Bible schema + drift-validation (G4)** — no standard schema; invent it (VGoT tuple + USD referencing model as inspiration). The drift-prevention *validation* is the single hardest-to-automate gate.
3. **VO-line → shot-duration propagation (G2→G6)** — small custom module; no tool does it.

**Where handoffs break:**
- **G3→G4→G5 Asset ID referential integrity:** if the sequence map names an asset the bible doesn't define, or a storyboard shot references an unknown Asset ID, the chain silently corrupts. Mitigation: treat Asset IDs as foreign keys; validate referential integrity at every gate (cheap, deterministic, high-value).
- **G2→G6 timing drift:** if the script is edited after VO recording, all downstream durations are stale. Mitigation: VO recording must hard-lock G2; any script change re-triggers the whole timing spine.
- **G4 canonical reference image ↔ generation layer:** your scope ends at the spec, but the bible's canonical reference image and locked attributes are the contract the (out-of-scope) generation layer consumes — design the bible JSON so those fields are generation-ready (e.g., reference image as a resolvable URL/path, attributes as discrete prompt-injectable strings).

**What's missing in the ecosystem for your specific pipeline:** there is no open tool that (a) sources real-world historical references programmatically and binds them to named assets, (b) maintains a machine-readable asset-consistency contract with drift validation, and (c) propagates VO timing into a structured shot manifest. ViMax is the closest and still does none of these three at the specification rigor you want. This is precisely the white space your build occupies.

## Recommendations

**Stage 1 (now) — Adopt the free primitives and lock contracts:**
1. Adopt **Fountain + screenplay-tools** (G2), **OpenTimelineIO** (G6), and **ElevenLabs Scribe v2** (timing spine, you already have it; add **WhisperX** self-hosted as fallback). These three are zero-risk and eliminate roughly half your schema design.
2. Stand up **OpenMontage's schema/skill/checkpoint pattern** in your `contracts/` folder as the orchestration template (you already run a fork). Define the 6 artifact JSON schemas as OpenAPI/dataclass contracts first — this is your existing build pattern and the gating dependency for parallel agent work.
3. Clone **ViMax** and read `agents/`, `interfaces/`, `pipelines/` directly to extract its agent decomposition and any internal schemas (do not rely on third-party blog descriptions, several of which are fabricated).

**Stage 2 (next) — Build the two novel modules:**
4. Build the **visual-reference sourcing module** (G1b): n8n flow → Smithsonian/LoC/Europeana/Wikimedia APIs → SigLIP 2 ranking → ranked URLs into dossier JSON. Reuses your SigLIP 2 scorer.
5. Design the **Asset Bible JSON schema** (G4) with Asset IDs as foreign keys, modeled on USD's reference/variant/stable-ID pattern; add a VGoT-style threshold-gated self-validation (τ_c semantic-coherence + τ_k completeness).
6. Build the **VO→shot-duration propagation** module (G2→G6).

**Stage 3 (then) — Automate gates progressively:**
7. Turn on LLM-as-judge + schema validation for G2/G3/G5/G6 immediately (use a different model family for judging than authoring to reduce self-preference bias; banned-words/patterns as deterministic hard-fails).
8. Keep humans on G1 (facts) and G4 (drift) until you have ~12 episodes of gate decisions; then calibrate automated judges against that labeled set.
9. Optionally adopt **Kitsu/Zou** (self-hosted, Gazu Python client, webhooks) as the human-review UI and asset-to-shot database if you want a GUI over the JSON artifacts.

**Benchmarks that change the plan:**
- If automated G4 drift-validation (SigLIP-2/face-consistency scoring of test renders) agrees with human reviewers >90% over ~12 episodes → remove the human from G4.
- If a single end-to-end framework (ViMax or a successor) ships documented JSON schemas for asset bible + shot manifest + reference binding → re-evaluate build-vs-adopt for G4/G6.
- If reference-sourcing recall from open archives proves too low for your subject matter → add a paid reverse-image/Lens API (Apify/TinEye) tier.

## Caveats
- **ViMax internal schemas are unverified.** Its agent *names*, star count, and config format are confirmed, but the exact fields of its character/asset records and shot manifest could not be read from public pages; `git clone` to verify before designing against it. Multiple third-party "ViMax API" blog posts (atomixweb, pixel4it) are demonstrably fabricated.
- **Version numbers and metrics** (ViMax 8.7k stars, OTIO 0.18.1, Smithsonian 5.1M items, VGoT 20.4%/17.4% gains) are as-reported around June 2026 and drift over time; OTIO is still labeled Beta on PyPI.
- **LLM-as-judge bias is real** (position bias, self-preference); the research consensus is that judges need calibration against human labels and bias-mitigation (permutation, cross-family judging) before trusting them on subjective quality (G2 voice, G4 drift). Do not treat automated creative-quality gates as fully reliable on day one.
- **Some commercial SaaS claims are marketing** (e.g., "true character consistency across every frame"); treat consistency claims from closed tools as unverified until tested.
- **PureRef has no API** — it is a human tool; do not architect it into an automated flow.
- **Generation layer explicitly out of scope** — IP-Adapter/LoRA/IC-LoRA details surfaced here are included only to show what your asset-bible *specification* must feed; selecting/operating those models was not researched per your constraint.