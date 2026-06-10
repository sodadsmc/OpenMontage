"""Deterministic narration lint for scored documentary scripts.

No LLM, no API calls, no cost — pure stdlib regex heuristics plus
``lib.scored_script`` for loading. Supersedes the ``_narration_stats.py``
prototype.

Every rule encodes a craft constraint from
``docs/research/Tech Disaster Documentary Scriptwriting.md`` (sections
"Linguistic Mechanics for Text-to-Speech (TTS) Engine Optimization" and
"Circumventing 'AI Slop' Through Advanced Prompt Engineering"):

* The ear processes a sentence once — comprehension collapses past
  15-20 words, so long sentences are flagged (``long_sentence``).
* Passive voice degrades auditory comprehension; the subject must act
  (``passive_voice``).
* LLM tells — negative parallelism ("It's not X. It's Y."), the
  rhetorical answer ("The result? Devastating."), tricolons / rigid
  symmetry, staccato-fragment metronomes — break immersion when they
  repeat. The episode may keep ONE signature instance of the first two;
  extras are flagged (``negative_parallelism``, ``rhetorical_answer``,
  ``tricolon_cluster``, ``fragment_metronome``).
* The TTS sanitizer converts em-dashes to commas before synthesis, so
  em-dash-dependent rhythm silently degrades into comma-stumbles
  (``em_dash_overload``).
* Contractions force conversational delivery — "do not" makes the TTS
  voice stiff and robotic, so long contraction-free runs are flagged
  (``contraction_desert``).
* AI-favorite vocabulary signals machine generation and must be purged
  (``banned_vocab``).
* Numbers and names are hard to absorb aurally: spelled-out ranges whose
  first bound ends in a sub-thousand component are ambiguous
  (``ambiguous_number_range``), letter+digit tokens may be mangled by
  TTS (``alphanumeric_token``), and titles must precede names
  (``title_after_name``).

Public API:
    lint_script(script) -> {"findings": [...], "stats": {...}}
    lint_file(path)     -> same, loading via lib.scored_script

CLI:
    python -m lib.script_lint <scored_script.yaml> [--report out.json]
                              [--fail-on error|warn]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (module constants — tune here, not inside rules)
# ---------------------------------------------------------------------------

#: Doc: "The human brain struggles to track audio sentences exceeding
#: fifteen to twenty words." Warn past the ceiling, error when far past it.
LONG_SENTENCE_WARN_WORDS = 20
LONG_SENTENCE_ERROR_WORDS = 28

#: The episode deliberately keeps exactly one signature instance of each
#: of these AI-tell shapes; only the extras get warned.
NEGATIVE_PARALLELISM_BUDGET = 1
RHETORICAL_ANSWER_BUDGET = 1

#: A "short question immediately self-answered" — the question sentence
#: itself must be short to count as the AI-slop rhetorical-answer shape
#: ("The result? Devastating."), not a genuine transitional question.
RHETORICAL_QUESTION_MAX_WORDS = 6

#: Tricolon proxy: a run of >=3 consecutive short sentences/fragments, or
#: >=3 consecutive sentences opening with the same word (anaphora).
TRICOLON_RUN_MAX_WORDS = 8
TRICOLON_MIN_RUN = 3
#: Two tricolons inside one segment = manufactured rhythm -> warn.
TRICOLON_WARN_PER_SEGMENT = 2

#: Fragment metronome: segments that ALL end on a <=4-word fragment, three
#: or more in a row, produce a predictable thump the doc calls "rigid
#: symmetry". Human rhythm is asymmetrical.
METRONOME_FRAGMENT_MAX_WORDS = 4
METRONOME_MIN_RUN = 3

#: The TTS sanitizer flattens em-dashes to commas; 3+ dashes in one
#: segment means the spoken rhythm will audibly degrade.
DASH_OVERLOAD_PER_SEGMENT = 3

#: Doc: contractions force the engine to sound human. 8+ consecutive
#: segments without a single true contraction = robotic staccato run.
CONTRACTION_DESERT_MIN_RUN = 8

_SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}

# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# "Glen A." — a name initial, not a sentence end. Requires a preceding
# capitalized name word so "presses P." (real sentence end) stays split.
_INITIAL_RE = re.compile(r"\b[A-Z][a-z]+\s+[A-Z]\.$")

# Em-dash, en-dash, or a spaced hyphen used as a dash.
_DASH_RE = re.compile(r"—|–|(?<=\s)-(?=\s)")


def _normalize(narration: str) -> str:
    """Collapse YAML folding whitespace into single spaces."""
    return " ".join(narration.split())


def _sentences(text: str) -> list[str]:
    """Split into sentences, re-merging splits after initials ("Glen A.")."""
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text) if p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and _INITIAL_RE.search(merged[-1]):
            merged[-1] = merged[-1] + " " + part
        else:
            merged.append(part)
    return merged


def _words(text: str) -> list[str]:
    """True words: whitespace tokens containing at least one alphanumeric.

    Excludes punctuation-only tokens — a free-standing em-dash is rhythm,
    not a word, and must not inflate aural sentence-length counts.
    """
    return [t for t in text.split() if _ALNUM_RE.search(t)]


def _word_count(text: str) -> int:
    return len(_words(text))


def _clip(text: str, limit: int = 120) -> str:
    """Trim evidence strings so findings stay scannable."""
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass
class _SegView:
    """Precomputed per-segment view shared by all rule functions."""

    id: str
    text: str
    sentences: list[str] = field(default_factory=list)
    sentence_words: list[int] = field(default_factory=list)

    @classmethod
    def from_segment(cls, seg: Any) -> "_SegView":
        text = _normalize(getattr(seg, "narration", "") or "")
        sents = _sentences(text)
        return cls(
            id=str(getattr(seg, "id", "?")),
            text=text,
            sentences=sents,
            sentence_words=[_word_count(s) for s in sents],
        )


def _finding(rule: str, severity: str, segment_id: str, evidence: str,
             detail: str) -> dict[str, str]:
    return {
        "rule": rule,
        "severity": severity,
        "segment_id": segment_id,
        "evidence": _clip(evidence),
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Rule: long_sentence
# ---------------------------------------------------------------------------

def check_long_sentences(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag sentences past the aural comprehension ceiling.

    Doc: the ear gets one pass; sentences beyond 15-20 words lose the
    listener. Warn >20 true words, error >28 (irrecoverable aurally).
    """
    out: list[dict[str, str]] = []
    for sv in segs:
        for sent, wc in zip(sv.sentences, sv.sentence_words):
            if wc > LONG_SENTENCE_WARN_WORDS:
                sev = "error" if wc > LONG_SENTENCE_ERROR_WORDS else "warn"
                out.append(_finding(
                    "long_sentence", sev, sv.id, sent,
                    f"{wc} words — aural ceiling is "
                    f"{LONG_SENTENCE_WARN_WORDS}; the ear can't backtrack. "
                    f"Shatter into independent sentences.",
                ))
    return out


# ---------------------------------------------------------------------------
# Rule: passive_voice
# ---------------------------------------------------------------------------

_IRREGULAR_PARTICIPLES = (
    "written|born|done|made|known|shown|given|taken|built|held|kept|left|"
    "lost|paid|put|sent|set|told|found|brought|thought|caught|bought|hurt|"
    "hidden|driven|broken|chosen|seen|said|begun|worn|torn|thrown|drawn|"
    "grown|flown|laid|led|met|sold|spent|stood|understood|won|fed|felt|"
    "fought|heard|meant|spoken|stolen|swept|taught"
)
#: be-verb (incl. negated) + optional adverb + past participle.
_PASSIVE_RE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being|"
    r"isn['’]t|aren['’]t|wasn['’]t|weren['’]t)\s+"
    r"(?:(?:\w+ly|first|once|never|already|still|just)\s+)?"
    rf"(?:\w+ed|{_IRREGULAR_PARTICIPLES})\b",
    re.IGNORECASE,
)


def check_passive_voice(segs: list[_SegView],
                        allowlist: Iterable[str] = ()) -> list[dict[str, str]]:
    """Flag be-verb + past-participle constructions.

    Doc: "Passive voice severely degrades auditory comprehension. Scripts
    must rely strictly on the active voice" — 'The engineers ignored the
    telemetry', not 'The telemetry was ignored'. Heuristic — adjectival
    participles ("was undocumented") also match; use ``allowlist`` for
    accepted machine-voice lines (substring match, case-insensitive).
    """
    allowed = [a.lower() for a in allowlist]
    out: list[dict[str, str]] = []
    for sv in segs:
        for m in _PASSIVE_RE.finditer(sv.text):
            phrase = m.group(0)
            if any(a in phrase.lower() or phrase.lower() in a for a in allowed):
                _log.debug("passive allowlisted: %s (%s)", phrase, sv.id)
                continue
            out.append(_finding(
                "passive_voice", "warn", sv.id, phrase,
                "Passive construction — recast so the subject performs "
                "the action; active verbs keep narrative momentum.",
            ))
    return out


# ---------------------------------------------------------------------------
# Rule: negative_parallelism
# ---------------------------------------------------------------------------

#: Shape A: negation, short gap, clause boundary, copula reframe —
#: "It's not a bug. It's a fundamental design flaw."
_NEG_PARALLEL_A_RE = re.compile(
    r"(?:\bnot\b|n['’]t\b|\bnever\b)"
    r"[^.!?;—]{0,60}"
    r"[,.;:—–]\s*"
    r"(?:[Ii]t['’]s|[Ii]t\s+(?:is|was)|[Tt]hat['’]s|[Tt]his\s+is)\b"
)
#: Shape B: "not because X but because Y".
_NEG_PARALLEL_B_RE = re.compile(
    r"\bnot\s+because\b[^.!?]{0,80}\bbut\s+because\b", re.IGNORECASE
)


def check_negative_parallelism(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag the LLM "Em-Dash Dismissal" / negative-parallelism tell.

    Doc: "It's not X — it's Y" manufactures false profundity; state the
    functional reality directly. The episode keeps exactly ONE signature
    instance (budget); the first found is reported info, extras warn.
    """
    hits: list[tuple[str, str]] = []
    for sv in segs:
        for rx in (_NEG_PARALLEL_A_RE, _NEG_PARALLEL_B_RE):
            for m in rx.finditer(sv.text):
                hits.append((sv.id, m.group(0)))
    out: list[dict[str, str]] = []
    for i, (sid, evidence) in enumerate(hits):
        within = i < NEGATIVE_PARALLELISM_BUDGET
        out.append(_finding(
            "negative_parallelism",
            "info" if within else "warn",
            sid, evidence,
            f"Negative-parallelism shape (instance {i + 1} of {len(hits)}; "
            f"budget {NEGATIVE_PARALLELISM_BUDGET}/script). "
            + ("Within budget — the kept signature instance."
               if within else
               "Over budget — state the reality directly without the "
               "'not X, it's Y' reframe."),
        ))
    return out


# ---------------------------------------------------------------------------
# Rule: rhetorical_answer
# ---------------------------------------------------------------------------

def check_rhetorical_answer(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag short questions immediately self-answered in the same segment.

    Doc: AI builds false tension via "The result? Devastating." A short
    (<= RHETORICAL_QUESTION_MAX_WORDS) question followed by its own answer
    is the tell; one signature instance is budgeted, extras warn. A
    segment-final question (a genuine open hook) does not match.
    """
    hits: list[tuple[str, str]] = []
    for sv in segs:
        for i, (sent, wc) in enumerate(zip(sv.sentences, sv.sentence_words)):
            if (sent.endswith("?")
                    and 0 < wc <= RHETORICAL_QUESTION_MAX_WORDS
                    and i + 1 < len(sv.sentences)):
                hits.append((sv.id, f"{sent} {sv.sentences[i + 1]}"))
    out: list[dict[str, str]] = []
    for i, (sid, evidence) in enumerate(hits):
        within = i < RHETORICAL_ANSWER_BUDGET
        out.append(_finding(
            "rhetorical_answer",
            "info" if within else "warn",
            sid, evidence,
            f"Short question self-answered (instance {i + 1} of "
            f"{len(hits)}; budget {RHETORICAL_ANSWER_BUDGET}/script). "
            + ("Within budget — the kept signature instance."
               if within else
               "Over budget — replace the question-answer beat with a "
               "direct statement."),
        ))
    return out


# ---------------------------------------------------------------------------
# Rule: tricolon_cluster
# ---------------------------------------------------------------------------

_COMMA_ANAPHORA_RE = re.compile(
    r"\b(\w{2,})\b[^,.!?;]{0,30},\s*\1\b[^,.!?;]{0,30},\s*(?:and\s+|or\s+)?\1\b",
    re.IGNORECASE,
)


def _first_word(sentence: str) -> str:
    m = re.search(r"[A-Za-z]+", sentence)
    return m.group(0).lower() if m else ""


def _runs(flags: list[bool], min_run: int) -> list[tuple[int, int]]:
    """Maximal runs of True of length >= min_run, as (start, end) inclusive."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, f in enumerate(flags + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= min_run:
                runs.append((start, i - 1))
            start = None
    return runs


def check_tricolon(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag rule-of-three / rigid-symmetry rhythm.

    Doc: AI "writes in blocks of rigid symmetry... overusing the 'rule of
    three' (tricolons) to create a manufactured, predictable rhythm."
    Detectors: (a) >=3 consecutive short sentences, (b) >=3 consecutive
    sentences opening with the same word (anaphora), (c) a comma list
    repeating its lead word three times ("every injury, every report,
    every death"). Each instance is info; two in one segment warns.
    """
    out: list[dict[str, str]] = []
    for sv in segs:
        n = len(sv.sentences)
        short = [0 < wc <= TRICOLON_RUN_MAX_WORDS for wc in sv.sentence_words]
        spans = _runs(short, TRICOLON_MIN_RUN)
        firsts = [_first_word(s) for s in sv.sentences]
        ana_flags = [
            bool(firsts[i]) and (
                (i + 1 < n and firsts[i] == firsts[i + 1])
                or (i > 0 and firsts[i] == firsts[i - 1])
            )
            for i in range(n)
        ]
        # Anaphora needs >=3 sentences sharing ONE first word consecutively.
        for a, b in _runs(ana_flags, TRICOLON_MIN_RUN):
            if len({firsts[i] for i in range(a, b + 1)}) == 1:
                spans.append((a, b))
        # Merge overlapping spans so one run counts once.
        spans.sort()
        merged: list[tuple[int, int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        instances = [" ".join(sv.sentences[a:b + 1]) for a, b in merged]
        instances += [m.group(0) for m in _COMMA_ANAPHORA_RE.finditer(sv.text)]
        for ev in instances:
            out.append(_finding(
                "tricolon_cluster", "info", sv.id, ev,
                "Tricolon / parallel-fragment rhythm — fine once, but "
                "rigid symmetry reads as machine cadence.",
            ))
        if len(instances) >= TRICOLON_WARN_PER_SEGMENT:
            out.append(_finding(
                "tricolon_cluster", "warn", sv.id,
                " | ".join(_clip(e, 55) for e in instances),
                f"{len(instances)} tricolons in one segment — manufactured "
                "rhythm; vary with an asymmetric long sentence.",
            ))
    return out


# ---------------------------------------------------------------------------
# Rule: fragment_metronome
# ---------------------------------------------------------------------------

def _ends_in_fragment(sv: _SegView) -> bool:
    return bool(sv.sentence_words) and (
        0 < sv.sentence_words[-1] <= METRONOME_FRAGMENT_MAX_WORDS
    )


def check_fragment_metronome(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag 3+ consecutive segments all ending on a <=4-word fragment.

    Doc: human rhythm is asymmetrical — "mixing sharp fragments with
    lengthy, descriptive passages". One punchy ending lands; every
    segment ending punchy becomes a predictable metronome.
    """
    flags = [_ends_in_fragment(sv) for sv in segs]
    out: list[dict[str, str]] = []
    for a, b in _runs(flags, METRONOME_MIN_RUN):
        tails = " / ".join(segs[i].sentences[-1] for i in range(a, b + 1))
        out.append(_finding(
            "fragment_metronome", "warn",
            f"{segs[a].id}-{segs[b].id}", tails,
            f"{b - a + 1} consecutive segments end on a "
            f"<= {METRONOME_FRAGMENT_MAX_WORDS}-word fragment — vary at "
            "least one ending to break the metronome.",
        ))
    return out


# ---------------------------------------------------------------------------
# Rule: em_dash_overload
# ---------------------------------------------------------------------------

#: First word after a dash that restates a clause opener just before it
#: ("if X — if X' — Y") collapses into a comma-stumble post-sanitizer.
_SELF_INTERRUPT_STOPWORDS = {"the", "a", "an", "and", "or", "but", "so", "then"}


def _self_interruptions(text: str) -> list[str]:
    hits: list[str] = []
    for m in _DASH_RE.finditer(text):
        pre = text[max(0, m.start() - 40):m.start()]
        post = re.match(r"\s*([A-Za-z]{2,})", text[m.end():])
        if not post:
            continue
        word = post.group(1).lower()
        if word in _SELF_INTERRUPT_STOPWORDS:
            continue
        if re.search(rf"\b{re.escape(word)}\b", pre, re.IGNORECASE):
            hits.append(_clip(text[max(0, m.start() - 40):m.end() + 40], 90))
    return hits


def check_em_dash(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag dash-dependent rhythm that the TTS sanitizer will flatten.

    The sanitizer converts em-dashes to commas before synthesis, so any
    rhythm that NEEDS the dash silently degrades. >=3 dashes in one
    segment warns; dash-bracketed self-interruptions ("if X — if X' — Y")
    warn individually because they become comma-stumbles; script totals
    are reported as info for trend-watching.
    """
    out: list[dict[str, str]] = []
    total = 0
    seg_count = 0
    for sv in segs:
        n = len(_DASH_RE.findall(sv.text))
        total += n
        seg_count += 1 if n else 0
        if n >= DASH_OVERLOAD_PER_SEGMENT:
            out.append(_finding(
                "em_dash_overload", "warn", sv.id, sv.text,
                f"{n} dashes in one segment — the TTS sanitizer turns each "
                "into a comma; restructure into separate sentences.",
            ))
        for ev in _self_interruptions(sv.text):
            out.append(_finding(
                "em_dash_overload", "warn", sv.id, ev,
                "Dash-bracketed self-interruption — post-sanitizer this "
                "reads as a comma-stumble; restate as one clean clause.",
            ))
    out.append(_finding(
        "em_dash_overload", "info", "script",
        f"{total} dashes across {seg_count} segments",
        "Script-level dash totals; every dash becomes a comma at "
        "synthesis time.",
    ))
    return out


# ---------------------------------------------------------------------------
# Rule: contraction_desert
# ---------------------------------------------------------------------------

#: True contractions only: n't / 're / 've / 'll / 'm / 'd always, and 's
#: only on pronoun-ish hosts ("it's") — possessives ("machine's") do NOT
#: make a voice conversational and must not satisfy the rule.
_CONTRACTION_RE = re.compile(
    r"\b(?:\w+n['’]t"
    r"|\w+['’](?:re|ve|ll|m|d)"
    r"|(?:it|that|this|there|here|he|she|what|who|where|when|why|how|let|"
    r"everything|nothing|something|everyone|someone|everybody|somebody|"
    r"nobody|one)['’]s"
    r")\b",
    re.IGNORECASE,
)
#: Any apostrophe token (prototype parity — includes possessives).
_APOSTROPHE_TOKEN_RE = re.compile(
    r"\b\w+['’](?:s|t|re|ve|ll|d|m)\b", re.IGNORECASE
)


def check_contraction_desert(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag runs of 8+ consecutive segments with zero true contractions.

    Doc: "the script must aggressively utilize contractions... Writing out
    'do not' will consistently cause the TTS engine to deliver a stiff,
    robotic staccato." A long contraction-free run is a robotic stretch.
    """
    flags = [not _CONTRACTION_RE.search(sv.text) for sv in segs]
    out: list[dict[str, str]] = []
    for a, b in _runs(flags, CONTRACTION_DESERT_MIN_RUN):
        out.append(_finding(
            "contraction_desert", "warn",
            f"{segs[a].id}-{segs[b].id}",
            f"{b - a + 1} consecutive segments without a contraction",
            "Contraction desert — the TTS voice goes stiff here; convert "
            "a few 'did not'/'it is' to 'didn't'/'it's'.",
        ))
    return out


# ---------------------------------------------------------------------------
# Rule: banned_vocab
# ---------------------------------------------------------------------------

def _verb_forms(stem: str) -> set[str]:
    forms = {stem, stem + "s", stem + "es", stem + "ed", stem + "d",
             stem + "ing"}
    if stem.endswith("e"):
        forms.add(stem[:-1] + "ing")          # delve -> delving
    if stem.endswith("l"):
        forms.update({stem + "led", stem + "ling"})  # propel -> propelled
    return forms


def _vocab_regex(words: Iterable[str], inflect: bool = False) -> re.Pattern[str]:
    alts: set[str] = set()
    for w in words:
        alts.update(_verb_forms(w) if inflect else {w})
    pattern = "|".join(sorted((re.escape(a) for a in alts), key=len,
                              reverse=True))
    return re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)


#: The doc's five prohibited lists + pedagogical tells. All error: these
#: words "simulate depth without providing actual meaning" and instantly
#: signal machine generation.
_BANNED_VOCAB: list[tuple[str, re.Pattern[str], str]] = [
    ("magic adverb",
     _vocab_regex(["quietly", "deeply", "fundamentally", "remarkably",
                   "arguably"]),
     "Delete it — let the verb dictate the action."),
    ("verb inflation",
     _vocab_regex(["delve", "utilize", "leverage", "harness", "facilitate",
                   "propel", "unlock"], inflect=True),
     "Use a plain verb: explore, use, apply, help, drive, reveal."),
    ("pompous noun",
     _vocab_regex(["tapestry", "landscape", "realm", "symphony", "beacon",
                   "epitome"], inflect=True),
     "Replace with a concrete noun: mixture, industry, field, example."),
    ("transition filler",
     _vocab_regex(["moreover", "furthermore", "additionally", "indeed"]),
     "Cut it — begin with the direct statement of fact."),
    ("transition filler",
     re.compile(r"\bit(?:\s+is|['’]s)\s+important\s+to\s+note\b",
                re.IGNORECASE),
     "Cut it — begin with the direct statement of fact."),
    ("dramatic cliche",
     _vocab_regex(["cutting-edge", "game-changer", "unprecedented"]),
     "Name the exact technological mechanism instead."),
    ("pedagogical tell",
     re.compile(r"\bthink\s+of\s+it\s+as\b", re.IGNORECASE),
     "Drop the patronizing metaphor — state it directly."),
    ("pedagogical tell",
     re.compile(r"\bserv(?:e|es|ed|ing)\s+as\b", re.IGNORECASE),
     "Use a direct active verb instead of 'serves as'."),
]


def check_banned_vocab(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag the doc's prohibited algorithmic vocabulary (error).

    Doc: LLMs have "favorite words... selected disproportionately more
    often than human writers" — magic adverbs, verb inflations, pompous
    nouns, transition filler, dramatic cliches — plus pedagogical tells
    ("think of it as", "serves as") that dilute investigative authority.
    """
    out: list[dict[str, str]] = []
    for sv in segs:
        for category, rx, advice in _BANNED_VOCAB:
            for m in rx.finditer(sv.text):
                out.append(_finding(
                    "banned_vocab", "error", sv.id, m.group(0),
                    f"Banned {category} '{m.group(0)}' — {advice}",
                ))
    return out


# ---------------------------------------------------------------------------
# Rule: ambiguous_number_range
# ---------------------------------------------------------------------------

_NUM_WORD = (
    "(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    "twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    "thousand|million|billion)"
)
_RANGE_RE = re.compile(
    rf"\bbetween\s+((?:{_NUM_WORD}[\s-]+)+?)and\s+((?:{_NUM_WORD}\b[\s-]*)+)",
    re.IGNORECASE,
)
_BIG_SCALE = {"thousand", "million", "billion"}


def check_ambiguous_number_range(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag spelled ranges whose first bound ends sub-thousand.

    "between sixteen thousand five hundred and twenty-five thousand" is
    aurally ambiguous — the ear can't tell whether "and" joins the number
    (16,500... and what?) or separates the bounds. Doc: numbers are
    "highly difficult to absorb aurally"; the first bound must end on a
    clean scale word, or the range needs "to" instead of "and".
    """
    out: list[dict[str, str]] = []
    for sv in segs:
        for m in _RANGE_RE.finditer(sv.text):
            first = re.split(r"[\s-]+", m.group(1).strip().lower())
            if first and first[-1] not in _BIG_SCALE and any(
                    w in _BIG_SCALE for w in first):
                out.append(_finding(
                    "ambiguous_number_range", "warn", sv.id, m.group(0),
                    f"First bound ends on '{first[-1]}' — the listener "
                    "can't tell whether 'and' continues the number or "
                    "starts the second bound; use 'to' or round the bound.",
                ))
    return out


# ---------------------------------------------------------------------------
# Rule: alphanumeric_token
# ---------------------------------------------------------------------------

_TOKEN_STRIP = ".,;:!?\"'()[]{}—–‘’“”"
_MIXED_ALNUM_RE = re.compile(r"[A-Za-z]\d|\d[A-Za-z]")
_BARE_DIGITS_RE = re.compile(r"\d+(?:[.,]\d+)*")


def check_alphanumeric_tokens(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag tokens TTS may mangle.

    Doc: "alphanumeric symbols must be normalized". A letter glued to a
    digit ("Class3") has no reliable pronunciation — warn. Bare digits
    ("54") in an otherwise spelled-out script are info: the sanitizer or
    pronunciation dictionary may handle them, but spelled-out is safer.
    Hyphen-joined model names ("Therac-25") read fine and are not flagged.
    """
    out: list[dict[str, str]] = []
    for sv in segs:
        for raw in sv.text.split():
            tok = raw.strip(_TOKEN_STRIP)
            if not tok:
                continue
            if _MIXED_ALNUM_RE.search(tok):
                out.append(_finding(
                    "alphanumeric_token", "warn", sv.id, tok,
                    f"Letter+digit token '{tok}' — TTS pronunciation is "
                    "unpredictable; spell it out ('Class Three') or add a "
                    "pronunciation-dictionary entry.",
                ))
            elif _BARE_DIGITS_RE.fullmatch(tok):
                out.append(_finding(
                    "alphanumeric_token", "info", sv.id, tok,
                    f"Bare digits '{tok}' in an otherwise spelled-out "
                    "script — consider spelling out for consistent "
                    "delivery.",
                ))
    return out


# ---------------------------------------------------------------------------
# Rule: title_after_name
# ---------------------------------------------------------------------------

_TITLE_AFTER_NAME_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+)+),\s+"
    r"(?:the|a|an)\s+([a-z][\w-]*(?:\s+[\w-]+){0,4}?)[,.;—]"
)


def check_title_after_name(segs: list[_SegView]) -> list[dict[str, str]]:
    """Flag "<Name>, the <title>" appositives (info).

    Doc: "the title must precede the name" — 'John Smith, the lead
    software architect' forces the listener to hold the name in working
    memory without context; 'Lead software architect John Smith' anchors
    it immediately.
    """
    out: list[dict[str, str]] = []
    for sv in segs:
        for m in _TITLE_AFTER_NAME_RE.finditer(sv.text):
            out.append(_finding(
                "title_after_name", "info", sv.id, m.group(0),
                f"Title follows the name — prefer title-first: "
                f"'{m.group(2)} {m.group(1)}'.",
            ))
    return out


# ---------------------------------------------------------------------------
# Stats (superset of the _narration_stats.py prototype)
# ---------------------------------------------------------------------------

def _compute_stats(segs: list[_SegView],
                   findings: list[dict[str, str]]) -> dict[str, Any]:
    all_text = " ".join(sv.text for sv in segs)
    long_sents = [
        {"segment_id": sv.id, "words": wc, "text": _clip(s, 90)}
        for sv in segs
        for s, wc in zip(sv.sentences, sv.sentence_words)
        if wc > LONG_SENTENCE_WARN_WORDS
    ]
    dash_segments = [sv.id for sv in segs if _DASH_RE.search(sv.text)]
    staccato = [
        sv.id for sv in segs
        if sum(1 for wc in sv.sentence_words
               if 0 < wc <= METRONOME_FRAGMENT_MAX_WORDS) >= 3
    ]
    by_rule: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {
        "segments": len(segs),
        "sentences": sum(len(sv.sentences) for sv in segs),
        "words": sum(_word_count(sv.text) for sv in segs),
        "chars": len(all_text),
        "long_sentence_count": len(long_sents),
        "long_sentences": long_sents,
        "longest_sentence_words": max((s["words"] for s in long_sents),
                                      default=0),
        "dash_total": len(_DASH_RE.findall(all_text)),
        "dash_segment_count": len(dash_segments),
        "dash_segments": dash_segments,
        "contraction_count": len(_CONTRACTION_RE.findall(all_text)),
        "apostrophe_token_count": len(_APOSTROPHE_TOKEN_RE.findall(all_text)),
        "passive_count": sum(1 for f in findings
                             if f["rule"] == "passive_voice"),
        "fragment_ending_segments": [sv.id for sv in segs
                                     if _ends_in_fragment(sv)],
        "staccato_segments": staccato,
        "negative_parallelism_count": by_rule.get("negative_parallelism", 0),
        "rhetorical_answer_count": by_rule.get("rhetorical_answer", 0),
        "banned_vocab_count": by_rule.get("banned_vocab", 0),
        "counts_by_rule": by_rule,
        "counts_by_severity": by_sev,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lint_script(script: Any, *,
                passive_allowlist: Iterable[str] = ()) -> dict[str, Any]:
    """Lint a scored script object (anything with .segments of .id/.narration).

    Returns ``{"findings": [...], "stats": {...}}`` where each finding is
    ``{"rule", "severity", "segment_id", "evidence", "detail"}``.
    """
    segs = [_SegView.from_segment(s) for s in script.segments]
    findings: list[dict[str, str]] = []
    findings += check_long_sentences(segs)
    findings += check_passive_voice(segs, passive_allowlist)
    findings += check_negative_parallelism(segs)
    findings += check_rhetorical_answer(segs)
    findings += check_tricolon(segs)
    findings += check_fragment_metronome(segs)
    findings += check_em_dash(segs)
    findings += check_contraction_desert(segs)
    findings += check_banned_vocab(segs)
    findings += check_ambiguous_number_range(segs)
    findings += check_alphanumeric_tokens(segs)
    findings += check_title_after_name(segs)
    stats = _compute_stats(segs, findings)
    _log.info("lint: %d segments, %d findings (%s)", len(segs),
              len(findings), stats["counts_by_severity"])
    return {"findings": findings, "stats": stats}


def lint_file(path: str | Path, *,
              passive_allowlist: Iterable[str] = ()) -> dict[str, Any]:
    """Load a scored_script.yaml and lint it."""
    from lib.scored_script import load_scored_script
    return lint_script(load_scored_script(path),
                       passive_allowlist=passive_allowlist)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(result: dict[str, Any]) -> None:
    stats = result["stats"]
    print(f"segments: {stats['segments']} | sentences: {stats['sentences']} "
          f"| words: {stats['words']} | dashes: {stats['dash_total']} "
          f"| contractions: {stats['contraction_count']}")
    print(f"findings: {stats['counts_by_severity'] or 'none'}")
    order = {"error": 0, "warn": 1, "info": 2}
    for f in sorted(result["findings"],
                    key=lambda f: (order[f["severity"]], f["segment_id"])):
        print(f"  [{f['severity'].upper():5s}] {f['rule']:24s} "
              f"{f['segment_id']:18s} {f['evidence']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lib.script_lint",
        description="Deterministic narration lint for scored scripts.",
    )
    parser.add_argument("script", help="path to scored_script.yaml")
    parser.add_argument("--report", metavar="OUT_JSON",
                        help="write full JSON report to this path")
    parser.add_argument("--fail-on", choices=["error", "warn"],
                        default="error",
                        help="exit 1 when findings at/above this severity "
                             "exist (default: error)")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING)

    result = lint_file(args.script)
    _print_report(result)

    if args.report:
        Path(args.report).write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"report written: {args.report}")

    threshold = _SEVERITY_RANK[args.fail_on]
    failing = [f for f in result["findings"]
               if _SEVERITY_RANK[f["severity"]] >= threshold]
    if failing:
        print(f"FAIL: {len(failing)} finding(s) at/above '{args.fail_on}'")
        return 1
    print(f"PASS: no findings at/above '{args.fail_on}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
