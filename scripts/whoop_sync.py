#!/usr/bin/env python3
"""
WHOOP -> repo exporter.

Refreshes the OAuth token, pulls cycles / recovery / sleep / workouts / body
measurements for the last N days and upserts them into:

  data/whoop/daily.json     one record per day (recovery, HRV, RHR, strain, kcal, sleep)
  data/whoop/workouts.json  one record per workout, keyed by WHOOP id
  data/whoop/body.json      latest body measurement (height, weight, max HR)

The new refresh token (WHOOP rotates it on every refresh) is written to
.whoop_rt immediately, so the workflow can save it as a secret even if a
later step fails.

Env:
  WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET   required
  WHOOP_REFRESH_TOKEN                    optional fallback if .whoop_rt is missing
Args:
  --days N    how many days back to fetch (default 7)
  --code C    first run only: exchange the authorization code instead of refreshing
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.prod.whoop.com/developer/v2"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
RT_FILE = Path(".whoop_rt")
OUT = Path("data/whoop")
KJ_PER_KCAL = 4.184
STATUS = OUT / "_last_run.json"


class ApiError(Exception):
    pass


def http(method, url, headers=None, data=None, retries=4):
    body = None
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "wellness-sync/1.0")
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt * 5)
                continue
            msg = e.read().decode(errors="replace")[:500]
            raise ApiError(f"HTTP {e.code} on {method} {url.split('?')[0].replace(API, '')}: {msg}")


def exchange_code(code, redirect_uri):
    """One-time: authorization code -> first refresh token."""
    tok = http("POST", TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code.strip(),
        "redirect_uri": redirect_uri,
        "client_id": os.environ["WHOOP_CLIENT_ID"].strip(),
        "client_secret": os.environ["WHOOP_CLIENT_SECRET"].strip(),
    })
    if "refresh_token" not in tok:
        sys.exit("No refresh token returned - was the 'offline' scope in the authorize link?")
    RT_FILE.write_text(tok["refresh_token"])
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{tok['refresh_token']}")
        print(f"::add-mask::{tok['access_token']}")
    return tok["access_token"]


def refresh_access_token():
    cid = os.environ["WHOOP_CLIENT_ID"].strip()
    secret = os.environ["WHOOP_CLIENT_SECRET"].strip()
    rt = RT_FILE.read_text().strip() if RT_FILE.exists() else os.environ.get("WHOOP_REFRESH_TOKEN", "").strip()
    if not rt:
        sys.exit("No refresh token. Run the 'WHOOP authorize' workflow first.")
    tok = http("POST", TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": cid,
        "client_secret": secret,
        "scope": "offline",
    })
    # Persist the rotated refresh token right away.
    RT_FILE.write_text(tok["refresh_token"])
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{tok['refresh_token']}")
        print(f"::add-mask::{tok['access_token']}")
    return tok["access_token"]


def fetch_collection(token, path, start):
    out, next_token = [], None
    while True:
        q = {"limit": 25, "start": start}
        if next_token:
            q["nextToken"] = next_token
        res = http("GET", f"{API}{path}?{urllib.parse.urlencode(q)}",
                   headers={"Authorization": f"Bearer {token}"})
        out.extend(res.get("records", []))
        next_token = res.get("next_token")
        if not next_token:
            return out


def local_dt(iso, offset):
    """UTC ISO timestamp + '+01:00'-style offset -> naive local datetime."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    sign = -1 if offset.startswith("-") else 1
    h, m = offset.lstrip("+-").split(":")
    return (dt + sign * timedelta(hours=int(h), minutes=int(m))).replace(tzinfo=None)


def day_of_cycle(cycle):
    # A WHOOP cycle starts at sleep onset. Shifting by 12h maps both a 23:30
    # and a 01:00 sleep onset onto the calendar day you woke up / lived.
    return (local_dt(cycle["start"], cycle.get("timezone_offset", "+00:00")) + timedelta(hours=12)).date().isoformat()


def r1(x, n=1):
    return None if x is None else round(x, n)


def load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def save(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def build(cycles, recoveries, sleeps, workouts):
    rec_by_cycle = {r.get("cycle_id"): r for r in recoveries}
    sleep_by_cycle = {}
    for s in sleeps:
        if s.get("nap"):
            continue
        sleep_by_cycle.setdefault(s.get("cycle_id"), s)

    days = {}
    for c in cycles:
        d = {"date": day_of_cycle(c), "cycle_id": c["id"], "cycle_complete": c.get("end") is not None}
        cs = c.get("score") or {}
        if c.get("score_state") == "SCORED":
            d.update(strain=r1(cs.get("strain")),
                     kcal_total=r1((cs.get("kilojoule") or 0) / KJ_PER_KCAL, 0),
                     avg_hr=cs.get("average_heart_rate"), max_hr=cs.get("max_heart_rate"))
        if c.get("step_count") is not None:
            d["steps"] = c["step_count"]

        r = rec_by_cycle.get(c["id"])
        if r and r.get("score_state") == "SCORED":
            rs = r.get("score") or {}
            d.update(recovery=rs.get("recovery_score"), rhr=rs.get("resting_heart_rate"),
                     hrv=r1(rs.get("hrv_rmssd_milli")), spo2=r1(rs.get("spo2_percentage")),
                     skin_temp=r1(rs.get("skin_temp_celsius"), 2))

        s = sleep_by_cycle.get(c["id"])
        if s and s.get("score_state") == "SCORED":
            ss = s.get("score") or {}
            st = ss.get("stage_summary") or {}
            ms = lambda k: st.get(k) or 0
            asleep = ms("total_in_bed_time_milli") - ms("total_awake_time_milli") - ms("total_no_data_time_milli")
            need = ss.get("sleep_needed") or {}
            need_ms = sum(v for v in need.values() if v)
            d.update(sleep_hours=r1(asleep / 3.6e6, 2),
                     sleep_deep_h=r1(ms("total_slow_wave_sleep_time_milli") / 3.6e6, 2),
                     sleep_rem_h=r1(ms("total_rem_sleep_time_milli") / 3.6e6, 2),
                     sleep_performance=ss.get("sleep_performance_percentage"),
                     sleep_efficiency=r1(ss.get("sleep_efficiency_percentage")),
                     sleep_need_h=r1(need_ms / 3.6e6, 2) if need_ms else None,
                     resp_rate=r1(ss.get("respiratory_rate")),
                     bedtime=local_dt(s["start"], s.get("timezone_offset") or "+00:00").strftime("%H:%M"),
                     wake=local_dt(s["end"], s.get("timezone_offset") or "+00:00").strftime("%H:%M") if s.get("end") else None)
        days[d["date"]] = {k: v for k, v in d.items() if v is not None}

    wos = {}
    for w in workouts:
        if w.get("score_state") != "SCORED" or not w.get("end"):
            continue
        ws = w.get("score") or {}
        tz = w.get("timezone_offset") or "+00:00"
        start = local_dt(w["start"], tz)
        end = local_dt(w["end"], tz)
        zones = ws.get("zone_durations") or {}
        wos[w["id"]] = {k: v for k, v in {
            "id": w["id"], "date": start.date().isoformat(), "start": start.strftime("%H:%M"),
            "minutes": round((end - start).total_seconds() / 60),
            "sport": w.get("sport_name"), "strain": r1(ws.get("strain")),
            "kcal": r1((ws.get("kilojoule") or 0) / KJ_PER_KCAL, 0),
            "avg_hr": ws.get("average_heart_rate"), "max_hr": ws.get("max_heart_rate"),
            "distance_m": r1(ws.get("distance_meter"), 0),
            "zones_min": [round((zones.get(f"zone_{z}_milli") or 0) / 60000) for z in
                          ("zero", "one", "two", "three", "four", "five")] if zones else None,
        }.items() if v is not None}
    return days, wos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--code", help="one-time authorization code from the redirect URL")
    ap.add_argument("--redirect-uri", default="https://localhost/whoop-callback")
    args = ap.parse_args()

    errors = []

    def status(ok, note=""):
        save(STATUS, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "ok": ok, "days": args.days, "errors": errors, "note": note})

    try:
        token = exchange_code(args.code, args.redirect_uri) if args.code else refresh_access_token()
    except ApiError as e:
        errors.append(f"token: {e}")
        status(False, "token refresh failed - re-run 'WHOOP authorize'")
        sys.exit(str(e))

    start = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def get(name, fn):
        try:
            return fn()
        except ApiError as e:
            errors.append(f"{name}: {e}")
            print(f"::warning::{name}: {e}")
            return None

    cycles = get("cycles", lambda: fetch_collection(token, "/cycle", start)) or []
    recoveries = get("recovery", lambda: fetch_collection(token, "/recovery", start)) or []
    sleeps = get("sleep", lambda: fetch_collection(token, "/activity/sleep", start)) or []
    workouts = get("workouts", lambda: fetch_collection(token, "/activity/workout", start)) or []
    body = get("body", lambda: http("GET", f"{API}/user/measurement/body",
                                    headers={"Authorization": f"Bearer {token}"}))

    days, wos = build(cycles, recoveries, sleeps, workouts)

    daily = load(OUT / "daily.json", {})
    daily.update(days)
    save(OUT / "daily.json", daily)

    allw = load(OUT / "workouts.json", {})
    allw.update(wos)
    save(OUT / "workouts.json", allw)

    if body:
        body["updated"] = datetime.now(timezone.utc).date().isoformat()
        save(OUT / "body.json", body)

    status(len(errors) < 5, f"{len(days)} days, {len(wos)} workouts")
    if len(errors) == 5:
        sys.exit("All WHOOP endpoints failed: " + "; ".join(errors))
    print(f"OK: {len(days)} days, {len(wos)} workouts (window {args.days}d). "
          f"Total stored: {len(daily)} days, {len(allw)} workouts.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        tb = traceback.format_exc()
        save(STATUS, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "ok": False, "crash": tb[-3000:]})
        print(tb)
        sys.exit(1)
