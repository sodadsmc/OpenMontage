"""Tests for the Scene Library integration (The Director's Touch).

Covers:
  - schema↔module enum drift (the single-source-of-truth guarantee)
  - emotional-default mapping validity (every default is a real enum value)
  - default camera derivation (tension→push-in, reveal→pull-back, grief→static)
  - structural validator: invalid tags block, unfired open_loops block,
    cold-open / cadence / variety warn — and legacy untagged scripts don't
  - scored_script parser round-trips the new fields
  - planner threads the derived camera move into the continuous-camera cap

Run:  python _scene_library_test.py
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lib import scene_library as sl
from lib.scored_script import (
    ActInfo, ScoredScript, Segment, VisualSpec, parse_scored_script_text,
)
from lib.script_validator import validate_structure
from lib.visual_router import _derive_default_motion, _plan_shots

ROOT = Path(__file__).parent
ARTIFACTS = ROOT / "schemas" / "artifacts"

_MUSIC_STATES = {"playing", "continue", "change", "stop", "fade_out"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _schema_enum(schema_file: str, *path: str) -> set[str]:
    d = json.loads((ARTIFACTS / schema_file).read_text(encoding="utf-8"))
    for key in path:
        d = d[key]
    return set(d["enum"])


def _seg(i: int, **kw) -> Segment:
    vis = VisualSpec(
        description=kw.pop("desc", "a sufficiently long visual description"),
        type=kw.pop("vtype", "ai_video"),
    )
    return Segment(
        id=f"seg_{i:03d}", act="act_1",
        narration=kw.pop("narration", "Some narration words go here today."),
        visual=vis, **kw,
    )


def _script(segments: list[Segment]) -> ScoredScript:
    return ScoredScript(title="t", segments=segments,
                        acts=[ActInfo(id="act_1", title="Act 1")])


# ---------------------------------------------------------------------------
# 1. schema ↔ module drift
# ---------------------------------------------------------------------------

def test_scored_script_enums_match_module():
    base = ("scored_script.schema.json", "$defs", "segment", "properties")
    assert _schema_enum(*base, "narration_mode") == set(sl.NARRATION_MODES)
    assert _schema_enum(*base, "rhythm") == set(sl.RHYTHM)
    assert _schema_enum(*base, "audio_transition") == set(sl.AUDIO_TRANSITIONS)
    assert _schema_enum(*base, "directors_move") == set(sl.DIRECTORS_MOVE_NAMES)
    assert _schema_enum(*base, "retention_beat") == set(sl.RETENTION_BEATS)


def test_scene_plan_enums_match_module():
    base = ("scene_plan.schema.json", "properties", "scenes", "items", "properties")
    assert _schema_enum(*base, "narration_mode") == set(sl.NARRATION_MODES)
    assert _schema_enum(*base, "rhythm") == set(sl.RHYTHM)
    assert _schema_enum(*base, "audio_transition") == set(sl.AUDIO_TRANSITIONS)
    assert _schema_enum(*base, "directors_move") == set(sl.DIRECTORS_MOVE_NAMES)
    assert _schema_enum(*base, "retention_beat") == set(sl.RETENTION_BEATS)
    # shot_language must still agree with the canonical phrase maps it mirrors.
    sl_block = ("scene_plan.schema.json", "properties", "scenes", "items",
                "properties", "shot_language", "properties")
    assert _schema_enum(*sl_block, "shot_size") == set(sl.SHOT_SIZES)
    assert _schema_enum(*sl_block, "camera_movement") == set(sl.CAMERA_MOVES)


def test_segment_plan_enums_match_module():
    base = ("segment_plan.schema.json", "properties", "scenes", "items", "properties")
    assert _schema_enum(*base, "narration_mode") == set(sl.NARRATION_MODES)
    assert _schema_enum(*base, "rhythm") == set(sl.RHYTHM)
    assert _schema_enum(*base, "audio_transition") == set(sl.AUDIO_TRANSITIONS)
    assert _schema_enum(*base, "directors_move") == set(sl.DIRECTORS_MOVE_NAMES)
    assert _schema_enum(*base, "retention_beat") == set(sl.RETENTION_BEATS)


# ---------------------------------------------------------------------------
# 2. mapping validity
# ---------------------------------------------------------------------------

def test_emotional_defaults_are_valid_enum_values():
    intents = {
        "establishing_atmosphere", "building_trust", "technical_explanation",
        "escalation", "crisis_moment", "pattern_recognition", "emotional_impact",
        "institutional_critique", "resolution", "call_to_action",
        "chapter_transition",
    }
    # Every editorial_intent has a default (parity with the validator's set).
    assert set(sl.EMOTIONAL_DEFAULTS) == intents
    for intent, d in sl.EMOTIONAL_DEFAULTS.items():
        assert d["shot_size"] in sl.SHOT_SIZES, intent
        assert d["camera_move"] in sl.CAMERA_MOVES, intent
        assert d["rhythm"] in sl.RHYTHM, intent
        assert d["narration_mode"] in sl.NARRATION_MODES, intent
        assert d["music_cue"] in _MUSIC_STATES, intent


def test_directors_move_camera_hints_are_valid():
    for name, m in sl.DIRECTORS_MOVES.items():
        hint = m.get("camera_move")
        if hint:
            assert hint in sl.CAMERA_MOVES, name


def test_mechanism_detection():
    assert sl.is_mechanism_action(
        "the steel cam rotates against the microswitch and the lever snaps")
    assert sl.is_mechanism_action("the solenoid plunger drives home and gears turn")
    assert not sl.is_mechanism_action("a calm institutional diagram of dose levels")
    assert not sl.is_mechanism_action("")


def test_technical_explanation_is_content_aware():
    mech = "the cam rotates and the lever snaps"
    inert = "a still chart of two columns"
    # mechanism → push; inert / no content → static
    assert sl.default_camera_move("technical_explanation", content=mech) == "dolly_in"
    assert sl.default_camera_move("technical_explanation", content=inert) == "static"
    assert sl.default_camera_move("technical_explanation") == "static"
    # phrase: mechanism gets a push clause; inert gets none
    assert "dolly in" in sl.default_camera_phrase("technical_explanation", content=mech)
    assert sl.default_camera_phrase("technical_explanation", content=inert) == ""
    # refinement is SCOPED to technical_explanation — mechanism words don't move
    # a grief beat off its static hold
    assert sl.default_camera_move("emotional_impact", content=mech) == "static"
    # shot_language reflects it too
    assert sl.default_shot_language("technical_explanation", content=mech)["camera_move"] == "dolly_in"


# ---------------------------------------------------------------------------
# 3. default camera derivation
# ---------------------------------------------------------------------------

def test_default_camera_phrase():
    assert "dolly in" in sl.default_camera_phrase(editorial_intent="escalation")
    assert "dolly out" in sl.default_camera_phrase(editorial_intent="resolution")
    # grief / gravity = static hold → no motion clause
    assert sl.default_camera_phrase(editorial_intent="emotional_impact") == ""
    # director's-move camera hint wins over (absent) intent
    assert "dolly out" in sl.default_camera_phrase(
        directors_move="recontextualized_replay")
    assert "dolly in" in sl.default_camera_phrase(
        directors_move="calibrated_cliffhanger")
    # unknown / blank → empty
    assert sl.default_camera_phrase(editorial_intent="nope") == ""
    assert sl.default_camera_phrase() == ""


# ---------------------------------------------------------------------------
# 4. structural validator — blocking
# ---------------------------------------------------------------------------

def _errors(script) -> list[str]:
    return validate_structure(script).errors


def test_invalid_scene_library_enums_block():
    for field, bad in [
        ("narration_mode", "bogus"),
        ("directors_move", "nope"),
        ("audio_transition", "weird"),
        ("rhythm", "turbo"),
        ("retention_beat", "boom"),
    ]:
        rep = validate_structure(_script([_seg(1, **{field: bad})]))
        assert rep.blocking, field
        assert any(field in e for e in rep.errors), (field, rep.errors)


def test_unfired_open_loop_blocks():
    s = _script([
        _seg(1, open_loop={"action": "plant", "id": "missing_byte"}),
        _seg(2),
    ])
    rep = validate_structure(s)
    assert rep.blocking
    assert any("never paid off" in e for e in rep.errors)


def test_matched_open_loop_passes():
    s = _script([
        _seg(1, open_loop={"action": "plant", "id": "missing_byte"}),
        _seg(2, open_loop={"action": "payoff", "id": "missing_byte"}),
    ])
    errs = _errors(s)
    assert not any("open_loop" in e for e in errs), errs


def test_open_loop_warnings():
    # payoff before plant → warning; payoff with no plant → warning
    s = _script([
        _seg(1, open_loop={"action": "payoff", "id": "early"}),
        _seg(2, open_loop={"action": "plant", "id": "early"}),
        _seg(3, open_loop={"action": "payoff", "id": "orphan"}),
    ])
    rep = validate_structure(s)
    assert any("before/at its" in w for w in rep.warnings), rep.warnings
    assert any("never planted" in w for w in rep.warnings), rep.warnings


def test_open_loop_action_required_fields():
    rep = validate_structure(_script([_seg(1, open_loop={"action": "explode", "id": "x"})]))
    assert any("must be 'plant' or 'payoff'" in e for e in rep.errors)
    rep2 = validate_structure(_script([_seg(1, open_loop={"action": "plant"})]))
    assert any("missing 'id'" in e for e in rep2.errors)


# ---------------------------------------------------------------------------
# 5. structural validator — advisory, opt-in
# ---------------------------------------------------------------------------

def test_legacy_untagged_script_no_retention_noise():
    # No retention_beat anywhere → cold-open/cadence checks must stay silent.
    s = _script([_seg(i) for i in range(1, 6)])
    warns = validate_structure(s).warnings
    assert not any("Cold open" in w for w in warns), warns
    assert not any("pattern interrupt" in w for w in warns), warns


def test_cold_open_warns_when_tagged_but_missing_hook():
    # A long opening segment (no hook beat) but the script DOES use retention
    # tags later → cold-open check fires.
    long_narr = "word " * 60  # ~23s of speech, fills the 15s window alone
    s = _script([
        _seg(1, narration=long_narr),
        _seg(2, retention_beat="payoff"),
    ])
    warns = validate_structure(s).warnings
    assert any("Cold open" in w for w in warns), warns


def test_narration_mode_monotony_warns():
    s = _script([_seg(i, narration_mode="literal") for i in range(1, 7)])
    warns = validate_structure(s).warnings
    assert any("Narration mode" in w for w in warns), warns


# ---------------------------------------------------------------------------
# 6. parser round-trip
# ---------------------------------------------------------------------------

def test_parser_roundtrips_new_fields():
    yaml_text = """
version: "2.0"
metadata:
  title: "T"
segments:
  - id: seg_001
    act: act_1
    narration: "Hello there world today."
    editorial_intent: escalation
    rhythm: fast
    narration_mode: evocative
    audio_transition: j_cut
    directors_move: calibrated_cliffhanger
    retention_beat: cold_open
    open_loop: {action: plant, id: the_byte}
    visual:
      description: "a long enough visual description"
      type: ai_video
"""
    script = parse_scored_script_text(yaml_text)
    seg = script.segments[0]
    assert seg.rhythm == "fast"
    assert seg.narration_mode == "evocative"
    assert seg.audio_transition == "j_cut"
    assert seg.directors_move == "calibrated_cliffhanger"
    assert seg.retention_beat == "cold_open"
    assert seg.open_loop == {"action": "plant", "id": "the_byte"}


# ---------------------------------------------------------------------------
# 7. planner threads the derived move into the continuous-camera cap
# ---------------------------------------------------------------------------

def test_plan_shots_default_motion_drives_cap():
    spec = SimpleNamespace(
        effective_prompt="an empty corridor", description="an empty corridor",
        ai_motion=None, shots=[],
    )
    # A continuous default move (orbit/tracking/dolly) caps legs at sub-10s, so a
    # 20s segment must split; a blank default leaves the long single-clip cap.
    continuous = _plan_shots(spec, 20.0, narration="",
                             default_motion="orbital camera circling subject")
    assert len(continuous) >= 2, continuous
    blank = _plan_shots(spec, 20.0, narration="", default_motion="")
    assert len(blank) == 1, blank


def test_segment_tags_reach_visual_for_planner():
    # The two-phase batch path holds only seg.visual; the parser denormalizes the
    # segment's emotional tags onto it so the planner can still derive motion.
    yaml_text = """
version: "2.0"
metadata: {title: "T"}
segments:
  - id: seg_001
    act: act_1
    narration: "Tension rises here now."
    editorial_intent: escalation
    visual: {description: "an empty corridor at night", type: ai_video}
"""
    seg = parse_scored_script_text(yaml_text).segments[0]
    assert seg.visual.editorial_intent == "escalation"
    # _derive_default_motion, given ONLY seg.visual, must produce the push-in.
    assert "dolly in" in _derive_default_motion(seg.visual)


def test_derive_default_motion_explicit_wins():
    spec = SimpleNamespace(ai_motion="wild handheld chaos", editorial_intent="resolution")
    assert _derive_default_motion(spec) == ""  # explicit ai_motion suppresses default
    spec2 = SimpleNamespace(ai_motion=None, editorial_intent="emotional_impact")
    assert _derive_default_motion(spec2) == ""  # grief = static hold = no clause


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

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
