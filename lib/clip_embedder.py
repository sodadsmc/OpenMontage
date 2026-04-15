"""SigLIP 2 embedder: thin wrapper around google/siglip2-so400m-patch14-384
for corpus indexing and text-to-visual similarity ranking.

IMPORTANT: Switching from CLIP to SigLIP 2 changes embedding dimensions
from 512 to 1152. Any existing corpus .npy files must be re-embedded.

Design notes
------------
This module intentionally does ONE thing: turn images and text into
normalised 1152-d float32 vectors that can be cosine-compared.

- Single shared model instance, lazy-loaded on first call, so the
  weights only load once per process regardless of how many places in
  the codebase embed something.
- CPU by default, GPU if available. SigLIP 2 So400m uses 384x384 input
  resolution (vs CLIP's 224x224) and produces 1152-d embeddings.
- Output vectors are L2-normalised so cosine similarity reduces to a
  dot product — downstream code can `embeddings @ query_vec.T` and
  interpret it as cosine similarity directly.
- Batched at the caller's request count; no internal mini-batching.
  For corpora > a few hundred items, the caller should chunk.

This file does NOT decide what to embed or how to use the embeddings.
That intelligence lives in the corpus manager and retrieval skills.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence, Union

import numpy as np

# Import heavy deps lazily inside methods so importing this module does
# not pull torch/transformers unless someone actually uses it.


_MODEL = None
_PROCESSOR = None
_DEVICE: str = "cpu"
_MODEL_ID = "google/siglip2-so400m-patch14-384"


def _load() -> None:
    """Load SigLIP 2 model and processor exactly once per process."""
    global _MODEL, _PROCESSOR, _DEVICE
    if _MODEL is not None:
        return
    import torch  # type: ignore
    from transformers import AutoModel, AutoProcessor  # type: ignore

    _DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    _PROCESSOR = AutoProcessor.from_pretrained(_MODEL_ID)
    _MODEL = AutoModel.from_pretrained(_MODEL_ID).to(_DEVICE)
    _MODEL.eval()


def model_info() -> dict:
    """Return metadata about the loaded model (for index provenance)."""
    return {
        "model_id": _MODEL_ID,
        "device": _DEVICE,
        "dim": 1152,
    }


def embed_images(image_paths: Sequence[Union[str, Path]]) -> np.ndarray:
    """Embed a list of image files into a (N, 1152) float32 matrix.

    Each row is L2-normalised.
    """
    if not image_paths:
        return np.zeros((0, 1152), dtype=np.float32)

    import torch  # type: ignore
    from PIL import Image  # type: ignore

    _load()
    assert _MODEL is not None and _PROCESSOR is not None

    images = []
    for p in image_paths:
        img = Image.open(str(p)).convert("RGB")
        images.append(img)

    inputs = _PROCESSOR(images=images, return_tensors="pt").to(_DEVICE)
    with torch.no_grad():
        features = _MODEL.get_image_features(pixel_values=inputs["pixel_values"])
    features = features / features.norm(dim=-1, keepdim=True).clamp_min(1e-8)
    arr = features.cpu().numpy().astype(np.float32, copy=False)
    # Close PIL handles to avoid leaking file handles on Windows
    for img in images:
        img.close()
    return arr


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    """Embed a list of text strings into a (N, 1152) float32 matrix.

    Each row is L2-normalised.
    """
    if not texts:
        return np.zeros((0, 1152), dtype=np.float32)

    import torch  # type: ignore

    _load()
    assert _MODEL is not None and _PROCESSOR is not None

    # Empty strings break the processor — substitute a placeholder so
    # the alignment with caller indices stays intact.
    safe_texts = [t if t and t.strip() else "untitled" for t in texts]

    inputs = _PROCESSOR(
        text=safe_texts,
        return_tensors="pt",
        padding=True,
        truncation=True,
    ).to(_DEVICE)
    with torch.no_grad():
        features = _MODEL.get_text_features(input_ids=inputs["input_ids"])
    features = features / features.norm(dim=-1, keepdim=True).clamp_min(1e-8)
    return features.cpu().numpy().astype(np.float32, copy=False)


def pool_frames(frame_embeddings: np.ndarray) -> np.ndarray:
    """Average a (K, 1152) stack of frame embeddings into a (1152,) clip vector.

    Re-normalises after the mean. This is the simplest temporal pooling
    that still respects the L2 assumption the rest of the pipeline makes.
    """
    if frame_embeddings.size == 0:
        return np.zeros(1152, dtype=np.float32)
    mean = frame_embeddings.mean(axis=0)
    norm = np.linalg.norm(mean)
    if norm < 1e-8:
        return np.zeros(1152, dtype=np.float32)
    return (mean / norm).astype(np.float32, copy=False)
