"""Shot Manifest — the bulk video-generation job spec.

The pipeline is split into two phases so the only GPU-bound work (Wan
image-to-video) happens in one batch on a rented box:

  Phase A (local, cheap, API-only): build the render package — Asset Bible +
    canonical images + per-shot keyframes (Nano Banana) + hero shot clips
    (Kie.ai) + this Shot Manifest.
  Phase B (rented GPU box): run every remaining Wan i2v job in the manifest.
  Phase C: concat shots -> segments -> final render.

A ShotJob fully specifies one clip to generate; nothing is decided at generation
time. Hero shots are generated during Phase A (status="done") so they can be
reviewed before paying for the box batch.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.1"


@dataclass
class ShotJob:
    """One clip to generate — fully resolved, ready for batch execution."""
    segment_id: str
    shot_id: str
    provider: str            # "wan" (box default) | "kie" (hero, generated in prep)
    operation: str           # "image_to_video" | "text_to_video"
    video_prompt: str
    duration_s: float
    seed: int
    output: str              # clip output path
    keyframe: str = ""       # i2v anchor image path; "" => text_to_video
    aspect_ratio: str = "16:9"
    hero: bool = False
    status: str = "pending"  # pending | done | failed
    # Continuation chaining: when set, this shot CONTINUES the named sibling shot —
    # at generation time its anchor is the previous clip's extracted final frame
    # (keyframe above stays as the fallback anchor if extraction/hosting fails).
    chain_from: str = ""     # shot_id of the predecessor leg in the same segment
    # Content context for the post-generation quality gate (clip-vs-meaning checks).
    # video_prompt alone is style-decorated; these carry the undecorated intent.
    description: str = ""    # the shot's content prompt (pre style suffix)
    narration: str = ""      # the segment narration this shot plays under
    # HARD-SHOT identity lane: hosted reference-image URLs (beat keyframe + model sheets) for
    # provider "veo-ref-kie" (reference_to_video). Empty for every other provider. Old manifests
    # without this key load fine (default) — do not reorder fields above it.
    veo_refs: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.segment_id}/{self.shot_id}"


@dataclass
class SegmentPlan:
    """How a segment's shots concatenate into one segment clip."""
    segment_id: str
    target_duration_s: float
    shot_ids: list[str] = field(default_factory=list)
    output: str = ""         # concatenated segment clip path


@dataclass
class ShotManifest:
    """The complete bulk-generation job spec for one documentary."""
    project: str
    shots: list[ShotJob] = field(default_factory=list)
    segments: list[SegmentPlan] = field(default_factory=list)
    version: str = SCHEMA_VERSION

    def pending_jobs(self, provider: str | None = None) -> list[ShotJob]:
        """Jobs not yet done, optionally filtered to one provider (e.g. 'wan')."""
        return [s for s in self.shots
                if s.status != "done" and (provider is None or s.provider == provider)]

    def shots_for_segment(self, segment_id: str) -> list[ShotJob]:
        return [s for s in self.shots if s.segment_id == segment_id]

    def get_shot(self, segment_id: str, shot_id: str) -> ShotJob | None:
        for s in self.shots:
            if s.segment_id == segment_id and s.shot_id == shot_id:
                return s
        return None

    def counts(self) -> dict[str, int]:
        by_provider: dict[str, int] = {}
        done = 0
        for s in self.shots:
            by_provider[s.provider] = by_provider.get(s.provider, 0) + 1
            if s.status == "done":
                done += 1
        return {"total": len(self.shots), "done": done, **{f"provider_{k}": v for k, v in by_provider.items()}}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "project": self.project,
            "segments": [asdict(s) for s in self.segments],
            "shots": [asdict(s) for s in self.shots],
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict) -> "ShotManifest":
        return cls(
            project=d.get("project", ""),
            shots=[ShotJob(**s) for s in d.get("shots", [])],
            segments=[SegmentPlan(**s) for s in d.get("segments", [])],
            version=d.get("version", SCHEMA_VERSION),
        )

    @classmethod
    def load(cls, path: str | Path) -> "ShotManifest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
