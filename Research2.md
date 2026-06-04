# Animating disaster: A code-first pipeline for Therac-25 (v2, cost-unconstrained, hardware-agnostic)

**Your primary stack is Manim + Remotion + After Effects (with GEOlayers 3 and Nexrender), all driven by Claude code generation, with DaVinci Fusion macros for edit-page titles, Rive for the one hero state-machine shot, and Veo 3.1 strictly gated for atmospheric B-roll under 20% of screen time.** Removing the free-and-local constraints expands the option space meaningfully — you gain the Johnny Harris–grade map pipeline, the entire Motion Array/Envato AE template economy, deterministic cloud rendering via Remotion Lambda and Nexrender workers, and a legitimate narrow role for generative video — but the core insight holds: **code-generation beats pixel-generation for diagrams, and visual authority is an editorial problem, not a budget problem.** Sora 2 stayed shut down, Veo 3.1 still cannot render "86 rad vs 10,000 rad" legibly, and YouTube's inauthentic content policy continues to reward structural variation and on-screen sourcing over production polish.

**What changed materially from v1.** After Effects + Nexrender is now a co-primary tool for maps and premium templates. Remotion Lambda becomes the default renderer rather than an optional cloud extra. DaVinci Resolve Studio and Fusion Studio standalone are worth their $295 one-time fees. Generative video earns a narrow slot for atmospheric-only B-roll. All hardware-specific concerns (GPU semaphore, 8GB VRAM juggling, Cairo-only Manim) are removed — the architecture will run identically on your current desktop, on a cloud workstation, on a Mac Studio, or on Lambda fleets. What did not change: the authority strategy, the grader architecture, the YouTube slop mitigations, and the pitfall list that matters most (template drift, citation discipline, human editorial checkpoint).

## The shortlist, rescored for Therac-25's specific visuals

| Shot type | Best tool | Second choice | Avoid |
|---|---|---|---|
| Dose comparison bars (86 vs 10,000 rad) | **Manim BarChart** with log-break axis | Remotion + D3 | Any pixel video model |
| Geographic timeline (Marietta/Hamilton/Tyler/Yakima) | **AE + GEOlayers 3 + MapTiler** | Remotion + D3-geo + `@remotion/paths` | Mermaid (no geo) |
| 1985–88 event timeline | **Manim NumberLine** or **AE template** (Motion Array) | Remotion timeline | Generative video |
| Race condition, 8s window, parallel lanes | **Fresh Claude→Manim generation** (Planner-Coder-Critic) | Hand-SVG + GSAP DrawSVG | Generative video |
| Byte overflow counter 254→255→000 | **Manim `ValueTracker` + `always_redraw`** | Remotion `spring` + `interpolate` | Lottie (hard to rollover) |
| X-ray ↔ Electron mode state machine | **Rive** (native state machines) | Mermaid `stateDiagram-v2` + GSAP | Manim (weak at graph layout) |
| PDP-11 terminal, scrolling assembly | **Motion Canvas / Revideo** (Shiki + typewriter) | Remotion + `react-syntax-highlighter` | Canvas+MediaRecorder |
| Beam path ± target | **Claude-drawn SVG + GSAP DrawSVGPlugin** | Manim vector primitives | Generative video |
| Three Mile Island dose comparison | **Manim BarChart** with log scale | D3 + annotation | — |
| Data flow (keystroke → Mal. 54 → P → fire) | **Mermaid `flowchart LR`** + GSAP reveals | Manim DiGraph | — |
| Text cards with dates, quotes, sources | **Fusion edit-page Title macros** (CSV-driven) | AE Essential Graphics templates | Generative video |
| Lower thirds, chapter cards | **AE Motion Array pack** via Nexrender | Fusion Title templates | Canva exports |
| Period atmosphere (1985 hospital exterior, CRT glow) | **Veo 3.1 fast tier** (gated, ≤20% screen time) | Artgrid / Storyblocks archival | Any diagram/chart shot |
| Archival-style medical equipment, PDP-11 closeups | **Artgrid / Storyblocks** (paid) | Veo 3.1 fast tier | Free Pexels (limited era coverage) |

## Primary tooling, rebalanced

**After Effects + GEOlayers 3 + Nexrender.** This is the Johnny Harris map pipeline and it's worth the subscription for your map shots alone. GEOlayers 3 pulls styled tiles from MapTiler or Mapbox, auto-animates camera moves along paths, and handles label fading — the Marietta → Hamilton → Tyler → Yakima timeline with connecting arcs becomes a 2-hour job here versus a real engineering effort in Remotion + D3-geo. Nexrender (github.com/inlife/nexrender) parameterizes AE projects via JSON at the `.aep` level: define template slots with `{{token}}` placeholders, Nexrender substitutes values from JSON, then aerender renders headlessly. This unlocks the Motion Array and Envato Elements template economy (~$16.50/mo Elements unlimited) for lower thirds, chapter cards, and any motion-graphic pattern you don't want to code from scratch. Claude's AE ExtendScript/JSX knowledge is moderate — weaker than its Manim and Remotion fluency — but sufficient for parameterizing existing templates, which is what you'll actually do 90% of the time. Rough costs: AE $23–55/mo, GEOlayers 3 $45 perpetual, MapTiler $25/mo, Motion Array or Envato Elements $16.50–20/mo, Nexrender free.

**Manim Community Edition** remains the backbone for math, state-tracking, and numeric overflow shots. The guidance from v1 is unchanged except that the Cairo-only alpha constraint no longer drives hardware decisions. Run on cloud GPU instances (Lambda GPU, RunPod, Modal, Vast.ai) if you move off your desktop or want parallel render fan-out. The Claude Code skill packs (`awesome-skills/manim-skill`, `Yusuke710/manim-skill`, `makefinks/manim-generator`) still push first-pass render success to ~90%+. The Planner-Coder-Critic loop from Code2Video (arXiv 2510.01174) and TheoremExplainAgent (ACL 2025) remains the right pattern for shots that don't correspond to any template.

**Remotion with Lambda as default renderer.** Flip the v1 guidance: render on Lambda by default, fall back to local only for iteration. At ~$0.017/min × 10-minute video × multiple candidates per scene × 34 scenes, you're at $5–15 per video in render costs but getting 30× parallelism and zero local compute constraints. Trivial at production cadence. The January 2026 Remotion Skills release (`npx skills add remotion-dev/skills`) is still the biggest Claude-Code usability unlock in the space — 38 skills including `charts.md`, `3d.md`, `captions.md`, `lottie.md`. Native ProRes 4444 with alpha remains a single CLI flag (`--pixel-format=yuva444p10le --codec=prores --prores-profile=4444`).

**DaVinci Resolve Studio ($295 one-time) + Fusion Studio standalone ($295 one-time).** Studio unlocks better codec support, noise reduction, batch rendering, and the features that matter for 4K delivery pipelines. Fusion Studio standalone is the only way to get a real `Fusion file.comp -render -quit` CLI for truly headless macro rendering — the bundled Fusion inside Resolve still requires the app running even with `-nogui`. Together these upgrades turn the Fusion macro branch of your architecture from "requires GUI" into a proper CI-renderable component.

**Rive** (Cadet tier $9/mo) unchanged — still purpose-built for the one hero state-machine shot (X-ray ↔ Electron mode). Alpha output via WebM or embed `<RemotionRiveCanvas>` inside Remotion for native ProRes+alpha.

**Motion Canvas / Revideo** remain the legitimate primary alternative for TypeScript-first creators who value live preview above Python fluency. Revideo's `renderVideo({projectFile, variables})` Node API is still the cleanest parameterized render API in the ecosystem. The 2025 Midrender pivot slowed upstream somewhat but the tools still work.

## Generative video: the narrow legitimate role

For **atmospheric B-roll only** — never for any shot requiring legible text, charts, state machines, or structural accuracy. Specifically viable for: scene_02 (1985 hospital exterior), scene_08 (PDP-11 amber CRT closeup), scene_10 (radiation gauge swinging into red zone), scene_12 (hospital corridor), scene_34 (empty corridor closing shot). Route through your existing SigLIP 2 + Claude Vision grader alongside stock footage candidates.

**Veo 3.1 via Google Vertex AI** ($0.15/sec fast tier, $0.40/sec standard) is the 2026 winner for period-accurate atmosphere after Sora 2's 25 March 2026 shutdown. Kling 3.0 via fal.ai ($0.029/sec) is cheaper and surprisingly good at grainy-film aesthetics but weaker at interior consistency. Runway Gen-4.5 Pro (~$0.31 per 5-second clip) is best for camera-movement control. **Budget ~$5–10 per video for AI atmospheric clips, hard cap at 20% of total screen time** — the specific threshold above which channels start reading as AI-native in 2026 audience surveys.

**Paid stock remains the first choice** for atmospheric content you can find — Artgrid ($25/mo), Storyblocks ($30/mo), Pond5 archival tier — all genuinely better 1985-era hospital and medical equipment footage than Pexels. Send Veo only for shots stock can't cover. The router logic should prefer stock, fall through to generative video, not the reverse.

## Architecture sketch, revised

```
segment_plan.json
    │
    ▼
[1] Router LLM (Claude Opus 4.5) — assigns visual_strategy
    ├─ stock_footage      → SigLIP 2 + Pexels/Pixabay/Artgrid/Storyblocks
    ├─ ae_template_fill   → Nexrender + Motion Array templates
    │                       (maps via GEOlayers 3, lower thirds, chapter cards)
    ├─ web_template_fill  → Remotion parametric library (70% of shots)
    ├─ custom_codegen     → fresh Manim / Remotion 
    │                       (Planner → Coder → Critic, N=5 retries)
    ├─ fusion_title       → date/quote/source cards at assembly time
    └─ genai_atmospheric  → Veo 3.1 for period B-roll 
                            (gated by budget + screen-time cap + extra grader pass)
    │
    ▼
[2] Generator (per strategy) — emits N candidates in parallel
    │    (Lambda fleet + Nexrender workers + Veo API all run concurrently)
    │
    ▼
[3] Grader (SigLIP 2 Tier 0 + Claude Opus 4.5 Vision Tier 1)
    extracts 3 keyframes per candidate, scores against 
      visual_description + mood + brand token compliance
    genai_atmospheric gets an additional "slop tell" pass
      (warped hands, text artifacts, anachronistic details)
    │
    ▼
[4] Assembly (FCPXML emitter + DaVinci Fusion Title injection)
    → import into Resolve Studio
    → timeline LUT + grain plate unify stock + animation + AI B-roll
    → human editorial pass before upload
```

Five strategy branches instead of v1's four. The `genai_atmospheric` branch is deliberately the most restricted: hard budget cap per video, hard screen-time cap, an extra grader pass that actively looks for slop tells. Gating is the whole point.

**Budget at sustainable 2-videos-per-week cadence.** AE + GEOlayers 3 amortized ~$50/mo, MapTiler ~$25/mo, Motion Array or Envato Elements ~$20/mo, Remotion Lambda ~$15–30/mo, Veo 3.1 ~$20–40/mo, Artgrid or Storyblocks ~$30/mo, Anthropic API for codegen + grading ~$30–50/mo, Rive Cadet $9/mo. Plus DaVinci Resolve Studio one-time $295 and Fusion Studio standalone one-time $295 (optional). **Call it $180–250/mo ongoing** after $295–590 in one-time software. Trivial against documentary YouTube economics if you're generating consulting leads on the adjacent Duskwire channel, and cheap against a single sponsorship at the rates your research shows for technical channels.

## What to skip, revised

**Still skip generative video for diagrams** — legibility failure is fundamental. **Still skip Canva and drag-and-drop design tools** — they break the reproducible-from-JSON premise. **Still skip Cavalry** unless you specifically love its aesthetic — the GUI escape hatch defeats the pipeline and its JS procedural layer is weaker than Manim's for your shot list. **Still skip talking-head avatar tools** (HeyGen, Synthesia) — strongest YouTube inauthenticity risk.

**Newly on the table but still skip for your use case.** Adobe Premiere Pro (Resolve Studio beats it at color grading and Fusion integration beats Essential Graphics for parametric titles). Final Cut Pro (Mac-only; FCPXML interop from Remotion and Manim gives you portability anyway). Blender for 2D motion graphics (overkill; use for 3D only if genuinely needed, and your Therac script has no 3D needs).

## Pitfalls and YouTube risk mitigation, updated

**The inauthentic content mitigations from v1 all still apply verbatim.** Vary structure across uploads. Bake citations into `segment_plan.json` as a required field and surface them as on-screen monospace captions on every archival image and quote. Use a cloned ElevenLabs voice with manual post-processing — breath inserts, room tone, custom pronunciation dictionary for technical terms. Maintain a visible human editorial checkpoint and document it. Slow cadence to Wendover-like pacing. None of these changed with the constraint loosening.

**Removed from the pitfall list** (were hardware-specific): GPU semaphore contention, Manim OpenGL alpha bug workarounds, 8GB VRAM juggling, Cairo-only constraint, ProRes 4444 export workarounds on Resolve Free. Cloud rendering sidesteps all of these.

**New pitfalls specific to the expanded stack.** *Nexrender template drift* — AE templates can silently break when AE itself updates; lock AE to a specific version in your Nexrender worker image and test template renders in CI when you bump. *AE Essential Graphics vs standalone MOGRT format* — stay with `.aep` + Nexrender; MOGRTs are harder to parameterize reliably. *GEOlayers 3 license is per-user* — single-seat is fine but plan for team seats if you scale or bring on an editor. *Generative video licensing varies and has moved twice in 2025* — Veo, Runway, and Kling all permit commercial use on paid tiers but check current ToS per provider before each production. *AI content disclosure labels* — if more than ~10–15% of a given video uses generative video, add YouTube's altered/synthetic content label in the upload flow; it doesn't affect monetization but undisclosed use that gets flagged does.

## Conclusion

The decisive insight from v1 still holds: the right answer to "how do I programmatically generate documentary animations" is not a tool, it's a **code-generation loop with a reviewer**, fed by a typed segment plan, grading candidates from multiple strategies against a locked brand token system, assembled in an NLE that supports parametric titles and timeline-level unification grading. With the budget constraint removed you get better maps (GEOlayers 3 + MapTiler), better templates (Motion Array + Envato Elements), deterministic cloud rendering (Remotion Lambda + Nexrender workers), headless Fusion (Studio standalone), and a narrow atmospheric B-roll slot (Veo 3.1 gated) — but the architecture and the authority strategy are identical to v1. The Therac-25 script with its bar charts, race conditions, byte overflows, state machines, and 1985–1988 timeline is almost a benchmark case for this pipeline. Build it once, and every subsequent tech-disaster episode costs mostly rendering time plus a modest API bill.

Key additions to bookmark alongside the v1 repository list: `github.com/inlife/nexrender`, `aescripts.com/geolayers/` (GEOlayers 3), `maptiler.com/cloud/`, `elements.envato.com`, `motionarray.com`, `artgrid.io`, `storyblocks.com`, `cloud.google.com/vertex-ai/generative-ai/docs/video/generate-videos` (Veo 3.1 API), `fal.ai/models/kling-video` (Kling 3.0).