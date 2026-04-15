"""U.S. Department of Energy (DOE) stock source adapter.

Wraps DOE-related YouTube channels behind the `StockSource` protocol
using ``yt-dlp`` as the search and download backend. The DOE and its
National Nuclear Security Administration (NNSA) publish declassified
footage of nuclear tests, reactor operations, facility tours, and
energy infrastructure — all public domain as U.S. federal government
works.

Key channels searched:
- ``@ABOROGER`` — curated collection of declassified DOE/AEC/NNSA
  nuclear testing films, reactor footage, and Cold War-era technical
  documentaries. Thousands of videos.
- ``@energy`` — official DOE channel with facility tours, research
  footage, and energy infrastructure content.

Requires ``yt-dlp`` binary on PATH. No API key needed.

What DOE is good for
--------------------
- nuclear testing footage (atmospheric and underground tests),
- reactor operations and control rooms,
- Three Mile Island and nuclear incident documentation,
- Cold War-era technical and scientific films,
- power grid and energy infrastructure footage,
- declassified government technical documentaries,
- any "nuclear disaster" or "infrastructure failure" documentary montage.

What it is *not* good for: chemical plant incidents (see CSB),
transportation accidents (see NTSB), or general-purpose B-roll. DOE
content is focused on nuclear, energy, and national security topics.
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

# Search across multiple DOE-adjacent channels for best coverage
_CHANNELS = ["@ABOROGER", "@energy"]
_LICENSE = "Public domain (U.S. federal government work)"


class DOESource:
    """U.S. Department of Energy adapter via yt-dlp.

    Satisfies ``StockSource``. Requires ``yt-dlp`` on PATH.
    """

    name = "doe"
    display_name = "U.S. Department of Energy"
    provider = "doe"
    priority = 12
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
        """Search YouTube for DOE/NNSA footage.

        Searches across multiple DOE-related channels using
        ``yt-dlp --flat-playlist --dump-json``. Results from all
        channels are merged and filtered client-side.
        """
        kind = (filters.kind or "video").lower()
        if kind not in ("video", "any"):
            return []

        yt_dlp = shutil.which("yt-dlp")
        if not yt_dlp:
            _log.warning("yt-dlp not found on PATH; DOE search unavailable")
            return []

        out: list[Candidate] = []
        per_channel = 10  # results per channel to keep total reasonable

        for channel in _CHANNELS:
            search_query = f"ytsearch{per_channel}:{query} site:youtube.com/{channel}"

            try:
                result = subprocess.run(
                    [
                        yt_dlp,
                        "--flat-playlist",
                        "--dump-json",
                        "--no-warnings",
                        search_query,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except subprocess.TimeoutExpired:
                _log.warning("yt-dlp timed out for DOE query on %s: %s", channel, query)
                continue
            except Exception as e:
                _log.warning("yt-dlp failed for DOE (%s): %s", channel, e)
                continue

            if result.returncode != 0:
                _log.warning(
                    "yt-dlp exited %d for DOE (%s) query: %s",
                    result.returncode, channel, query,
                )

            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cand = self._entry_to_candidate(entry, channel, filters)
                if cand is not None:
                    out.append(cand)

        return out

    def download(self, candidate: Candidate, out_path: Path) -> Path:
        """Download a DOE video via yt-dlp.

        Fetches the best available MP4 rendition at 720p or below.
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
        self, entry: dict, channel: str, filters: SearchFilters
    ) -> Optional[Candidate]:
        """Convert a yt-dlp JSON entry to a Candidate."""
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
            creator="U.S. Department of Energy",
            license=_LICENSE,
            source_tags=source_tags,
            thumbnail_url=entry.get("thumbnail") or "",
            extra={
                "channel": entry.get("channel") or channel,
                "upload_date": entry.get("upload_date") or "",
                "view_count": entry.get("view_count"),
            },
        )
