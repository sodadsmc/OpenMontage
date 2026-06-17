"""Persistent, append-only API cost ledger — a running tally across ALL runs.

Every paid generation call appends one JSON line (provider, operation, cost_usd, real
credits when known, + context). ``report()`` aggregates by provider/operation so you can
see the running total and the cheap-vs-pricier split (e.g. grok-kie i2v vs kling-kie FLF).
Logging is FAIL-SAFE — a ledger error never breaks a generation.

Wired into the paid chokepoints: tools/video/grok_kie_video, tools/video/kling_kie_video,
and lib/visual_router._nano_image. Point it elsewhere with COST_LEDGER=<path>.

CLI:  python -m lib.cost_ledger            # print the running tally
      python -m lib.cost_ledger --since 2026-06-16   # only entries on/after a date
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LEDGER = "projects/therac-25-test/artifacts/cost_ledger.jsonl"


def _path(ledger: str | Path | None = None) -> Path:
    return Path(ledger or os.environ.get("COST_LEDGER") or DEFAULT_LEDGER)


def log(provider: str, operation: str, cost_usd: float, *,
        credits: float | None = None, ledger: str | Path | None = None, **ctx) -> None:
    """Append one paid call to the ledger. Never raises."""
    try:
        rec = {"ts": datetime.now(timezone.utc).isoformat(),
               "provider": provider, "operation": operation,
               "cost_usd": round(float(cost_usd or 0.0), 4)}
        if credits is not None:
            rec["credits"] = round(float(credits), 1)
        rec.update({k: v for k, v in ctx.items() if v not in (None, "")})
        p = _path(ledger)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — telemetry must never break generation
        pass


def read(ledger: str | Path | None = None, since: str | None = None) -> list[dict]:
    p = _path(ledger)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if since and r.get("ts", "") < since:
            continue
        out.append(r)
    return out


def report(ledger: str | Path | None = None, since: str | None = None) -> dict:
    recs = read(ledger, since)
    by_provider: dict[str, dict] = {}
    total = 0.0
    for r in recs:
        prov = r.get("provider", "?")
        c = r.get("cost_usd", 0.0)
        b = by_provider.setdefault(prov, {"calls": 0, "cost_usd": 0.0, "credits": 0.0})
        b["calls"] += 1
        b["cost_usd"] = round(b["cost_usd"] + c, 4)
        b["credits"] += r.get("credits", 0.0) or 0.0
        total += c
    return {"total_usd": round(total, 4), "calls": len(recs), "by_provider": by_provider}


def print_summary(ledger: str | Path | None = None, since: str | None = None) -> None:
    rep = report(ledger, since)
    scope = f" since {since}" if since else ""
    print(f"API COST LEDGER{scope}  —  {rep['calls']} calls,  ${rep['total_usd']:.2f} total")
    for prov, b in sorted(rep["by_provider"].items(), key=lambda x: -x[1]["cost_usd"]):
        cr = f"  ({b['credits']:.0f} Kie cr)" if b["credits"] else ""
        print(f"  {prov:14s} {b['calls']:4d} calls   ${b['cost_usd']:7.2f}{cr}")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m lib.cost_ledger")
    ap.add_argument("--ledger", default=None, help="ledger path (default: $COST_LEDGER or the project file)")
    ap.add_argument("--since", default=None, help="only entries with ts >= this ISO date/time")
    a = ap.parse_args(argv)
    print_summary(a.ledger, a.since)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
