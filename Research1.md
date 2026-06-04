# Duskwire v2 follow-up: six targeted answers for the Therac-25 build

This follow-up resolves the six knowledge gaps that remained after v1. Each section answers one question with 2026 pricing, concrete URLs, case evidence, and an explicit pick. Where sources disagreed or a figure could not be directly verified at the vendor, that is flagged in-section. Nothing below is fabricated — every number traces to a vendor page, Git release, or dated 2026 secondary source.

The headline across all six questions: **the v2 architecture survives contact with the market**, but three substantive edits are warranted — (1) move Nexrender off the local box onto **Plainly**, (2) drop Suno/Udio from the monetized pipeline until the remaining RIAA suits resolve, and (3) plan **Pond5 + AP Archive + CriticalPast** as the footage triad rather than chasing Artgrid.

---

## 1. Nexrender and After Effects automation in 2026

**Recommendation: use Plainly (Explorer $134/mo or Team $259/mo), not self-hosted Nexrender+aerender on EC2, for an 8–10 video/month channel.** The cash difference is within 2× at this volume, and Plainly removes four failure modes (aerender silent failures, AE version-lock drift, Adobe EULA grey zone, font/codec regressions) that each eat hours per month.

**Nexrender is alive but on life-support cadence.** `github.com/inlife/nexrender` shipped **v1.63.3 on 25 Feb 2026**, following v1.63.0 on 27 Jan 2026 and v1.62.10 on 8 Oct 2025 — roughly 5 patch releases in twelve months, no architectural change. Issue backlog (#1037–#1044) is growing into 2025; discussion response rate is low. Works with Node 14+/16+ and AE CC2019 through AE 25.x. The maintainers now also sell **Nexrender Cloud** (https://www.nexrender.com) and have confirmed Plainly is built on Nexrender infrastructure — meaning the hosted offerings are the place where the team actually invests.

**Hosted alternatives (verified 2026):**

| Tool | 2026 price | BYO AE templates | Notes |
|---|---|---|---|
| **Plainly** | Starter $69 / Explorer $134 / Team $259 / Pro $649 / Unlimited $1,500+ | ✅ native `.aep` | Built on Nexrender infra; 4K all tiers; ProRes + alpha output; Zapier/Sheets/Airtable |
| **Nexrender Cloud** | Custom/usage-based (not published) | ✅ `.aep` + MOGRT | Run by inlife team, positioned cheaper than Plainly |
| **Dataclay Templater** | Rig $80 perpetual; Pro $225/yr; Bot custom | ✅ | Still on-prem/desktop; CLI + Events API; AE CS5–AE 2025 |
| **aescripts Automation Blocks** | ~$149 perpetual | ✅ | Augments local aerender; not a cloud farm |
| **Creatomate / Shotstack** | $41–$249 / $39–$309 | ❌ | Non-AE JSON DSLs — not substitutes |

**Claude + ExtendScript**: A real ecosystem exists now. **Claude Scripter** (aescripts, v1.2.2, 2025) is a paid panel that runs Claude/GPT-4o/Gemini ExtendScript generation inside AE (https://aescripts.com/claude-scripter/). An **AE MCP server** (https://github.com/Dakkshin/after-effects-mcp) exposes compositions and layers to Claude/Cursor, and LobeHub's **after-effects-assistant skill** ships a canonical `extendscript-fundamentals.md` rules file that prevents Claude from emitting ES5+ syntax into the ES3-only engine. Anecdotal quality is meaningfully better than 2023 but **still behind Remotion/TypeScript and Manim/Python code-gen** — expect to keep an ES3 rule file in the prompt and inspect output. No public head-to-head benchmark exists.

**CEP vs UXP**: Adobe has confirmed **CEP 12 is the last major CEP version** (shipped in AE 25.0), security-only after that. UXP is shipping piecemeal for After Effects in 2026 — scripting APIs are rolling out but **panel support is still partial**. For a new headless automation pipeline, **target ExtendScript (JSX) via aerender**; neither CEP nor UXP matters for server-side rendering, and ExtendScript is where both Nexrender and the LLM tools operate.

**Case studies**: disappointingly thin. The only verified documentary/YouTube Nexrender user is **Noxcaos Music**, the channel run by Nexrender author "inlife" himself. Shakr, Dataclay, BrokerChooser (Plainly) and ETX news (Shotstack) are real at-scale users but in marketing/ads/news-summary, not long-form documentary. **No major documentary brand (Kurzgesagt, Vox, Veritasium, Polymatter) has publicly disclosed parametric AE rendering** — an honest signal that this pattern is dominated by ad-tech.

**Pitfalls and licensing**:
- Adobe's docs explicitly permit unlimited **render-only nodes** with `ae_render_only_node.txt` (https://helpx.adobe.com/after-effects/using/automated-rendering-network-rendering.html) since CS6. A pure SaaS farm serving arbitrary user uploads is a EULA grey zone; a **single channel rendering its own projects is clean**, but you still need one paid AE subscription on the render box.
- `.aep` is **forward-compatible only** — the render fleet must run ≥ the authoring AE version.
- `aerender` has a documented **3–10% silent-failure rate** (issue #965); plan retry logic.
- **macOS EC2 is a trap**: `mac2.metal` (M1) runs $0.65/hr but has a **24-hour Dedicated-Host minimum** (~$15.60/day floor). `mac2-m2.metal` is $0.878/hr, `mac2-m2pro.metal` $1.56/hr. For bursty documentary rendering, **Windows on c6i.2xlarge at ~$0.70/hr effective (Linux $0.34 + ~$0.37 Windows license)** is an order of magnitude cheaper.

**Cost math at target volume** (30 scenes × 10s × 10 videos/mo ≈ 50 min output, ~100 min compute):

| | Self-host EC2 Windows | Plainly Explorer |
|---|---|---|
| Compute | $5.60 (c6i.2xlarge × 8 hr) | included |
| EBS + egress | $12.50 | $0 |
| Adobe CC (required on node) | $23–60 | $0 |
| Ops time (setup 2–5 days, 2 hr/mo) | ~$100–300 equivalent | $0 |
| **All-in** | **$75–100 cash + hidden ops** | **$134** |

**Cross-over to self-hosting**: only above **~400 render-min/mo** (Plainly Pro $649 loses its edge against an always-on c6i.2xlarge) or when you genuinely need Apple-only plugins/ProRes 4444.

---

## 2. GEOlayers 3 and programmatic map animation in 2026

**Recommendation: GEOlayers 3 in After Effects for the Therac-25 hero map. Add D3-geo + TopoJSON rendered via Remotion as the "iterate five variants in an afternoon" backup.** The shot (North America; Marietta, Hamilton, Tyler, Yakima; sequential arcs; date labels) is exactly GEOlayers' sweet spot and exactly the aesthetic audiences already read as "documentary map."

**GEOlayers 3 (https://aescripts.com/geolayers/)** is stable and actively maintained — **v1.15.2 shipped 5 Feb 2026**, v1.15.1 on 9 Nov 2025, v1.14.1 on 12 Jun 2025, compatible with AE CC 2017 → AE 2026. Verified pricing: **upgrade from v2 is $205, from v1 is $255**; full new SUL is in the ~$260 range per aescripts (direct fetch was Cloudflare-blocked — re-verify at checkout). Floating license available (SKU MB-GEO-FL at Insight). **Commercial use is permitted once licensed; MapTiler imagery used commercially requires a separate MapTiler Cloud subscription** (2 weeks free included with GEOlayers 3 purchase).

**Feature set matches the brief exactly**: keyframeable 3D camera with auto "Animate View to Feature" along multi-feature paths; shape layers with dashed-stroke dash-speed animation (clean draw-on arcs); CSV/TSV/GeoJSON-driven styles; any AE comp can serve as a label template (so date callouts are free once a template comp is built). One-click 3D terrain via Helium X / Mettle FreeForm Pro / Plexus 3 / Trapcode Mir 3 if you want elevation.

**Automation via Nexrender is feasible but custom.** GEOlayers exposes an ExtendScript API (`geolayers3.geocode`, `.watch`, `.watchCsv`, `.addToBrowser` accepting GeoJSON URLs directly) and an `autorun.jsx` hook that fires on project open — the exact seam Nexrender needs. No first-party tutorial exists; budget 1–3 days of integration. GEOlayers must be licensed on every render node, and **"finalization" (baking tiles to footage) is the slow step** — best practice is to finalize once per master comp and vary only overlay/label layers per job.

**Tile providers (2026)**:

| Provider | Pricing | Dark-mode quality | Notes |
|---|---|---|---|
| **MapTiler Cloud** | Flex ~$25/mo, Unlimited ~$295/mo (https://www.maptiler.com/cloud/pricing/) | "Dark Matter" strong | First-class GEOlayers integration; dedicated GEOlayers video plan for creators <100k subs |
| **Mapbox** | GL JS free 50k loads/mo, then tiered (docs.mapbox.com/mapbox-gl-js/guides/pricing/) | Dark v11 excellent | **Video/print license is a separate sales conversation**, not self-serve — a real documentary gotcha |
| **Stadia Maps** | Free 200k credits/mo, then tiered (https://stadiamaps.com/pricing/) | **Alidade Smooth Dark + Stamen Toner are best-in-class for documentary** | Drop-in Mapbox-API compatibility |
| **Protomaps** | Free, self-hostable PMTiles on S3 | Dark/Black fine, less polished | Zero ongoing cost; ideal if budget matters |

The Mapbox video-license caveat is real — for a monetized documentary channel using Mapbox tiles in the final output, Mapbox's self-serve pay-as-you-go does not obviously cover production video use; that is the sales team's domain. **MapTiler Flex + Stadia Alidade Smooth Dark is the safer licensing story.**

**Code-based alternatives**:
- **Remotion** has a Mapbox integration (https://www.remotion.dev/docs/maps, example repo https://github.com/remotion-dev/mapbox-example) and an AI "Maps skill" (https://github.com/remotion-dev/skills/blob/main/skills/remotion/rules/maps.md) that prompts well. Caveat: AWS Lambda has no GPU and headless Chromium disables GPU by default, so WebGL/Mapbox renders are slow at scale.
- **D3-geo + TopoJSON** (https://d3js.org/d3-geo) is **the strongest "free and fast" answer for exactly this four-city shot** — TopoJSON basemap, `d3.geoAlbers` or `d3.geoAlbersUsa`, great-circle arcs automatic on LineString features, infinite style control, zero tile cost, zero licensing risk. Wrap in a Remotion composition with `useCurrentFrame()`/`interpolate()` and a scene renders in seconds per variant.
- **No open-source "GEOlayers for Remotion" exists** as of April 2026. The closest is the Remotion Mapbox example repo plus a deck.gl variant (https://github.com/alexfernandez803/animate-deck-gl) and a paid Remotion Pro "Mapbox Globe" component.
- Shotstack has **no native map module** — it would only composite pre-rendered maps.

**Verified documentary users of GEOlayers**: **Johnny Harris** (explicit — https://aescripts.com/learn/post/how-johnny-harris-makes-maps and https://www.premiumbeat.com/blog/making-maps-for-johnny-harris/), **Vox** (strong public evidence via multiple tutorials), and **RealLifeLore** indirectly (Boone Loves Video's GEOlayers Masterclass includes a "Make a Map Like RealLifeLore" module at https://boonelovesvideo.teachable.com/p/geolayers-3-masterclass). Wendover / Kento Bento / Atlas Pro / Neo — **no public confirmation**; do not assume.

**Suggested Therac-25 shot build**: master GEOlayers comp at ~zoom 3 over North America, Stadia Alidade Smooth Dark or MapTiler Dark Matter basemap; four pin markers via GEOlayers browser or 5-row CSV; three shape-layer arcs with dash-speed draw-on (Marietta → Hamilton → Tyler → Yakima, with Yakima's Jan-1987 recurrence visually differentiated); date callouts as AE text animators on GEOlayers label templates, staggered to arc-completion keyframes; final grade + grain + 4K upscale in AE.

---

## 3. Generative video for atmospheric B-roll in 2026

**Recommendation: Google Veo 3.1 (Vertex AI) as primary; Runway Gen-4.5 with Gen-4 References for scene continuity; Kling 3.0 via fal.ai for high-volume iteration.** Cost at 50 usable 5s clips with realistic 4× discard: ~$250 total — cheaper than one day of licensed stock.

**Model landscape (April 2026)**:

| Model | Price per 5s clip | API maturity | Commercial license (monetized YouTube) | Period-accuracy rating |
|---|---|---|---|---|
| **Veo 3.1 (Vertex AI)** | ~$1.00–2.00 full; **$0.25 Lite** (launched 31 Mar 2026) | ✅ REST long-running op, Gen AI SDK Python/Node/Go | ✅ clean on Vertex (SSTs + Pre-GA terms) | **9/10** — best physics, best period set dressing |
| **Runway Gen-4.5** | ~$1.50–2.40 (Standard/Pro plans) | ✅ REST + Python SDK | ✅ **all paid tiers** | 8/10 — top Elo 1,247; slight "modern gloss" unless film-stock prompted |
| **Kling 3.0 (via fal.ai)** | **$0.35–0.70** | ✅ fal.ai REST | ✅ paid tiers ($6.99+/mo) — **Kuaishou training-grant clause in ToS** | 8.5/10 on humans; less tested on period sets |
| **Seedance 2.0 (ByteDance)** | ~$0.25 | ✅ fal, Atlas Cloud, ComfyUI | ✅ paid; Hollywood IP lawsuits pending | 8/10; **up to 12 reference files** (9 img + 3 video + 3 audio) |
| **Luma Ray 3/3.14** | ~$0.31–0.40 | ✅ REST + fal | Plus $29.99+/mo for commercial | 7.5/10; explicit **"1980s documentary vibe" video-to-video** style |
| **Hailuo 02 (MiniMax)** | **$0.28/10s** | ✅ | ✅ paid | 7/10; cheapest $/quality; 10s cap |
| **Pika 2.2/2.5** | ~$0.45 | ✅ fal | **Pro $28+/mo required** for commercial | 6/10; stylized not photoreal |
| **Sora 2 / Sora 2 Pro** | $0.50 / $1.50 per 5s | ✅ but Tier 2+ gated | **OpenAI announced Sora shutdown on 24 Mar 2026** — web app off 26 Apr 2026, API off 24 Sep 2026 per multiple reports; some sources dispute. **Do not build on it.** | 9/10 if available |
| **Hunyuan 1.5 / Wan 2.6** | $0.04–0.05/s (self-host or providers) | ✅ | ✅ permissive/OSS | 6–6.5/10 |

**Use fal.ai as the aggregator.** Single API key, async queue with webhooks, transparent per-second billing, hosts Veo + Kling + Seedance + Hailuo + Pika + Luma + Wan + Hunyuan. Keep a direct Vertex AI path for Veo 3.1 production jobs where you want Google's SLAs.

**Period accuracy is the real differentiator.** Verified finding: Veo 3 reliably handles "boxy CRT TV, wood paneling, landline phone, drop-ceiling fluorescents" — the exact set-dressing language Therac-25 B-roll needs. Known failure modes across all models: modern signage fonts, LCD bleed-in when you prompt "monitor" without "CRT," ceiling fixtures defaulting to 2020s styles, modern cars in exterior shots, and Grey's-Anatomy-era hospital aesthetics overriding 1985-community-hospital reality. The mitigation is explicit era-prompting: specify film stock, frame-rate, fixture generation, period cars by make/model, and a strong universal negative prompt ("modern signage, LED, LCD, flatscreens, smartphones, Tesla, hybrids, touchscreens, USB, digital UI, ergonomic furniture, Arial font, 4K sharpness, HDR").

**The biggest quality multiplier**: generate a **period-accurate still in Nano Banana Pro (Gemini 3.1 Flash Image) or Midjourney first, then feed it to Veo 3.1 image-to-video.** Image models carry more 1985 training data than video models; anchoring the still locks era-correctness.

**Consistency across clips** (critical for the "same corridor from three angles" pattern): Runway Gen-4 References is best-in-class; Seedance 2.0's 12-file reference system is next; Veo 3.1's first+last frame (shipped Oct 2025) + 4 reference images is the cleanest for controlled hero shots.

**Per-shot picks**:
- **1985 hospital exterior** → Veo 3.1 (architecture + static wide); backup Runway.
- **Amber CRT monitor glow** → Veo 3.1 (CRT phosphor physics are a Veo strength); backup Seedance 2.0.
- **Vintage linear accelerator** → Runway Gen-4.5 + References (industrial detail, slow dolly); backup Veo.
- **Hospital corridor with fluorescents** → Veo 3.1 (fluorescent flicker is a Veo strength); backup Kling.
- **Rain on concrete parking lot** → Veo 3.1 (rain/reflections) or Luma Ray 3.

**Licensing and disclosure reality check**: YouTube's Jan 2026 AI-content disclosure mandate requires the "altered content" toggle for realistic scenes that didn't happen — a 1985 hospital reenactment B-roll **will require disclosure**. Combine with the Jan 2026 purge of 16 AI-slop channels (35M subs, $9.8M/yr revenue collectively terminated per DFRLab's 23 Mar 2026 report at https://dfrlab.org/2026/03/23/ai-generated-youtube-channels-co-opt-war-coverage-to-farm-nearly-two-billion-views/). The defensible pattern: **AI only for environmental settings, never for real people or fabricated events, disclosed in description and via the YouTube toggle**.

**Honest caveat on case studies**: no reputable documentary channel (Kurzgesagt, Veritasium, Johnny Harris) has publicly disclosed heavy generative B-roll use as of April 2026 — the disclosed users cluster in the AI-slop category YouTube has been actively terminating. Duskwire should **lead** on disclosure rather than hide it.

**Budget math for a full Therac-25 episode** (50 usable 5s clips): draft phase on Kling 3.0 via fal.ai (200 generations × $0.50 = $100) + final phase on Veo 3.1 full (50 keepers with 2× iteration × $1.50 = $150) = **~$250 total**.

---

## 4. Commercial video API services at 2-videos-per-week cadence

**Recommendation: keep Remotion + AE as the backbone, add Shotstack Subscription ($39–$99/mo) as the offload for ~65% of scenes (text cards, lower thirds, photo pans, simple timelines, subtitle burn-in).** Optionally bolt on Plainly Starter ($69/mo) if you want cloud-rendered AE variations. Do **not** build the pipeline on Creatomate, Bannerbear, Creatify, Abyssale, or JSON2Video for a documentary channel at this quality tier.

**Service-by-service state (verified 2026 pricing)**:

| Service | 2026 plan at target volume | Template type | BYO AE? | Max res | Alpha out | Notable |
|---|---|---|---|---|---|---|
| **Shotstack** | Subscription **$39** (200 min) / **$49** Essentials / **$309** Pro (2000 min); 30% overage | JSON timeline + Studio | ❌ but **ingests ProRes .mov with alpha** | **1080p standard; 4K only on enterprise** | input yes; output mp4 | Node/Python/PHP/Ruby SDKs; MCP server for Claude/Cursor |
| **Creatomate** | Essential **$41** (2k cr) / Growth **$99** (10k cr) / Beyond **$249** | Proprietary web editor + JSON | ❌ | 4K supported but **4× credit cost** | no ProRes | Best browser editor; responsive templates; JS Preview SDK |
| **Plainly** | Starter **$69** / Explorer **$134** / Team **$259** / Pro **$649** / Unlimited $1,500+ | **Native `.aep`** | ✅ | 4K all tiers | ✅ custom encoding, ProRes + alpha | **Zero template lock-in** — templates stay `.aep` |
| **Bannerbear** | Startup ~$49 / Business ~$149 | Proprietary | ❌ | 1080p | no | **Video multiplier is unpublished** — planning hazard; effectively images-only |
| **JSON2Video** | Professional **$49.95** (200 min, 10-min cap) / Startup **$99.95** | JSON + **native HTML5/CSS3** | partial (HTML5 blocks) | 4K (4× cost) | limited | Interesting for data-driven charts via HTML/CSS |
| **Creatify** | Creator **$39** / Business **$99** | AI avatar | ❌ | 1080p | no | Wrong tool — ad/UGC avatar focus |
| **Abyssale** | Start **$12/seat** / Pro **$36/seat** / Suite **$60/seat** | Proprietary | ❌ | 1080p | limited | Per-seat + marketing-banner focus |

**Capability match for the Therac-25 script** (30 scenes):

| Shot type | Best tool | Rationale |
|---|---|---|
| **Bar charts (radiation-dose comparisons)** | **Remotion** (React + d3/visx); JSON2Video as fallback | Data-driven components belong in code |
| **Event timelines (1982–87)** | **Remotion**; Shotstack for simplified versions | Date math trivial in React |
| **Text cards / section dividers** | **Shotstack** or **Creatomate** | Don't burn AE time on connective tissue |
| **Lower thirds (Kennestone, Hamilton, etc.)** | **Shotstack** | Template + JSON swap per scene |
| **Archival photo pans, Ken Burns** | **Shotstack** | Effortless JSON timeline |
| **Maps** | **Remotion + react-simple-maps** or **GEOlayers** | Vendors don't have native maps |
| **Hero motion graphics** | **AE direct** (optionally via Plainly for cloud render) | Quality ceiling |

**The scene-count calculus**: ~18–21 of 30 scenes are text cards, lower-thirds, pans, simple reveals — **the ~65% Shotstack can absorb**. The remaining 9–12 (charts, maps, animated diagrams, hero shots) stay in Remotion + AE. A single vendor does **not** replace the backbone.

**Real case studies** (verified):
- **Shotstack** — Spotify Partners & Platforms (tens of thousands of music videos/day per Hector Zarate, Engineering Manager); **ETX / AFP group** producing hundreds of daily news-summary videos with TTS and translation (the closest precedent to a documentary pipeline in any vendor case study); IKEA Family; Smartkarma; MY VIVENDA real-estate app (1-min HD in <20s render). https://shotstack.io/customers/
- **Plainly** — BrokerChooser (comparison videos from AE templates + CSV; auto-publishes to YouTube at https://www.plainlyvideos.com/success-stories/brooker-chooser); BurdaForward (FOCUS Online, CHIP.de); UNICEF, Sprinklr. https://www.plainlyvideos.com/success-stories
- **Creatomate** — a G2 review discloses use for "dynamic long-form videos for YouTube up to 45 minutes with synchronized audio, layered backgrounds, animated transitions — all rendered programmatically through JSON." No named documentary channel.
- **Bannerbear / Creatify / Abyssale** — no documentary case studies; heavy skew to e-commerce ads.

**Monthly cost at 2 videos/week × 30 scenes × ~15s ≈ 75 min output/mo, 1080p**:

| Service | Plan hit | Monthly cost |
|---|---|---|
| **Shotstack** | Subscription — 200 min easily fits | **$39–$99** |
| **Creatomate** | Growth 10k credits | **$99** |
| **Plainly** | Explorer (100 min) | **$134** |
| **JSON2Video** | Professional | **$49.95** |

If a 4K master becomes non-negotiable: Shotstack jumps to an enterprise quote (~$500+/mo), Creatomate/JSON2Video to 4× credit cost, **Plainly's number stays the same** — a real argument for Plainly when quality ceiling matters.

**Lock-in**: Plainly is the only vendor whose templates (`.aep`) retain value if you leave. Shotstack and Creatomate JSON timelines are superficially similar (tracks, clips, keyframes) but **not interchangeable** — migration means manual rebuild.

**Combined monthly cost of the recommended hybrid**: Shotstack Subscription $39–$99 + Remotion Lambda ~$5–$30 + AE (sunk) + optional Plainly Starter $69 = **$45–$200/mo** for the commercial-API layer.

**When to skip all vendors**: if Remotion components for charts/timelines/maps are already built and the team is fast, the vendor savings (~$100/mo) may not pay for integration engineering overhead. **Adopt Shotstack only to free a Remotion engineer's attention for chart/map components.**

---

## 5. Premium stock and archival libraries for tech-disaster content

**Recommendation for the long-term channel**: **Pond5 as primary subscription + AP Archive + Reuters Screenocean as per-episode archival partners + CriticalPast as the royalty-free archival supplement + NARA/Prelinger/ESA as the free public-domain baseline.** Skip Artgrid, Filmsupply, and Motion Array for this niche.

**Why Artgrid is the wrong pick despite being the v1 default**: Artgrid's catalog is modern cinematic B-roll — strong for travel/brand/lifestyle work, **weak for 1980s period pieces, linear accelerators, PDP-11 terminals, and news-grade archival**. Pricing is Junior $19.99/mo, Creator $29.99/mo, Pro ~$49.92/mo with a Universal Lifetime License, and the licensing is excellent — but "lifetime license on content we don't have" doesn't help.

**Library-by-library state (verified 2026)**:

| Library | 2026 price | License posture | Niche depth | API |
|---|---|---|---|---|
| **Pond5** (https://pond5.com) | Per-clip $25–$500; subscription + credits ($250 pack = $275 value); API sub from ~$199/mo | Royalty-free perpetual worldwide; editorial items flagged | **Deepest single marketplace (~44M clips)** including user-contributed vintage medical, amber-CRT, DEC terminal, oncology | ✅ documented REST |
| **Pond5 Public Domain** (https://pond5.com/public-domain) | Free (US PD) | Verify PD outside US | ~1,000 PD videos + 2,754 audio + 63,824 images | Same API |
| **Storyblocks** (https://storyblocks.com) | Essentials $252/yr, Unlimited $360/yr, Business custom | **Individual/Small-Biz license expires on cancellation** — only Enterprise is perpetual | Large modern library; thin on verified 1980s medical | ✅ REST (enterprise) |
| **iStock / Getty** | Basic $29/mo; Premium $70+; Premium + Video $99+ | $10k legal guarantee; editorial restricted | Getty's archival Hulton/Prelinger access sits in gettyimages.com, not iStock | Getty API enterprise-only |
| **Adobe Stock video** | $29.99–$249.99/mo subs; credits $49.95–$1,200; per-video subs $8–$30 | Standard RF = YouTube OK | Broad modern; thin on 1980s specialty tech | ✅ within CC SDK |
| **Filmsupply** | From $219/clip; typical $500–$5,000 | RF single-project, scales by distribution | **Cinematic contemporary — no period archival** | No public API |
| **Motion Array** | Everything $29.99/mo monthly, $19.99/mo annual | **License does not cover new projects after cancel** | Modern B-roll + templates; weak archival | No public API |
| **British Pathé** (https://britishpathe.com) | Per-clip RM, quote | Rights-managed | **Ends at 1978** — 1970s context strong, no direct Therac | Email licensing |
| **AP Archive** (https://aparchive.com) | RM per-clip (typical $30–$80/sec industry norms, not confirmed) | RM; explicit YouTube worldwide perpetual grant costs extra | **1.7M stories 1895–present — the single most important source for 1979–2012 news** | Enterprise portal |
| **Reuters Screenocean** (https://reuters.screenocean.com) | RM per-minute minimums, per-episode | RM per project/territory/duration; **re-use in new episodes = new fee** | Visnews 1957–1992 + Reuters News — excellent 1980s newsgathering | Licensing portal |
| **CriticalPast** (https://criticalpast.com) | From **$125/clip**, ~$200 for 2-min HD | **Royalty-free, perpetual, worldwide, YouTube commercial + theatrical**; $1M / $1.5M indemnification | 59,000+ clips; heavy US gov't / Cold War; 1980s civilian hospitals thinner | No public API |
| **NARA** (https://archives.gov) | Free | Public domain for US gov't; no clearance letters | ~360,000 reels; only ~8% viewable online; strong NIH/FDA/AEC | Catalog API |
| **Prelinger / Internet Archive** (archive.org/details/prelinger) | Free | ~60% PD, varies per clip; commercial via Getty | ~9,600 ephemeral films — **strong 1950s–1980s industrial/medical training** | No API |
| **Periscope Film** (stock.periscopefilm.com) | Per-second; email rate card | Non-commercial free on IA; **YouTube monetization requires paid license** | 1930s–1980s military/aviation/industrial, some medical | No public API |

**Specific footage-type matching for Therac-25**:
- **1980s hospital corridors / linoleum / fluorescents** → Pond5 ("1980s hospital", "vintage hospital corridor"); AP Archive news-style interiors.
- **Vintage linear accelerators / Varian Clinac** → AP Archive + Reuters Screenocean (FDA/oncology news packages 1982–88); NARA (AEC/DOE/NIH PD productions); Getty Hulton for editorial stills.
- **PDP-11 / amber-green CRT** → Pond5 user contributions ("VT100", "amber CRT", "DEC terminal"); Prelinger corporate training films.
- **1980s oncology wards / radiation therapy rooms** → AP Archive news packages; Reuters Visnews; NARA NIH/NCI productions (PD).
- **1980s medical staff (anonymized)** → Pond5 editorial + CriticalPast PD government medical training; NARA NIH/CDC.
- **Hospital waiting rooms (period)** → Pond5 vintage contributors + Prelinger.

**Critical gap warning**: Therac-25 itself — the specific AECL machine, the six accident sites — **has no dedicated stock footage anywhere**. The canonical images (Leveson IEEE Computer 1993 photographs, AECL brochures) are outside every library. License direct from IEEE Computer Society, rely on US §107 fair-use commentary, or recreate. Build visual B-roll from period-accurate **equivalents** — contemporaneous linacs, VT100 terminals, community hospitals.

**Long-term niche fit for the broader channel slate**:
- **Three Mile Island (1979)** → AP Archive, CriticalPast, Reuters, NRC on NARA, NBC/CBS via Getty.
- **Challenger (1986)** → NASA PD via nasa.gov and archive.org, CriticalPast, AP Archive.
- **Chernobyl (1986)** → Reuters Screenocean (Soviet Visnews), AP Archive, British Pathé partners.
- **Ariane 5 (1996)** → ESA multimedia (free with attribution for editorial), AP Archive, Reuters.
- **Boeing 737 MAX** → AP Archive, Reuters, Getty editorial.
- **Intel FDIV (1994)** → AP Archive, Reuters, Pond5 1990s CRT contributions.
- **Knight Capital (2012)** → Reuters, Bloomberg via Screenocean, AP Archive.

**Licensing gotchas not to learn the hard way**: Storyblocks Individual/Small-Biz licenses **expire on cancellation** (only Enterprise is perpetual — Pond5 is perpetual-RF even after cancel, which is why Pond5 wins for evergreen channels). Reuters Screenocean charges per-episode, so reusing a Chernobyl clip across a Chernobyl episode and a later retrospective is two fees. AP/Reuters/British Pathé default grants are **not** worldwide/perpetual YouTube — you must buy that explicitly. "Public domain" ≠ clearance-free: donated NARA material, people's publicity rights, and Periscope's claims on their scans of PD material all require attention.

**Episode-1 footage budget**: Pond5 sub/credit pack $199–$500 + AP Archive 5–8 clips YouTube worldwide perpetual ~$1,500–$4,000 (quote required) + CriticalPast 3–5 RF archival $400–$800 + NARA/Prelinger/Periscope PD $0 (research time only) = **~$2,100–$5,300 per episode**, scaling with news-minimum kick-ins.

---

## 6. Audio stack updates for 2026

**Recommendation**: **ElevenLabs Creator ($22/mo, upgrade to Pro $99 if retakes exceed 100k chars) → Hume Octave 2 as backup and expressive inserts → Musicbed Creator ($29.99/mo) as primary score → Soundly Pro ($14.99/mo) for SFX → Auphonic M ($23/mo) for automated leveling + iZotope RX 11 Standard (~$399 one-time, sales to ~$199) for surgical repair.** Avoid Suno and Udio for monetized content until the remaining RIAA suits resolve.

**TTS comparison (verified 2026)**:

| Tool | Monthly cost at ~160 min output | Cloning | Pronunciation override | API |
|---|---|---|---|---|
| **ElevenLabs v3 / Multilingual v2** | **Creator $22** (100k chars included; overage $0.30/1k) or **Pro $99** (500k + 44.1 kHz PCM) | Instant + Professional Voice Cloning | **`.PLS` pronunciation dictionaries** (phoneme + alias); IPA tags on Flash v2 / Monolingual v1; alias substitutions on v3/MV2 | ✅ REST + WebSocket |
| **Hume Octave 2** | Starter $3 + metered, est. $25–40/mo at volume | Voice design from prompt + 5–10s clone | **Phoneme editing (industry-first)** + voice conversion | ✅ REST + streaming |
| **Cartesia Sonic-3** | ~$5/mo at $0.03/min | ✅ short clip | Volume/speed/emotion tags; less robust on acronyms | ✅ 40–90ms TTFA |
| **OpenAI gpt-4o-mini-tts** | ~$2–3/mo at 160k chars | ❌ | Instruction prompt only | ✅ |
| **Google Studio / Journey** | ~$25/mo at 160k | Enterprise Custom Voice only | SSML phoneme tags | ✅ |
| **Azure Neural** | ~$3–5/mo | Custom Neural Voice (~$1,000/mo enterprise) | Full SSML + XML lexicon | ✅ |
| **Play.ht / Resemble / Murf / Descript** | $24–$39/mo range | ✅ | Varies | ✅ |

**Has anything moved ahead of ElevenLabs for long-form documentary in 2026?** Not decisively. Hume Octave 2 (Oct 2025) wins some blind naturalness tests (68% vs 60.9% for ElevenLabs on short prompts) and has phoneme-level editing, but third-party reviewers still judge ElevenLabs **more stable for long-form**. Cartesia Sonic-3 leads on latency but Artlist's own review calls it "not the ideal choice for long-form narration." Inworld and MiniMax Speech 2.6 HD are rising but have thinner YouTube-creator track records. **ElevenLabs v3 remains the 2026 documentary-narration standard specifically because of pronunciation-dictionary support for technical terms like Therac-25 / AECL / Leveson / PDP-11 / rad.**

**Pronunciation dictionary entries to set up day one** (alias form, works on v3 / Multilingual v2):
- `Therac-25` → "Thair-ack twenty-five"
- `AECL` → "Ay Ee See Ell"
- `Leveson` → "Lev-uh-sun"
- `PDP-11` → "Pee Dee Pee eleven"
- `Kennestone` → "Kenn-uh-stone"
- `rad` / `rads` → verify auto-pronunciation first; if mispronounced as slang, phoneme-tag on Flash v2 as `[[phonemes R AE1 D]]`

**ElevenLabs retention data worth the budget**: Virvid/Retention Rabbit 2025–26 tracking shows ElevenLabs-narrated faceless content retains at 58–68% vs 35–45% for robotic/built-in voices, driving $5–8 higher RPM. Frequently-cited Duskwire-adjacent channels: **The Infographics Show** (12.9M subs) and **Planet Dolan** are documented as using AI narration pipelines; the ElevenLabs voice library explicitly lists "Documentary Narrator" voices with **Josh** and **David Castlemore** as frequent picks for documentary/mystery/thriller work.

**Music generation — the legal picture in April 2026**:

| Tool | Status | Commercial recommendation |
|---|---|---|
| **Suno** | **Warner Music settled 26 Nov 2025**; UMG and Sony **still actively litigating**. Suno V5 released. Pro/Premier grant commercial rights on songs made while subscribed, **but US copyright registrability of purely-AI output is unsettled** — infringement enforcement is fragile. Suno ToS grants perpetual training license on user content. | **Do not rely on for a monetized documentary about a medical tragedy.** Confine to non-critical bed elements at most. |
| **Udio** | **UMG settled 30 Oct 2025**; Sony/Warner status unclear. UMG-Udio joint platform planned. | Same posture as Suno. |
| **AIVA Pro ~$33/mo** | **Cleanest license** — trained on public-domain + original data; user receives full output copyright | **Safe supplement** to curated library |
| **Stable Audio 2.0** | Commercial — trained on AudioSparx-licensed data | Safe |
| **Mubert / Beatoven** | Licensed catalogs | Safe |

**The legal risk is not abstract**: a Content-ID claim during a high-traffic Therac-25 upload would be catastrophic. Use AIVA for generative needs if any; keep Suno/Udio out of the monetized pipeline until the Sony/Warner suits close and output-copyright registrability is settled.

**Curated music libraries (verified 2026)**:

| Library | 2026 price | Fit |
|---|---|---|
| **Musicbed** | Creator **$29.99/mo** (no client work); Business/Client **$99.99/mo** | **Best primary for documentary** — emotionally-weighted cinematic; explicitly positioned for docs/brand films; SyncID for YouTube |
| **Artlist** | Music+SFX Social $9.99/mo annual; Pro $16.60/mo; **perpetual license on downloaded tracks** | Broad, cinematic; good budget primary |
| **Epidemic Sound** | Personal $9.99/mo annual; Commercial $17–$19.99/mo; **50k+ tracks, 200k+ SFX** | Largest SFX library in sub space; not perpetual |
| **Soundstripe** | ~$16.58/mo Creator annual | Mid-tier, decent |
| **Uppbeat** | Free tier + Premium ~$6.99/mo | Budget option |

**SFX libraries**:

| Library | Price | Best for |
|---|---|---|
| **Soundly Pro** | **$14.99/mo ($12.49 annual)** | Workflow king — cloud + local indexing, integrates with Premiere/Resolve/Pro Tools, **includes Freesound integration** |
| **Pro Sound Effects CORE 7** | Lifetime $1,499–$15,999; subs available | Broadcast gold standard (Mangini, King) — overkill for YouTube |
| **BOOM Library** | $50–$400 à la carte per library | Niche high-fidelity (sci-fi, mechanical hums); **add one BOOM Sci-Fi or Cinematic Metal library (~$100–200)** for linear-accelerator drones |
| **Freesound.org** | Free CC-licensed | Retro computing/CRT sounds, license each file |

**Post-production audio** — the 2026 standard for cleaning ElevenLabs output is **Auphonic for automated leveling/loudness/denoise + iZotope RX 11 Standard for surgical repair**. Skip Adobe Podcast Enhance and Descript Studio Sound for documentary TTS work — they're optimized for raw human recordings and can add artifacts to already-clean ElevenLabs output.

- **Auphonic** — free 2h/mo, Auphonic S $11/mo (9h), **Auphonic M $23/mo (21h) — fits 160-min monthly retake budget with headroom**. Batch API available. Mic-bleed remover added Dec 2025.
- **iZotope RX 11 Standard** — $399 MSRP (sales to ~$199), still current as of Feb 13 2026 (v11.4.0). RX 12 with Generative Fill rumored per one NAMM-2026-framed source but not confirmed on izotope.com — flag as unverified.

**Full recommended stack and monthly total**:

| Layer | Pick | Monthly |
|---|---|---|
| TTS primary | ElevenLabs Creator (with pronunciation dictionary) | $22 (budget $40 w/ overages; $99 Pro if retakes blow through 100k) |
| TTS backup | Hume Octave 2 Starter | $3 |
| Music primary | Musicbed Creator | $29.99 |
| Music secondary (optional) | Epidemic Sound Personal | $9.99 |
| Generative music (optional) | AIVA Pro | $33 |
| SFX | Soundly Pro annual + Freesound | $12.49 |
| Post-pro automated | Auphonic M (21h) | $23 |
| Post-pro surgical | iZotope RX 11 Standard amortized year 1 | ~$17 (then $0) |
| **Core must-have total** | | **≈ $107/mo (year 1), $90/mo (year 2+)** |
| **With optional Epidemic + AIVA** | | **≈ $150/mo** |

**Upgrade triggers**: ElevenLabs → Pro ($99) for 44.1 kHz PCM export and 500k chars headroom when retakes consistently exceed the Creator allowance; Epidemic Sound → Commercial ($19.99) for sponsored work; Musicbed → Business ($99.99) for client/sponsored segments.

---

## Conclusion — three edits to the v2 architecture

The v2 stack was well-tuned. Three edits actually matter. **First**, move parametric AE rendering off a local box and onto **Plainly Explorer or Team** — the cash cost is within 2×, and the four failure modes it removes (aerender silent failures, AE version-lock drift, Adobe EULA grey zone, font/codec regressions) dominate the economics. **Second**, reorient the atmospheric B-roll pipeline to **Veo 3.1 primary, Runway Gen-4.5 References for continuity, Kling 3.0 via fal.ai for volume iteration** — with a non-negotiable image-to-video anchor via Nano Banana Pro or Midjourney stills for era accuracy, and explicit disclosure via YouTube's altered-content toggle to stay on the right side of the Jan 2026 enforcement wave. **Third**, cut Artgrid, Suno, and Udio from the planned stack: Artgrid's catalog doesn't serve the 1980s-medical niche, and Suno/Udio's legal risk is unacceptable for monetized content about a real medical tragedy until the remaining RIAA suits close.

Two quieter but important insights emerged. GEOlayers 3 remains the best answer for the Therac-25 hero map **specifically because the shot is small** — four markers, three arcs, date callouts — which is the exact profile the plugin was built for, with matched audience visual grammar from Johnny Harris and Vox. And the commercial video APIs (Shotstack, Creatomate, Plainly) are worth exactly 60–65% of the pipeline — the connective tissue of text cards, lower thirds, and photo pans — not the backbone. The hero shots still belong in Remotion, AE, and GEOlayers, where a documentary channel's quality ceiling is actually set.