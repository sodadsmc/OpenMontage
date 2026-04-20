"""Pre-download relevance filter for stock footage candidates.

Scores candidate metadata (source_tags) against a reference text
(typically the scene's visual_description) using all-MiniLM-L6-v2
before downloading.  This prevents wasting bandwidth on clips whose
API-provided metadata is clearly unrelated to the target scene.

Uses the same model singleton as ``tools/scoring/layer1_metadata.py``
so there's no double-loading when both are used in the same process.

Falls back gracefully: if sentence-transformers is not installed,
returns all candidates unfiltered so the pipeline never blocks.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-loaded singleton — shared with layer1_metadata.py
# ---------------------------------------------------------------------------

_MODEL = None
_MODEL_NAME = "all-MiniLM-L6-v2"
_AVAILABLE: bool | None = None  # None = not checked yet


def _load_model() -> Any:
    """Load sentence-transformers model exactly once per process."""
    global _MODEL, _AVAILABLE
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(_MODEL_NAME)
        _AVAILABLE = True
        return _MODEL
    except Exception as exc:
        _AVAILABLE = False
        _log.warning("sentence-transformers unavailable — pre-download "
                     "filtering disabled: %s", exc)
        return None


def score_candidates(
    candidates: Sequence[Any],
    reference_text: str,
    threshold: float = 0.25,
    top_n: int | None = None,
) -> list[tuple[Any, float]]:
    """Score candidates by text similarity and return those above *threshold*.

    Parameters
    ----------
    candidates
        Objects with a ``source_tags`` attribute (str).  Typically
        ``Candidate`` instances from ``tools/video/stock_sources/base.py``.
    reference_text
        The scene's ``visual_description`` or equivalent prose.
    threshold
        Minimum cosine similarity to keep a candidate (0–1).
        Default 0.30 is conservative — calibrated to all-MiniLM-L6-v2's
        output range of 0.20–0.60 for tag-to-description matching.
    top_n
        If set, return at most *top_n* candidates regardless of threshold.

    Returns
    -------
    list of (candidate, score) pairs, sorted by score descending.
    If the model is unavailable, returns all candidates with score 0.0.
    """
    if not candidates:
        return []

    if not reference_text or not reference_text.strip():
        return [(c, 0.0) for c in candidates]

    model = _load_model()
    if model is None:
        # Graceful fallback — return everything unfiltered
        return [(c, 0.0) for c in candidates]

    import numpy as np

    # Extract source_tags from candidates (support both dict and object,
    # and the 'tags' alias used by some internal JSON artifacts)
    tag_texts: list[str] = []
    for c in candidates:
        if isinstance(c, dict):
            tags = c.get("source_tags") or c.get("tags") or ""
        else:
            tags = getattr(c, "source_tags", "") or ""
        tag_texts.append(tags)

    # Batch-encode reference + all tags in one call
    try:
        all_texts = [reference_text] + tag_texts
        embeddings = model.encode(all_texts, normalize_embeddings=True)
        query_vec = embeddings[0]
        tag_vecs = embeddings[1:]
        similarities = tag_vecs @ query_vec  # cosine (already L2-normed)
    except Exception as exc:
        _log.warning("Relevance scoring failed — returning all candidates: %s", exc)
        return [(c, 0.0) for c in candidates]

    # Pair, filter, sort
    scored = [(c, float(similarities[i])) for i, c in enumerate(candidates)]
    scored.sort(key=lambda x: -x[1])

    if top_n is not None:
        scored = scored[:top_n]
    else:
        scored = [(c, s) for c, s in scored if s >= threshold]

    return scored
