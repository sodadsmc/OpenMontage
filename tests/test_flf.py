"""Unit tests for the deterministic FLF endpoint authoring (lib.flf.drain_endpoint).

The whole point of deriving the FLF end frame in code (not via a generative edit) is that it
is pixel-matched and predictable. These tests lock that behaviour: a uniform drain cools the
whole frame toward navy; a banded drain cools only the chosen region and leaves the rest lit.
No network / API — pure PIL.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image

from lib.flf import NAVY, drain_endpoint

AMBER = (200, 150, 80)


def _mean(img: Image.Image, box=None) -> tuple[float, float, float]:
    region = img.crop(box) if box else img
    px = list(region.getdata())
    n = len(px)
    return tuple(sum(c[i] for c in px) / n for i in range(3))  # type: ignore[return-value]


def test_uniform_drain_cools_toward_navy():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "a.png"
        out = Path(d) / "b.png"
        Image.new("RGB", (16, 16), AMBER).save(src)
        drain_endpoint(src, out, drain=0.8)
        m = _mean(Image.open(out))
        # 80% blend toward navy: each channel must land near 0.2*amber + 0.8*navy.
        expected = tuple(0.2 * AMBER[i] + 0.8 * NAVY[i] for i in range(3))
        for got, exp in zip(m, expected):
            assert abs(got - exp) <= 1.5, (m, expected)
        # and strictly darker than the amber source
        assert sum(m) < sum(AMBER)


def test_banded_drain_keeps_top_lit_cools_bottom():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "a.png"
        out = Path(d) / "b.png"
        Image.new("RGB", (16, 40), AMBER).save(src)        # tall frame
        drain_endpoint(src, out, drain=0.85, band=(0.50, 0.62))
        img = Image.open(out)
        top = _mean(img, box=(0, 0, 16, 12))               # above the band -> untouched
        bottom = _mean(img, box=(0, 30, 16, 40))           # below the band -> fully drained
        assert top == AMBER, top                            # back rows stay lit
        assert sum(bottom) < sum(top)                       # front rows go cold
        for got, exp in zip(bottom, (0.15 * AMBER[i] + 0.85 * NAVY[i] for i in range(3))):
            assert abs(got - exp) <= 2.0
