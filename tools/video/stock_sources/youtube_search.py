"""YouTube general search adapter via yt-dlp.

Searches all of YouTube (not channel-scoped) for relevant video footage.
By default, filters for Creative Commons licensed content to ensure
reusability in documentary production.

Unlike the channel-scoped CSB/NTSB/DOE adapters, this adapter searches
globally and returns videos from any uploader.  The CC filter dramatically
reduces copyright risk, though the scoring pipeline should still verify
suitability.

Requires ``yt-dlp`` binary on PATH.  No API key needed.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from .base import Candidate, SearchFilters

_log = logging.getLogger(__name__)


class YouTubeSearchSource:
    """General YouTube search adapter via yt-dlp."""

    name = "youtube_search"
    display_name = "YouTube (General Search)"
    provider = "youtube"
    priority = 50
    install_instructions = (
        "Requires yt-dlp on PATH. Install with: pip install yt-dlp"
    )
    supports = {"video": True, "image": False}

    # When True, only return Creative Commons licensed videos.
    cc_only: bool = True

    # Maximum video duration in seconds (avoid pulling full documentaries).
    max_duration_cap: float = 300.0

    def is_available(self) -> bool:
        return shutil.which("yt-dlp") is not None

    def search(self, query: str, filters: SearchFilters) -> list[Candidate]:
        """Search YouTube globally via yt-dlp ytsearch.

        Uses ``ytsearch20:<query>`` to fetch up to 20 results.
        Applies CC license filtering and duration caps.
        """
        kind = (filters.kind or "video").lower()
        if kind not in ("video", "any"):
            return []

        yt_dlp = shutil.which("yt-dlp")
        if not yt_dlp:
            _log.warning("yt-dlp not found on PATH; YouTube search unavailable")
            return []

        per_page = max(1, min(filters.per_page, 20))

        if self.cc_only:
            # Use YouTube's URL-based Creative Commons filter.
            # sp=EgIwAQ%3D%3D is the base64-encoded protobuf for CC filter.
            # This is more reliable than yt-dlp's --match-filter because
            # --flat-playlist doesn't populate the license field.
            encoded_query = query.replace(" ", "+")
            search_url = (
                f"https://www.youtube.com/results"
                f"?search_query={encoded_query}"
                f"&sp=EgIwAQ%3D%3D"
            )
        else:
            search_url = f"ytsearch{per_page}:{query}"

        cmd = [
            yt_dlp,
            "--flat-playlist",
            "--dump-json",
            "--no-warnings",
            "--playlist-items", f"1-{per_page}",
            search_url,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            _log.warning("yt-dlp search timed out for query: %s", query)
            return []
        except Exception as e:
            _log.warning("yt-dlp search failed: %s", e)
            return []

        if result.returncode != 0:
            _log.debug(
                "yt-dlp exited %d for YouTube query: %s",
                result.returncode, query,
            )

        out: list[Candidate] = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            cand = self._entry_to_candidate(entry, filters)
            if cand is not None:
                out.append(cand)

        return out

    def download(self, candidate: Candidate, out_path: Path) -> Path:
        """Download a YouTube video via yt-dlp."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        yt_dlp = shutil.which("yt-dlp")
        if not yt_dlp:
            raise RuntimeError("yt-dlp not found on PATH")

        try:
            result = subprocess.run(
                [
                    yt_dlp,
                    "-f",
                    "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
                    "/best[height<=720][ext=mp4]"
                    "/best[height<=720]",
                    "--merge-output-format", "mp4",
                    "-o", str(out_path),
                    candidate.download_url,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"yt-dlp download timed out for {candidate.source_url}"
            )
        except Exception as e:
            raise RuntimeError(f"yt-dlp download failed: {e}") from e

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(
                f"yt-dlp exited {result.returncode}: {stderr[:300]}"
            )

        return out_path

    def _entry_to_candidate(
        self, entry: dict, filters: SearchFilters,
    ) -> Candidate | None:
        """Convert a yt-dlp JSON entry to a Candidate."""
        video_id = entry.get("id") or entry.get("url") or ""
        if not video_id:
            return None

        title = entry.get("title") or ""
        description = entry.get("description") or ""
        duration = float(entry.get("duration") or 0)

        # Duration filtering
        if filters.min_duration is not None and duration and duration < filters.min_duration:
            return None
        # Hard cap from user filters (if set)
        if filters.max_duration is not None and duration and duration > filters.max_duration:
            return None

        # Classify as short clip (direct use) vs long-form (corpus pre-build)
        long_form = duration > self.max_duration_cap if duration else False

        # License from yt-dlp metadata
        yt_license = entry.get("license") or "YouTube Standard License"

        source_url = f"https://www.youtube.com/watch?v={video_id}"

        source_tags = f"{title} {description}".strip()
        if len(source_tags) > 500:
            source_tags = source_tags[:500]

        return Candidate(
            source=self.name,
            source_id=video_id,
            source_url=source_url,
            download_url=source_url,
            kind="video",
            width=0,
            height=0,
            duration=duration,
            creator=entry.get("uploader") or entry.get("channel") or "",
            license=yt_license,
            source_tags=source_tags,
            thumbnail_url=entry.get("thumbnail") or "",
            extra={
                "channel": entry.get("channel") or "",
                "channel_url": entry.get("channel_url") or "",
                "upload_date": entry.get("upload_date") or "",
                "view_count": entry.get("view_count"),
                "like_count": entry.get("like_count"),
                "long_form": long_form,
                "usage": "corpus_prebuild" if long_form else "direct_clip",
            },
        )
