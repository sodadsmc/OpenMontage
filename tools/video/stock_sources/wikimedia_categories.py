"""Wikimedia Commons category-tree adapter.

Navigates the category hierarchy on Wikimedia Commons to find curated
files that plain text search misses.  For example, ``Category:Therac-25``
contains 13 hand-catalogued diagrams and photos that no keyword query
will surface because the file names are in Vietnamese and Spanish.

Complements the text-search ``wikimedia.py`` adapter — enable both for
maximum coverage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Candidate, SearchFilters
from .wikimedia import (
    WikimediaSource,
    _page_to_candidate,
    _API_URL,
    _USER_AGENT,
)


class WikimediaCategoriesSource:
    """Navigate Wikimedia Commons category trees to find curated files."""

    name = "wikimedia_categories"
    display_name = "Wikimedia Commons Categories"
    provider = "wikimedia"
    priority = 24  # just above wikimedia text search (25)
    install_instructions = (
        "No setup required. Uses the public MediaWiki API."
    )
    supports = {"video": True, "image": True}

    def is_available(self) -> bool:
        return True

    def search(self, query: str, filters: SearchFilters) -> list[Candidate]:
        """Find files by navigating category trees matching *query*.

        1. ``opensearch`` in namespace 14 (Category) to find matching categories
        2. ``categorymembers`` (cmtype=file) to list files in each category
        3. ``imageinfo`` to build Candidate objects (shared with wikimedia.py)

        Walks one level of subcategories to catch nested curated sets
        without crawling the entire tree.
        """
        import requests

        categories = self._find_categories(query, requests)
        if not categories:
            return []

        # Collect file page IDs from categories + one level of subcategories
        file_titles: list[str] = []
        seen: set[str] = set()
        per_page = max(1, min(filters.per_page, 50))

        for cat_title in categories:
            self._collect_files(cat_title, file_titles, seen, per_page, requests)
            # Walk one level of subcategories
            subcats = self._list_subcategories(cat_title, requests)
            for subcat in subcats[:5]:  # limit depth
                self._collect_files(subcat, file_titles, seen, per_page, requests)
            if len(file_titles) >= per_page:
                break

        if not file_titles:
            return []

        # Fetch imageinfo for all collected files
        return self._files_to_candidates(file_titles[:per_page], filters, requests)

    def download(self, candidate: Candidate, out_path: Path) -> Path:
        """Download — delegates to the same logic as WikimediaSource."""
        import requests

        if not candidate.download_url:
            raise ValueError(f"Candidate {candidate.clip_id} has no download_url")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with requests.get(
            candidate.download_url,
            stream=True,
            timeout=300,
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
    def _find_categories(query: str, requests: Any) -> list[str]:
        """Find Commons categories matching *query* via opensearch."""
        try:
            r = requests.get(
                _API_URL,
                params={
                    "action": "opensearch",
                    "search": query,
                    "namespace": 14,  # Category namespace
                    "limit": 5,
                    "format": "json",
                },
                headers={"User-Agent": _USER_AGENT},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            # opensearch returns [query, [titles], [descriptions], [urls]]
            if len(data) >= 2 and isinstance(data[1], list):
                return [
                    t.replace("Category:", "") for t in data[1] if t.startswith("Category:")
                ]
        except Exception:
            pass
        return []

    @staticmethod
    def _collect_files(
        category: str,
        out: list[str],
        seen: set[str],
        limit: int,
        requests: Any,
    ) -> None:
        """Append file titles from *category* to *out*, skipping duplicates."""
        try:
            r = requests.get(
                _API_URL,
                params={
                    "action": "query",
                    "list": "categorymembers",
                    "cmtitle": f"Category:{category}",
                    "cmtype": "file",
                    "cmlimit": min(limit, 50),
                    "format": "json",
                },
                headers={"User-Agent": _USER_AGENT},
                timeout=20,
            )
            r.raise_for_status()
            members = r.json().get("query", {}).get("categorymembers", [])
            for m in members:
                title = m.get("title", "")
                if title and title not in seen:
                    seen.add(title)
                    out.append(title)
        except Exception:
            pass

    @staticmethod
    def _list_subcategories(category: str, requests: Any) -> list[str]:
        """Return subcategory names (one level only)."""
        try:
            r = requests.get(
                _API_URL,
                params={
                    "action": "query",
                    "list": "categorymembers",
                    "cmtitle": f"Category:{category}",
                    "cmtype": "subcat",
                    "cmlimit": 10,
                    "format": "json",
                },
                headers={"User-Agent": _USER_AGENT},
                timeout=20,
            )
            r.raise_for_status()
            members = r.json().get("query", {}).get("categorymembers", [])
            return [
                m["title"].replace("Category:", "")
                for m in members
                if m.get("title", "").startswith("Category:")
            ]
        except Exception:
            return []

    @staticmethod
    def _files_to_candidates(
        titles: list[str],
        filters: SearchFilters,
        requests: Any,
    ) -> list[Candidate]:
        """Fetch imageinfo for *titles* and build Candidate objects."""
        # MediaWiki API accepts up to 50 titles per request
        out: list[Candidate] = []
        for i in range(0, len(titles), 50):
            batch = titles[i : i + 50]
            try:
                r = requests.get(
                    _API_URL,
                    params={
                        "action": "query",
                        "titles": "|".join(batch),
                        "prop": "imageinfo|info",
                        "iiprop": "url|size|mime|extmetadata|mediatype",
                        "iiurlwidth": 640,
                        "inprop": "url",
                        "format": "json",
                    },
                    headers={"User-Agent": _USER_AGENT},
                    timeout=30,
                )
                r.raise_for_status()
                pages = (r.json().get("query") or {}).get("pages") or {}
                for page in pages.values():
                    cand = _page_to_candidate(page, filters)
                    if cand is not None:
                        # Override source name so corpus deduplication
                        # distinguishes category-sourced from text-sourced
                        cand = Candidate(
                            source="wikimedia_categories",
                            source_id=cand.source_id,
                            source_url=cand.source_url,
                            download_url=cand.download_url,
                            kind=cand.kind,
                            width=cand.width,
                            height=cand.height,
                            duration=cand.duration,
                            creator=cand.creator,
                            license=cand.license,
                            source_tags=cand.source_tags,
                            thumbnail_url=cand.thumbnail_url,
                            extra=cand.extra,
                        )
                        out.append(cand)
            except Exception:
                continue
        return out
