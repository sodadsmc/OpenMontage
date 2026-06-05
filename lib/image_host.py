"""Host a local image at a public URL.

Kie.ai (Nano Banana edit, hero-video image-to-video) takes reference images as
public URLs and provides no upload endpoint. This helper uploads a local image
and returns a URL, with content-hash caching so a canonical reference shared by
many shots uploads only once.

Default: keyless catbox.moe (no key needed). Set IMAGE_HOST=fal to use fal.ai
(requires FAL_KEY). Returns None if hosting fails (callers fall back to URL-free
behavior).
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
    chain = {
        "fal": [_upload_fal],
        "catbox": [_upload_catbox],
        "0x0": [_upload_0x0],
        "auto": [_upload_catbox, _upload_0x0],
    }.get(backend, [_upload_catbox, _upload_0x0])

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
