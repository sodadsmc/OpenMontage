"""First-party Google image generation/editing (gemini-2.5-flash-image).

Why this exists (2026-07-08, the machine-identity day): the KIE nano-banana queue
intermittently 500s, and — worse — its EDIT mode fetches reference images from
hosted URLs; when that fetch silently fails the task degrades to text-to-image
and re-imagines the scene (that is how an "edit this keyframe" call once returned
a completely different machine in a different palette). The Google API takes the
reference images as INLINE BYTES, so there is no hosted-URL fetch to fail and no
third-party queue: the master machine still and the corrected reference sheet
were both produced through this path after KIE failed 5/9 calls on the same job.

Use it directly for one-off asset work (sheets, canonicals, healing edits), or
let lib.visual_router._nano_image fall back to it automatically when KIE fails.

CLI:
  python -m lib.gemini_image "prompt" out.png [ref1.png ref2.jpg ...]
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

_log = logging.getLogger(__name__)

MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
# Gemini 2.5 Flash Image bills ~1290 output tokens per image ≈ $0.039.
COST_PER_IMAGE_USD = 0.039

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp"}


def _inline_part(path: Path) -> dict:
    mime = _MIME.get(path.suffix.lower(), "image/png")
    data = base64.b64encode(path.read_bytes()).decode()
    return {"inline_data": {"mime_type": mime, "data": data}}


def _download(url: str, dest: Path) -> Path | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
        return dest
    except Exception as exc:  # noqa: BLE001
        _log.warning("gemini_image: could not download ref %s: %s", url[:60], exc)
        return None


def gemini_image(prompt: str, output_path: str | Path,
                 image_paths: list[str | Path] | None = None,
                 image_urls: list[str] | None = None,
                 model: str | None = None,
                 attempts: int = 2,
                 retry_wait_s: float = 20.0) -> Path | None:
    """Generate (or edit, when reference images are given) via the Google API.

    Local ``image_paths`` are preferred (inlined as bytes). ``image_urls`` are
    downloaded to a temp file first — if a URL is dead we SKIP it loudly rather
    than let the model quietly reinterpret the prompt unanchored.
    Returns the written local Path, or None. Cost is logged to the ledger.
    """
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        _log.warning("gemini_image: GOOGLE_API_KEY not set")
        return None
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    parts: list[dict] = []
    n_refs = 0
    for p in (image_paths or []):
        p = Path(p)
        if p.is_file():
            parts.append(_inline_part(p))
            n_refs += 1
        else:
            _log.warning("gemini_image: ref missing on disk: %s", p)
    for i, u in enumerate(image_urls or []):
        tmp = output_path.parent / f".gemini_ref_{i}.bin"
        got = _download(u, tmp)
        if got:
            # sniff mime from magic bytes (default png)
            head = got.read_bytes()[:4]
            suffix = ".jpg" if head[:3] == b"\xff\xd8\xff" else ".png"
            typed = got.with_suffix(suffix)
            got.replace(typed)
            parts.append(_inline_part(typed))
            typed.unlink(missing_ok=True)
            n_refs += 1
    parts.append({"text": prompt})

    body = json.dumps({"contents": [{"parts": parts}]}).encode()
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model or MODEL}:generateContent?key={key}")
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.load(r)
        except Exception as exc:  # noqa: BLE001
            _log.warning("gemini_image: attempt %d failed: %s", attempt, exc)
            if attempt < attempts:
                time.sleep(retry_wait_s)
            continue
        for cand in resp.get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                blob = part.get("inlineData") or part.get("inline_data")
                if blob and blob.get("data"):
                    output_path.write_bytes(base64.b64decode(blob["data"]))
                    try:
                        from lib.cost_ledger import log as _cost_log
                        _cost_log("gemini-image",
                                  "image_edit" if n_refs else "image_generate",
                                  COST_PER_IMAGE_USD)
                    except Exception:  # noqa: BLE001
                        pass
                    return output_path
        _log.warning("gemini_image: attempt %d returned no image part", attempt)
        if attempt < attempts:
            time.sleep(retry_wait_s)
    return None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    out = gemini_image(sys.argv[1], sys.argv[2],
                       image_paths=[Path(p) for p in sys.argv[3:]])
    print(f"WROTE {out}" if out else "FAILED")
    sys.exit(0 if out else 1)
