"""Clip-review dashboard — the human-in-the-loop surface for the v6 pipeline.

WHY THIS EXISTS
---------------
The channel research (Research*.md, pain-point audit) converged on one
non-negotiable: AI-generated documentary footage ships with silent failures —
morphing limbs, identity drift, clips that contradict the narration they play
under. Automated gates (quality_gate, Gemini semantic checks) catch a lot, but
the final accept/reject call is editorial and belongs to a human. Before this
dashboard, that review meant scrubbing through assets/shots/*.mp4 in a file
browser with the manifest open in a second window — no narration context, no
way to record a verdict, and rerolling a bad clip meant hand-editing JSON and
re-running a build script.

This module is that missing review surface as ONE stdlib-only file:

* every segment in script order, with its narration, overlay text, motion
  brief, prompts, keyframes, clips, and per-segment narration audio together;
* one-click approve / flag (+ note) persisted to artifacts/review_decisions.json;
* one-click re-roll: bumps the seed, refreshes the (stale) hosted keyframe
  anchor from local files, and re-generates via lib.visual_router on a single
  background worker so paid calls never run concurrently.

Run:  python scripts/review_dashboard.py [--project projects/therac-25-test]
                                         [--port 8765] [--open]
"""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import queue
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.base_tool  # noqa: F401  (side effect: loads .env so reroll has API keys)
from lib.scored_script import Segment, load_scored_script
from lib.shot_manifest import ShotJob, ShotManifest

_log = logging.getLogger("review_dashboard")

SEED_BUMP = 37
MEDIA_CHUNK = 64 * 1024
_CTYPES = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg",
    ".wav": "audio/wav", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".json": "application/json", ".srt": "text/plain",
}


# ---------------------------------------------------------------------------
# Dashboard state — manifest + script + decisions + the reroll worker
# ---------------------------------------------------------------------------

class Dashboard:
    """All mutable review state, guarded by one lock; owns the reroll worker."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir.resolve()
        self.project_name = self.project_dir.name
        self.manifest_path = self.project_dir / "artifacts" / "shot_manifest_v6.json"
        self.decisions_path = self.project_dir / "artifacts" / "review_decisions.json"
        self.bible_path = self.project_dir / "artifacts" / "asset_bible_v6.json"
        self.script_path = self.project_dir / "script_v5" / "scored_script.yaml"

        self.lock = threading.RLock()
        self.manifest: ShotManifest = ShotManifest.load(self.manifest_path)
        self.script = load_scored_script(self.script_path)
        self.segments_by_id: dict[str, Segment] = {s.id: s for s in self.script.segments}
        self.decisions: dict[str, dict[str, Any]] = self._load_decisions()

        # Reroll machinery: ONE worker thread => paid calls are serialized.
        self._queue: "queue.Queue[str]" = queue.Queue()
        self.queued: list[str] = []          # keys waiting ("seg/shot")
        self.active: str | None = None       # key currently generating
        self.errors: dict[str, str] = {}     # key -> last worker error
        self._worker = threading.Thread(target=self._worker_loop, daemon=True,
                                        name="reroll-worker")
        self._worker.start()

    # -- persistence --------------------------------------------------------

    def _load_decisions(self) -> dict[str, dict[str, Any]]:
        if self.decisions_path.exists():
            try:
                return json.loads(self.decisions_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:  # corrupt file: start clean
                _log.warning("could not parse %s: %s", self.decisions_path, e)
        return {}

    def _save_decisions(self) -> None:
        """Atomic write: tmp + os.replace so a crash never truncates the file."""
        tmp = self.decisions_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.decisions, indent=2), encoding="utf-8")
        os.replace(tmp, self.decisions_path)

    def _save_manifest(self) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        self.manifest.save(tmp)
        os.replace(tmp, self.manifest_path)

    # -- media path helpers --------------------------------------------------

    def abs_path(self, p: str | Path) -> Path:
        """Manifest paths are repo-root-relative (Windows separators OK)."""
        path = Path(str(p))
        return path if path.is_absolute() else (REPO_ROOT / path)

    def media_url(self, p: str | Path | None) -> str | None:
        """Map a local file to /media/<relpath>?v=<mtime>, or None if absent/outside."""
        if not p:
            return None
        ap = self.abs_path(p)
        if not ap.is_file():
            return None
        try:
            rel = ap.resolve().relative_to(self.project_dir)
        except ValueError:
            return None  # outside the project root: not servable
        return f"/media/{rel.as_posix()}?v={int(ap.stat().st_mtime)}"

    def local_keyframe(self, job: ShotJob) -> Path | None:
        """Best LOCAL anchor image for a shot.

        Preference: keyframes dir _key_pop.png > _key.png > the bible asset's
        canonical_reference_image. Needed because stored job.keyframe URLs go
        stale (premiumize signed links expire; tmpfiles dies in ~1h).
        """
        kf_dir = self.project_dir / "assets" / "keyframes"
        for suffix in ("_key_pop.png", "_key.png"):
            cand = kf_dir / f"{job.segment_id}_{job.shot_id}{suffix}"
            if cand.is_file():
                return cand
        try:
            from lib.asset_bible import AssetBible
            bible = AssetBible.load(self.bible_path)
            seg = self.segments_by_id.get(job.segment_id)
            if seg is not None:
                asset = bible.asset_for_segment(seg)
                if asset and asset.canonical_reference_image:
                    cand = self.abs_path(asset.canonical_reference_image)
                    if cand.is_file():
                        return cand
        except Exception as e:  # noqa: BLE001 — bible is best-effort here
            _log.warning("asset bible lookup failed for %s: %s", job.key, e)
        return None

    def keyframe_thumb_url(self, job: ShotJob) -> str | None:
        """Thumbnail for the UI: local file if we have one, else the stored URL."""
        local = self.local_keyframe(job)
        if local is not None:
            return self.media_url(local)
        kf = job.keyframe or ""
        return kf if kf.startswith(("http://", "https://")) else self.media_url(kf)

    def final_render(self) -> str | None:
        renders = self.project_dir / "renders"
        if not renders.is_dir():
            return None
        mp4s = sorted(renders.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        return self.media_url(mp4s[-1]) if mp4s else None

    # -- state snapshot ------------------------------------------------------

    def build_state(self) -> dict[str, Any]:
        with self.lock:
            seg_plans = {sp.segment_id: sp for sp in self.manifest.segments}
            counts = {"shots": 0, "done": 0, "pending": 0, "failed": 0,
                      "approved": 0, "flagged": 0, "unreviewed": 0}
            segments: list[dict[str, Any]] = []

            seg_ids = [s.id for s in self.script.segments]
            # Manifest-only segments (shouldn't happen, but never hide work):
            seg_ids += [sid for sid in {j.segment_id for j in self.manifest.shots}
                        if sid not in self.segments_by_id]

            for seg_id in seg_ids:
                seg = self.segments_by_id.get(seg_id)
                shots = []
                for job in self.manifest.shots_for_segment(seg_id):
                    counts["shots"] += 1
                    counts[job.status if job.status in ("done", "pending", "failed")
                           else "pending"] += 1
                    dec = self.decisions.get(job.key, {})
                    verdict = dec.get("decision") or ""
                    if verdict in ("approved", "flagged"):
                        counts[verdict] += 1
                    else:
                        counts["unreviewed"] += 1
                    shots.append({
                        "key": job.key, "segment_id": job.segment_id,
                        "shot_id": job.shot_id, "provider": job.provider,
                        "status": job.status, "hero": job.hero,
                        "chain_from": job.chain_from, "seed": job.seed,
                        "duration_s": job.duration_s,
                        "video_prompt": job.video_prompt,
                        "description": job.description,
                        "clip_url": self.media_url(job.output),
                        "keyframe_url": self.keyframe_thumb_url(job),
                        "decision": verdict, "note": dec.get("note", ""),
                        "rerolling": job.key == self.active,
                        "queued": job.key in self.queued,
                        "error": self.errors.get(job.key, ""),
                    })
                plan = seg_plans.get(seg_id)
                seg_clip = plan.output if plan and plan.output else \
                    self.project_dir / "assets" / "ai_segments" / f"{seg_id}.mp4"
                audio = None
                if seg is not None:
                    audio = self.media_url(
                        self.project_dir / "assets" / "audio_v6" / f"seg_{seg.index:03d}.mp3")
                segments.append({
                    "id": seg_id,
                    "act": seg.act if seg else "",
                    "narration": seg.narration if seg else "",
                    "text_overlay": list(seg.visual.text_overlay) if seg else [],
                    "ai_motion": (seg.visual.ai_motion or "") if seg else "",
                    "audio_url": audio,
                    "segment_clip_url": self.media_url(seg_clip),
                    "shots": shots,
                })

            return {
                "project": self.project_name,
                "title": self.script.title,
                "final_render": self.final_render(),
                "counts": counts,
                "segments": segments,
                "reroll": {"active": self.active, "queued": list(self.queued),
                           "errors": dict(self.errors)},
                "ts": time.time(),
            }

    # -- actions -------------------------------------------------------------

    def set_decision(self, segment_id: str, shot_id: str,
                     decision: str, note: str) -> dict[str, Any]:
        if decision not in ("approved", "flagged", "clear"):
            raise ValueError(f"bad decision {decision!r}")
        with self.lock:
            job = self.manifest.get_shot(segment_id, shot_id)
            if job is None:
                raise KeyError(f"unknown shot {segment_id}/{shot_id}")
            key = job.key
            if decision == "clear" and not note:
                self.decisions.pop(key, None)
            else:
                self.decisions[key] = {
                    "decision": "" if decision == "clear" else decision,
                    "note": note, "ts": time.time(),
                }
            self._save_decisions()
            return {"ok": True, "key": key, "decision": self.decisions.get(key)}

    def reroll(self, segment_id: str, shot_id: str, regenerate: bool) -> dict[str, Any]:
        with self.lock:
            job = self.manifest.get_shot(segment_id, shot_id)
            if job is None:
                raise KeyError(f"unknown shot {segment_id}/{shot_id}")
            if regenerate and (job.key == self.active or job.key in self.queued):
                return {"ok": False, "error": "already queued", "key": job.key}
            job.seed += SEED_BUMP
            job.status = "pending"
            self.errors.pop(job.key, None)
            self._save_manifest()
            if regenerate:
                self.queued.append(job.key)
                self._queue.put(job.key)
                _log.info("reroll queued: %s (seed=%d)", job.key, job.seed)
            return {"ok": True, "key": job.key, "seed": job.seed,
                    "queued": regenerate}

    # -- background worker ----------------------------------------------------

    def _fresh_anchor(self, job: ShotJob) -> str | Path | None:
        """Resolve the i2v anchor for a reroll, refreshing stale hosted URLs.

        Stored keyframe URLs expire (signed premiumize links, ~1h tmpfiles), so
        prefer a LOCAL keyframe / bible canonical and re-host it fresh. Fall
        back to the stored URL only when no local file exists.
        """
        kf = job.keyframe or ""
        if not kf:
            return None  # text_to_video shot: no anchor by design
        if not kf.startswith(("http://", "https://")):
            p = self.abs_path(kf)
            if p.is_file():
                return p  # local anchors don't go stale
        local = self.local_keyframe(job)
        if local is not None:
            os.environ["IMAGE_HOST"] = "tmpfiles"  # Grok-ingestible host for anchors
            from lib.image_host import upload_image
            url = upload_image(local)
            if url:
                _log.info("rehosted anchor for %s: %s -> %s", job.key, local.name, url)
                return url
            _log.warning("re-hosting %s failed; falling back to stored URL", local)
        return kf or None

    def _worker_loop(self) -> None:
        """Single consumer: serializes paid generation; never crashes the server."""
        while True:
            key = self._queue.get()
            with self.lock:
                if key in self.queued:
                    self.queued.remove(key)
                self.active = key
                seg_id, _, shot_id = key.partition("/")
                job = self.manifest.get_shot(seg_id, shot_id)
            try:
                if job is None:
                    raise KeyError(f"shot vanished from manifest: {key}")
                anchor = self._fresh_anchor(job)
                out = self.abs_path(job.output)
                out.parent.mkdir(parents=True, exist_ok=True)
                _log.info("reroll generating %s (provider=%s seed=%d dur=%.2fs)",
                          key, job.provider, job.seed, job.duration_s)
                from lib import visual_router as vr
                clip = vr.generate_shot(
                    job.video_prompt, anchor, job.duration_s, job.provider,
                    job.seed, out, enable_gemini=True,
                    description=job.description, narration=job.narration,
                )
                with self.lock:
                    job.status = "done" if clip else "failed"
                    if not clip:
                        self.errors[key] = "generation returned no clip (all attempts failed)"
                    self._save_manifest()
                _log.info("reroll %s -> %s", key, job.status)
            except Exception as e:  # noqa: BLE001 — catch-all: worker must survive
                _log.exception("reroll worker error on %s", key)
                with self.lock:
                    if job is not None:
                        job.status = "failed"
                        self._save_manifest()
                    self.errors[key] = f"{type(e).__name__}: {e}"
            finally:
                with self.lock:
                    self.active = None
                self._queue.task_done()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "OpenMontageReview/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def dash(self) -> Dashboard:
        return self.server.dashboard  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet access log
        _log.debug("%s %s", self.address_string(), fmt % args)

    # -- plumbing -------------------------------------------------------------

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, msg: str) -> None:
        self._send_json({"error": msg}, status=status)

    def _read_body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("body must be a JSON object")
        return obj

    # -- GET -------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        try:
            path = urlparse(self.path).path
            if path == "/":
                self._serve_index()
            elif path == "/api/state":
                self._send_json(self.dash.build_state())
            elif path.startswith("/media/"):
                self._serve_media(path[len("/media/"):])
            else:
                self._send_error_json(404, f"no route: {path}")
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass  # browser cancelled (video seek aborts are routine)
        except Exception as e:  # noqa: BLE001 — never crash the server
            _log.exception("GET %s failed", self.path)
            try:
                self._send_error_json(500, f"{type(e).__name__}: {e}")
            except OSError:
                pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            try:
                body = self._read_body_json()
            except (ValueError, json.JSONDecodeError) as e:
                self._send_error_json(400, f"bad JSON body: {e}")
                return
            if path == "/api/decision":
                self._send_json(self.dash.set_decision(
                    str(body.get("segment_id", "")), str(body.get("shot_id", "")),
                    str(body.get("decision", "")), str(body.get("note", ""))))
            elif path == "/api/reroll":
                res = self.dash.reroll(
                    str(body.get("segment_id", "")), str(body.get("shot_id", "")),
                    bool(body.get("regenerate", False)))
                self._send_json(res, status=200 if res.get("ok") else 409)
            else:
                self._send_error_json(404, f"no route: {path}")
        except KeyError as e:
            self._send_error_json(404, str(e))
        except ValueError as e:
            self._send_error_json(400, str(e))
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            _log.exception("POST %s failed", self.path)
            try:
                self._send_error_json(500, f"{type(e).__name__}: {e}")
            except OSError:
                pass

    # -- index ------------------------------------------------------------------

    def _serve_index(self) -> None:
        state = self.dash.build_state()
        # "</" must not terminate the inline <script>:
        state_js = json.dumps(state).replace("</", "<\\/")
        html = PAGE_TEMPLATE.replace("__STATE_JSON__", state_js)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- media with Range support -------------------------------------------------

    def _serve_media(self, rel_quoted: str) -> None:
        rel = unquote(rel_quoted)
        if "\x00" in rel or rel.startswith(("/", "\\")) or ":" in rel:
            self._send_error_json(403, "forbidden path")
            return
        root = self.dash.project_dir
        target = (root / rel).resolve()
        if not target.is_relative_to(root):  # path-traversal guard
            self._send_error_json(403, "path escapes project root")
            return
        if not target.is_file():
            self._send_error_json(404, f"not found: {rel}")
            return

        size = target.stat().st_size
        ctype = _CTYPES.get(target.suffix.lower()) or \
            mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)\s*$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:  # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
                    end = size - 1
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with target.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(MEDIA_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass  # player aborted mid-stream — normal during seeking


# ---------------------------------------------------------------------------
# Page (inline CSS+JS, dark navy/amber — matches the channel's duotone look)
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenMontage Review</title>
<style>
  :root {
    --bg:#0a1020; --panel:#101a30; --panel2:#15223e; --line:#22304f;
    --ink:#dce6f5; --dim:#8294b3; --amber:#f0a832; --amber-dim:#8a6420;
    --green:#3ecf8e; --red:#ef5b5b; --blue:#5b8def;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.5 "Segoe UI", system-ui, sans-serif; }
  a { color:var(--amber); }
  /* ---- top bar ---- */
  header { position:sticky; top:0; z-index:10; background:rgba(10,16,32,.96);
           border-bottom:1px solid var(--line); padding:10px 18px;
           display:flex; flex-wrap:wrap; gap:10px 22px; align-items:center; }
  header .brand { font-weight:700; letter-spacing:.08em; color:var(--amber);
                  text-transform:uppercase; font-size:13px; }
  header .brand small { color:var(--dim); text-transform:none; letter-spacing:0;
                        font-weight:400; margin-left:8px; }
  #counts { display:flex; gap:6px; flex-wrap:wrap; }
  .chip { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px;
          border:1px solid var(--line); background:var(--panel); color:var(--dim); }
  .chip b { color:var(--ink); }
  .chip.done b   { color:var(--green); }
  .chip.failed b { color:var(--red); }
  .chip.flag b   { color:var(--amber); }
  nav.filters { display:flex; gap:4px; }
  nav.filters button { background:var(--panel); color:var(--dim); cursor:pointer;
        border:1px solid var(--line); border-radius:6px; padding:3px 10px; font-size:12px; }
  nav.filters button.on { color:#0a1020; background:var(--amber); border-color:var(--amber);
        font-weight:600; }
  #rerollbar { font-size:12px; color:var(--amber); }
  /* ---- cards ---- */
  main { max-width:1100px; margin:0 auto; padding:18px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
          margin-bottom:18px; padding:14px 16px; }
  .card.has-flagged  { border-color:var(--amber); box-shadow:0 0 0 1px var(--amber-dim); }
  .card.all-approved { border-color:var(--green); }
  .seghead { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
  .seghead h2 { margin:0; font-size:16px; color:var(--amber); letter-spacing:.04em; }
  .seghead .act { color:var(--dim); font-size:11px; text-transform:uppercase; }
  .narration { color:var(--ink); border-left:3px solid var(--amber-dim);
               padding:2px 0 2px 12px; margin:10px 0; font-style:italic; }
  .overlaybox { margin:6px 0; }
  .overlayline { display:inline-block; margin:2px 6px 2px 0; padding:2px 10px;
       background:#1a2742; border:1px dashed var(--amber-dim); color:var(--amber);
       font-weight:600; letter-spacing:.03em; font-size:13px; }
  details { margin:6px 0; }
  details summary { cursor:pointer; color:var(--dim); font-size:12px;
                    text-transform:uppercase; letter-spacing:.06em; }
  details pre { white-space:pre-wrap; background:var(--panel2); color:var(--ink);
       border:1px solid var(--line); border-radius:6px; padding:10px; font-size:12px;
       margin:6px 0 0; font-family:Consolas, monospace; }
  /* ---- shots ---- */
  .shot { background:var(--panel2); border:1px solid var(--line); border-radius:8px;
          padding:10px 12px; margin:10px 0; }
  .shot.approved { border-color:var(--green); }
  .shot.flagged  { border-color:var(--amber); box-shadow:0 0 0 1px var(--amber-dim); }
  .shot.failed   { border-color:var(--red); }
  .shothead { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
  .shothead .sid { font-weight:700; }
  .st { padding:0 8px; border-radius:9px; font-size:11px; font-weight:600;
        text-transform:uppercase; letter-spacing:.05em; }
  .st.done    { background:#10311f; color:var(--green); }
  .st.pending { background:#16243d; color:var(--blue); }
  .st.failed  { background:#3a1414; color:var(--red); }
  .st.hero    { background:#3a2c10; color:var(--amber); }
  .st.chain   { background:#1a2742; color:var(--dim); }
  .st.meta    { background:transparent; color:var(--dim); font-weight:400;
                text-transform:none; }
  .spin { display:inline-block; width:12px; height:12px; border-radius:50%;
          border:2px solid var(--amber); border-top-color:transparent;
          animation:spin .8s linear infinite; vertical-align:-2px; }
  @keyframes spin { to { transform:rotate(360deg);} }
  .rerolling-chip { color:var(--amber); font-size:12px; }
  .mediarow { display:flex; gap:12px; margin:10px 0; flex-wrap:wrap; align-items:flex-start; }
  .mediarow img { width:200px; border-radius:6px; border:1px solid var(--line); }
  .mediarow video { width:420px; max-width:100%; border-radius:6px; background:#000;
                    border:1px solid var(--line); }
  .nomedia { color:var(--dim); font-size:12px; padding:14px;
             border:1px dashed var(--line); border-radius:6px; }
  .err { color:var(--red); font-size:12px; margin:4px 0; }
  /* ---- controls ---- */
  .controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:8px; }
  .controls button { cursor:pointer; border-radius:6px; padding:4px 12px; font-size:12px;
        border:1px solid var(--line); background:var(--panel); color:var(--ink); }
  .controls button.approve.on { background:var(--green); color:#06281a;
        border-color:var(--green); font-weight:700; }
  .controls button.flag.on { background:var(--amber); color:#231903;
        border-color:var(--amber); font-weight:700; }
  .controls button.reroll { border-color:var(--amber-dim); color:var(--amber); }
  .controls button:disabled { opacity:.45; cursor:wait; }
  .controls input.note { flex:1; min-width:160px; background:var(--bg); color:var(--ink);
        border:1px solid var(--line); border-radius:6px; padding:4px 8px; font-size:12px; }
  .segmedia { display:flex; gap:14px; align-items:center; flex-wrap:wrap;
              margin-top:10px; border-top:1px solid var(--line); padding-top:10px; }
  .segmedia label { color:var(--dim); font-size:11px; text-transform:uppercase; }
  .segmedia audio { height:30px; }
  .hidden { display:none; }
</style>
</head>
<body>
<header>
  <div class="brand">OpenMontage Review <small id="ptitle"></small></div>
  <div id="counts"></div>
  <nav class="filters" id="filters">
    <button data-f="all" class="on">All</button>
    <button data-f="unreviewed">Unreviewed</button>
    <button data-f="flagged">Flagged</button>
    <button data-f="failed">Failed</button>
  </nav>
  <span id="rerollbar"></span>
  <a id="finalrender" class="hidden" target="_blank">Final render</a>
</header>
<main id="cards"></main>
<script>
"use strict";
let STATE = __STATE_JSON__;
let FILTER = "all";
const FP = {};   // segment id -> fingerprint of dynamic fields

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function shotMatches(s) {
  if (FILTER === "all") return true;
  if (FILTER === "unreviewed") return !s.decision;
  if (FILTER === "flagged") return s.decision === "flagged";
  if (FILTER === "failed") return s.status === "failed";
  return true;
}
function segMatches(seg) {
  if (FILTER === "all") return true;
  return seg.shots.some(shotMatches);
}

function fingerprint(seg) {
  return JSON.stringify(seg.shots.map(s =>
    [s.status, s.decision, s.note, s.rerolling, s.queued, s.clip_url, s.error, s.seed]));
}

async function post(url, body) {
  try {
    const r = await fetch(url, { method:"POST",
      headers:{ "Content-Type":"application/json" }, body: JSON.stringify(body) });
    if (!r.ok) console.warn(url, r.status, await r.text());
    return r.ok;
  } catch (e) { console.warn(url, e); return false; }
}

function findShot(segId, shotId) {
  const seg = STATE.segments.find(s => s.id === segId);
  return seg && seg.shots.find(s => s.shot_id === shotId);
}

function decide(segId, shotId, decision) {
  const s = findShot(segId, shotId);
  if (!s) return;
  const card = document.querySelector(`[data-shot="${segId}/${shotId}"]`);
  const note = card ? card.querySelector("input.note").value : (s.note || "");
  // optimistic toggle: clicking the active verdict clears it
  decision = (s.decision === decision) ? "clear" : decision;
  s.decision = decision === "clear" ? "" : decision;
  s.note = note;
  rerenderSeg(segId);
  renderTopbar();
  post("/api/decision", { segment_id:segId, shot_id:shotId, decision:decision, note:note });
}

function saveNote(segId, shotId, note) {
  const s = findShot(segId, shotId);
  if (!s) return;
  s.note = note;
  post("/api/decision", { segment_id:segId, shot_id:shotId,
        decision: s.decision || "clear", note:note });
}

function reroll(segId, shotId) {
  const s = findShot(segId, shotId);
  if (!s) return;
  if (!confirm(`Re-roll ${segId}/${shotId}?\n\nThis makes a PAID generation call (seed +37, fresh anchor).`))
    return;
  s.queued = true; s.status = "pending";
  rerenderSeg(segId);
  post("/api/reroll", { segment_id:segId, shot_id:shotId, regenerate:true });
}

function renderShot(seg, s) {
  const d = el("div", "shot " + (s.decision || "") + (s.status === "failed" ? " failed" : ""));
  d.dataset.shot = s.key;

  const head = el("div", "shothead");
  head.append(el("span", "sid", s.shot_id));
  head.append(el("span", "st " + s.status, s.status));
  if (s.hero) head.append(el("span", "st hero", "hero"));
  if (s.chain_from) head.append(el("span", "st chain", "chain ← " + s.chain_from));
  head.append(el("span", "st meta", s.provider + " · " + s.duration_s.toFixed(1) +
                                    "s · seed " + s.seed));
  if (s.rerolling) {
    const r = el("span", "rerolling-chip");
    r.append(el("span", "spin"), document.createTextNode(" re-rolling…"));
    head.append(r);
  } else if (s.queued) {
    head.append(el("span", "rerolling-chip", "queued for re-roll"));
  }
  d.append(head);

  if (s.error) d.append(el("div", "err", "worker: " + s.error));

  const media = el("div", "mediarow");
  if (s.keyframe_url) {
    const img = el("img");
    img.src = s.keyframe_url; img.loading = "lazy"; img.alt = "keyframe";
    img.onerror = () => { img.style.display = "none"; };
    media.append(img);
  }
  if (s.clip_url) {
    const v = el("video");
    v.controls = true; v.preload = "metadata"; v.src = s.clip_url;
    media.append(v);
  } else {
    media.append(el("div", "nomedia", "no clip on disk yet (" + s.status + ")"));
  }
  d.append(media);

  const det = el("details");
  if (s.status === "failed") det.open = true;       // failed shots auto-expand
  det.dataset.d = s.key + "/prompt";
  det.append(el("summary", "", "video_prompt"));
  det.append(el("pre", "", s.video_prompt));
  d.append(det);

  const c = el("div", "controls");
  const bA = el("button", "approve" + (s.decision === "approved" ? " on" : ""), "Approve");
  bA.onclick = () => decide(seg.id, s.shot_id, "approved");
  const bF = el("button", "flag" + (s.decision === "flagged" ? " on" : ""), "Flag");
  bF.onclick = () => decide(seg.id, s.shot_id, "flagged");
  const note = el("input", "note");
  note.placeholder = "note…"; note.value = s.note || "";
  note.onchange = () => saveNote(seg.id, s.shot_id, note.value);
  const bR = el("button", "reroll", "Re-roll (paid)");
  bR.disabled = !!(s.rerolling || s.queued);
  bR.onclick = () => reroll(seg.id, s.shot_id);
  c.append(bA, bF, note, bR);
  d.append(c);
  return d;
}

function renderSegment(seg) {
  const card = el("section", "card");
  card.dataset.seg = seg.id;
  const decs = seg.shots.map(s => s.decision);
  if (decs.includes("flagged")) card.classList.add("has-flagged");
  else if (seg.shots.length && decs.every(d => d === "approved"))
    card.classList.add("all-approved");

  const head = el("div", "seghead");
  head.append(el("h2", "", seg.id));
  if (seg.act) head.append(el("span", "act", seg.act));
  for (const s of seg.shots) {
    head.append(el("span", "st " + s.status, s.shot_id + ": " + s.status));
  }
  card.append(head);

  if (seg.narration) card.append(el("p", "narration", seg.narration));

  if (seg.text_overlay.length) {
    const ob = el("div", "overlaybox");
    for (const line of seg.text_overlay) ob.append(el("span", "overlayline", line));
    card.append(ob);
  }

  if (seg.ai_motion) {
    const det = el("details");
    det.dataset.d = seg.id + "/motion";
    det.append(el("summary", "", "ai_motion"));
    det.append(el("pre", "", seg.ai_motion));
    card.append(det);
  }

  const shots = el("div", "shots");
  for (const s of seg.shots) shots.append(renderShot(seg, s));
  if (!seg.shots.length) shots.append(el("div", "nomedia",
      "no AI shots in manifest (text card / animation segment)"));
  card.append(shots);

  if (seg.audio_url || seg.segment_clip_url) {
    const m = el("div", "segmedia");
    if (seg.audio_url) {
      m.append(el("label", "", "narration audio"));
      const a = el("audio");
      a.controls = true; a.preload = "none"; a.src = seg.audio_url;
      m.append(a);
    }
    if (seg.segment_clip_url) {
      const det = el("details");
      det.dataset.d = seg.id + "/segclip";
      det.append(el("summary", "", "assembled segment clip"));
      const v = el("video");
      v.controls = true; v.preload = "none"; v.src = seg.segment_clip_url;
      v.style.width = "420px"; v.style.maxWidth = "100%";
      det.append(v);
      m.append(det);
    }
    card.append(m);
  }
  return card;
}

function rerenderSeg(segId) {
  const seg = STATE.segments.find(s => s.id === segId);
  const node = document.querySelector(`section[data-seg="${segId}"]`);
  if (!seg || !node) return;
  if (node.contains(document.activeElement) &&
      document.activeElement.tagName === "INPUT") return; // don't clobber typing
  const fresh = renderSegment(seg);
  const open = new Set([...node.querySelectorAll("details[data-d]")]
                       .filter(d => d.open).map(d => d.dataset.d));
  fresh.querySelectorAll("details[data-d]").forEach(d => {
    if (open.has(d.dataset.d)) d.open = true;
  });
  fresh.classList.toggle("hidden", !segMatches(seg));
  node.replaceWith(fresh);
  FP[segId] = fingerprint(seg);
}

function renderTopbar() {
  const c = STATE.counts;
  document.getElementById("ptitle").textContent =
      STATE.project + " — " + STATE.title;
  const box = document.getElementById("counts");
  box.innerHTML = "";
  const mk = (cls, label, n) => {
    const ch = el("span", "chip " + cls);
    ch.append(document.createTextNode(label + " "));
    ch.append(el("b", "", String(n)));
    box.append(ch);
  };
  mk("done", "done", c.done); mk("", "pending", c.pending); mk("failed", "failed", c.failed);
  mk("done", "approved", c.approved); mk("flag", "flagged", c.flagged);
  mk("", "unreviewed", c.unreviewed);
  const fr = document.getElementById("finalrender");
  if (STATE.final_render) { fr.href = STATE.final_render; fr.classList.remove("hidden"); }
  else fr.classList.add("hidden");
  const rb = document.getElementById("rerollbar");
  const q = STATE.reroll.queued.length;
  rb.textContent = STATE.reroll.active
      ? ("re-rolling " + STATE.reroll.active + (q ? " (+" + q + " queued)" : ""))
      : (q ? q + " re-roll(s) queued" : "");
}

function renderAll() {
  const main = document.getElementById("cards");
  main.innerHTML = "";
  for (const seg of STATE.segments) {
    const node = renderSegment(seg);
    node.classList.toggle("hidden", !segMatches(seg));
    main.append(node);
    FP[seg.id] = fingerprint(seg);
  }
  renderTopbar();
}

function applyFilter() {
  for (const seg of STATE.segments) {
    const node = document.querySelector(`section[data-seg="${seg.id}"]`);
    if (node) node.classList.toggle("hidden", !segMatches(seg));
  }
}

document.getElementById("filters").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-f]");
  if (!b) return;
  FILTER = b.dataset.f;
  document.querySelectorAll("#filters button").forEach(x =>
      x.classList.toggle("on", x === b));
  applyFilter();
});

async function poll() {
  try {
    const r = await fetch("/api/state");
    if (!r.ok) return;
    STATE = await r.json();
    for (const seg of STATE.segments) {
      if (FP[seg.id] !== fingerprint(seg)) rerenderSeg(seg.id);
    }
    renderTopbar();
  } catch (e) { /* server restarting — keep polling */ }
}

renderAll();
setInterval(poll, 3000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def make_server(project: str | Path, port: int) -> ThreadingHTTPServer:
    """Build the dashboard + HTTP server (port 0 => OS-assigned, for tests)."""
    project_dir = Path(project)
    if not project_dir.is_absolute():
        project_dir = REPO_ROOT / project_dir
    dash = Dashboard(project_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ReviewHandler)
    httpd.dashboard = dash  # type: ignore[attr-defined]
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenMontage clip-review dashboard")
    parser.add_argument("--project", default="projects/therac-25-test",
                        help="project dir (repo-relative or absolute)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the browser")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    httpd = make_server(args.project, args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    _log.info("review dashboard for %s at %s",
              httpd.dashboard.project_name, url)  # type: ignore[attr-defined]
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log.info("shutting down")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
