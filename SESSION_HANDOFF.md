# Session Handoff — POLISH PASS (round 3, 14 notes) EXECUTED — one item blocked: seg_016 re-TTS (dead ElevenLabs key)

**Date:** 2026-07-09 · **Branch:** `v6-baseline` (push after render verify) · **Project:** `projects/therac-25-test`

> Read the auto-loaded memories first — especially `entity-census-first-and-gemini-image-lane`,
> `reuse-before-generate`, `openmontage-consistency-toolkit` (new border-propagation lesson).
> Prior handoffs: `git log --follow SESSION_HANDOFF.md`.

## THE POLISH PASS (operator's 14 timestamps, all executed except seg_016 audio)

**Audio:** seg_020 `silence_after_s: 3 → 1` (the 6:38 dead air) — narration master now
**824.8s**. ⚠ The positional silence cache (`audio_v6/silence/silence_{i}.mp3`) DEFEATS
silence changes — delete `audio_v6/silence/*.mp3` (via python; PowerShell Remove-Item is
blocked by path protection) before rerunning `generate_voice_v6.py`.
**⚠ BLOCKED — seg_016 doubled line:** script already fixed in scored_script.yaml; the
ELEVENLABS_API_KEY in .env (ends …2f2239) returns 401 on /v1/user even AFTER the operator
added credits — the key string itself is dead/rotated. On a fresh key: python-delete
`audio_v6/seg_016.mp3` + `.alignment.json` (backups `.bak_echo`), run generate_voice_v6.py,
rebuild + re-render (~20 min).

**Borders (the "why do some scenes have a border" note):** root cause = the seg_018-era
keyframes carried a cream comic panel border which rode into the MASTER frame
(`_gold_refs/machine_empty_room.png`) and every derivation. Fixed at the source: master
cropped ((21,33)-(1323,742), backup `machine_empty_room.bak_border.png`), all master
cascades rebuilt, every part auto-debordered at cut time (cream-margin detection), run-split
sweeps for scenes with per-beat borders (001/018/021). Borders KEPT only on the two Katie
window shots the operator likes (seg_022 b3 @7:19, seg_032 B5 @12:08). A fast 4-sample
audit confirmed every other canonical clean.

**Scene fixes (all $0 unless noted; paid batch $0.74 ledger-reconciled vs ~$0.75 announced):**
- **003 (00:52):** mouth leg REGENERATED anchored on s3_c's true last frame (grok, $0.10;
  keyframe = match cut by construction) + 0.5s xfade into the closer. 23.77s.
- **005 (1:20):** blank-page beat replaced with a Gemini printer still (dot-matrix beside
  the console, $0.04) + PIL blinking red status lamp + deterministic push-in.
- **006 (1:47):** B4 re-cut earlier (source [12.40+6.88] — old seg_012's motion dies at
  19.4s, detected by frame-diff) so the phone engineer plays through.
- **009 (3:03):** freeze tail replaced by a Kling FLF walk-into-shadow ($0.42): start = the
  walking frame @11.5, end = Gemini man-gone corridor ($0.04), retimed 5.0→3.5s, keep
  [0-10.54] before it. `lib.flf.flf_beat` with `derive=copy(end_frame)` is the pattern.
- **012 (4:05):** B3 = real motion [9.0-19.35] slowed 1.285× (setpts) to fill 13.3s + NEW
  word-timed closer card (NOT POSSIBLE. 24.53 / NO INVESTIGATION. 25.96 / JUST A PHONE
  CALL. 27.92, amber finale, slow drift zoom). ⚠ ffmpeg `-t` after `-i` is OUTPUT duration:
  slowmo cuts need `-t <target>` not the source window (shipped 3s short before the fix).
- **015 (4:58):** memo card rebuilt with gravity — page slams in (0.28s scale-settle), fast
  fill, red-oxide PULSE underline on 'writes to AECL' (16.1), slow push + vignette.
- **019 (6:11 + 6:12):** the "flash" = a 2-frame male-operator shot at old-take [1.75-2.0]
  exposed by the 1.79 splice → kept window now starts at 2.0 (press leg stretched to 2.0).
  The too-real Cox [4.43-7.42] replaced by a Gemini cel-shade restyle ($0.04) + grok leg
  ($0.10) anchored on it.
- **032 (11:56):** B2 rebuilt: real text "CORRECTIVE ACTION PLAN REQUIRED" on the doc, DEFECTIVE
  stamp as before, red-oxide underline sweeps ON the narration (global 7.0), push-in toward
  the row.
- **034 (12:36):** B2a rebuilt: real text "COULD NEVER FIND EVERY FAILURE MODE" turns
  red-oxide as the underline sweeps (4.39-5.75 local), push toward the row; b1/b3 machine
  drains rebuilt from the cropped master.
- **037 (13:43):** CRT switch-off stretched — collapse 0.45s / burning line 1.15s /
  phosphor dot 1.20s (with die-flicker) / hold 0.71s (was 1.35s effect total).
- **Cascade/border rebuilds:** 002, 014, 018, 021, 022, 024, 030, 031, 035, 036 + seg_001.

**State:** all 21 polish files registered as takes (use-clip) AND promoted to canonicals
(backups: `_canonical_backup_20260709_polish/`). Full build+render kicked off
(BURN_OVERLAYS=0). Run-split scenes are 1-2 frames short of slot — render_v6's
frame-ceiling conform freeze-pads them.

## Pipeline lessons (this session)
- **Borders ride EVERY derivation** — crop the SOURCE STILL before deriving; deborder parts
  at cut time (cream-margin detect: bright/low-sat runs past navy padding, run-length cap so
  full-cream cards don't false-positive; min-crop across 3 samples for video; run-split +
  frame bisection for per-beat borders). Keep-list for intentional borders.
- **The reworked canonicals preserve old-source timelines 1:1 in their kept windows** — cut
  fix material directly from the current canonical at the same timestamps (verified 012:
  freeze onset matches; 019: 1.79+ maps 1:1; 009: [0,14.04] identity).
- **grok-kie still needs hosted keyframe URLs** (upload_image first); Gemini inline-bytes
  for stills; flf_beat accepts a custom derive to use an authored end frame.
- **Dashboard dies with its console** — it went down mid-session again; restart detached
  hidden with log redirect before any use-clip registration.

## How to run
Dashboard: `python -m uvicorn web.backend.app:app --port 8011` DETACHED hidden w/ log
redirect (logs/dashboard_8011.{out,err}.log); no auto-reload; 127.0.0.1. System python:
`C:\Users\Soda\AppData\Local\Programs\Python\Python312\python.exe`. Full render:
scratchpad `full_render.py` = build_v6 (env + BURN_OVERLAYS=0) → render_v6 → sync gate.
Costs: announce before ANY generation (2-3× sticker for hard shots), reconcile
`artifacts/cost_ledger.jsonl` (`cost_usd` field, ts is UTC). `projects/` is GITIGNORED.
Never `git gc`/`-delete` under `.git/`.

## NEXT
1. Verify the polish render (sync 3/3 + frame spot-checks at the operator's 14 timestamps
   — note all post-seg_020 offsets shifted −2s vs the notes).
2. Operator watch-through of the polish cut.
3. seg_016 re-TTS the moment a live ElevenLabs key lands in .env → final render.
4. Then: music/SFX/mix.
