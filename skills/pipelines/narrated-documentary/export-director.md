# Export Director - Narrated Documentary Pipeline

## When To Use

The video is reviewed and approved. You now prepare it for publishing
with YouTube-optimized metadata: SEO title, description with chapter
markers, tags, and the final color-graded render.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Prior artifact | review_report | Approved status confirmation |
| Prior artifact | segment_plan | Scene data for chapter markers |
| Prior artifact | render_report | Final render path and metadata |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Takes the approved render and prepares all metadata needed for YouTube
publishing. Generates an SEO-optimized title, a description with
chapter markers derived from the segment plan, and a tag set covering
the disaster name, industry, and documentary terms.

## Inputs

- **review_report** — must have status "approved"
- **segment_plan** — scene data for chapter timestamps
- **render_report** — final render path

## Outputs

- **publish_log** — version, title, description, tags, render path,
  publishing metadata

## Workflow

### 1. Verify Approval Status

Read review_report.status. If not "approved", STOP. Do not export a
video that has not passed review. If status is "revision_needed",
direct the user back to the review stage.

### 2. Generate SEO Title

Tech disaster documentary titles follow a specific pattern for
YouTube SEO:

**Pattern:** "[What Happened] — [Consequence/Scale] | [Series Name]"

Examples:
- "The Texas City Refinery Explosion — 15 Dead, BP's Worst Day | Tech Disasters"
- "Deepwater Horizon: The 87 Days That Changed Drilling | Tech Disasters"
- "Bhopal: The Night That Killed Thousands | Tech Disasters"

Rules:
- Under 100 characters (YouTube truncates beyond this)
- Include the disaster name (primary search term)
- Include a consequence or scale indicator (creates urgency)
- Include the series name for channel consistency
- No clickbait — the real story is dramatic enough

### 3. Write Description With Chapter Markers

YouTube descriptions need:

**First 2 lines (above the fold):** Hook that makes viewers click.
"On March 23, 2005, a routine startup at BP's Texas City refinery
turned into the deadliest industrial disaster in a decade."

**Chapter markers:** Derived from the segment plan. Map major scene
transitions to timestamps:

```
0:00 The Morning Shift
0:42 Warning Signs Ignored
1:18 The Explosion
2:05 Emergency Response
3:12 The Investigation
4:28 What We Learned
```

Rules for chapters:
- At least 3 chapters (YouTube requirement for chapter feature)
- First chapter must start at 0:00
- Chapter titles are 2-5 words (short and scannable)
- Align to the segment plan's natural scene breaks

**Sources and credits:** List all footage sources used:
"Footage: U.S. Chemical Safety Board, National Archives, Pexels"

**Standard footer:** Subscribe CTA, links to related videos, hashtags.

### 4. Generate Tags

Tags should cover three tiers:

**Tier 1 — Specific (highest value):**
- Disaster name: "texas city refinery explosion"
- Organization: "BP Texas City" "CSB investigation"
- Year: "2005 industrial disaster"

**Tier 2 — Category:**
- "industrial disaster documentary"
- "chemical plant explosion"
- "workplace safety documentary"
- "refinery accident"

**Tier 3 — General:**
- "documentary" "tech disasters" "engineering failure"
- "investigation" "safety" "what went wrong"

Aim for 15-25 tags. YouTube ignores tags beyond 500 characters total.

### 5. Prepare The Final Package

Confirm the final render file:
- Path from render_report
- Verify the file still exists and is playable
- Record file size for upload estimation

### 6. Emit The Publish Log

```json
{
  "version": "1.0",
  "title": "The Texas City Refinery Explosion — 15 Dead, BP's Worst Day | Tech Disasters",
  "description": "On March 23, 2005, a routine startup...\n\n0:00 The Morning Shift\n0:42 Warning Signs...",
  "tags": [
    "texas city refinery explosion",
    "BP Texas City",
    "CSB investigation",
    "industrial disaster documentary",
    "tech disasters"
  ],
  "render_path": "projects/texas-city/renders/final.mp4",
  "thumbnail_suggestions": [
    "Frame at 1:18 — explosion moment with CSB watermark",
    "Frame at 0:05 — wide refinery shot with title overlay"
  ],
  "metadata": {
    "category": "Education",
    "language": "en",
    "visibility": "public",
    "chapters_count": 6,
    "tags_count": 18,
    "description_length": 842
  }
}
```

## Quality Bar

- Title under 100 characters with disaster name included
- Description has at least 3 chapter markers starting at 0:00
- Tags cover all three tiers (specific, category, general)
- Total tag characters under 500
- Render file verified as existing and playable
- Review status confirmed as "approved" before any export work

## Common Mistakes

- **Clickbait titles.** "YOU WON'T BELIEVE WHAT HAPPENED" destroys
  credibility for a documentary channel. The real story is dramatic
  enough — use it.
- **Missing chapter markers.** YouTube's chapter feature is a major
  SEO signal. Always include at least 3 chapters. Always start at 0:00.
- **Generic tags.** "documentary" alone is useless. Include the specific
  disaster name, organization, and year — those are the search terms
  people actually use.
- **Forgetting source credits.** Investigation footage from CSB/NTSB
  should be credited in the description. This is both ethical and
  builds authority.
- **Exporting before approval.** If review_report.status is not
  "approved", do not proceed. This is a hard gate.
- **Overly long descriptions.** YouTube shows only 2-3 lines above
  the fold. Front-load the hook and chapters. Save detailed credits
  for below the fold.

## Tools Available

No tools are used in this stage. The export is a metadata preparation
process driven by the agent using data from upstream artifacts.
