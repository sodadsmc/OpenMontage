"""Host a local image at a public URL.

Kie.ai (Nano Banana edit, hero-video image-to-video) takes reference images as
public URLs and provides no upload endpoint. This helper uploads a local image
and returns a URL, with content-hash caching so a canonical reference shared by
many shots uploads only once.

Default chain: tmpfiles.org -> uguu.se -> 0x0 -> catbox. Order matters: Grok i2v
(Kie.ai) CAN ingest tmpfiles/uguu but CANNOT ingest catbox (anti-hotlink), so
Grok-compatible hosts come first or image-to-video anchors silently fail. Set
IMAGE_HOST=fal for fal.ai (requires FAL_KEY), or IMAGE_HOST=tmpfiles|uguu|catbox|0x0
to force one. Returns None if hosting fails (callers fall back to URL-free behavior).
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)
_CACHE: dict[str, str] = {}  # file content hash -> public url


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _upload_fal(path: Path) -> str | None:
    try:
        from tools.video._shared import upload_image_fal
        return upload_image_fal(str(path))
    except Exception as exc:  # noqa: BLE001
        _log.warning("image_host: fal upload failed: %s", exc)
        return None


def _upload_catbox(path: Path) -> str | None:
    """Upload to catbox.moe (keyless). Returns a direct URL or None."""
    import requests
    with open(path, "rb") as f:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (path.name, f)},
            timeout=60,
        )
    r.raise_for_status()
    url = r.text.strip()
    return url if url.startswith("http") else None


def _upload_0x0(path: Path) -> str | None:
    """Upload to 0x0.st (keyless). Returns a direct URL or None."""
    import requests
    with open(path, "rb") as f:
        r = requests.post(
            "https://0x0.st",
            files={"file": (path.name, f)},
            headers={"User-Agent": "OpenMontage/1.0 (+https://github.com/calesthio/OpenMontage)"},
            timeout=60,
        )
    r.raise_for_status()
    url = r.text.strip()
    return url if url.startswith("http") else None


# Browser-like UA so file-share hosts don't 403 a bare python-requests agent.
_BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def _upload_tmpfiles(path: Path) -> str | None:
    """Upload to tmpfiles.org (keyless, ~1h retention).

    Unlike catbox, Grok i2v (Kie.ai) CAN ingest tmpfiles URLs as i2v anchors.
    """
    import requests
    with open(path, "rb") as f:
        r = requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (path.name, f)},
            headers=_BROWSER_UA,
            timeout=90,
        )
    r.raise_for_status()
    u = (r.json().get("data") or {}).get("url")
    # API returns a viewer URL; /dl/ is the direct (raw image) link.
    return u.replace("tmpfiles.org/", "tmpfiles.org/dl/") if u else None


def _upload_uguu(path: Path) -> str | None:
    """Upload to uguu.se (keyless, ~3h retention).

    Grok i2v (Kie.ai) CAN ingest uguu URLs as i2v anchors (catbox it cannot).
    """
    import requests
    with open(path, "rb") as f:
        r = requests.post(
            "https://uguu.se/upload",
            files={"files[]": (path.name, f)},
            headers=_BROWSER_UA,
            timeout=90,
        )
    r.raise_for_status()
    try:
        return r.json()["files"][0]["url"]
    except Exception:  # noqa: BLE001
        u = r.text.strip()
        return u if u.startswith("http") else None


def _premiumize_key() -> str | None:
    return os.environ.get("PREMIUMIZE_ME_API_KEY") or os.environ.get("PREMIUMIZE_API_KEY")


def _upload_premiumize(path: Path) -> str | None:
    """Upload to premiumize.me cloud and return a direct-download link.

    DURABLE: the file persists permanently in the account, so unlike the keyless
    temp hosts (tmpfiles ~1h, uguu ~3h) it never expires — we just (re)generate a
    fresh link per call. Grok i2v ingests the resulting energycdn link (confirmed).
    Requires PREMIUMIZE_ME_API_KEY. Returns None if unset/unavailable.
    """
    import requests
    key = _premiumize_key()
    if not key:
        return None
    base = "https://www.premiumize.me/api"
    size = path.stat().st_size
    # CONTENT-ADDRESSED remote name. Every authored still is literally called
    # "keyframe.png": the account accumulated dozens, premiumize renames same-named
    # uploads ("keyframe (2).png"), and name+size verification then NEVER matches —
    # refs silently dropped and every Grok task ran unanchored and failed (caught
    # live on seg_024, both dispatches). A hash-suffixed name is collision-free, so
    # both the reuse pre-check and the post-upload pick become exact.
    uniq = f"{path.stem}-{_file_hash(path)[:10]}{path.suffix}"
    # Reuse an already-uploaded copy of THIS content (unique name = exact match).
    try:
        lst = requests.get(f"{base}/folder/list", params={"apikey": key}, timeout=30).json()
        for it in lst.get("content", []):
            if (it.get("type") == "file" and it.get("name") == uniq
                    and int(it.get("size", -1)) == size and it.get("link")):
                return it["link"]
    except Exception as exc:  # noqa: BLE001
        _log.warning("image_host: premiumize list failed: %s", exc)
    # Otherwise upload it.
    info = requests.get(f"{base}/folder/uploadinfo", params={"apikey": key}, timeout=30).json()
    if info.get("status") != "success":
        _log.warning("image_host: premiumize uploadinfo: %s", info)
        return None
    with open(path, "rb") as f:
        up = requests.post(info["url"], data={"token": info["token"]},
                           files={"file": (uniq, f)}, timeout=180)
    up.raise_for_status()
    # The listing is eventually consistent, so poll for the (unique) name+size.
    import time as _t
    for _ in range(8):
        _t.sleep(2)
        lst = requests.get(f"{base}/folder/list", params={"apikey": key}, timeout=30).json()
        exact = [it for it in lst.get("content", [])
                 if (it.get("type") == "file" and it.get("name") == uniq
                     and int(it.get("size", -1)) == size and it.get("link"))]
        if exact:
            return exact[-1]["link"]
    _log.warning("image_host: premiumize upload of %s (as %s) not visible in listing",
                 path.name, uniq)
    return None


def upload_image(path: str | Path) -> str | None:
    """Upload a local image and return a public URL (cached), or None on failure.

    If ``path`` is already an http(s) URL it is returned unchanged.
    """
    s = str(path)
    if s.startswith("http://") or s.startswith("https://"):
        return s
    p = Path(path)
    if not p.exists():
        _log.warning("image_host: file not found: %s", p)
        return None

    key = _file_hash(p)
    if key in _CACHE:
        return _CACHE[key]

    # Keyless by default: try catbox.moe, then 0x0.st (so one host being down
    # doesn't break us). Use fal only when IMAGE_HOST=fal.
    backend = os.environ.get("IMAGE_HOST", "auto").lower()
    # Host order for "auto": premiumize.me first when its key is set (DURABLE — the
    # file never expires, so i2v anchors can't go stale mid-run), then the keyless
    # Grok-compatible temp hosts (tmpfiles/uguu). Grok i2v CANNOT ingest catbox URLs
    # (anti-hotlink); catbox/0x0 stay as last-resort fallback (fine for Nano Banana
    # image edits, which CAN read catbox).
    _DURABLE = [_upload_premiumize, _upload_tmpfiles, _upload_uguu, _upload_0x0, _upload_catbox]
    chain = {
        "fal": [_upload_fal],
        "catbox": [_upload_catbox],
        "0x0": [_upload_0x0],
        "tmpfiles": [_upload_tmpfiles],
        "uguu": [_upload_uguu],
        "premiumize": [_upload_premiumize],
        "auto": _DURABLE,
    }.get(backend, _DURABLE)

    url: str | None = None
    for fn in chain:
        try:
            url = fn(p)
        except Exception as exc:  # noqa: BLE001
            _log.warning("image_host: %s failed: %s", fn.__name__, exc)
            url = None
        if url:
            break

    if url:
        _CACHE[key] = url
    return url
