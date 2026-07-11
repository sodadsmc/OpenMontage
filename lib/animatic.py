"""Storyboard shots (animatics): narration + planned stills, cut on the
word-timing alignment — the $0 review artifact between stills vetting and
video spend.

Per scene: the approved/authored stills are held across sentence groups of the
narration (hard cuts on sentence starts, house style), muxed with the scene's
narration mp3 and its scripted silence tail. Per episode: every scene's
animatic concatenated in board order — watch the whole story as narrated
stills before the first paid clip.

CLI:
    python -m lib.animatic <project_id> <scene_id> [still1.png still2.png ...]
    python -m lib.animatic <project_id> --episode
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

PROJECTS = Path("projects")
FPS = 30
NORM = ("scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=#091327,fps=30,format=yuv420p")
NAVY = "#091327"


def _run(args: list) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(str(args[:4]) + " :: " + r.stderr[-300:])


def _dur(path: str | Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def sentence_starts(alignment_path: str | Path) -> list[float]:
    """Start time of each sentence in the narration, from the character-level
    ElevenLabs alignment. [0.0] when the alignment is missing/unreadable."""
    try:
        al = json.loads(Path(alignment_path).read_text(encoding="utf-8"))
        chars = al["alignment"]["characters"]
        starts = al["alignment"]["character_start_times_seconds"]
    except Exception:
        return [0.0]
    out = [float(starts[0])] if starts else [0.0]
    for i, ch in enumerate(chars[:-1]):
        if ch in ".!?" and (chars[i + 1].isspace() or i + 1 == len(chars) - 1):
            # next non-space char starts the next sentence
            j = i + 1
            while j < len(chars) and chars[j].isspace():
                j += 1
            if j < len(starts):
                out.append(float(starts[j]))
    return sorted(set(out))


def _placeholder(sid: str, out_png: Path) -> Path:
    """Navy card naming the scene — used when a scene has no stills yet, so
    the EPISODE animatic still assembles end-to-end."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1920, 1080), (9, 19, 39))
    d = ImageDraw.Draw(im)
    try:
        f1 = ImageFont.truetype("arialbd.ttf", 96)
        f2 = ImageFont.truetype("arialbd.ttf", 44)
    except OSError:
        f1 = f2 = ImageFont.load_default()
    d.text((960, 480), sid, font=f1, fill=(243, 165, 65), anchor="mm")
    d.text((960, 610), "NO STILLS AUTHORED YET", font=f2,
           fill=(224, 217, 197), anchor="mm")
    im.save(out_png)
    return out_png


def scene_animatic(pid: str, sid: str, stills: list[str | Path] | None = None,
                   out: str | Path | None = None, audio_dir: str = "audio_v6",
                   tail_s: float | None = None) -> Path:
    """Render one scene's storyboard shot. Stills are distributed across the
    narration's sentences (proportionally, in order) and cut on sentence
    starts; audio = the scene's narration mp3 + its scripted silence tail."""
    proj = PROJECTS / pid
    audio = proj / "assets" / audio_dir / f"{sid}.mp3"
    if not audio.is_file():
        raise FileNotFoundError(f"no narration audio at {audio}")
    adur = _dur(audio)

    if tail_s is None:
        tail_s = 0.0
        try:
            from lib.scored_script import load_scored_script
            script = load_scored_script(str(next(proj.glob("script_*/scored_script.yaml"))))
            seg = next((s for s in script.segments if s.id == sid), None)
            if seg is not None:
                tail_s = float(seg.silence_after_s or 0)
        except Exception:
            pass
    total = adur + tail_s

    tmp = Path(tempfile.mkdtemp(prefix=f"anim_{sid}_"))
    stills = [Path(s) for s in (stills or []) if Path(s).is_file()]
    if not stills:
        stills = [_placeholder(sid, tmp / "placeholder.png")]

    # sentence starts -> still windows (proportional groups, order preserved)
    starts = sentence_starts(proj / "assets" / audio_dir / f"{sid}.alignment.json")
    n_sent, n_still = len(starts), len(stills)
    if n_sent >= n_still > 1:
        idx = [round(k * n_sent / n_still) for k in range(n_still)]
        cut_ts = [starts[min(i, n_sent - 1)] for i in idx]
    else:                      # fewer sentences than stills: even time split
        cut_ts = [total * k / n_still for k in range(n_still)]
    cut_ts[0] = 0.0
    bounds = cut_ts + [total]

    parts = []
    for k, still in enumerate(stills):
        win = max(0.2, bounds[k + 1] - bounds[k])
        p = tmp / f"p{k:02d}.mp4"
        _run(["ffmpeg", "-y", "-loop", "1", "-i", str(still),
              "-t", f"{win:.3f}", "-vf", NORM, "-c:v", "libx264",
              "-preset", "fast", "-crf", "20", "-an", str(p)])
        parts.append(p)
    lst = tmp / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    video = tmp / "video.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-an", str(video)])

    out = Path(out) if out else proj / "assets" / "animatics" / f"{sid}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    # audio = narration padded with the silence tail; video padded to match
    _run(["ffmpeg", "-y", "-i", str(video), "-i", str(audio),
          "-af", f"apad=whole_dur={total:.3f}",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-t", f"{total:.3f}", str(out)])
    return out


def episode_animatic(pid: str, out: str | Path | None = None,
                     audio_dir: str = "audio_v6") -> Path:
    """Concat every scene's animatic in script order (building any that are
    missing, placeholders included) — the $0 full-story pacing pass."""
    from lib.scored_script import load_scored_script
    proj = PROJECTS / pid
    script = load_scored_script(str(next(proj.glob("script_*/scored_script.yaml"))))
    anim_dir = proj / "assets" / "animatics"
    parts = []
    for seg in script.segments:
        p = anim_dir / f"{seg.id}.mp4"
        if not p.is_file():
            p = scene_animatic(pid, seg.id, stills=None, audio_dir=audio_dir)
        parts.append(p)
    out = Path(out) if out else proj / "renders" / "episode_animatic.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".concat.txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts))
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c:v", "libx264", "-preset", "fast", "-crf", "20",
          "-c:a", "aac", "-b:a", "192k", str(out)])
    lst.unlink(missing_ok=True)
    return out


def _main():
    import argparse
    ap = argparse.ArgumentParser(description="Storyboard animatics")
    ap.add_argument("project_id")
    ap.add_argument("scene_id", nargs="?")
    ap.add_argument("stills", nargs="*")
    ap.add_argument("--episode", action="store_true")
    args = ap.parse_args()
    if args.episode:
        print(episode_animatic(args.project_id))
    elif args.scene_id:
        print(scene_animatic(args.project_id, args.scene_id,
                             stills=args.stills or None))
    else:
        ap.error("give a scene_id or --episode")


if __name__ == "__main__":
    _main()
