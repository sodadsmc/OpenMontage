"""Asset Identity Registry — prevents duplicate and reused visual assets.

Tracks every generated asset, enforces uniqueness constraints from the
scored script, and uses perceptual hashing to detect near-duplicate
clips that would make different locations look identical.

Uniqueness modes:
  unique              — asset must be visually distinct from ALL others
  unique_per_location — asset must differ from other locations' assets
  reusable            — asset can be shared across segments (rare)

Usage:
    registry = AssetRegistry()

    # Check before generating
    registry.check_uniqueness(seg_id, visual_spec)

    # Register after generating
    asset_id = registry.register(seg_id, asset_path, visual_spec)

    # Verify no perceptual duplicates
    violations = registry.post_generation_check(asset_id)
    if violations:
        regenerate_with_different_seed()
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import numpy as np

_log = logging.getLogger(__name__)


@dataclass
class AssetRecord:
    """A registered visual asset with identity metadata."""
    id: str
    segment_id: str
    path: str
    visual_type: str
    uniqueness: str
    location_id: str | None
    phash: str  # Perceptual hash (hex string)
    file_hash: str  # SHA-256 of file bytes
    duration_s: float
    # Intended-continuity keys: shots that share a non-null asset_ref (same
    # Asset Bible entry) or continuity_group are SUPPOSED to look alike, so they
    # are exempt from the perceptual-duplicate check (AI-video consistency).
    asset_ref: str | None = None
    continuity_group: str | None = None


class AssetRegistry:
    """Tracks generated assets and enforces uniqueness constraints."""

    def __init__(self):
        self._assets: dict[str, AssetRecord] = {}  # asset_id → record
        self._by_segment: dict[str, str] = {}  # segment_id → asset_id
        self._by_location: dict[str, list[str]] = {}  # location_id → [asset_ids]

    @property
    def count(self) -> int:
        return len(self._assets)

    def register(
        self,
        segment_id: str,
        asset_path: str | Path,
        visual_spec: Any,
        continuity_group: str | None = None,
    ) -> str:
        """Register a generated asset. Returns asset_id.

        ``continuity_group`` (usually the Asset Bible entry's group) lets the
        caller mark assets that are intended to look alike so the perceptual
        duplicate check exempts them. The asset's own ``asset_ref`` is read from
        the visual_spec and serves the same purpose for shots of one asset.
        """
        asset_path = Path(asset_path)
        asset_id = f"asset_{segment_id}"

        # Compute hashes
        phash = compute_perceptual_hash(asset_path)
        file_hash = _file_sha256(asset_path)

        # Get duration
        try:
            dur = _probe_duration(asset_path)
        except Exception:
            dur = 0.0

        # Extract spec fields
        uniqueness = getattr(visual_spec, "uniqueness", "unique")
        location_id = getattr(visual_spec, "location_id", None)
        visual_type = getattr(visual_spec, "type", "unknown")
        asset_ref = getattr(visual_spec, "asset_ref", None)

        record = AssetRecord(
            id=asset_id,
            segment_id=segment_id,
            path=str(asset_path),
            visual_type=visual_type,
            uniqueness=uniqueness,
            location_id=location_id,
            phash=phash,
            file_hash=file_hash,
            duration_s=dur,
            asset_ref=asset_ref,
            continuity_group=continuity_group,
        )

        self._assets[asset_id] = record
        self._by_segment[segment_id] = asset_id

        if location_id:
            self._by_location.setdefault(location_id, []).append(asset_id)

        return asset_id

    def remove(self, asset_id: str):
        """Unregister an asset (e.g., after uniqueness violation)."""
        record = self._assets.pop(asset_id, None)
        if record:
            self._by_segment.pop(record.segment_id, None)
            if record.location_id and record.location_id in self._by_location:
                locs = self._by_location[record.location_id]
                if asset_id in locs:
                    locs.remove(asset_id)

    def post_generation_check(
        self,
        asset_id: str,
        similarity_threshold: float = 0.90,
    ) -> list[str]:
        """After generating, verify the asset isn't a perceptual duplicate.

        Returns list of violation messages (empty = OK).
        """
        if asset_id not in self._assets:
            return [f"Asset {asset_id} not registered"]

        new = self._assets[asset_id]
        violations = []

        for existing_id, existing in self._assets.items():
            if existing_id == asset_id:
                continue

            # Check perceptual similarity
            similarity = phash_similarity(new.phash, existing.phash)

            # Exact file match — always a violation for non-reusable assets
            if new.file_hash == existing.file_hash and new.uniqueness != "reusable":
                violations.append(
                    f"EXACT DUPLICATE: {new.id} is the same file as {existing.id} "
                    f"({existing.segment_id})"
                )
                continue

            # Intended continuity: shots of the SAME Asset Bible entry (asset_ref)
            # or the same continuity_group are SUPPOSED to look alike, so they are
            # exempt from the perceptual-duplicate check below. Exact-file dupes
            # (above) are still flagged even within an asset.
            same_continuity = (
                (new.asset_ref is not None and new.asset_ref == existing.asset_ref)
                or (new.continuity_group is not None
                    and new.continuity_group == existing.continuity_group)
            )

            # Perceptual similarity check
            if similarity > similarity_threshold and not same_continuity:
                if new.uniqueness == "unique":
                    violations.append(
                        f"PERCEPTUAL DUPLICATE: {new.id} is {similarity:.0%} similar "
                        f"to {existing.id} ({existing.segment_id}). "
                        f"Uniqueness constraint: unique."
                    )
                elif new.uniqueness == "unique_per_location":
                    # Only a violation if the other asset is for a DIFFERENT location
                    if existing.location_id and existing.location_id != new.location_id:
                        violations.append(
                            f"LOCATION REUSE: {new.id} (location={new.location_id}) "
                            f"is {similarity:.0%} similar to {existing.id} "
                            f"(location={existing.location_id}). "
                            f"Different locations must look different."
                        )

        return violations

    def check_all(self, similarity_threshold: float = 0.90) -> list[str]:
        """Check all registered assets for uniqueness violations."""
        all_violations = []
        checked_pairs: set[tuple[str, str]] = set()

        for asset_id in self._assets:
            violations = self.post_generation_check(asset_id, similarity_threshold)
            for v in violations:
                # Avoid reporting same pair twice
                other_id = v.split(" to ")[1].split(" (")[0] if " to " in v else ""
                pair = tuple(sorted([asset_id, other_id]))
                if pair not in checked_pairs:
                    all_violations.append(v)
                    checked_pairs.add(pair)

        return all_violations

    def summary(self) -> str:
        """Print a summary of registered assets."""
        lines = [f"Asset Registry: {self.count} assets"]
        by_type: dict[str, int] = {}
        for rec in self._assets.values():
            by_type[rec.uniqueness] = by_type.get(rec.uniqueness, 0) + 1
        for utype, count in sorted(by_type.items()):
            lines.append(f"  {utype}: {count}")

        locations = len(self._by_location)
        if locations:
            lines.append(f"  Distinct locations: {locations}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_count": self.count,
            "assets": {k: asdict(v) for k, v in self._assets.items()},
        }

    def save(self, path: str | Path):
        """Save registry to JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)


# ---------------------------------------------------------------------------
# Perceptual hashing
# ---------------------------------------------------------------------------

def compute_perceptual_hash(path: str | Path, hash_size: int = 16) -> str:
    """Compute a perceptual hash of a video/image file.

    For video: extracts 3 frames (10%, 50%, 90%) and combines their hashes.
    For images: hashes the image directly.

    Returns hex string.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in (".mp4", ".webm", ".mov", ".avi", ".mkv"):
        return _phash_video(path, hash_size)
    else:
        return _phash_image(path, hash_size)


def _phash_image(path: Path, hash_size: int = 16) -> str:
    """Average perceptual hash of a single image."""
    from PIL import Image
    img = Image.open(path).convert("L").resize(
        (hash_size, hash_size), Image.Resampling.LANCZOS
    )
    pixels = np.array(img, dtype=np.float64)
    avg = pixels.mean()
    bits = (pixels > avg).flatten()
    # Convert to hex
    byte_array = np.packbits(bits)
    return byte_array.tobytes().hex()


def _phash_video(path: Path, hash_size: int = 16) -> str:
    """Perceptual hash of a video — samples 3 frames and combines."""
    import tempfile
    from PIL import Image

    hashes = []
    for pos in ["10%", "50%", "90%"]:
        # Use ffmpeg to extract a frame at the given position
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Get video duration first
            dur_result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(dur_result.stdout.strip()) if dur_result.returncode == 0 else 5.0
            pct = int(pos.rstrip("%")) / 100.0
            seek_time = duration * pct

            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{seek_time:.2f}", "-i", str(path),
                 "-frames:v", "1", "-q:v", "2", tmp_path],
                capture_output=True, timeout=10,
            )

            if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 0:
                h = _phash_image(Path(tmp_path), hash_size)
                hashes.append(h)
        except Exception:
            pass
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass

    if not hashes:
        # Fallback: hash based on file size and name
        return hashlib.sha256(f"{path.name}:{path.stat().st_size}".encode()).hexdigest()[:hash_size * 2]

    # Combine frame hashes by XOR
    combined = int(hashes[0], 16)
    for h in hashes[1:]:
        combined ^= int(h, 16)
    return format(combined, f"0{hash_size * 2}x")


def phash_similarity(hash_a: str, hash_b: str) -> float:
    """Compute similarity between two perceptual hashes (0.0 to 1.0).

    1.0 = identical, 0.0 = completely different.
    """
    if not hash_a or not hash_b:
        return 0.0

    # Make hashes same length
    min_len = min(len(hash_a), len(hash_b))
    hash_a = hash_a[:min_len]
    hash_b = hash_b[:min_len]

    try:
        a = int(hash_a, 16)
        b = int(hash_b, 16)
    except ValueError:
        return 0.0

    # Hamming distance
    xor = a ^ b
    bit_length = min_len * 4  # each hex char = 4 bits
    hamming = bin(xor).count("1")
    similarity = 1.0 - (hamming / bit_length)
    return max(0.0, min(1.0, similarity))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    """SHA-256 hash of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_duration(path: Path) -> float:
    """Get duration via ffprobe."""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    if p.returncode == 0 and p.stdout.strip():
        return float(p.stdout.strip())
    return 0.0
