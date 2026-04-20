"""Openverse (Creative Commons) image search adapter.

Openverse is the Creative Commons media search engine, aggregating
CC-licensed content from Wikimedia Commons, Flickr, museums, government
archives, and hundreds of other sources.  It returns license and
attribution metadata natively — perfect for documentary use where
credits must appear in the YouTube description.

Free, no API key required for up to 100 requests/day (unauthenticated).
Register at https://api.openverse.org/v1/#tag/auth for 10,000/day.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import Candidate, SearchFilters


_SEARCH_URL = "https://api.openverse.org/v1/images/"
_TOKEN_URL = "https://api.openverse.org/v1/auth_tokens/token/"
_USER_AGENT = "OpenMontageBot/0.1 (https://github.com/calesthio/OpenMontage)"


class OpenverseSource:
    """Search Creative Commons images via the Openverse API."""

    name = "openverse"
    display_name = "Openverse (Creative Commons)"
    provider = "openverse"
    priority = 20
    install_instructions = (
        "No API key required (100 requests/day unauthenticated). "
        "For higher limits set OPENVERSE_CLIENT_ID and "
        "OPENVERSE_CLIENT_SECRET in .env — register free at "
        "https://api.openverse.org/v1/#tag/auth"
    )
    supports = {"video": False, "image": True}

    def is_available(self) -> bool:
        return True  # works without credentials

    def search(self, query: str, filters: SearchFilters) -> list[Candidate]:
        import requests

        kind = (filters.kind or "image").lower()
        if kind == "video":
            return []  # Openverse is image-only

        params: dict[str, Any] = {
            "q": query,
            "page_size": max(1, min(filters.per_page, 20)),
            "page": max(1, filters.page),
            "license_type": "commercial",  # CC-BY, CC-BY-SA, CC0, PDM
            "mature": "false",
        }

        if filters.orientation:
            aspect = {
                "landscape": "wide",
                "portrait": "tall",
                "square": "square",
            }.get(filters.orientation)
            if aspect:
                params["aspect_ratio"] = aspect

        headers: dict[str, str] = {"User-Agent": _USER_AGENT}
        token = self._get_token(requests)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            r = requests.get(
                _SEARCH_URL,
                params=params,
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            return []

        out: list[Candidate] = []
        for result in data.get("results", []):
            cand = self._result_to_candidate(result, filters)
            if cand is not None:
                out.append(cand)
        return out

    def download(self, candidate: Candidate, out_path: Path) -> Path:
        import requests

        if not candidate.download_url:
            raise ValueError(f"Candidate {candidate.clip_id} has no download_url")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with requests.get(
            candidate.download_url,
            stream=True,
            timeout=120,
            headers={"User-Agent": _USER_AGENT},
        ) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
        return out_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _result_to_candidate(
        result: dict[str, Any],
        filters: SearchFilters,
    ) -> Candidate | None:
        """Convert an Openverse API result to a Candidate."""
        url = result.get("url", "")
        if not url:
            return None

        width = int(result.get("width") or 0)
        height = int(result.get("height") or 0)

        if filters.min_width and width and width < filters.min_width:
            return None

        # Build attribution-rich source_tags
        title = result.get("title", "")
        tags = [t.get("name", "") for t in result.get("tags", [])]
        source_tags = " ".join([title] + tags).strip()
        if len(source_tags) > 500:
            source_tags = source_tags[:500]

        # License: e.g. "by" + "4.0" → "CC BY 4.0"
        lic_code = (result.get("license", "") or "").upper()
        lic_version = result.get("license_version", "")
        license_str = f"CC {lic_code} {lic_version}".strip()
        if lic_code in ("CC0", "PDM"):
            license_str = lic_code

        return Candidate(
            source="openverse",
            source_id=str(result.get("id", "")),
            source_url=result.get("foreign_landing_url", "") or result.get("detail_url", ""),
            download_url=url,
            kind="image",
            width=width,
            height=height,
            duration=0.0,
            creator=result.get("creator", "") or "",
            license=license_str,
            source_tags=source_tags,
            thumbnail_url=result.get("thumbnail", "") or url,
            extra={
                "upstream_source": result.get("source", ""),
                "license_url": result.get("license_url", ""),
            },
        )

    @staticmethod
    def _get_token(requests: Any) -> str | None:
        """Obtain OAuth2 token if credentials are configured."""
        client_id = os.environ.get("OPENVERSE_CLIENT_ID")
        client_secret = os.environ.get("OPENVERSE_CLIENT_SECRET")
        if not client_id or not client_secret:
            return None

        try:
            r = requests.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=15,
            )
            r.raise_for_status()
            return r.json().get("access_token")
        except Exception:
            return None
