"""Internet Archive TV News Archive stock source adapter.

Wraps the TV News Archive at ``archive.org/details/tv`` behind the
`StockSource` protocol. This is a separate collection from the general
Archive.org adapter (``archive_org.py``) — the TV News Archive has its
own search API, its own clip-serving infrastructure, and a distinct
data model oriented around broadcast segments rather than uploaded
films.

The archive holds millions of searchable TV news clips from major U.S.
and international broadcasters (CNN, MSNBC, Fox News, BBC, Al Jazeera,
etc.), timestamped and captioned. Clips are served as short MP4
segments (typically up to 60 seconds).

No API key required. Everything is openly searchable.

What TV News Archive is good for
---------------------------------
- news broadcast footage of technology disasters (Challenger, Columbia,
  Deepwater Horizon, Fukushima, Boeing 737 MAX grounding),
- breaking-news coverage of industrial accidents,
- talking-head pundit clips for "media reaction" montages,
- dated broadcast footage for "as reported at the time" framing,
- any documentary sequence that needs real TV news atmosphere.

What it is *not* good for: raw investigation footage (see CSB, NTSB),
full-length documentaries (see Archive.org), or non-news archival
footage (see NARA, Prelinger via Archive.org).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from .base import Candidate, SearchFilters

_log = logging.getLogger(__name__)

_SEARCH_URL = "https://archive.org/details/tv"
_LICENSE = "Internet Archive TV News Archive (research/educational use)"


class ArchiveTVNewsSource:
    """Internet Archive TV News Archive adapter.

    Satisfies ``StockSource``. Stateless, no credentials required.
    """

    name = "archive_tv_news"
    display_name = "TV News Archive"
    provider = "archive_tv_news"
    priority = 20
    install_instructions = (
        "No setup required. The TV News Archive is freely searchable "
        "at archive.org/details/tv without API keys."
    )
    supports = {"video": True, "image": False}

    # LICENSING WARNING: TV News Archive clips are available for
    # research and educational use only. They are NOT cleared for
    # commercial use or YouTube monetization. This source is disabled
    # by default — set ARCHIVE_TV_NEWS_ENABLED=true in .env to opt in
    # for research/reference purposes only.
    _COMMERCIAL_USE = False

    def is_available(self) -> bool:
        import os
        # Disabled by default due to licensing restrictions.
        # Set ARCHIVE_TV_NEWS_ENABLED=true to enable for research use.
        return os.environ.get("ARCHIVE_TV_NEWS_ENABLED", "").lower() == "true"

    # ------------------------------------------------------------------
    # Public protocol
    # ------------------------------------------------------------------

    def search(self, query: str, filters: SearchFilters) -> list[Candidate]:
        """Search the TV News Archive for broadcast clips.

        Hits ``archive.org/details/tv?q=...&output=json`` and parses
        the response into Candidate objects. The TV News Archive serves
        clips up to ~60 seconds, so duration filters are applied
        client-side where metadata is available.
        """
        import requests  # lazy

        kind = (filters.kind or "video").lower()
        if kind not in ("video", "any"):
            return []

        params: dict[str, str] = {
            "q": query,
            "output": "json",
        }

        try:
            r = requests.get(_SEARCH_URL, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            _log.warning("TV News Archive search failed: %s", e)
            return []

        # The TV News Archive JSON response contains a "results" list
        # (or similar top-level structure depending on the endpoint
        # version). We handle both possible shapes defensively.
        results: list[dict] = []
        if isinstance(data, dict):
            results = data.get("results", []) or []
            if not results:
                results = data.get("docs", []) or []
            if not results:
                # Some endpoint versions wrap in "response"
                response = data.get("response", {}) or {}
                if isinstance(response, dict):
                    results = response.get("docs", []) or []
        elif isinstance(data, list):
            results = data

        out: list[Candidate] = []
        for item in results:
            cand = self._item_to_candidate(item, filters)
            if cand is not None:
                out.append(cand)

        return out

    def download(self, candidate: Candidate, out_path: Path) -> Path:
        """Download a TV News Archive clip to ``out_path``.

        The clip MP4 URL is stored in ``candidate.download_url`` and
        served directly by archive.org's clip infrastructure.
        """
        import requests  # lazy

        if not candidate.download_url:
            raise ValueError(
                f"Candidate {candidate.clip_id} has no download_url"
            )

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with requests.get(
            candidate.download_url, stream=True, timeout=180
        ) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
        return out_path

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _item_to_candidate(
        self, item: dict, filters: SearchFilters
    ) -> Optional[Candidate]:
        """Convert a TV News Archive result item to a Candidate.

        Returns None if required fields are missing or duration filters
        exclude the clip.
        """
        identifier = (
            item.get("identifier")
            or item.get("id")
            or item.get("_id")
            or ""
        )
        if not identifier:
            return None

        title = item.get("title") or item.get("snippet") or ""
        network = item.get("network") or item.get("channel") or ""
        show_title = item.get("showTitle") or item.get("show") or ""
        broadcast_date = (
            item.get("date")
            or item.get("broadcastDate")
            or item.get("start")
            or ""
        )

        # Duration — TV News clips are typically short (up to 60s) but
        # the field may be absent or in varying formats.
        duration = _parse_duration(item.get("duration"))

        # Client-side duration filtering
        if (
            filters.min_duration is not None
            and duration
            and duration < filters.min_duration
        ):
            return None
        if (
            filters.max_duration is not None
            and duration
            and duration > filters.max_duration
        ):
            return None

        # Build source URL — the TV News Archive has a per-clip viewer
        source_url = (
            item.get("url")
            or item.get("source_url")
            or f"https://archive.org/details/tv?q={quote(title or identifier)}"
        )

        # Download URL — the archive serves clip MP4s. The exact field
        # varies; fall back to constructing from identifier.
        download_url = (
            item.get("download_url")
            or item.get("mp4")
            or item.get("clipUrl")
            or item.get("mediaUrl")
            or ""
        )
        if not download_url and identifier:
            # Construct a plausible download URL from the identifier.
            # TV News Archive items use the standard archive.org
            # download path pattern.
            download_url = f"https://archive.org/download/{identifier}/{identifier}.mp4"

        if not download_url:
            return None

        # Build rich source_tags including broadcast metadata
        tag_parts = [
            title,
            network,
            show_title,
            str(broadcast_date) if broadcast_date else "",
            item.get("description") or "",
        ]
        source_tags = " ".join(s for s in tag_parts if s).strip()
        if len(source_tags) > 500:
            source_tags = source_tags[:500]

        return Candidate(
            source=self.name,
            source_id=identifier,
            source_url=source_url,
            download_url=download_url,
            kind="video",
            width=0,
            height=0,
            duration=duration,
            creator=network or "TV News Archive",
            license=_LICENSE,
            source_tags=source_tags,
            thumbnail_url=item.get("thumbnail") or item.get("thumb") or "",
            extra={
                "network": network,
                "show_title": show_title,
                "broadcast_date": broadcast_date,
                "language": item.get("language") or "",
            },
        )


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def _parse_duration(value: Any) -> float:
    """Parse a duration value into seconds.

    Handles numeric values, ``"HH:MM:SS"`` strings, ``"MM:SS"``
    strings, and bare float strings. Returns 0.0 for missing or
    unparseable values (interpreted as "unknown" by the caller).
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0

    # HH:MM:SS or HH:MM:SS.ss
    parts = s.split(":")
    if len(parts) == 3:
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except (ValueError, TypeError):
            pass
    # MM:SS or MM:SS.ss
    if len(parts) == 2:
        try:
            return int(parts[0]) * 60 + float(parts[1])
        except (ValueError, TypeError):
            pass
    # Bare number
    try:
        return float(s)
    except ValueError:
        return 0.0
