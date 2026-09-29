#!/usr/bin/env python3
"""
Apple Health (iOS Shortcut) -> repo.

Triggered by a repository_dispatch event (event_type "health") sent from the
iOS Shortcut. The payload is a flat dictionary, e.g.

  {"date": "2026-09-29", "time": "08:12",
   "weight": "72,4", "body_fat": "18.5", "lean_mass": "57.9", "bmi": "22.1"}

Any extra numeric keys are stored as-is. Values may use a decimal comma or
carry units ("72,4 kg"); body fat given as a fraction (0.185) becomes 18.5.
One record per date: the earliest reading of the day is primary (morning,
fasted); other readings that day are kept in "history" so nothing is lost.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

OUT = Path("data/health/scale.json")
TZ = ZoneInfo("Europe/Lisbon")
META = {"date", "time", "source"}
CALIB = OUT.parent / "calibration.json"


def derive(rec):
    """Xiaomi computes muscle mass as lean mass minus bone mass. Recreate it
    (and estimate skeletal muscle) from lean mass using calibration.json."""
    if not CALIB.exists() or "lean_mass" not in rec or "weight" not in rec:
        return
    c = json.loads(CALIB.read_text())
    muscle = rec["lean_mass"] - rec["weight"] * c["bone_pct_of_weight"] / 100
    rec["muscle_mass_est"] = round(muscle, 1)
    rec["muscle_pct_est"] = round(muscle / rec["weight"] * 100, 1)
    rec["skeletal_muscle_est"] = round(rec["lean_mass"] * c["skeletal_muscle_ratio_of_lean"], 1)


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v).replace(" ", " "))
    return float(m.group(0).replace(",", ".")) if m else None


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    p = event.get("client_payload") or {}
    # Keep the raw payload of the last run for troubleshooting the Shortcut.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    (OUT.parent / "_last_payload.json").write_text(json.dumps(p, ensure_ascii=False, indent=1) + "\n")
    if isinstance(p.get("metrics"), dict):  # also accept {"date":..., "metrics": {...}}
        p = {**{k: v for k, v in p.items() if k != "metrics"}, **p["metrics"]}

    # Menstrual flow from Flo -> Apple Health: "period" carries the date of the latest flow sample.
    period_raw = str(p.pop("period", "") or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", period_raw)
    if m:
        cyc = OUT.parent / "cycle.json"
        c = json.loads(cyc.read_text()) if cyc.exists() else {"period_days": []}
        if m.group(1) not in c["period_days"]:
            c["period_days"] = sorted(c["period_days"] + [m.group(1)])
            cyc.write_text(json.dumps(c, indent=1) + "\n")
        print("Period day:", m.group(1))

    now = datetime.now(TZ)
    raw_date = str(p.get("date") or "").strip()
    # ISO 8601 from Shortcuts ("2026-09-29T08:12:00+01:00") carries the time too.
    iso = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", raw_date)
    if iso and not p.get("time"):
        p["time"] = iso.group(2)
    date = raw_date[:10] if raw_date else now.date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        sys.exit(f"Bad date: {date!r} (the Shortcut should format it as yyyy-MM-dd)")

    rec = {"date": date, "time": p.get("time") or now.strftime("%H:%M"),
           "source": p.get("source") or "ios-shortcut"}
    for k, v in p.items():
        if k in META:
            continue
        x = num(v)
        if x is None:
            continue
        if k == "body_fat" and x < 1:
            x *= 100
        rec[k] = round(x, 2)

    derive(rec)
    if not any(k not in META for k in rec):
        if m:  # only a period update, no scale reading today
            return
        sys.exit(f"No numeric values in payload: {json.dumps(p, ensure_ascii=False)}")

    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    readings = {}
    old = data.get(date)
    if old:
        for r in old.pop("history", []) + [old]:
            readings[r.get("time")] = r
    readings[rec["time"]] = rec  # same sample re-sent -> replaced, not duplicated
    ordered = sorted(readings.values(), key=lambda r: r.get("time") or "99:99")
    # The earliest reading of the day (morning, fasted) is the one used for trends.
    primary = dict(ordered[0])
    if len(ordered) > 1:
        primary["history"] = ordered[1:]
    data[date] = rec = primary
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print("Stored:", json.dumps({k: v for k, v in rec.items() if k != "history"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
