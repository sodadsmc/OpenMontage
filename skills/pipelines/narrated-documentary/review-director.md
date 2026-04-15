# Review Director - Narrated Documentary Pipeline

## When To Use

The video is rendered. Before export, a human must review the final
output and decide whether to approve, request revisions, or swap
weak clips. This is the quality gate between rendering and publishing.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/review_report.schema.json` | Artifact validation |
| Prior artifact | render_report | Rendered video path and metadata |
| Meta | `skills/meta/reviewer.md` | Self-review pass |

## What This Stage Does

Presents the rendered video to the human reviewer. Flags weak spots
proactively (scenes with low scoring confidence, AI-generated gap
fills, color coherence issues). Collects approval or revision notes.
Target: complete the review within 10-15 minutes.

## Inputs

- **render_report** — path to rendered video, duration, verification
  notes

## Outputs

- **review_report** — version, status (approved/revision_needed),
  flagged_scenes, notes

## Workflow

### 1. Prepare The Review Package

Before presenting to the user, compile a concise review summary:

- Total video duration
- Number of scenes
- Footage breakdown: X% investigation footage, Y% stock, Z% AI-generated
- Any warnings from the render stage
- Scenes flagged during scoring (low confidence picks)
- Scenes with AI-generated gap fills

Present this summary BEFORE asking the user to watch. It gives context
for what to look for.

### 2. Flag Weak Spots Proactively

Do not make the reviewer hunt for problems. Pre-flag:

- **Low-confidence scenes:** Scenes where the scoring manifest shows
  the selected clip had a composite score below 0.60
- **AI-generated scenes:** Any scene filled by gap_fill (always flag
  these — the reviewer should know which footage is generated)
- **Color coherence warnings:** Scene pairs flagged by the two-pass
  color check in the scoring stage
- **Duration mismatches:** Scenes where the rendered cut duration
  differs from the assembly plan by > 0.5s
- **Transition issues:** Hard cuts where dissolves were planned, or
  vice versa

For each flagged scene, provide:
- Scene ID and timestamp in the video
- Reason for flagging
- Runner-up clip suggestion (from scoring_manifest) if the reviewer
  wants to swap

### 3. Present For Human Review

Structure the review request clearly:

```
REVIEW: The Texas City Disaster (5:12)

Render: projects/texas-city/renders/final.mp4

SUMMARY:
- 24 scenes, 5:12 total duration
- 79% investigation footage (CSB/NTSB), 17% stock, 4% AI-generated
- Color grade: cool_desaturated_40
- Music: ambient_dark_tension.mp3 from library

FLAGGED SCENES (review these first):
1. Scene 07 (1:23-1:38) — AI-generated footage
   Reason: No investigation footage of the internal valve failure
   Swap option: pexels_industrial_valve_28441 (score 0.42)

2. Scene 15 (3:01-3:12) — Low confidence (score 0.51)
   Reason: Best available clip shows a different refinery
   Swap option: archive_org_csb_2005_clip19 (score 0.48)

PLEASE REVIEW:
- Watch the full video once through
- Check flagged scenes for visual quality and narrative fit
- Note any additional scenes that feel wrong
- Approve or request revisions
```

### 4. Process Review Feedback

Based on user response:

**If approved:** Record status as "approved" with any notes.

**If revision needed:** Record:
- Which scenes need changes
- What kind of change (swap clip, adjust timing, fix audio, re-grade)
- Which runner-up clip to use (if swapping)
- Any new notes or direction

Revision requests loop back to the appropriate upstream stage:
- Clip swap -> re-run scoring or assembly for affected scenes
- Timing fix -> re-run assembly
- Audio fix -> re-run render
- Re-grade -> re-run render with different LUT

### 5. Emit The Review Report

```json
{
  "version": "1.0",
  "status": "approved",
  "flagged_scenes": [
    {
      "scene_id": "scene_07",
      "reason": "AI-generated footage — no real footage available",
      "timestamp": "1:23-1:38"
    }
  ],
  "notes": "Approved with note: scene 07 AI footage is acceptable given no real footage exists for internal valve failure."
}
```

Or for revisions:

```json
{
  "version": "1.0",
  "status": "revision_needed",
  "flagged_scenes": [
    {
      "scene_id": "scene_15",
      "reason": "Wrong refinery — reviewer recognized the facility",
      "timestamp": "3:01-3:12",
      "action": "swap to archive_org_csb_2005_clip19"
    }
  ],
  "notes": "Swap scene 15 clip and re-render. Everything else approved."
}
```

## Quality Bar

- Review package presented with proactive flags and timestamps
- All AI-generated scenes explicitly flagged
- Runner-up swap suggestions provided for flagged scenes
- Review completed within 10-15 minutes
- Clear approval or actionable revision notes recorded
- Status is either "approved" or "revision_needed" (no ambiguity)

## Common Mistakes

- **Presenting the video without context.** The reviewer needs to know
  what to look for. Always provide the summary and flagged scenes.
- **Not flagging AI-generated footage.** The reviewer must know which
  scenes use generated footage. Hiding this is a trust violation.
- **Vague revision notes.** "Make it better" is not actionable. Record
  the specific scene, the problem, and the proposed fix.
- **Making the review take too long.** Pre-flagging weak spots lets the
  reviewer focus attention. Without flags, they watch the whole video
  twice trying to spot problems.
- **Auto-approving.** This stage has human_approval_default: true for a
  reason. Do not skip the human review, even if all scores are high.

## Tools Available

No tools are used in this stage. The review is a human evaluation
process facilitated by the agent presenting structured information
and collecting feedback.
