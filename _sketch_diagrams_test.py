"""Tests for the narration-synced sketch_diagrams scene + motion primitives
(the locked-in seg_007 diagram). Renders one real frame to exercise the draw
path; the rest is offline.

Run:  python _sketch_diagrams_test.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from lib.word_timing import load_alignment
from lib import sketch_diagrams as sk

ALIGN = "projects/therac-25-test/assets/audio_v6/seg_007.alignment.json"


def test_motion_primitives_exist():
    for name in ("fired_beam", "scatter_rays", "beam_flow", "pulse_glow",
                 "synced_linac", "resolve_cues", "LINAC_SYNC_CUES"):
        assert hasattr(sk, name), name


def test_resolve_cues_lands_on_words():
    C = sk.resolve_cues(load_alignment(ALIGN), sk.LINAC_SYNC_CUES)
    assert set(C) == set(sk.LINAC_SYNC_CUES)
    assert all(v >= 0 for v in C.values())
    # the key beats resolve near their spoken times
    assert 6.0 < C["electron"] < 7.5
    assert 11.0 < C["xray"] < 12.5
    assert 16.0 < C["target"] < 18.0
    assert C["electron"] < C["xray"] < C["target"] < C["rotate"] < C["depends"]


def test_synced_linac_returns_drawable():
    C = sk.resolve_cues(load_alignment(ALIGN), sk.LINAC_SYNC_CUES)
    assert callable(sk.synced_linac(C))


def test_scene_renders_a_frame_without_error():
    # the whole draw path (all phases + motion primitives) must not raise
    C = sk.resolve_cues(load_alignment(ALIGN), sk.LINAC_SYNC_CUES)
    draw = sk.synced_linac(C)
    with tempfile.TemporaryDirectory() as td:
        out = sk.render_frame(draw, 20.0, 34.5, Path(td) / "f.png")
        assert Path(out).exists() and Path(out).stat().st_size > 1000


def _run() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1; print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
