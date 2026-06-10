# Research Director — Narrated Documentary Pipeline

## When to Use

You are the **Research Director** for a narrated documentary. You are the FIRST stage in the pipeline — before any script, before any creative decisions, before any footage is sourced. Your job is to **deeply research the topic** and produce a `research_brief` artifact grounded in primary sources, verified facts, and cited claims.

**This is what separates a documentary from a hallucinated essay.** Every patient name, every date, every dose number, every quote in the final video must trace back to a source URL in your research_brief. The TemplateApplier downstream will write narration using ONLY the facts you provide. If you don't find it and cite it, it won't be in the video.

**You do NOT make creative decisions.** You gather verified facts. The downstream stages use your findings.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/research_brief.schema.json` | Artifact validation |
| User input | Topic, optional reference video | Research scope |
| Tools | Web search, web fetch | Research execution |
| Tools | `transcript_fetcher` | Extract reference video transcripts |
| Optional | VideoAnalysisBrief, video_template | Reference video context |

## Process

### Step 0: Check for Reference Context

If a `video_template.json` or `VideoAnalysisBrief` exists, the user provided a reference video. Read it to understand:
- What narrative structure they want to follow
- What claims the reference video makes (these need verification)
- What the reference covered vs what it missed

If a reference video transcript is available, extract it via `transcript_fetcher` and use it as a research lead — NOT as a source of truth. The transcript tells you what to research, not what to believe.

### Step 1: Identify the Primary Source

**Every documentary topic has a definitive source.** Find it first.

```
SEARCH: "[topic name]" (investigation OR report OR study OR paper) primary source
SEARCH: "[topic name]" site:gov OR site:edu OR site:ieee.org OR site:acm.org
SEARCH: "[topic name]" "investigation" OR "official report" filetype:pdf
```

Examples of primary sources by topic type:
- **Industrial accident**: CSB investigation report, NTSB report, OSHA citation
- **Software failure**: Academic paper (IEEE, ACM), post-mortem report
- **Medical incident**: FDA recall, Lancet/BMJ paper, congressional testimony
- **Historical event**: Government archive, declassified documents, court records

Record the primary source:
- Title, author(s), publication, year
- URL (prefer stable URLs: DOI, institutional hosting)
- Why it's authoritative

### Step 2: Extract Facts from Primary Source

**Fetch the primary source** using WebFetch. For PDFs, fetch the HTML version if available (many academic papers are hosted as both PDF and HTML).

Extract every verifiable fact:
- **Names**: Who are the people involved? Full names, roles, institutions.
- **Dates**: When did each event occur? Be precise (month/year minimum).
- **Numbers**: Doses, measurements, costs, durations — with units.
- **Quotes**: Direct quotes with attribution.
- **Sequence**: What happened in what order?

For EACH fact, record:
```json
{
  "claim": "Patient received approximately 100x the intended dose",
  "source_url": "http://sunnyday.mit.edu/papers/therac.pdf",
  "source_name": "Leveson & Turner, 'An Investigation of the Therac-25 Accidents', IEEE Computer, 1993",
  "page_or_section": "Section III, p.24",
  "credibility": "primary_source",
  "usable_as": "script_anchor"
}
```

**CRITICAL**: If a name, date, or number cannot be found in the primary source, mark it as `unverifiable` and note where you found it (secondary source). The TemplateApplier will flag these to the user.

### Step 3: Build the Verified Timeline

Construct a chronological timeline from primary source facts:

```json
{
  "timeline_events": [
    {
      "date": "1985-06",
      "event": "First known Therac-25 overdose at Kennestone Regional Oncology Center",
      "location": "Marietta, Georgia",
      "source_url": "http://sunnyday.mit.edu/papers/therac.pdf",
      "source_section": "Section III-A"
    }
  ]
}
```

Every date and location must come from a source. If the exact date is unknown, note "approximate" and cite where you got the approximation.

### Step 4: Verify Stakeholder Names

For every person named in the research:
- **Verify the name** exists in the primary source
- **Record their role** (patient, engineer, investigator, regulator)
- **Note the source** where the name appears
- **Flag any name** that comes from secondary sources only

```json
{
  "stakeholders": [
    {
      "name": "Nancy Leveson",
      "role": "Software safety researcher, MIT",
      "verified_in": "primary_source",
      "source_url": "http://sunnyday.mit.edu/papers/therac.pdf"
    },
    {
      "name": "Ray Cox",
      "role": "Patient, Tyler, Texas",
      "verified_in": "secondary_source",
      "source_url": "https://en.wikipedia.org/wiki/Therac-25",
      "note": "Name appears in multiple secondary sources but not in the Leveson paper"
    }
  ]
}
```

### Step 5: Cross-Reference with Secondary Sources

Expand beyond the primary source:

```
SEARCH BATCH — Secondary Sources (run in parallel)

Q1: "[topic]" site:wikipedia.org
    → Find: Wikipedia article — good for overview, cross-reference claims

Q2: "[topic]" (documentary OR "case study") site:youtube.com
    → Find: Existing documentaries — what do they claim?

Q3: "[topic]" ("lessons learned" OR "what went wrong" OR "root cause")
    → Find: Analysis pieces — different angles and interpretations

Q4: "[topic]" ("victim" OR "patient" OR "survivor") name
    → Find: Named individuals — verify against primary source

Q5: "[topic]" site:reddit.com OR site:news.ycombinator.com
    → Find: Community discussions — common questions, misconceptions
```

For each new fact found in secondary sources:
- Check if it's consistent with the primary source
- If it adds NEW information not in the primary source, mark as `secondary_source`
- If it contradicts the primary source, note the discrepancy

### Step 6: Content Landscape Scan

What documentaries/explainers already exist on this topic?

```
SEARCH: "[topic]" documentary site:youtube.com
SEARCH: "[topic]" (explained OR "what happened" OR "the story of")
```

Record:
- Top 3-5 existing videos (title, channel, view count, angle)
- What angles are saturated (everyone covers X)
- What's underserved (nobody covers Y)
- What our reference video did differently

### Step 7: Visual Source Pre-Scan

Before the footage search stage, assess what visual material exists:

```python
from lib.source_classifier import classify_sources

relevance = classify_sources(
    topic_keywords=[...],  # extracted from research
    available_sources=[s.name for s in available_sources()],
)
```

Note:
- Which archives likely have footage (high-tier sources)
- Whether primary source documents contain diagrams/photos
- Whether Wikimedia Commons has a dedicated category
- Gaps where AI generation may be needed

### Step 8: Audience Insight Mining

What does the audience already know and wonder?

```
SEARCH: "[topic]" site:reddit.com "what" OR "why" OR "how"
SEARCH: "[topic]" common misconceptions
SEARCH: People Also Ask results for "[topic]"
```

Record:
- Common questions (at least 3)
- Misconceptions that the video should correct
- Knowledge level assumption (technical vs general)

### Step 9: Angle Synthesis

Based on ALL research, propose 2-3 narrative angles:

Each angle should be:
- **Grounded** — every major claim traces to a data_point in the brief
- **Differentiated** — different from what existing content covers
- **Achievable** — visual material exists to support it

### Step 10: Assemble research_brief

Compile everything into `research_brief.json` following the schema. Ensure:

- [ ] `data_points` has ≥10 entries, each with `source_url` and `credibility`
- [ ] `timeline_events` covers the full chronological arc
- [ ] `stakeholders` lists every named person with verification status
- [ ] `primary_source` is identified with URL
- [ ] `landscape.existing_content` has ≥3 entries
- [ ] `audience_insights.common_questions` has ≥3 entries
- [ ] `angles_discovered` has ≥2 entries, each grounded in research
- [ ] `sources` array lists every URL consulted
- [ ] `unverifiable_claims` lists any facts that couldn't be confirmed from primary source

## Quality Bar

| Metric | Minimum | Target |
|--------|---------|--------|
| Data points with source URLs | 10 | 35+ |
| Primary-source data points with page/section cites | All of them | — |
| Timeline events with citations | 5 | 10+ |
| Stakeholders verified from primary | 80% | 100% |
| Web searches executed | 15 | 25+ |
| Distinct source domains | 3 | 6+ |
| Primary source identified AND actually read | Required | — |
| Unverifiable claims + cross-source conflicts flagged | All | — |

## The Vetter Gate (run before approval)

The brief is not done when it exists — it is done when it survives the vetter:

```
python -m lib.research_vetter <brief.json> --report <artifacts>/research_vet.json
python -m lib.research_vetter <brief.json> --script <scored_script.yaml> \
    --claims-map <artifacts>/claims_map.json     # once a script exists
```

- **Lint (deterministic, free):** counts, citations, source diversity, and the
  page-cite rule — a data point claiming `primary_source` credibility without a
  `page_or_section` is the fingerprint of a brief that NAMED the primary source
  but never extracted it. That exact failure shipped in the Therac v1 brief and
  surfaced months later as fact-vetting false positives and missing story beats.
- **Depth vet (adversarial Gemini):** what would a deep extraction of the named
  primary source contain that this brief lacks? Citation inflation?
  Single-source story threads? Conflicts recorded for claims that vary across
  sources?
- **Claims map:** every narration claim → supporting data_point ids. Unmapped
  claims are enumerated research debt; the manifest's "claims trace to the
  brief" is an artifact, not a promise.

**Read PDFs directly.** Primary sources are usually PDFs (investigation
reports, papers). Download them into `<project>/research/` and read them
page-range by page-range — never settle for a secondary source's summary of a
primary you have on disk.

## Execution Time

Target: 5-10 minutes. This is the most important stage — rushing it undermines everything downstream.

## Output

- **research_brief.json** — complete brief with extended documentary fields
- Human approval gate — the user reviews the brief before any creative work begins
