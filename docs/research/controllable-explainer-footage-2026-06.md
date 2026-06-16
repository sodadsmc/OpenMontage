# Controllable Explainer Footage — Problems & Solutions

*OpenMontage / Therac-25 documentary · drafted 2026-06-15*

This document records the recurring problems we hit using AI image-to-video (i2v) to
build **narration-synced explanatory** beats in a unified graphic-novel style, and
surveys what's available to solve them. Part 1 (the problem) is written from direct
evidence in the seg_006 production; Part 2/3 (the solution landscape + recommendations)
come from a focused research sweep.

---

## 1. Context — what we're building and how

- **Goal:** an animated documentary where each beat both *looks* cinematic (one authored
  graphic-novel look: bold ink linework, cross-hatch/halftone, deep-navy + warm-amber
  duotone, film grain) **and** *teaches* a specific fact, with visuals landing on the
  narration word-for-word.
- **Pipeline:** script → per-segment shots → a **style keyframe** per shot (Nano Banana,
  often an edit of an Asset-Bible canonical) → **AI i2v** (Grok Imagine via Kie.ai,
  ~$0.017/s, anchored to the keyframe) → a uniform **finishing pass** (duotone/grain) →
  mux **ElevenLabs** narration using char-level word timing.
- **The other tool we have:** a deterministic diagram engine (matplotlib "xkcd" sketch
  mode + a manim variant). It can express anything *exactly* — but it reads as a
  **slideshow**, which the director explicitly wants to avoid.

## 2. The core tension — the explainer triangle

Every beat wants three things at once, and our two media each only give two:

```
            PRECISE & DIRECTABLE
           (exact counts, timing,
            per-object control)
                  /\
                 /  \
   deterministic/    \  ??? (the gap we
   diagrams  ●        \  keep falling into)
   (slideshow)         \
                 /------●  AI i2v footage
   CINEMATIC LOOK        NO/LOW EFFORT
   (rich, authored,      (one prompt,
    moving)               no hand-animation)
```

- **Deterministic diagrams** = precise + low-effort, but not cinematic ("slideshow").
- **AI i2v footage** = cinematic + low-effort, but **not directable** to precision.
- The whole problem is the missing third combination: **precise AND cinematic.**

## 3. The nine issues (with evidence)

### Group A — Directability (the model won't take precise stage direction)

1. **Camera won't lock.** Explicit "LOCKED, static, fixed camera" is treated as a soft
   suggestion; the model adds dolly/pan/tilt anyway. *Evidence:* shot 2 re-staged twice
   with "LOCKED STATIC CAMERA"; both times Grok dollied in and the gantry loomed larger
   across the shot. (A third attempt finally held the lock only by removing all depth
   cues — a flat, head-on composition with nothing to dolly toward.)
2. **Can't hold an exact count.** *Evidence:* asked for **six** bodies on tables; got
   **five** (first stage), then **eight** (work-around). The model does not enumerate
   objects, so "six" is never stable, especially once the camera moves and reveals more.
3. **No per-object control.** Cannot direct "dim **exactly three** of six figures." The
   change applies **globally**. *Evidence:* every attempt drained the warm light from the
   whole frame (a uniform cold wash), never from three identifiable figures while three
   stayed lit — even when each figure was given its own separate light pool.
4. **Non-deterministic timing.** A specific action cannot be pinned to a specific
   narration word; each generation is a different plausible interpretation. We can nudge
   and re-roll, but not script choreography. (Our narration sync works because we *cut/
   trim* generated clips to word timings after the fact — we cannot place an *internal*
   action on a word.)

### Group B — Fidelity (the model overrides authored look / breaks consistency)

5. **Style/era drift.** Strong stock-image priors override the channel style. *Evidence:*
   "extreme close-up of a 1980s circuit board" rendered as a **modern surface-mount board
   in a glossy photoreal 3D-render** look — wrong era *and* broke out of the ink style —
   despite the style suffix. Fixed only by baking hard "NOT a 3D render / NO surface-mount
   / through-hole DIP chips / hand-inked" language into the keyframe itself.
6. **Object permanence breaks.** Props/architecture materialize or warp between frames.
   *Evidence:* a structural column appeared mid-shot in shot 2; room geometry rearranged
   between the wide frames of shot 1. (Our `MOTION_DISCIPLINE` clause reduces but does not
   eliminate this.)

### Group C — Semantics (some facts resist footage entirely)

7. **Abstract/symbolic beats.** Facts like "the manufacturer *denied* an overdose was
   possible" do not exist as photographable objects. *Evidence:* shot 3 reads as "a hand
   rubber-stamping reports" — the *denial* meaning lands only with the narration; the
   metaphor needs the VO to complete it.
8. **The slideshow-vs-footage tension** (the core gap from §2). The beats that teach best
   in the slideshow (count six, X-out three, flip one bit) are exactly the ones footage
   can't direct; the beats footage does best (mood, place, dread) carry no precise fact.

### Group D — Tooling / operations

9. **Provider brittleness + a verification blind spot.**
   - *Infra:* Grok Imagine via Kie.ai **500s** when a clip **>6 s** is paired with a long
     prompt (its internal video-*extension* call has a tiny prompt budget); a **7 s** clip
     throws a separate 400. Workaround: generate **6 s legs** with the full prompt and
     chain them. Also: provider-hosted keyframe URLs can be stale/refused by the video
     model's fetcher.
   - *Verification:* the per-clip quality gate scored **98–100 %** on every shot while two
     shots completely **failed to teach their fact**. Artifact/prompt-adherence QC is blind
     to "does the picture actually convey the point?" — we only caught the failures with a
     separate **adversarial, frame-level, semantic** review.

## 4. What we tried this session, and the lessons

- **Full-fidelity 6 s chained legs** (vs. compressing the prompt) — solved the provider
  500 with **zero prompt loss**. *Lesson:* sidestep the extension path entirely.
- **An LLM "art-director" pass** (Gemini reads the slideshow, rewrites each beat as a
  footage shot under our object-permanence rules) — good at *translation*, but it can only
  request effects the downstream model can actually execute.
- **Three re-stages of the "three of six" beat** (anchored→fresh keyframe; explicit camera
  lock; per-figure light pools; single locked clip) — **all failed** the selective drain.
  *Lesson:* this is a **structural** limit of i2v, not a prompt-tuning problem.
- **Adversarial frame-level verification** (independent agents extract frames and judge
  whether each beat teaches its fact) — caught real failures the QC gate rated 98–100 %.
  *Lesson:* verification must test **teaching/semantics**, not just artifacts.

**Conclusion going in:** AI i2v is excellent for **atmosphere/emotion** and weak-to-unable
at **symbolic, countable, precisely-timed, per-object** explanation. The open question for
Part 2 is whether 2025–2026 tooling closes the gap — via **controllable generation**
(camera/region/object control), **keyframe interpolation** (pin both ends), or a
**build-then-stylize** bridge (author exactly, then restyle to footage) — without dragging
us back to the slideshow.

---

## Part 2 — Solution landscape

Six dimensions were researched (~107 web sources). The short version: **the gap is
closed, and mostly by one move — *build-then-stylize*.** Author the beat in a medium
where counts/timing/camera/per-object are exact, then use AI only to buy the look.

### 2.1 Camera control (issues 1, 6)

There are **two tiers, and only one is a real lock**:

- **Soft (free-text bias)** — Grok, Veo, most i2v parse "locked/static" from the prompt
  and override it. *This is literally our issue 1.*
- **Hard (structured input)** — "static" is an explicit control, not a word:

| Tool | Control | Maturity | Note |
|---|---|---|---|
| **Kling Static Brush** ([kie/fal](https://kling.ai/quickstart/how-to-animate-image-parts)) | paint a stability-anchor mask; up to 6 motion elements | prod-UI/API | **same Kie gateway we already use** |
| **Hailuo/MiniMax "Director"** ([minimax](https://www.minimax.io/news/01-director)) | in-prompt `[static shot]` command, tuned for low randomness | prod-API | cheapest drop-in A/B |
| **Wan2.2-Fun-Camera** ([comfy](https://docs.comfy.org/tutorials/video/wan/wan2-2-fun-camera)) | native "static" camera embedding | open (Apache-2.0) | self-host, no per-clip cost |
| **CameraCtrl / Go-with-the-Flow** ([camctrl](https://hehao13.github.io/projects-CameraCtrl/), [gwf](https://eyeline-labs.github.io/Go-with-the-Flow/)) | per-frame camera pose / warped-noise template — a **true** hard lock | research/open | only attaches to open base models |

**Catch:** the hard locks only bolt onto *open* models (Wan/CogVideoX), not Grok/Veo/Kling.
And camera tools never deliver per-object *lighting* — "dim three" still comes from the keyframe.

### 2.2 Object / region / exact-count control (issues 2, 3)

**Real per-object/count control exists only when WE author a mask** — never from a prompt
or a reference image (those quietly miscount on near-identical subjects).

| Tool | Mechanism | Gives exact count? |
|---|---|---|
| **SAM2 + VideoPainter** ([TencentARC](https://github.com/TencentARC/VideoPainter)) | we author a per-frame mask; edits only the masked region, byte-preserves the rest | **Yes — by construction** |
| Kling / Pika / Wan-Move motion brush | paint per-region *motion* (≤6 elements) | motion only, not lighting/count |
| Runway **Aleph** relight ([runway](https://runwayml.com/research/introducing-runway-aleph)) | text "dim the three on the left" + relight | soft — can bleed/miscount |
| Kling Elements / Vidu multi-ref | keep the *same* subjects consistent shot-to-shot | consistency, **not** count |

So "dim exactly 3 of 6" = **SAM2 mask → VideoPainter (masked relight)**, optionally Aleph for speed.

### 2.3 Deterministic motion-graphics that look cinematic (issues 4, 7, 8)

**The "slideshow" feel is NOT inherent to determinism — it's a missing craft layer.** A
code-rendered, frame-locked compositor fed our existing narration-timing JSON gives exact
counts, per-object state, and word-locked timing *by construction* (no model to negotiate with).

| Tool | Role |
|---|---|
| **Remotion** ([docs](https://www.remotion.dev/docs/the-fundamentals)) | React→MP4, every frame `=f(frame)`; consumes our ElevenLabs word-timing JSON; **the lead bridge** |
| **Rive** ([state machines](https://help.rive.app/editor/state-machine)) | author reusable *directable* diagram components (named `count`, `dimmed[]` inputs); render frame-locked inside Remotion |
| **Cavalry** (now free) / **Blender Grease Pencil** | richer procedural-ink / true moving-camera hand-drawn ink (hero shots) |
| **The finishing craft** | 2.5D parallax camera over layered keyframe art + **one** uniform halftone/duotone/grain pass + **graphic match-cuts** into footage |

Kurzgesagt-grade look = layered art + virtual-camera parallax + a uniform post-process — *not* a generative model.

### 2.4 Build-then-stylize / video-to-video (issues 5, 8 — the bridge for 1–4)

**v2v adds no precision — it inherits whatever precision is in the clip you feed it.** So
author the exact scene deterministically, then stylize. The one load-bearing detail:
**condition on CANNY / line-edge maps, NOT depth** (depth leaks the source's photoreal texture;
edges pass pure geometry so the ink style wins).

| Tool | Use | Cost |
|---|---|---|
| **Wan 2.2 VACE Fun Control** ([comfy](https://comfyui-wiki.com/en/tutorial/advanced/video/wan2.2/wan2-2-fun-control)) | feed our **manim/sketch diagram** as a Canny control video → ink footage | open / ~$0.05–0.10/s on fal |
| **EbSynth V2** ([cg](https://www.cgchannel.com/2025/10/ebsynth-2-can-turn-video-into-animation-without-using-ai/)) | propagate one keyframe across a shot (non-AI, deterministic, zero drift) | one-time license |
| Runway **Aleph** | whole-clip restyle/relight, best coherence | ~$0.18/s (**~10× Grok** — hero only) |
| Blender → ControlNet | block scene → depth+Canny → ink restyle (maximal control) | local GPU |

**Key reframe:** the "janky slideshow" diagram engine the director dislikes is *not a liability* —
it's the **ideal controllable source** for v2v. The thing we were escaping is the key to the fix.

### 2.5 Keyframe interpolation / first-last-frame (issues 1, 2, 3, 6)

**Pinning BOTH ends solves issues 1/2/3/6 by construction** — for one small-delta transition
per clip. Author "six lit" and "three lit/three dark" as identical-framing keyframes; the model
only fills the middle, so it can't re-imagine the count, the camera, or the 1980s board.

| Tool | FLF support | Access |
|---|---|---|
| **Kling start+end** ([fal](https://fal.ai/models/fal-ai/kling-video/o3/standard/image-to-video/api)) | `end_image_url`, 3–15s | **via Kie (already wired)**, ~$0.084/s |
| **Wan 2.1 FLF2V** ([HF](https://huggingface.co/Wan-AI/Wan2.1-FLF2V-14B-720P)) | open, **fixed-seed = reproducible beat** | self-host |
| Luma Ray2 Keyframes / Runway Gen-3 (first+**middle**+last) | secondary; Gen-3 pins an intermediate state | API |

**Hard fact:** Grok on Kie has **no end-frame input** — it only extends forward. *That's exactly
why it failed our beats.* FLF requires swapping the explanatory-beat engine to Kling/Wan/Luma.
Literature warns endpoints can still drift on large deltas → keep deltas small + verify the final frame.

### 2.6 Semantic verification (issue 9 + catching 1–7)

**The blind spot:** every metric we reach for scores **prompt-adherence** (frame vs. the text we
asked for), never **narration-adherence** (frame vs. the words the viewer hears) — which is why
our gate scored 98–100% while two shots taught nothing. The fix is a 3-tier verifier:

| Tier | Tool | Checks |
|---|---|---|
| Semantic authority | **Gemini 2.5 audio+visual judge** ([docs](https://ai.google.dev/gemini-api/docs/video-understanding)) | "at the spoken word, does the picture TEACH it?" — the missing gate (retrofit `quality_gate.py`) |
| Objective detectors | **Grounding DINO + CLIP-Count** ([T2V-CompBench](https://arxiv.org/abs/2407.14505)) | hard counts + per-object luminance ("exactly 3 of 6 darker"); **double-fail → route to diagram lane** |
| | **VideoScore2** / **VBench-2.0** dims | physics/permanence (6), camera-moved (1), reproducible numeric score |
| Fast pre-filter | CLIPScore / VideoScore v1 | triage + style-drift sentinel (5) before paying for Gemini |

A single VLM judge is noisy → keep a hard threshold + one reroll, backed by the objective detectors.

---

## Part 3 — Recommendations for our pipeline

### The one-paragraph thesis

**Stop asking i2v for precision it structurally cannot give, and adopt build-then-stylize.**
Author every *explanatory* beat in a deterministic medium (Remotion, our manim/sketch engine,
or a matched keyframe pair) where counts, per-object state, locked camera, and word-synced timing
are exact **by construction**; then use AI only to buy the cinematic look — via FLF interpolation,
Canny-conditioned v2v, or just a uniform finishing pass. Keep Grok i2v for what it's genuinely
great at — **atmosphere**. The diagram engine we were trying to escape becomes the *controllable
source* that makes the whole thing work.

### Route each beat by type

| Beat type | Example (Therac) | Lane | Why |
|---|---|---|---|
| **Atmospheric / emotional** | empty room, looming machine, the terminal | **Keep Grok i2v** | i2v's strength; no precision needed |
| **Simple state-change** | "six lit → three dark" | **FLF: Kling start+end** (via Kie) with two authored keyframes | both ends pinned → count/camera/era locked |
| **Symbolic / count-critical / multi-step** | the byte overflow, mode confusion | **Deterministic (Remotion/manim) → Canny v2v (Wan VACE) → uniform finish** | exact control + ink look |
| **Reusable directable diagram** | counters, tableaus, turntables | **Rive component → frame-locked via Remotion** | author once, drive by data |

### Phased adoption (ranked by ROI)

**Phase 0 — in-pipeline, cheap, do now**
1. **Retrofit `quality_gate.py`** with a second Gemini-2.5 call that ingests the muxed clip +
   the beat's narration words and returns a teaching rubric `{teaches_fact, on_style, period_ok}`;
   gate on `teaches_fact ≥ 0.7`, one reroll. *(Uses our existing `GOOGLE_API_KEY`; this is the
   single highest-payoff change — it's what manually caught shots 2 & 4 this session.)*
2. **Route "locked" beats off Grok** to **Kling Static Brush** or **Hailuo Director `[static]`**
   via Kie — a near-hard lock instead of hoping Grok holds "static" (issue 1).

**Phase 1 — the FLF bridge (kills issues 1,2,3,6 for state-change beats)**
3. Swap the explanatory-beat engine to **Kling start+end frame** (Kie/fal). Author **two
   matched-framing** Nano-Banana keyframes per beat (only the target objects differ). Codify a
   "matched start/end pair, small delta, motion-minimal prompt" rule in the visual-director skill.
4. Extend the gate to **compare the rendered final frame vs. the authored end frame** (reuse the
   existing final-frame extraction) — the literature warns FLF endpoints can drift.

**Phase 2 — kill the "slideshow" (issue 8) without losing determinism**
5. Adopt **Remotion** as the deterministic compositor for explanatory beats (replaces the
   matplotlib/manim slideshow path); it consumes the ElevenLabs word-timing JSON directly.
6. Standardize **ONE graphic-novel finishing pass** (duotone + ink + halftone + grain) applied to
   **both** Remotion beats **and** Grok i2v shots, joined by **graphic match-cuts**. *This is the
   cheapest change that makes everything read as one film* (also masks style/era drift, issue 5).
7. Author reusable **Rive** components (e.g. a 6-figure tableau with `count`/`dimmed[]` inputs).

**Phase 3 — strategic build-then-stylize lane**
8. Stand up **Wan 2.2 VACE Fun Control** (self-host or fal): take the manim/sketch diagram —
   already exact on count/timing/camera — extract **Canny** edges, v2v into ink footage, chain in
   ~5s legs like our Grok tail-frame chaining. *Turns the diagram engine into the bridge.*
9. Add the **Grounding DINO counting gate**; wire its **double-failure to route the beat to the
   diagram lane** instead of an endless i2v reroll — the verifier becomes the footage/diagram router.
10. For count-exact relights ("dim exactly 3 of 6") use **SAM2 → VideoPainter** as a masked-edit
    finishing stage.

### Coverage — every issue now has an owner

| Issue | Primary fix |
|---|---|
| ① camera won't lock | Kling Static Brush / Hailuo `[static]` / Wan camera embedding; FLF matched frames |
| ② exact count | author it (Remotion/keyframe); verify with Grounding DINO |
| ③ per-object control | SAM2→VideoPainter masked relight; bake into keyframe |
| ④ word-synced timing | Remotion (frame-locked to timing JSON); FLF bounds the transition to the cut |
| ⑤ style/era drift | author both keyframes in-style; Canny (not depth) v2v; uniform finish |
| ⑥ object permanence | FLF pins both ends; deterministic source; VideoScore2/VBench physics gate |
| ⑦ abstract beats | Rive metaphor components; Gemini teaching-judge confirms it reads |
| ⑧ slideshow vs footage | **build-then-stylize** — deterministic source + Canny v2v + uniform finish |
| ⑨ infra + verification | Gemini narration-teaching gate + objective detectors; (Grok extension limit already handled) |

### The seg_006 retro-fit (concrete)

The exact beat that beat us — **"six bodies, dim three"** — is now a solved recipe:
author **two keyframes** ("six lit" → "three lit, three dark", identical framing) → **Kling
start+end** → finish. Or render it in **Remotion** (`{figures:6, dimmed:[0,1,2]}`, locked virtual
camera) and **Canny-v2v** it to ink. Either gives the slideshow's exact "three of six" *and* the
cinematic look — the combination three Grok attempts could not reach.

---

*Sources: ~107 sites across camera-control, object/region control, motion-graphics, video-to-video,
keyframe-interpolation, and verification. Full URL list in the research run transcript
(`wf_99d67ac1-ed9`).*
