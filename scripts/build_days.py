#!/usr/bin/env python3
"""Merge repo data (WHOOP + scale) into per-day documents for the dashboard.

Output: one JSON per date in OUT_DIR, shaped {date, w:{...}, wo:[...], s:{...}, synced_at}.
Food, strength logs and manual scale readings are written by Claude separately
and are never touched here (the sync uses a merge-update).
Usage: build_days.py OUT_DIR [--since YYYY-MM-DD]
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "data"
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "0000"
load = lambda p: json.loads(p.read_text()) if p.exists() else {}
daily = load(root / "whoop/daily.json")
workouts = load(root / "whoop/workouts.json")
scale = load(root / "health/scale.json")
now = datetime.now(timezone.utc).isoformat(timespec="seconds")

days = {}
for d, v in daily.items():
    days.setdefault(d, {})["w"] = {k: x for k, x in v.items() if k not in ("date", "cycle_id")}
for w in workouts.values():
    days.setdefault(w["date"], {}).setdefault("wo", []).append(
        {k: w.get(k) for k in ("sport", "start", "minutes", "strain", "kcal", "avg_hr") if w.get(k) is not None})
for d in load(root / "health/cycle.json").get("period_days", []):
    days.setdefault(d, {})["c"] = {"period": True}
for d, v in scale.items():
    days.setdefault(d, {})["s"] = {k: x for k, x in v.items() if k not in ("date", "history", "source")}

n = 0
for d, doc in sorted(days.items()):
    if d < since:
        continue
    doc["date"] = d
    doc["synced_at"] = now
    if "wo" in doc:
        doc["wo"].sort(key=lambda x: x.get("start", ""))
    (out / f"{d}.json").write_text(json.dumps(doc, ensure_ascii=False))
    n += 1
print(f"{n} day documents -> {out}")
