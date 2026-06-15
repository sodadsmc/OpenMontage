"""Offline tests for the Motion Director.

These never hit the network: they cover the deterministic fallback (the floor),
prompt construction, the seed grammar, and Segment→context extraction. The LLM
authoring quality itself is validated by hand against the gold standard, not
asserted here.

Run:  python _motion_director_test.py
"""
from __future__ import annotations

import os
from types import SimpleNamespace

from lib import motion_director as md
from lib import scene_library as sl


def test_disabled_falls_back_to_deterministic_floor():
    os.environ["SCENE_LIBRARY_MOTION_DIRECTOR"] = "0"
    try:
        # escalation → push-in floor; emotional_impact → '' (static hold)
        assert md.author_motion({"editorial_intent": "escalation"}) == \
            sl.default_camera_phrase(editorial_intent="escalation")
        assert "dolly in" in md.author_motion({"editorial_intent": "escalation"})
        assert md.author_motion({"editorial_intent": "emotional_impact"}) == ""
    finally:
        os.environ.pop("SCENE_LIBRARY_MOTION_DIRECTOR", None)


def test_seed_grammar():
    # escalation seeds a push-in
    assert "dolly in" in md._seed_grammar("escalation", "")
    # emotional_impact seeds a static hold note (no phrase)
    assert "static hold" in md._seed_grammar("emotional_impact", "")
    # a director's move injects its description
    g = md._seed_grammar("resolution", "recontextualized_replay")
    assert "recontextualized_replay" in g and "meaning inverts" in g


def test_technical_explanation_mechanism_seed_not_static():
    # A mechanism-actuation beat must NOT be seeded as a static hold.
    mech = "the cam rotates against the microswitch and the lever snaps, gears turn"
    g = md._seed_grammar("technical_explanation", "", content=mech)
    assert "static hold" not in g
    assert "mechanism" in g.lower()
    assert "dolly in" in g  # the push seed
    # An inert technical_explanation beat still seeds a static hold.
    g2 = md._seed_grammar("technical_explanation", "", content="a still labelled diagram")
    assert "static hold" in g2


def test_build_prompt_includes_scene_and_exemplars():
    ctx = {
        "narration": "The machine fires and she feels searing heat.",
        "shot_prompt": "tight close-up on an older woman's face on a treatment table",
        "editorial_intent": "crisis_moment", "pacing": "escalation",
    }
    p = md._build_prompt(ctx)
    assert "SIX layers" in p                       # the rubric
    assert "GOLD-STANDARD EXEMPLARS" in p          # few-shot
    assert "orbital arc" in p                      # an exemplar's text is present
    assert "searing heat" in p                     # the scene's narration
    assert "crisis_moment" in p                    # the intent
    assert "OBJECT-PERMANENCE" in p                # the discipline layer


def test_exemplars_well_formed():
    assert len(md.EXEMPLARS) >= 3
    for ex in md.EXEMPLARS:
        assert ex["context"].strip() and ex["motion"].strip()


def test_author_for_segment_extracts_context(monkeypatch=None):
    # author_for_segment must read shot_prompt from shots[0] and has_caption
    # from text_overlay. We disable the LLM and check it doesn't crash + returns
    # the floor for the segment's intent.
    os.environ["SCENE_LIBRARY_MOTION_DIRECTOR"] = "0"
    try:
        seg = SimpleNamespace(
            narration="dense code races up the screen",
            editorial_intent="escalation", pacing="escalation", directors_move=None,
            visual=SimpleNamespace(
                ai_motion=None, ai_prompt=None, ai_style="cold isolation",
                description="a terminal glowing in the dark", location_id="control_room",
                text_overlay=["AECL"],
                shots=[SimpleNamespace(ai_prompt="amber monospace on a CRT, indistinct")],
                editorial_intent="escalation",
            ),
        )
        out = md.author_for_segment(seg)
        assert "dolly in" in out  # escalation floor
    finally:
        os.environ.pop("SCENE_LIBRARY_MOTION_DIRECTOR", None)


def _run() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
