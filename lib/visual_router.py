"""Visual strategy router for the narrated-documentary pipeline.

Reads a scene's ``visual_strategy`` field and generates the appropriate
visual asset — Manim animation, Mermaid diagram, Remotion chart, or
text card.  Scenes routed to ``stock_footage``, ``archival``, or
``generated`` are handled by the existing footage_search and gap_fill
stages and return None here.

Runs AFTER footage_search and BEFORE assembly.  Produces short MP4 or
PNG files that the assembly stage inserts alongside stock footage cuts.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.quality_gate import generate_with_quality_gate
from lib.shot_prompt_builder import build_shot_prompt
from lib.channel_style import apply_to_prompt

_log = logging.getLogger(__name__)

# AI-video seam tuning
MAX_SHOT_SECONDS = 12.0              # Grok i2v does 6-15s clips; long segments split into N shots
DEFAULT_VIDEO_PROVIDER = "grok-kie"  # Grok Imagine i2v via Kie.ai (cloud — no GPU box)
HERO_VIDEO_PROVIDER = "grok-kie"     # same model for the hero preview (matches bulk look)
# Alternatives: "wan" (free, needs a GPU box) for bulk; "kie" (premium Veo/Runway) per hero shot.


@dataclass
class VisualAsset:
    """A generated visual asset ready for the assembly stage."""
    scene_id: str
    path: str
    kind: str        # "video" or "image"
    duration: float  # seconds (0 for images)
    strategy: str    # which visual_strategy produced this
    description: str


def _sanitize_for_manim(text: str) -> str:
    """Clean text for safe embedding in Manim Python code strings.

    Replaces Unicode characters that cause rendering artifacts or
    syntax errors in Manim's Text() objects and Python string literals.
    """
    replacements = {
        "\u2014": " - ",    # em-dash
        "\u2013": " - ",    # en-dash
        "\u2192": "->",     # right arrow →
        "\u2190": "<-",     # left arrow ←
        "\u2194": "<->",    # left-right arrow ↔
        "\u2018": "'",      # left single curly quote
        "\u2019": "'",      # right single curly quote
        "\u201C": '"',      # left double curly quote
        "\u201D": '"',      # right double curly quote
        "\u2026": "...",    # horizontal ellipsis
        "\u2012": " - ",    # figure dash
        "\u2015": " - ",    # horizontal bar
        "\u00e2\u20ac\u201c": " - ",  # mojibake em-dash
        "\u00e2\u20ac\u201d": " - ",  # mojibake em-dash variant
        '"': "'",           # double quotes break Python strings in code templates
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    # Strip remaining non-ASCII but preserve \n (Manim Text supports newlines)
    text = text.encode("ascii", errors="replace").decode("ascii")
    return text


def route_scene(
    scene: dict[str, Any],
    output_dir: str | Path,
    topic: str = "",
    target_duration: float = 0.0,
) -> VisualAsset | None:
    """Generate a visual asset for one scene based on its visual_strategy.

    Returns None for footage strategies handled by other pipeline stages
    (stock_footage, archival, generated, mixed). ``ai_video`` is generated here.
    """
    strategy = scene.get("visual_strategy", "stock_footage")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if strategy == "math_animation":
        return _generate_manim(scene, output_dir, topic)
    elif strategy == "diagram":
        return _generate_manim_diagram(scene, output_dir, topic)
    elif strategy == "animated_chart":
        return _generate_remotion_chart(scene, output_dir)
    elif strategy == "text_card":
        return _generate_text_card(scene, output_dir)
    elif strategy == "ai_video":
        from types import SimpleNamespace
        spec = SimpleNamespace(
            description=scene.get("description") or scene.get("narration", ""),
            effective_prompt=(scene.get("ai_prompt") or scene.get("description")
                              or scene.get("narration", "")),
            ai_prompt=scene.get("ai_prompt"),
            ai_motion=scene.get("ai_motion"),
            ai_style=scene.get("ai_style"),
            ai_reference_image=scene.get("ai_reference_image"),
            asset_ref=scene.get("asset_ref"),
            location_id=scene.get("location_id"),
            shots=[],
        )
        return generate_ai_video(
            scene.get("scene_id", "scene"), spec, output_dir, target_duration,
        )
    else:
        # stock_footage, archival, generated, mixed — handled elsewhere
        return None


def route_all_scenes(
    segment_plan: dict[str, Any],
    output_dir: str | Path,
    topic: str = "",
) -> dict[str, VisualAsset]:
    """Route all scenes and return a map of scene_id → VisualAsset.

    Only scenes with generatable strategies are included.
    """
    results: dict[str, VisualAsset] = {}
    for scene in segment_plan.get("scenes", []):
        target_duration = float(scene.get("target_duration_s", scene.get("duration_s", 0.0)) or 0.0)
        asset = route_scene(scene, output_dir, topic, target_duration)
        if asset is not None:
            results[asset.scene_id] = asset
            _log.info("Generated %s for %s: %s", asset.strategy, asset.scene_id, asset.path)
    return results


# ---------------------------------------------------------------------------
# AI video generation (the AI-primary seam)
# ---------------------------------------------------------------------------

def plan_ai_video(
    segment_id: str,
    visual_spec: Any,
    keyframe_dir: str | Path,
    target_duration_s: float,
    bible: Any = None,
    asset: Any = None,
) -> list[dict[str, Any]]:
    """Phase A (planning): split a segment into shots, generate each shot's
    keyframe (Nano Banana — API, no GPU), and return fully-resolved shot-job
    dicts. Does NOT generate video; that is the bulk/box step (Phase B).

    Every shot is anchored to the asset's canonical reference image so the
    location/subject stays consistent. Returns one job dict per shot.
    """
    keyframe_dir = Path(keyframe_dir)
    keyframe_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the Asset Bible entry (explicit param > asset_ref > location_id)
    if asset is None and bible is not None:
        ref_id = getattr(visual_spec, "asset_ref", None)
        if ref_id:
            asset = bible.get(ref_id)
        if asset is None and getattr(visual_spec, "location_id", None):
            from lib.asset_bible import location_asset_id
            asset = bible.get(location_asset_id(visual_spec.location_id))

    asset_id = getattr(asset, "asset_id", None) or getattr(visual_spec, "asset_ref", None)
    canonical_ref = None
    if asset is not None:
        # Prefer the provider-hosted URL (host-free i2v anchor); fall back to the local file.
        canonical_ref = getattr(asset, "canonical_image_url", "") or None
        if canonical_ref is None and bible is not None:
            ref = bible.resolve_reference_image(asset.asset_id)
            canonical_ref = str(ref) if ref else None
    if canonical_ref is None:
        canonical_ref = getattr(visual_spec, "ai_reference_image", None)

    base_prompt = (getattr(visual_spec, "effective_prompt", None)
                   or getattr(visual_spec, "description", "") or "")
    ai_style = getattr(visual_spec, "ai_style", None)
    seg_motion = getattr(visual_spec, "ai_motion", None)
    locked = list(getattr(asset, "locked_attributes", []) or [])
    base_seed = abs(hash(segment_id)) % 1_000_000

    jobs: list[dict[str, Any]] = []
    for i, shot in enumerate(_plan_shots(visual_spec, target_duration_s)):
        shot_id = shot["shot_id"]
        shot_prompt = shot["ai_prompt"] or base_prompt
        shot_motion = shot["ai_motion"] or seg_motion
        provider = HERO_VIDEO_PROVIDER if shot["hero"] else DEFAULT_VIDEO_PROVIDER

        # Keyframe (i2v anchor) locked to the canonical look + channel style (medium)
        keyframe_prompt = apply_to_prompt(
            bible.build_prompt_anchor(asset_id, shot_prompt) if bible is not None else shot_prompt
        )
        keyframe = _nano_keyframe(
            keyframe_prompt, canonical_ref, keyframe_dir / f"{segment_id}_{shot_id}_key.png"
        )

        # Motion/mood video prompt + the channel style medium
        scene_dict = {
            "description": shot_prompt,
            "texture_keywords": locked,
            "shot_language": ({"camera_movement": shot_motion} if shot_motion else {}),
        }
        video_prompt = apply_to_prompt(
            build_shot_prompt(scene_dict, {"mood": ai_style} if ai_style else None)
        )

        jobs.append({
            "segment_id": segment_id,
            "shot_id": shot_id,
            "provider": provider,
            "operation": "image_to_video" if keyframe else "text_to_video",
            "video_prompt": video_prompt,
            "duration_s": round(shot["seconds"], 3),
            "seed": base_seed + i * 1000,
            "keyframe": str(keyframe) if keyframe else "",
            "aspect_ratio": "16:9",
            "hero": bool(shot["hero"]),
        })
    return jobs


def generate_shot(
    video_prompt: str,
    keyframe: Path | str | None,
    duration_s: float,
    provider: str,
    seed: int,
    output_path: Path | str,
    visual_spec: Any = None,
    enable_gemini: bool = True,
) -> Path | None:
    """Phase B (execution): generate one shot clip with the quality-gate retry
    loop. Returns the clip Path, or None if all attempts fail."""
    kf = Path(keyframe) if keyframe else None
    out = Path(output_path)

    def gen_fn(spec, dur, attempt):
        return _gen_shot_clip(video_prompt, kf, dur, provider, seed + attempt, out)

    clip, _report = generate_with_quality_gate(
        gen_fn, visual_spec, duration_s, str(out),
        max_attempts=3, enable_gemini=enable_gemini,
    )
    return clip


def concat_segment_shots(clip_paths: list[str], output_path: Path | str,
                         target_duration_s: float) -> Path | None:
    """Concatenate a segment's shot clips into one clip of exactly target_duration_s."""
    return _concat_clips(clip_paths, Path(output_path), target_duration_s)


def generate_ai_video(
    segment_id: str,
    visual_spec: Any,
    output_dir: str | Path,
    target_duration_s: float,
    bible: Any = None,
    asset: Any = None,
    enable_gemini: bool = True,
) -> VisualAsset | None:
    """Inline all-in-one AI video for one segment (plan + execute + concat).

    Used for non-batched runs. The two-phase prep/bulk path calls plan_ai_video
    (Phase A) and generate_shot (Phase B) directly instead. Returns None if no
    shot could be produced (the caller then falls back to stock footage).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    jobs = plan_ai_video(segment_id, visual_spec, output_dir, target_duration_s,
                         bible=bible, asset=asset)
    clip_paths: list[str] = []
    for job in jobs:
        out = output_dir / f"{segment_id}_{job['shot_id']}.mp4"
        clip = generate_shot(
            job["video_prompt"], job["keyframe"] or None, job["duration_s"],
            job["provider"], job["seed"], out, visual_spec, enable_gemini,
        )
        if clip is None:
            clip = _fallback_shot(segment_id, job["shot_id"], visual_spec, job["duration_s"], output_dir)
        if clip is None:
            _log.warning("ai_video: no clip for %s/%s — skipping shot", segment_id, job["shot_id"])
            continue
        clip_paths.append(str(clip))

    if not clip_paths:
        return None

    combined = _concat_clips(
        clip_paths, output_dir / f"{segment_id}_aivideo.mp4", target_duration_s
    )
    if combined is None:
        return None
    dur = _probe_duration(str(combined)) or target_duration_s
    desc = (getattr(visual_spec, "effective_prompt", None)
            or getattr(visual_spec, "description", "") or "")[:60]
    return VisualAsset(
        scene_id=segment_id, path=str(combined), kind="video",
        duration=dur, strategy="ai_video", description=desc,
    )


def _plan_shots(visual_spec: Any, target_duration_s: float) -> list[dict[str, Any]]:
    """Split a segment into shots — explicit visual_spec.shots, else auto by length."""
    import math

    shots = list(getattr(visual_spec, "shots", None) or [])
    if shots:
        total_w = sum(max(0.0, getattr(s, "duration_weight", 1.0)) for s in shots)
        out: list[dict[str, Any]] = []
        for s in shots:
            w = max(0.0, getattr(s, "duration_weight", 1.0))
            secs = (target_duration_s * (w / total_w)) if total_w else (target_duration_s / len(shots))
            out.append({
                "shot_id": getattr(s, "shot_id"),
                "ai_prompt": getattr(s, "ai_prompt", None),
                "ai_motion": getattr(s, "ai_motion", None),
                "hero": bool(getattr(s, "hero", False)),
                "seconds": secs,
            })
        return out

    n = max(1, math.ceil(target_duration_s / MAX_SHOT_SECONDS)) if target_duration_s > 0 else 1
    secs = target_duration_s / n if n else target_duration_s
    return [
        {"shot_id": f"shot_{i + 1:02d}", "ai_prompt": None, "ai_motion": None,
         "hero": False, "seconds": secs}
        for i in range(n)
    ]


def _nano_keyframe(prompt: str, ref_image: str | None, output_path: Path) -> Path | str | None:
    """Resolve the per-shot image-to-video anchor.

    Default: use the canonical reference directly as the i2v anchor — a provider
    URL (host-free, passed straight to the video model) or a local path. Maximizes
    cross-shot consistency.

    Opt-in per-shot variety (AI_PER_SHOT_KEYFRAMES=1): edit the canonical reference
    into a distinct per-shot framing via Nano Banana (hosting a local ref via
    lib.image_host if needed). Falls back to the direct anchor if it fails.

    When there is no reference, generate a fresh keyframe via text-to-image.
    """
    ref = str(ref_image) if ref_image else ""
    is_url = ref.startswith("http://") or ref.startswith("https://")
    have_ref = is_url or (bool(ref) and Path(ref).exists())

    if have_ref and os.environ.get("AI_PER_SHOT_KEYFRAMES") == "1":
        from lib.image_host import upload_image
        url = ref if is_url else upload_image(ref)
        if url:
            edited = _nano_image(prompt, output_path, image_urls=[url])
            if edited is not None:
                return edited
        # hosting/edit failed -> fall through to the direct anchor

    if have_ref:
        return ref if is_url else Path(ref)

    return _nano_image(prompt, output_path)


def _nano_image(prompt: str, output_path: Path, image_urls: list[str] | None = None) -> Path | None:
    """Call the Nano Banana image provider (generate, or edit when image_urls given)."""
    try:
        from tools.graphics.image_selector import ImageSelector
        sel = ImageSelector()
    except Exception as exc:  # noqa: BLE001
        _log.warning("ai_video: image selector unavailable: %s", exc)
        return None
    inputs: dict[str, Any] = {
        "prompt": prompt,
        "preferred_provider": "nano_banana",
        "aspect_ratio": "16:9",
        "output_path": str(output_path),
    }
    if image_urls:
        inputs["generation_mode"] = "edit"
        inputs["image_urls"] = image_urls
    try:
        res = sel.execute(inputs)
    except Exception as exc:  # noqa: BLE001
        _log.warning("ai_video: keyframe generation error: %s", exc)
        return None
    if getattr(res, "success", False):
        out = (res.data or {}).get("output") or (res.artifacts[0] if getattr(res, "artifacts", None) else None)
        return Path(out) if out else None
    _log.warning("ai_video: keyframe generation failed: %s", getattr(res, "error", ""))
    return None


def _gen_shot_clip(video_prompt: str, keyframe: Path | None, duration_s: float,
                   provider: str, seed: int, output_path: Path) -> Path:
    """Generate one shot via the video selector. Raises on failure so the gate retries."""
    from tools.video.video_selector import VideoSelector
    sel = VideoSelector()
    inputs: dict[str, Any] = {
        "prompt": video_prompt,
        "preferred_provider": provider,
        "aspect_ratio": "16:9",
        "duration": str(max(1, int(round(duration_s)))),
        "seed": seed,
        "output_path": str(output_path),
    }
    if keyframe:
        inputs["operation"] = "image_to_video"
        inputs["reference_image_path"] = str(keyframe)
    else:
        inputs["operation"] = "text_to_video"
    res = sel.execute(inputs)
    if not getattr(res, "success", False):
        raise RuntimeError(f"video generation failed: {getattr(res, 'error', '')}")
    out = (res.data or {}).get("output") or (res.artifacts[0] if getattr(res, "artifacts", None) else None)
    if not out:
        raise RuntimeError("video generation returned no output path")
    return Path(out)


def _fallback_shot(segment_id: str, shot_id: str, visual_spec: Any,
                   shot_seconds: float, output_dir: Path) -> Path | None:
    """Per-shot fallback hook.

    Returns None by default — segment-level stock fallback is handled by the
    caller (build_v6 / the stock_fallback stage). Kept as an explicit extension
    point so a future per-shot stock/text-card fallback can slot in here.
    """
    return None


def _concat_clips(clip_paths: list[str], output_path: Path,
                  target_duration_s: float) -> Path | None:
    """Concatenate shot clips into one segment clip of exactly target_duration_s."""
    output_path = Path(output_path)
    if len(clip_paths) == 1:
        return _trim_to_duration(clip_paths[0], output_path, target_duration_s)

    list_file = output_path.parent / f"{output_path.stem}_concat.txt"
    list_file.write_text(
        "".join(f"file '{Path(p).as_posix()}'\n" for p in clip_paths), encoding="utf-8"
    )
    joined = output_path.parent / f"{output_path.stem}_joined.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(joined)],
            capture_output=True, timeout=600, check=True,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("ai_video: concat failed: %s", exc)
        return None
    return _trim_to_duration(str(joined), output_path, target_duration_s)


def _trim_to_duration(src: str, output_path: Path, target_duration_s: float) -> Path | None:
    output_path = Path(output_path)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-t", f"{target_duration_s:.3f}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output_path)],
            capture_output=True, timeout=600, check=True,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("ai_video: trim failed: %s", exc)
        return None
    return output_path


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def _generate_manim(
    scene: dict[str, Any],
    output_dir: Path,
    topic: str,
) -> VisualAsset | None:
    """Generate a Manim animation for math/data scenes.

    Uses the classifier's ``animation_type`` when available, otherwise
    falls back to keyword detection from narration.
    """
    sid = scene["scene_id"]
    narration = scene.get("narration", "")
    atype = scene.get("animation_type", "")
    narr_lower = narration.lower()

    # Use classifier's animation_type if available
    if atype == "scientific_diagram":
        return _manim_scientific(scene, output_dir)
    elif atype == "data_visualization":
        return _manim_dose_chart(scene, output_dir)
    elif atype == "process_flow":
        return _manim_process_flow(scene, output_dir)
    elif atype == "timeline":
        return _manim_timeline(scene, output_dir)
    elif atype == "counter":
        return _manim_byte_overflow(scene, output_dir)
    elif atype == "state_diagram":
        return _manim_state_machine(scene, output_dir)

    # Fallback: keyword detection
    if any(kw in narr_lower for kw in ["dose", "rad ", "gray", "overdose", "prescribed"]):
        return _manim_dose_chart(scene, output_dir)
    elif any(kw in narr_lower for kw in ["overflow", "counter", "255", "256", "byte", "rollover"]):
        return _manim_byte_overflow(scene, output_dir)
    elif any(kw in narr_lower for kw in ["timeline", "chronolog", "sequence of events"]):
        return _manim_timeline(scene, output_dir)
    else:
        _log.warning("No Manim template matched for %s, falling back to text card", sid)
        return _generate_text_card(scene, output_dir)


def _generate_manim_diagram(
    scene: dict[str, Any],
    output_dir: Path,
    topic: str,
) -> VisualAsset | None:
    """Generate a Manim animated diagram (flowchart, state diagram)."""
    atype = scene.get("animation_type", "")
    narr_lower = scene.get("narration", "").lower()

    # Use classifier type first
    if atype == "state_diagram":
        return _manim_state_machine(scene, output_dir)
    elif atype == "process_flow":
        return _manim_process_flow(scene, output_dir)
    elif atype == "scientific_diagram":
        return _manim_scientific(scene, output_dir)

    # Fallback: keyword detection
    if any(kw in narr_lower for kw in ["race condition", "timing", "8 second", "edit"]):
        return _manim_race_condition(scene, output_dir)
    elif any(kw in narr_lower for kw in ["state machine", "mode", "x-ray", "electron"]):
        return _manim_state_machine(scene, output_dir)
    elif any(kw in narr_lower for kw in ["flow", "keystroke", "malfunction", "press"]):
        return _manim_data_flow(scene, output_dir)
    else:
        return _manim_data_flow(scene, output_dir)  # default diagram


def _generate_remotion_chart(
    scene: dict[str, Any],
    output_dir: Path,
) -> VisualAsset | None:
    """Generate an animated chart via Remotion Explainer composition."""
    # TODO: implement Remotion chart rendering
    _log.warning("Remotion chart generation not yet implemented for %s", scene["scene_id"])
    return _generate_text_card(scene, output_dir)


def generate_styled_card(
    segment_id: str,
    visual_spec: "VisualSpec",
    output_dir: str | Path,
    target_duration_s: float = 5.0,
) -> VisualAsset | None:
    """Generate a styled text card using the card_type design system.

    Routes to one of 7 distinct Manim templates based on card_type.
    Falls back to the old single-style text card if Manim fails.

    Args:
        segment_id: Segment identifier (e.g. seg_004)
        visual_spec: VisualSpec from the scored script
        output_dir: Directory for output files
        target_duration_s: Required duration from the Duration Map

    Returns:
        VisualAsset with the rendered card video
    """
    from lib.card_templates import generate_card_code

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    card_type = visual_spec.card_type or "technical_label"
    text = visual_spec.description

    # Build kwargs from visual spec fields
    kwargs: dict[str, Any] = {}
    if visual_spec.values:
        kwargs["values"] = visual_spec.values
    if visual_spec.emphasis:
        kwargs["emphasis"] = visual_spec.emphasis
    if visual_spec.terminal_text:
        kwargs["terminal_text"] = visual_spec.terminal_text
    if visual_spec.subtext:
        kwargs["subtext"] = visual_spec.subtext

    try:
        code, class_name = generate_card_code(
            card_type=card_type,
            text=text,
            target_duration_s=target_duration_s,
            **kwargs,
        )
        asset = _run_manim_scene(code, class_name, output_dir, segment_id)
        if asset:
            asset.strategy = f"text_card:{card_type}"
            return asset
    except Exception as exc:
        _log.warning("Styled card failed for %s (%s): %s", segment_id, card_type, exc)

    # Fallback to old-style text card
    _log.info("Falling back to plain text card for %s", segment_id)
    return _generate_text_card(
        {"scene_id": segment_id, "narration": text},
        output_dir,
    )


def _generate_text_card(
    scene: dict[str, Any],
    output_dir: Path,
) -> VisualAsset | None:
    """Generate a text card as a PNG image (renders as Ken Burns in CinematicRenderer)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        _log.warning("Pillow not installed — cannot generate text cards")
        return None

    sid = scene["scene_id"]
    narration = _sanitize_for_manim(scene.get("narration", ""))

    # Extract the key text — first sentence or the whole thing if short
    text = narration.split(".")[0].strip() + "." if "." in narration else narration
    if len(text) > 120:
        text = text[:117] + "..."

    # Create dark card
    img = Image.new("RGB", (1920, 1080), color=(10, 10, 26))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 52)
        small_font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
        small_font = font

    # Wrap text
    wrapped = textwrap.fill(text, width=40)
    bbox = draw.textbbox((0, 0), wrapped, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (1920 - text_w) // 2
    y = (1080 - text_h) // 2
    draw.text((x, y), wrapped, fill=(243, 246, 250), font=font)

    # Source citation at bottom
    source = scene.get("source_citation", "")
    if source:
        draw.text((60, 1020), source, fill=(128, 128, 128), font=small_font)

    png_path = output_dir / f"{sid}_text_card.png"
    img.save(str(png_path), quality=95)

    # Convert PNG to video with Ken Burns zoom so FFmpeg compose can use it
    mp4_path = output_dir / f"{sid}_text_card.mp4"
    card_duration = min(8, max(3, len(text) // 15))  # scale duration with text length
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", str(png_path),
                "-t", str(card_duration),
                "-filter_complex",
                f"zoompan=z='min(zoom+0.0006,1.04)':d={card_duration * 30}:s=1920x1080:fps=30,format=yuv420p",
                "-c:v", "libx264", "-crf", "23", "-preset", "medium",
                str(mp4_path),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        duration = _probe_duration(str(mp4_path))
        return VisualAsset(
            scene_id=sid,
            path=str(mp4_path),
            kind="video",
            duration=duration,
            strategy="text_card",
            description=text[:60],
        )
    except Exception as exc:
        _log.warning("Ken Burns conversion failed for %s: %s", sid, exc)
        # Fall back to image
        return VisualAsset(
            scene_id=sid,
            path=str(png_path),
            kind="image",
            duration=0,
            strategy="text_card",
            description=text[:60],
        )


# ---------------------------------------------------------------------------
# Manim scene generators
# ---------------------------------------------------------------------------

def _run_manim_scene(code: str, class_name: str, output_dir: Path, scene_id: str) -> VisualAsset | None:
    """Write Manim code to a temp file, render it, return the asset."""
    # Sanitize any Unicode in the code that could cause rendering artifacts
    code = _sanitize_for_manim(code)
    script_path = output_dir / f"{scene_id}_manim.py"
    script_path.write_text(code, encoding="utf-8")

    try:
        # Run via Python API (CLI has path bugs on Windows)
        abs_script = str(script_path.resolve()).replace("\\", "\\\\")
        abs_media = str((output_dir / "media").resolve()).replace("\\", "\\\\")
        render_code = f"""
import sys
exec(open(r'{abs_script}').read())
from manim import tempconfig
with tempconfig({{
    'quality': 'medium_quality',
    'media_dir': r'{abs_media}',
    'disable_caching': True,
    'pixel_width': 1920,
    'pixel_height': 1080,
    'frame_rate': 30,
}}):
    scene = {class_name}()
    scene.render()
    print('MANIM_OUTPUT:' + str(scene.renderer.file_writer.movie_file_path))
"""
        result = subprocess.run(
            ["python", "-c", render_code],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            _log.warning("Manim render failed for %s: %s", scene_id, result.stderr[-300:])
            return None

        # Find output path
        for line in result.stdout.splitlines():
            if line.startswith("MANIM_OUTPUT:"):
                mp4_path = line.split(":", 1)[1].strip()
                if Path(mp4_path).is_file():
                    duration = _probe_duration(mp4_path)
                    return VisualAsset(
                        scene_id=scene_id,
                        path=mp4_path,
                        kind="video",
                        duration=duration,
                        strategy="manim",
                        description=class_name,
                    )

        _log.warning("Manim output not found for %s", scene_id)
        return None

    except subprocess.TimeoutExpired:
        _log.warning("Manim render timed out for %s", scene_id)
        return None
    except Exception as exc:
        _log.warning("Manim render error for %s: %s", scene_id, exc)
        return None


def _manim_dose_chart(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Animated dose comparison bar chart."""
    code = '''
from manim import *

class DoseChart(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("Radiation Dose Comparison", font_size=36, color=WHITE, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        bars = BarChart(
            values=[86, 10000],
            bar_names=["Prescribed\\n(86 rad)", "Received\\n(~10,000 rad)"],
            y_range=[0, 12000, 2000],
            y_length=4,
            x_length=8,
            bar_colors=[GREEN, RED],
            y_axis_config={"label_constructor": Text, "font_size": 18},
            x_axis_config={"label_constructor": Text, "font_size": 18},
        )
        bars.next_to(title, DOWN, buff=0.5)
        self.play(Create(bars), run_time=2)

        label = Text("116x overdose", font_size=30, color=RED, weight=BOLD)
        label.next_to(bars, DOWN, buff=0.3)
        self.play(FadeIn(label))

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=14, color=GREY)
        source.to_edge(DOWN, buff=0.2)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "DoseChart", output_dir, scene["scene_id"])


def _manim_byte_overflow(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Animated byte counter showing the 255→0 overflow."""
    code = '''
from manim import *

class ByteOverflow(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("The Arithmetic Overflow", font_size=36, color=WHITE, weight=BOLD)
        subtitle = Text("Class3 variable: 1 byte (max 255)", font_size=20, color=GREY_B)
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.15)
        header.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)
        self.play(FadeIn(subtitle), run_time=0.5)

        counter = Text("252", font_size=120, color=GREEN)
        counter.move_to(ORIGIN)
        counter_label = Text("Class3 value", font_size=22, color=GREY_B)
        counter_label.next_to(counter, UP, buff=0.3)

        status = Text("SAFETY CHECK: PASS", font_size=24, color=GREEN)
        status.next_to(counter, DOWN, buff=0.5)

        self.play(FadeIn(counter_label), FadeIn(counter), FadeIn(status))
        self.wait(0.5)

        # Count up: 252 -> 253 -> 254 -> 255
        warning = None
        for val in [253, 254, 255]:
            new_counter = Text(str(val), font_size=120, color=GREEN if val < 255 else YELLOW)
            new_counter.move_to(ORIGIN)
            self.play(Transform(counter, new_counter), run_time=0.4)
            if val == 255:
                warning = Text("MAX VALUE", font_size=18, color=YELLOW)
                warning.next_to(counter, RIGHT, buff=0.5)
                self.play(FadeIn(warning), run_time=0.3)
            self.wait(0.2)

        self.wait(0.5)

        # OVERFLOW: 255 -> 0
        zero = Text("0", font_size=120, color=RED)
        zero.move_to(ORIGIN)
        anims = [Transform(counter, zero)]
        if warning:
            anims.append(FadeOut(warning))
        self.play(*anims, run_time=0.5)
        self.play(Flash(counter, color=RED, line_length=0.5, num_lines=12), run_time=0.6)

        new_status = Text("SAFETY CHECK: BYPASSED", font_size=24, color=RED, weight=BOLD)
        new_status.move_to(status)
        self.play(Transform(status, new_status), run_time=0.5)

        explanation = Text("Value 0 = safe to proceed (incorrectly)", font_size=18, color=RED_B)
        explanation.next_to(status, DOWN, buff=0.3)
        self.play(FadeIn(explanation), run_time=0.5)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=14, color=GREY)
        source.to_edge(DOWN, buff=0.2)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "ByteOverflow", output_dir, scene["scene_id"])


def _manim_race_condition(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Animated flowchart showing the race condition."""
    code = '''
from manim import *

class RaceCondition(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("The Race Condition", font_size=38, color=WHITE, weight=BOLD)
        subtitle = Text("How the Therac-25 killed patients", font_size=20, color=GREY_B)
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.15)
        header.to_edge(UP, buff=0.3)
        self.play(Write(title), run_time=0.8)
        self.play(FadeIn(subtitle), run_time=0.5)

        def make_node(text, color=BLUE, w=3.5, h=0.65):
            box = RoundedRectangle(corner_radius=0.12, width=w, height=h, fill_color=color, fill_opacity=0.15, stroke_color=color, stroke_width=2)
            lbl = Text(text, font_size=17, color=WHITE)
            lbl.move_to(box)
            return VGroup(box, lbl)

        def make_diamond(text, color=YELLOW, s=1.3):
            d = Square(side_length=s, color=color, fill_opacity=0.12, stroke_width=2).rotate(PI/4)
            lbl = Text(text, font_size=15, color=WHITE)
            lbl.move_to(d)
            return VGroup(d, lbl)

        n1 = make_node("Operator types command", BLUE_C)
        n2 = make_node("Changes mode (X to E)", BLUE_C)
        n3 = make_diamond("Edit < 8\\nseconds?", YELLOW, 1.5)
        n_danger = make_node("Software state inconsistent", RED, w=3.8)
        n_safe = make_node("Normal operation", GREEN)
        n_mal = make_node("Malfunction 54 displayed", ORANGE, w=3.8)
        n_press = make_node("Operator presses P", ORANGE, w=3.8)
        n_fire = make_node("BEAM FIRES — NO TARGET", RED_E, w=3.8, h=0.75)
        n_ok = make_node("Safe treatment delivered", GREEN)

        n1.move_to(UP * 2.0)
        n2.move_to(UP * 0.9)
        n3.move_to(DOWN * 0.4)
        n_danger.move_to(DOWN * 1.6 + LEFT * 3.2)
        n_safe.move_to(DOWN * 1.6 + RIGHT * 3.2)
        n_mal.move_to(DOWN * 2.6 + LEFT * 3.2)
        n_press.move_to(DOWN * 3.5 + LEFT * 3.2)
        n_fire.move_to(DOWN * 3.5 + RIGHT * 1.5)
        n_ok.move_to(DOWN * 2.6 + RIGHT * 3.2)

        def varrow(s, e, c=GREY_B):
            return Arrow(s.get_bottom(), e.get_top(), buff=0.08, color=c, stroke_width=2, max_tip_length_to_length_ratio=0.12)

        a1 = varrow(n1, n2)
        a2 = varrow(n2, n3)
        a_yes = Arrow(n3.get_left()+DOWN*0.2, n_danger.get_top(), buff=0.08, color=RED, stroke_width=2.5, max_tip_length_to_length_ratio=0.12)
        a_no = Arrow(n3.get_right()+DOWN*0.2, n_safe.get_top(), buff=0.08, color=GREEN, stroke_width=2.5, max_tip_length_to_length_ratio=0.12)
        yes_lbl = Text("YES", font_size=14, color=RED, weight=BOLD).next_to(a_yes, LEFT, buff=0.08).shift(UP*0.3)
        no_lbl = Text("NO", font_size=14, color=GREEN, weight=BOLD).next_to(a_no, RIGHT, buff=0.08).shift(UP*0.3)
        a_mal = varrow(n_danger, n_mal, RED)
        a_press = varrow(n_mal, n_press, ORANGE)
        a_fire = Arrow(n_press.get_right(), n_fire.get_left(), buff=0.08, color=RED_E, stroke_width=3, max_tip_length_to_length_ratio=0.12)
        a_ok = varrow(n_safe, n_ok, GREEN)

        self.play(FadeIn(n1, shift=DOWN*0.2), run_time=0.5)
        self.play(GrowArrow(a1), FadeIn(n2, shift=DOWN*0.2), run_time=0.7)
        self.wait(0.4)
        self.play(GrowArrow(a2), FadeIn(n3, scale=0.8), run_time=0.7)
        self.wait(0.5)
        self.play(GrowArrow(a_no), FadeIn(no_lbl), FadeIn(n_safe, shift=LEFT*0.3), run_time=0.7)
        self.play(GrowArrow(a_ok), FadeIn(n_ok, shift=DOWN*0.2), run_time=0.5)
        self.wait(0.3)
        self.play(GrowArrow(a_yes), FadeIn(yes_lbl), FadeIn(n_danger, shift=RIGHT*0.3), run_time=0.8)
        self.play(n_danger[0].animate.set_fill(RED, opacity=0.25), run_time=0.4)
        self.wait(0.3)
        self.play(GrowArrow(a_mal), FadeIn(n_mal, shift=DOWN*0.2), run_time=0.7)
        self.wait(0.3)
        self.play(GrowArrow(a_press), FadeIn(n_press, shift=DOWN*0.2), run_time=0.7)
        self.wait(0.3)
        self.play(GrowArrow(a_fire), run_time=0.6)
        self.play(FadeIn(n_fire, scale=1.1), n_fire[0].animate.set_fill(RED_E, opacity=0.4), run_time=0.5)
        self.play(Flash(n_fire, color=RED, line_length=0.4, num_lines=16, flash_radius=1.2), Indicate(n_fire, color=RED_E, scale_factor=1.05), run_time=0.8)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=13, color=GREY)
        source.to_edge(DOWN, buff=0.15)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "RaceCondition", output_dir, scene["scene_id"])


def _manim_state_machine(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """X-ray / Electron mode state machine — turntable positions."""
    code = r'''
from manim import *

class StateMachine(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("Therac-25 Operating Modes", font_size=36, color=WHITE, weight=BOLD)
        subtitle = Text("Turntable position determines beam type", font_size=20, color=GREY_B)
        VGroup(title, subtitle).arrange(DOWN, buff=0.15).to_edge(UP, buff=0.3)
        self.play(Write(title), run_time=0.8)
        self.play(FadeIn(subtitle), run_time=0.5)

        def state_box(label, sublabel, color, w=3.5, h=1.2):
            box = RoundedRectangle(corner_radius=0.15, width=w, height=h, fill_color=color, fill_opacity=0.15, stroke_color=color, stroke_width=2.5)
            t = Text(label, font_size=22, color=WHITE, weight=BOLD)
            s = Text(sublabel, font_size=14, color=GREY_B)
            g = VGroup(t, s).arrange(DOWN, buff=0.12)
            g.move_to(box)
            return VGroup(box, g)

        xray = state_box("X-Ray Mode", "25 MeV + tungsten target\n+ flattening filter", BLUE_C)
        electron = state_box("Electron Mode", "5-25 MeV direct beam\nno target needed", TEAL)
        field_light = state_box("Field Light", "Visible light only\nfor patient alignment", YELLOW)

        xray.move_to(LEFT * 4 + DOWN * 0.1)
        electron.move_to(RIGHT * 4 + DOWN * 0.1)
        field_light.move_to(DOWN * 2.2)

        # Turntable in center
        turntable = Circle(radius=0.5, color=WHITE, stroke_width=2, fill_color=GREY_E, fill_opacity=0.3)
        tt_label = Text("Turntable", font_size=14, color=WHITE)
        tt = VGroup(turntable, tt_label).arrange(DOWN, buff=0.08)
        tt.move_to(DOWN * 0.1)

        # Arrows between states
        a1 = CurvedArrow(xray.get_right(), electron.get_left(), angle=-TAU/6, color=ORANGE, stroke_width=2)
        a2 = CurvedArrow(electron.get_left(), xray.get_right(), angle=-TAU/6, color=ORANGE, stroke_width=2)
        a3 = Arrow(xray.get_bottom(), field_light.get_left(), buff=0.1, color=GREY_B, stroke_width=1.5)
        a4 = Arrow(electron.get_bottom(), field_light.get_right(), buff=0.1, color=GREY_B, stroke_width=1.5)

        mode_label = Text("Mode switch" + chr(10) + "(operator command)", font_size=13, color=ORANGE)
        mode_label.next_to(a1, UP, buff=0.1)

        # Danger zone — below field light, near bottom edge
        danger = RoundedRectangle(corner_radius=0.1, width=8, height=0.6, fill_color=RED, fill_opacity=0.2, stroke_color=RED, stroke_width=2)
        danger_text = Text("DANGER: If turntable fails to rotate, beam fires without safety target", font_size=14, color=RED)
        danger_group = VGroup(danger, danger_text)
        danger_text.move_to(danger)
        danger_group.to_edge(DOWN, buff=0.2)

        # Animate
        self.play(FadeIn(tt), run_time=0.5)
        self.play(FadeIn(xray, shift=RIGHT * 0.3), run_time=0.7)
        self.wait(0.3)
        self.play(FadeIn(electron, shift=LEFT * 0.3), run_time=0.7)
        self.wait(0.3)
        self.play(FadeIn(field_light, shift=UP * 0.2), GrowArrow(a3), GrowArrow(a4), run_time=0.7)
        self.wait(0.3)

        self.play(Create(a1), Create(a2), FadeIn(mode_label), run_time=1.0)
        self.wait(0.5)

        # Highlight danger + source combined at bottom
        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=11, color=GREY)
        bottom = VGroup(danger_group, source).arrange(DOWN, buff=0.1)
        bottom.to_edge(DOWN, buff=0.15)
        self.play(FadeIn(danger_group), run_time=0.8)
        self.play(Indicate(danger, color=RED, scale_factor=1.02), run_time=0.6)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "StateMachine", output_dir, scene["scene_id"])


def _manim_data_flow(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Keystroke → Malfunction 54 → P → Fire — linear data flow with step reveals."""
    code = r'''
from manim import *

class DataFlow(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("The Fatal Sequence", font_size=36, color=WHITE, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        def step_box(num, text, color, w=5.5, h=0.7):
            box = RoundedRectangle(corner_radius=0.1, width=w, height=h, fill_color=color, fill_opacity=0.12, stroke_color=color, stroke_width=2)
            num_circle = Circle(radius=0.22, color=color, fill_opacity=0.3, stroke_width=1.5)
            num_text = Text(str(num), font_size=16, color=WHITE, weight=BOLD)
            num_text.move_to(num_circle)
            num_g = VGroup(num_circle, num_text)
            label = Text(text, font_size=17, color=WHITE)
            content = VGroup(num_g, label).arrange(RIGHT, buff=0.3)
            content.move_to(box)
            return VGroup(box, content)

        steps = [
            step_box(1, "Operator enters treatment parameters", BLUE_C),
            step_box(2, "Operator edits mode (X-ray to Electron) quickly", BLUE_C),
            step_box(3, "Software begins reconfiguration (8s window)", YELLOW),
            step_box(4, "Safety check runs on OLD configuration — PASSES", ORANGE),
            step_box(5, "'Malfunction 54' displayed on screen", ORANGE),
            step_box(6, "Operator presses 'P' to proceed", ORANGE),
            step_box(7, "Beam fires — NO target in position", RED_E),
        ]

        flow = VGroup(*steps).arrange(DOWN, buff=0.18)
        flow.next_to(title, DOWN, buff=0.4)

        # Scale to fit
        if flow.get_bottom()[1] < -3.5:
            flow.scale_to_fit_height(6.0)
            flow.next_to(title, DOWN, buff=0.3)

        # Animate step by step
        for i, step in enumerate(steps):
            rt = 0.5 if i < 3 else 0.7
            self.play(FadeIn(step, shift=LEFT * 0.3), run_time=rt)
            if i == 3:  # safety check passes incorrectly
                self.play(step[0].animate.set_fill(ORANGE, opacity=0.25), run_time=0.3)
            elif i == 6:  # beam fires
                self.play(
                    step[0].animate.set_fill(RED_E, opacity=0.3),
                    Flash(step, color=RED, line_length=0.3, num_lines=10),
                    run_time=0.8,
                )
            self.wait(0.2)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=13, color=GREY)
        source.to_edge(DOWN, buff=0.15)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "DataFlow", output_dir, scene["scene_id"])


def _manim_timeline(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Timeline of Therac-25 incidents 1985-1987."""
    code = r'''
from manim import *

class IncidentTimeline(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("Therac-25 Incident Timeline", font_size=36, color=WHITE, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        # Timeline axis
        line = Line(LEFT * 6, RIGHT * 6, color=GREY_B, stroke_width=2)
        line.move_to(DOWN * 0.2)
        self.play(Create(line), run_time=0.5)

        # Year markers
        years = {"1985": -5, "1986": -1.5, "1987": 2.5, "1988": 5.5}
        for year, x in years.items():
            tick = Line(UP * 0.15, DOWN * 0.15, color=GREY_B, stroke_width=1.5).move_to(line.get_center() + RIGHT * x)
            label = Text(year, font_size=18, color=GREY_B).next_to(tick, DOWN, buff=0.15)
            self.play(Create(tick), FadeIn(label), run_time=0.3)

        # Incidents — alternate above/below to prevent overlap
        # (x_pos, date, name, color, note, above=True/False)
        incidents = [
            (-5.0, "Jun 85", "Yarbrough", YELLOW, "75-100x dose", True),
            (-3.5, "Jul 85", "Hill", YELLOW, "Died Nov 85", False),
            (-2.2, "Dec 85", "Yakima", ORANGE, "", True),
            (-0.8, "Mar 86", "Cox", RED, "Died Aug 86", False),
            (0.5, "Apr 86", "Kidd", RED, "Died May 86", True),
            (2.0, "May 86", "FDA defective", BLUE_C, "", False),
            (3.5, "Jan 87", "Dodd", RED, "Died Apr 87", True),
            (5.0, "Feb 87", "FDA: remove", BLUE_C, "", False),
        ]

        for x, date, name, color, note, above in incidents:
            dot = Dot(line.get_center() + RIGHT * x, radius=0.08, color=color)
            direction = UP if above else DOWN
            marker_line = Line(ORIGIN, direction * 0.8, color=color, stroke_width=1.5)
            marker_line.next_to(dot, direction, buff=0)

            date_text = Text(date, font_size=11, color=color)
            name_text = Text(name, font_size=11, color=WHITE)
            info = VGroup(date_text, name_text).arrange(DOWN if above else UP, buff=0.04)
            info.next_to(marker_line, direction, buff=0.06)

            if note:
                note_text = Text(note, font_size=9, color=RED_B if "Died" in note else GREY_B)
                info.add(note_text)
                info.arrange(DOWN if above else UP, buff=0.04)
                info.next_to(marker_line, direction, buff=0.06)

            self.play(FadeIn(dot), Create(marker_line), FadeIn(info), run_time=0.5)
            self.wait(0.2)

        # Summary
        summary = Text("6 patients overdosed  |  3 confirmed deaths  |  2 years to fix", font_size=16, color=RED_B)
        summary.to_edge(DOWN, buff=0.8)
        self.play(FadeIn(summary), run_time=0.6)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=13, color=GREY)
        source.to_edge(DOWN, buff=0.15)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "IncidentTimeline", output_dir, scene["scene_id"])


def _manim_scientific(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Scientific process diagram — radiation beam, cellular damage, beam paths."""
    desc = scene.get("animation_description", scene.get("narration", ""))[:200]
    narr_lower = desc.lower()

    # Pick sub-type based on content
    if any(kw in narr_lower for kw in ["cancer", "tumor", "cell", "dna", "divide", "radiation therapy"]):
        template = "RadiationTherapy"
        code = r'''
from manim import *

class RadiationTherapy(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("How Radiation Therapy Works", font_size=36, color=WHITE, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        # Linear accelerator (left)
        linac = RoundedRectangle(width=2, height=1.2, corner_radius=0.1, color=BLUE_C, fill_opacity=0.2, stroke_width=2)
        linac_label = Text("Linear" + chr(10) + "Accelerator", font_size=14, color=WHITE)
        linac_label.move_to(linac)
        linac_group = VGroup(linac, linac_label).move_to(LEFT * 4.5 + DOWN * 0.5)

        # Beam
        beam = Arrow(LEFT * 3.3 + DOWN * 0.5, RIGHT * 0.5 + DOWN * 0.5, buff=0, color=YELLOW, stroke_width=4, max_tip_length_to_length_ratio=0.08)
        beam_label = Text("High-energy beam", font_size=13, color=YELLOW).next_to(beam, UP, buff=0.1)

        # Tumor (center-right)
        healthy = Circle(radius=1.2, color=GREEN, fill_opacity=0.1, stroke_width=1.5).move_to(RIGHT * 2 + DOWN * 0.5)
        healthy_label = Text("Healthy tissue", font_size=12, color=GREEN_B).next_to(healthy, DOWN, buff=0.15)
        tumor = Circle(radius=0.5, color=RED, fill_opacity=0.3, stroke_width=2).move_to(RIGHT * 2 + DOWN * 0.5)
        tumor_label = Text("Tumor", font_size=13, color=RED).move_to(tumor)

        # DNA strands inside tumor
        dna1 = Line(UP * 0.2 + LEFT * 0.15, DOWN * 0.2 + RIGHT * 0.15, color=RED_B, stroke_width=2).move_to(tumor.get_center() + UP * 0.15)
        dna2 = Line(UP * 0.2 + RIGHT * 0.15, DOWN * 0.2 + LEFT * 0.15, color=RED_B, stroke_width=2).move_to(tumor.get_center() + DOWN * 0.15)

        # Animate
        self.play(FadeIn(linac_group), run_time=0.6)
        self.play(FadeIn(healthy), FadeIn(healthy_label), run_time=0.5)
        self.play(FadeIn(tumor), FadeIn(tumor_label), FadeIn(dna1), FadeIn(dna2), run_time=0.6)
        self.wait(0.3)

        # Beam fires
        self.play(GrowArrow(beam), FadeIn(beam_label), run_time=1.0)
        self.wait(0.3)

        # DNA breaks
        self.play(
            dna1.animate.set_color(GREY).set_opacity(0.3),
            dna2.animate.set_color(GREY).set_opacity(0.3),
            Flash(tumor, color=YELLOW, line_length=0.3, num_lines=8),
            run_time=0.8,
        )

        # Tumor shrinks
        destroyed_label = Text("DNA destroyed" + chr(10) + "Cells stop dividing", font_size=13, color=GREEN)
        destroyed_label.move_to(tumor)
        self.play(
            tumor.animate.scale(0.3).set_opacity(0.1),
            FadeOut(tumor_label),
            FadeIn(destroyed_label),
            run_time=1.2,
        )
        self.wait(0.3)

        # Goal text
        goal = Text("Goal: destroy tumor while sparing healthy tissue", font_size=18, color=WHITE)
        goal.next_to(healthy, DOWN, buff=0.6)
        self.play(FadeIn(goal), run_time=0.6)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=13, color=GREY)
        source.to_edge(DOWN, buff=0.15)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    elif any(kw in narr_lower for kw in ["interlock", "hardware", "safety", "removed", "therac-6", "therac-20"]):
        template = "SafetyInterlocks"
        code = r'''
from manim import *

class SafetyInterlocks(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("Hardware Safety Interlocks: Removed", font_size=34, color=WHITE, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        # Therac-20 (with interlocks)
        t20_box = RoundedRectangle(width=4, height=2.5, corner_radius=0.1, color=GREEN, fill_opacity=0.08, stroke_width=2)
        t20_label = Text("Therac-20", font_size=22, color=GREEN, weight=BOLD)
        t20_label.next_to(t20_box, UP, buff=0.15)

        sw = RoundedRectangle(width=1.5, height=0.6, corner_radius=0.05, color=BLUE_C, fill_opacity=0.15, stroke_width=1.5)
        sw_lbl = Text("Software", font_size=13, color=WHITE).move_to(sw)
        hw = RoundedRectangle(width=1.5, height=0.6, corner_radius=0.05, color=GREEN, fill_opacity=0.25, stroke_width=2)
        hw_lbl = Text("Hardware" + chr(10) + "Interlocks", font_size=11, color=WHITE).move_to(hw)
        t20_content = VGroup(VGroup(sw, sw_lbl), VGroup(hw, hw_lbl)).arrange(DOWN, buff=0.3)
        t20_content.move_to(t20_box)
        t20_group = VGroup(t20_box, t20_label, t20_content).move_to(LEFT * 3.5 + DOWN * 0.3)

        # Therac-25 (without)
        t25_box = RoundedRectangle(width=4, height=2.5, corner_radius=0.1, color=RED, fill_opacity=0.08, stroke_width=2)
        t25_label = Text("Therac-25", font_size=22, color=RED, weight=BOLD)
        t25_label.next_to(t25_box, UP, buff=0.15)

        sw2 = RoundedRectangle(width=1.5, height=0.6, corner_radius=0.05, color=BLUE_C, fill_opacity=0.15, stroke_width=1.5)
        sw2_lbl = Text("Software", font_size=13, color=WHITE).move_to(sw2)
        hw2 = RoundedRectangle(width=1.5, height=0.6, corner_radius=0.05, color=RED, fill_opacity=0.1, stroke_width=1.5, stroke_opacity=0.4)
        hw2_lbl = Text("REMOVED", font_size=13, color=RED, weight=BOLD).move_to(hw2)
        cross1 = Line(hw2.get_corner(UL), hw2.get_corner(DR), color=RED, stroke_width=2)
        cross2 = Line(hw2.get_corner(UR), hw2.get_corner(DL), color=RED, stroke_width=2)
        t25_content = VGroup(VGroup(sw2, sw2_lbl), VGroup(hw2, hw2_lbl, cross1, cross2)).arrange(DOWN, buff=0.3)
        t25_content.move_to(t25_box)
        t25_group = VGroup(t25_box, t25_label, t25_content).move_to(RIGHT * 3.5 + DOWN * 0.3)

        # Arrow between
        arrow = Arrow(LEFT * 1, RIGHT * 1, color=ORANGE, stroke_width=3).move_to(DOWN * 0.3)
        arrow_lbl = Text("Evolution", font_size=14, color=ORANGE).next_to(arrow, UP, buff=0.1)

        # Animate
        self.play(FadeIn(t20_group), run_time=0.8)
        self.wait(0.5)
        self.play(GrowArrow(arrow), FadeIn(arrow_lbl), run_time=0.6)
        self.play(FadeIn(t25_group), run_time=0.8)
        self.wait(0.3)
        self.play(Indicate(VGroup(cross1, cross2, hw2_lbl), color=RED, scale_factor=1.1), run_time=0.8)

        warning = Text("Software alone now responsible for patient safety", font_size=16, color=RED_B)
        warning.to_edge(DOWN, buff=0.8)
        self.play(FadeIn(warning), run_time=0.6)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=13, color=GREY)
        source.to_edge(DOWN, buff=0.15)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    else:
        # Generic scientific diagram — use the description as a text card
        return _generate_text_card(scene, output_dir)

    return _run_manim_scene(code, template, output_dir, scene["scene_id"])


def _manim_process_flow(scene: dict[str, Any], output_dir: Path) -> VisualAsset | None:
    """Generic process flow — builds numbered steps from the animation_description."""
    desc = scene.get("animation_description", "")
    narration = scene.get("narration", "")

    # Extract key steps from the description or narration
    # Use a simplified version — numbered steps
    steps_text = desc if desc else narration[:200]

    # Pre-sanitize narration and extract steps BEFORE embedding in code
    clean_narration = _sanitize_for_manim(narration[:500])
    sentences = [s.strip() for s in clean_narration.split('.') if len(s.strip()) > 10][:6]
    # Truncate each to fit in a box (max 50 chars)
    steps_data = []
    for s in sentences:
        txt = s[:42] + ("..." if len(s) > 42 else "")
        txt = txt.replace("'", "").replace('"', '')  # strip quotes for code safety
        steps_data.append(txt)

    scene_title = _sanitize_for_manim(scene.get("scene_id", "Process").replace("_", " ").title())

    # Build the steps list as a Python literal
    steps_literal = repr(steps_data)

    code = f'''
from manim import *

class ProcessFlow(Scene):
    def construct(self):
        self.camera.background_color = "#0a0a1a"
        title = Text("{scene_title}", font_size=32, color=WHITE, weight=BOLD)
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        steps_text = {steps_literal}
        colors = [BLUE_C, BLUE_C, YELLOW, ORANGE, ORANGE, RED_E]

        def step_box(num, text, color=BLUE_C):
            label = Text(text, font_size=14, color=WHITE)
            circ = Circle(radius=0.18, color=color, fill_opacity=0.3, stroke_width=1)
            num_t = Text(str(num), font_size=14, color=WHITE, weight=BOLD).move_to(circ)
            content = VGroup(VGroup(circ, num_t), label).arrange(RIGHT, buff=0.25)
            # Auto-size box to fit content
            box = RoundedRectangle(
                corner_radius=0.08,
                width=content.width + 0.6,
                height=content.height + 0.3,
                fill_color=color, fill_opacity=0.12,
                stroke_color=color, stroke_width=1.5,
            )
            content.move_to(box)
            return VGroup(box, content)

        steps = []
        for i, txt in enumerate(steps_text):
            c = colors[i] if i < len(colors) else BLUE_C
            steps.append(step_box(i + 1, txt, c))

        flow = VGroup(*steps).arrange(DOWN, buff=0.15)
        flow.next_to(title, DOWN, buff=0.35)
        if flow.height > 5.5:
            flow.scale_to_fit_height(5.5)
            flow.next_to(title, DOWN, buff=0.3)

        for step in steps:
            self.play(FadeIn(step, shift=LEFT * 0.2), run_time=0.5)
            self.wait(0.15)

        source = Text("Source: Leveson & Turner, IEEE Computer, 1993", font_size=13, color=GREY)
        source.to_edge(DOWN, buff=0.15)
        self.play(FadeIn(source), run_time=0.4)
        self.wait(2)
'''
    return _run_manim_scene(code, "ProcessFlow", output_dir, scene["scene_id"])


def _probe_duration(path: str) -> float:
    """Get video duration via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0
