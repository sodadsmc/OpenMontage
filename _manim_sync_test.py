"""Offline tests for the narration-sync core (lib/manim_sync + word_timing.find_phrase_start).

Pure timing logic — no manim, no API. Uses a small synthetic word list.

Run:  python _manim_sync_test.py
"""
from __future__ import annotations

from lib.word_timing import find_phrase_start
from lib.manim_sync import Cue, Timeline, schedule

# Synthetic narration word timeline (word_timing.words() shape).
WORDS = [
    {"word": "The",     "start_s": 0.0, "end_s": 0.2},
    {"word": "machine", "start_s": 0.3, "end_s": 0.7},
    {"word": "has",     "start_s": 0.8, "end_s": 1.0},
    {"word": "two",     "start_s": 1.1, "end_s": 1.3},
    {"word": "modes",   "start_s": 1.4, "end_s": 1.8},
    {"word": "X-ray",   "start_s": 2.0, "end_s": 2.4},
    {"word": "mode",    "start_s": 2.5, "end_s": 2.8},
    {"word": "metal",   "start_s": 3.0, "end_s": 3.3},
    {"word": "target",  "start_s": 3.4, "end_s": 3.9},
]


def test_find_phrase_start_sequences():
    assert find_phrase_start(WORDS, "metal target") == 3.0
    assert find_phrase_start(WORDS, "X-ray mode") == 2.0   # interior hyphen kept
    assert find_phrase_start(WORDS, "two modes") == 1.1
    assert find_phrase_start(WORDS, "machine") == 0.3


def test_find_phrase_start_punctuation_and_case():
    assert find_phrase_start(WORDS, "Metal, Target!") == 3.0   # case + edge punct
    assert find_phrase_start(WORDS, "TWO   modes") == 1.1       # extra whitespace


def test_find_phrase_start_fallback_and_miss():
    # full sequence absent → falls back to the first word of the phrase
    assert find_phrase_start(WORDS, "two beams") == 1.1
    # nothing matches at all
    assert find_phrase_start(WORDS, "nonexistent phrase") is None


def test_schedule_orders_and_lands_on_words():
    cues = [
        Cue("late",  "metal target", run_time=0.5),
        Cue("early", "two modes",    run_time=0.5),
    ]
    tl = schedule(cues, WORDS, total_s=6.0)
    assert [b.beat for b in tl.beats] == ["early", "late"]      # sorted by time
    assert tl.beats[0].start_s == 1.1 and tl.beats[1].start_s == 3.0
    assert tl.unresolved == []
    # final pad makes the scene exactly total_s
    assert abs((tl.end_s + 0) - (3.0 + 0.5)) < 1e-6
    assert tl.final_wait == round(6.0 - (3.0 + 0.5), 3)


def test_schedule_records_unresolved():
    tl = schedule([Cue("x", "no such phrase")], WORDS, total_s=5.0)
    assert tl.beats == [] and tl.unresolved == ["no such phrase"]


def test_schedule_collision_clamps_wait():
    # two anchors 0.3s apart but run_time 0.5s — second can't start before the
    # first play ends, so its wait clamps to 0 and it never goes backwards.
    cues = [Cue("a", "metal", run_time=0.5), Cue("b", "target", run_time=0.5)]
    tl = schedule(cues, WORDS, total_s=6.0)
    a, b = tl.beats
    assert a.start_s == 3.0
    assert b.wait_before == 0.0           # clamped (would be negative)
    assert b.start_s == round(3.0 + 0.5, 3)


def test_timeline_code_shape():
    tl = schedule([Cue("a", "two modes", run_time=0.5)], WORDS, total_s=4.0)
    code = tl.code()
    assert 'self.play(*BEATS["a"](), run_time=0.500)' in code
    assert code.count("self.wait(") == 2  # lead-in + final pad
    # custom var names
    assert 'scene.play(*B["a"]()' in tl.code(beats_var="B", scene="scene")


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
