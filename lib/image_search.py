"""Broad web image search via Google Programmable Search (Custom Search JSON API).

The curated open-license repos (Wikimedia / Openverse / LoC) are blind to press photos
and most specific real objects — useless for "what does a real Therac-25 actually look
like?". This searches the WHOLE web for real reference imagery to GROUND AI generation.
The grounded canonical is a transformed graphic-novel illustration (the photo is used as
inspiration / shape reference), then every i2v shot inherits the accurate look.

Setup (one-time):
  1. Create a Programmable Search Engine: https://programmablesearchengine.google.com/
     -> "Add" -> turn ON "Search the entire web" -> copy the "Search engine ID" (cx).
  2. Enable the "Custom Search API" for the project behind GOOGLE_API_KEY
     (https://console.cloud.google.com/ -> APIs & Services -> Library -> Custom Search API).
  3. Put the id in .env:  GOOGLE_CSE_ID=<your search engine id>
Free tier: 100 queries/day.
"""
from __future__ import annotations

import os
from pathlib import Path

CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def _serper_key() -> str | None:
    """serper.dev key — ANY env var whose name contains SERPER (e.g. SERPER_API_KEY,
    or the dotted 'SERPER.DEV_API_KEY'). serper is a Google-Search proxy: no Cloud
    project, no API-enablement dance, one header. PREFERRED when present."""
    for k in os.environ:
        if "SERPER" in k.upper() and (os.environ.get(k) or "").strip():
            return os.environ[k]
    return None


def _search_key() -> str | None:
    """Google Custom Search key (fallback path). Prefer a DEDICATED key
    (GOOGLE_CSE_API_KEY) so GOOGLE_API_KEY stays reserved for Gemini/nano-banana."""
    return os.environ.get("GOOGLE_CSE_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def is_configured() -> tuple[bool, str]:
    """Return (ready, reason). Checks key NAMES only — never reads/prints values.
    serper.dev is preferred; Google Custom Search is the fallback."""
    if _serper_key():
        return True, "ok (serper.dev)"
    if _search_key() and os.environ.get("GOOGLE_CSE_ID"):
        return True, "ok (google cse)"
    return False, "no serper key, and no google-cse key+cx configured"


def search_images(query: str, num: int = 8, **filters) -> list[dict]:
    """Return up to `num` real-web image candidates for `query`. serper.dev is used
    when a SERPER key is present, else Google Custom Search. Each candidate:
    {url, title, source_page, thumbnail, width, height, mime, byte_size}."""
    ready, reason = is_configured()
    if not ready:
        raise RuntimeError(f"image_search not configured: {reason}")
    sk = _serper_key()
    if sk:
        return _serper_images(sk, query, num)
    return _google_cse_images(query, num, **filters)


def _serper_images(key: str, query: str, num: int) -> list[dict]:
    import json
    import requests
    r = requests.post(
        "https://google.serper.dev/images",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        data=json.dumps({"q": query, "num": max(1, min(20, int(num)))}), timeout=30,
    )
    r.raise_for_status()
    out: list[dict] = []
    for it in r.json().get("images", []):
        url = it.get("imageUrl") or ""
        if not url.startswith(("http://", "https://")):
            continue  # skip x-raw-image:/// data URIs (not downloadable)
        out.append({
            "url": url, "title": it.get("title"), "source_page": it.get("link"),
            "thumbnail": it.get("thumbnailUrl"), "width": it.get("imageWidth"),
            "height": it.get("imageHeight"), "mime": None, "byte_size": None,
        })
    return out


def _google_cse_images(query: str, num: int, **filters) -> list[dict]:
    import requests
    params = {
        "key": _search_key(), "cx": os.environ["GOOGLE_CSE_ID"], "q": query,
        "searchType": "image", "num": max(1, min(10, int(num))), "safe": "active",
    }
    params.update(filters)
    r = requests.get(CSE_ENDPOINT, params=params, timeout=30)
    if r.status_code == 403:
        raise RuntimeError("Custom Search API 403 — API enabled for this key's project?")
    r.raise_for_status()
    out: list[dict] = []
    for it in r.json().get("items", []):
        img = it.get("image", {}) or {}
        out.append({
            "url": it.get("link"), "title": it.get("title"),
            "source_page": img.get("contextLink"), "thumbnail": img.get("thumbnailLink"),
            "width": img.get("width"), "height": img.get("height"),
            "mime": it.get("mime"), "byte_size": img.get("byteSize"),
        })
    return out


def download_image(url: str, out_path: str | Path, timeout: int = 30) -> Path | None:
    """Download one image URL to out_path (browser UA, follows redirects). None on failure."""
    import requests
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        out_path.write_bytes(r.content)
        return out_path
    except Exception as exc:  # noqa: BLE001
        print(f"  download failed for {url[:80]}: {exc}")
        return None


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import tools.base_tool  # noqa: F401  -- loads .env
    ready, reason = is_configured()
    print("configured:", ready, "-", reason)
    if ready and len(sys.argv) > 1:
        for i, c in enumerate(search_images(" ".join(sys.argv[1:]), num=8)):
            print(f"  [{i}] {c['width']}x{c['height']} {c['mime']}  {c['title'][:60]}")
            print(f"       {c['url']}")
