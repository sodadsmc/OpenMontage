"""U.S. Chemical Safety Board (CSB) and disaster investigation YouTube adapter.

Searches YouTube for investigation documentaries, animated
reconstructions, and safety analysis videos via ``yt-dlp``. Returns
full-length videos that are best processed through the disaster corpus
pre-build pipeline (VideoAnalyzer → VideoTrimmer → CorpusBuilder) to
extract individual clips, rather than used directly in
``direct_clip_search`` (which expects short stock clips).

The adapter appends ``investigation disaster documentary`` to every
query to bias results toward investigation-quality content. Results
include both official government channels (USCSB, NRC, etc.) and
high-quality third-party documentaries (Kyle Hill, Fascinating Horror,
etc.) — the scoring pipeline handles quality filtering.

Requires ``yt-dlp`` binary on PATH. No API key needed.

What this adapter is good for
-----------------------------
- sourcing full investigation documentaries for corpus pre-build,
- finding animated reconstructions of industrial accidents,
- discovering disaster analysis content across YouTube,
- any topic where YouTube has deep documentary coverage.

What it is *not* good for: short stock clips (use Pexels/Pixabay),
real-time news footage (use archive_tv_news), or government archive
footage (use NARA/LOC). Results from this adapter are long-form and
need segmentation before use in a timeline.
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

_CHANNEL = "@USCSB"
_LICENSE = "Public domain (U.S. federal government work)"


class CSBSource:
    """U.S. Chemical Safety Board adapter via yt-dlp.

    Satisfies ``StockSource``. Requires ``yt-dlp`` on PATH.
    """

    name = "csb"
    display_name = "U.S. Chemical Safety Board"
    provider = "csb"
    priority = 60  # Low priority in live search — these return full docs, not clips.
                   # Best used via corpus pre-build (VideoAnalyzer → VideoTrimmer).
    install_instructions = (
        "Requires yt-dlp on PATH. Install with: pip install yt-dlp"
    )
    supports = {"video": True, "image": False}

    def is_available(self) -> bool:
        return shutil.which("yt-dlp") is not None

    # ------------------------------------------------------------------
    # Public protocol
    # ------------------------------------------------------------------

    def search(self, query: str, filters: SearchFilters) -> list[Candidate]:
        """Search YouTube for CSB investigation videos.

        Uses ``yt-dlp --flat-playlist --dump-json`` with a
        ``ytsearch20:`` prefix scoped to the ``@USCSB`` channel.
        Results are filtered client-side for duration constraints.
        """
        kind = (filters.kind or "video").lower()
        if kind not in ("video", "any"):
            return []

        yt_dlp = shutil.which("yt-dlp")
        if not yt_dlp:
            _log.warning("yt-dlp not found on PATH; CSB search unavailable")
            return []

        # Search within the USCSB channel only — all content is public
        # domain (US government work). Never search all of YouTube, which
        # would return copyrighted third-party content.
        channel_search_url = (
            f"https://www.youtube.com/{_CHANNEL}/search?query="
            + query.replace(" ", "+")
        )

        try:
            result = subprocess.run(
                [
                    yt_dlp,
                    "--flat-playlist",
                    "--dump-json",
                    "--no-warnings",
                    "--playlist-items", "1-20",
                    channel_search_url,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            _log.warning("yt-dlp search timed out for CSB query: %s", query)
            return []
        except Exception as e:
            _log.warning("yt-dlp search failed for CSB: %s", e)
            return []

        if result.returncode != 0:
            _log.warning(
                "yt-dlp exited %d for CSB query: %s", result.returncode, query
            )
            # Still try to parse — yt-dlp may emit partial results on
            # non-zero exit when some entries fail.

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
        """Download a CSB video via yt-dlp.

        Fetches the best available MP4 rendition at 720p or below to
        keep file sizes reasonable for corpus building.
        """
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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _entry_to_candidate(
        self, entry: dict, filters: SearchFilters
    ) -> Optional[Candidate]:
        """Convert a yt-dlp JSON entry to a Candidate.

        Returns None if the entry is missing required fields or fails
        duration filters.
        """
        video_id = entry.get("id") or entry.get("url") or ""
        if not video_id:
            return None

        title = entry.get("title") or ""
        description = entry.get("description") or ""
        duration = float(entry.get("duration") or 0)

        # Client-side duration filtering
        if filters.min_duration is not None and duration and duration < filters.min_duration:
            return None
        if filters.max_duration is not None and duration and duration > filters.max_duration:
            return None

        source_url = f"https://www.youtube.com/watch?v={video_id}"

        # Build source_tags from title and description
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
            creator="U.S. Chemical Safety Board",
            license=_LICENSE,
            source_tags=source_tags,
            thumbnail_url=entry.get("thumbnail") or "",
            extra={
                "channel": entry.get("channel") or _CHANNEL,
                "upload_date": entry.get("upload_date") or "",
                "view_count": entry.get("view_count"),
            },
        )
