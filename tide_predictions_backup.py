"""
tide_predictions_backup.py

Generates a backup of tide high/low predictions for every departure
location's NOAA tide station, computed WITHOUT calling NOAA's live
predictions/datagetter service -- the service that had a multi-day,
all-stations outage on 2026-07-17/18 (every station, every date, every
datum returned "No Predictions data was found").

Two-phase design:

  1. Constituent cache (tide_constituents_cache.json) -- each station's
     37 harmonic constituents (amplitude/phase) plus its MSL-MLLW datum
     offset, fetched from NOAA's *metadata* API
     (mdapi/prod/webapi/stations/{id}/harcon.json and .../datums.json).
     This is a different NOAA service than the live predictions API and
     stayed up throughout the outage above (verified live). Harmonic
     constituents are re-derived by NOAA only on station re-analysis,
     roughly once every several years, so this cache is only refreshed
     when an entry is missing or older than REFRESH_DAYS -- it is NOT
     re-fetched on every run.

  2. Prediction generation -- using pytides2, purely local computation
     from the cached constituents, zero network calls, producing a
     rolling PREDICT_DAYS-day window of hi/lo predictions for every
     station. Written to tide_predictions_backup.json in the same
     {t, v, type} shape the frontend already expects from the live NOAA
     call, so useMarineForecast.js can drop this in as a fallback with
     no shape translation.

Because step 2 needs no network access, this script still produces a
valid, freshly-windowed backup file even if NOAA's metadata API is ALSO
down on a given run -- it just reuses whatever's already in the
constituents cache. Only a *permanent* loss of both the live API and
this cache would leave the app with no tide data, which is no worse
than the status quo today.

Validated 2026-07-18: predictions for Boston (station 8443970, not one
of ours but a good stress-test station) matched real observed water
levels within ~5-10 minutes of timing and well under 1 ft of height --
that residual is real wind/pressure surge, which harmonic prediction
doesn't capture and NOAA's own "predictions" product doesn't either.

Subordinate stations: a handful of departure-location stations were never
independently harmonically analyzed by NOAA and have no harcon of their
own -- only a tidepredoffsets.json (a fixed time + height-ratio adjustment
applied to a nearby reference station's own harmonic predictions, NOAA's
classic paper-tide-table method). This is still pure mdapi metadata, so
still independent of the live predictions outage. The ratio/offset method
itself was verified by hand 2026-07-18 against live NOAA data for station
8458694 (ref 8452660): applying the published offsets directly to NOAA's
own live reference-station predictions reproduced NOAA's own three hi/lo
subordinate extrema for that day exactly. The deployed pipeline instead
applies those offsets to pytides2's own harmonic synthesis of the
reference station (not NOAA's live numbers), so it inherits that
synthesis's small residual too -- same few-minutes/hundredths-of-a-foot
order of magnitude as the direct-harmonic-station validation above, not
an additional source of error.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

# --- pytides2 compatibility shims -------------------------------------------
# pytides2 hasn't been updated for modern Python/numpy; both of these
# aliases were removed upstream. Patch before importing pytides2 itself.
import collections
import collections.abc
collections.Iterable = collections.abc.Iterable  # removed in Python 3.10
import numpy as np
np.float = float  # removed in numpy>=1.24

from pytides2.tide import Tide
from pytides2 import constituent as cons

CONSTITUENTS_CACHE_FILE = "tide_constituents_cache.json"
BACKUP_OUTPUT_FILE = "tide_predictions_backup.json"
REFRESH_DAYS = 180   # re-fetch a station's constituents only if the cache entry is older than this
PREDICT_DAYS = 60    # rolling prediction window written to the backup file

# All current departure locations sit in the US Eastern time zone (VA/MD/DE/
# NJ/NY/RI/CT down through NC/SC/GA/FL all observe America/New_York rules).
# If a future region adds a station outside ET, this needs to become a
# per-station mapping instead of a single constant.
STATION_TZ = ZoneInfo("America/New_York")

# Unique NOAA CO-OPS tide stations used across every departure location in
# NOAA_SOURCES (src/hooks/useMarineForecast.js). Keep this list in sync when
# adding a new location with a new tideStation id -- same "must stay in
# sync" pattern already documented for the NOAA_SOURCES forecast table.
STATION_IDS = [
    "8452660", "8455083", "8458694", "8510560", "8512354", "8515186", "8516385",
    "8531680", "8532585", "8533615", "8534720", "8536110", "8557380", "8570283",
    "8630249", "8631044", "8632200", "8637689", "8638863", "8652659", "8654467",
    "8656483", "8658120", "8658163", "8659084", "8661070", "8665530", "8667999",
    "8670870", "8679511", "8720030", "8720218", "8720587", "8721147", "8721604",
    "8722004", "8722212", "8722357", "8722588", "8722956",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RipLoc-tide-backup/1.0)"}


def load_constituents_cache():
    if os.path.exists(CONSTITUENTS_CACHE_FILE):
        with open(CONSTITUENTS_CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_constituents_cache(cache):
    with open(CONSTITUENTS_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def fetch_station_constituents(station_id):
    """Fetch harmonic constituents + MSL/MLLW datum offset for a station
    from NOAA's metadata API (mdapi) -- not the predictions/datagetter
    service that has historically gone down. Falls back to a subordinate-
    station tidepredoffsets record when the station has no harmonic
    constituents of its own (see predict_station)."""
    harcon_url = f"https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{station_id}/harcon.json"
    datums_url = f"https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{station_id}/datums.json"

    r = requests.get(harcon_url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    harcon = r.json().get("HarmonicConstituents", [])

    if harcon:
        # NOAA's "number" field (1-37) is a fixed identity for each of the 37
        # standard constituents, in the same order as pytides2's cons.noaa --
        # NOT every station has all 37 analyzed. Weaker/subordinate stations
        # (e.g. 8722588 came back with 29) have "unstable" ones deleted, with
        # gaps in the number sequence rather than a clean prefix. Build a full
        # 37-length array indexed by that number and default any missing
        # constituent to zero amplitude -- the same treatment NOAA itself uses
        # for unstable constituents it keeps but zeroes (e.g. MM/MSF/MF at many
        # stations).
        amplitudes = [0.0] * 37
        phases_gmt = [0.0] * 37
        for c in harcon:
            idx = c["number"] - 1
            if 0 <= idx < 37:
                amplitudes[idx] = c["amplitude"]
                phases_gmt[idx] = c["phase_GMT"]

        r = requests.get(datums_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        datums = {d["name"]: d["value"] for d in r.json().get("datums", [])}
        if "MSL" not in datums or "MLLW" not in datums:
            raise ValueError(f"station {station_id}: missing MSL/MLLW datum")

        return {
            "type": "harmonic",
            "amplitudes": amplitudes,
            "phases_gmt": phases_gmt,
            "n_constituents": len(harcon),
            "msl_minus_mllw": datums["MSL"] - datums["MLLW"],
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

    # No harmonic constituents of its own -- try NOAA's subordinate-station
    # record instead. This is the classic paper-tide-table method: a fixed
    # time offset (minutes) and height ratio, separately for highs and lows,
    # applied to a nearby reference station's own harmonic predictions.
    # Still pure mdapi metadata (independent of the live predictions API).
    # Validated 2026-07-18 against live NOAA predictions -- see module
    # docstring.
    offsets_url = f"https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{station_id}/tidepredoffsets.json"
    r = requests.get(offsets_url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    off = r.json()
    ref_id = off.get("refStationId")
    adj_type = off.get("heightAdjustedType")
    if not ref_id or adj_type != "R":
        # Only the ratio ("R") method has been validated. If NOAA ever
        # returns a different heightAdjustedType for one of our stations,
        # skip it rather than silently guess at an unverified formula.
        raise ValueError(
            f"station {station_id}: no harmonic constituents and no usable "
            f"ratio-type tidepredoffsets (refStationId={ref_id!r}, "
            f"heightAdjustedType={adj_type!r})"
        )

    return {
        "type": "subordinate",
        "refStationId": ref_id,
        "heightOffsetHighTide": off["heightOffsetHighTide"],
        "heightOffsetLowTide": off["heightOffsetLowTide"],
        "timeOffsetHighTide": off["timeOffsetHighTide"],
        "timeOffsetLowTide": off["timeOffsetLowTide"],
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def ensure_constituents(cache):
    """Refresh any station missing from the cache or older than REFRESH_DAYS.
    Network failures fall back to whatever's already cached rather than
    aborting the run. Subordinate stations pull in their reference
    station's constituents too, even if that reference isn't itself a
    departure-location station (e.g. 8652587, 8723178)."""
    now = datetime.now(timezone.utc)
    changed = False

    def needs_refresh(station_id):
        entry = cache.get(station_id)
        if not entry or not entry.get("fetched_at"):
            return True
        age = now - datetime.strptime(entry["fetched_at"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return age > timedelta(days=REFRESH_DAYS)

    def ensure_one(station_id, depth=0):
        nonlocal changed
        if depth > 3:
            print(f"  ERROR: reference chain too deep at {station_id} (depth {depth}), skipping")
            return
        if needs_refresh(station_id):
            entry = cache.get(station_id)
            try:
                print(f"Fetching constituents for station {station_id}...")
                cache[station_id] = fetch_station_constituents(station_id)
                changed = True
            except Exception as e:
                if entry:
                    print(f"  WARNING: refresh failed for {station_id} ({e}); keeping cached constituents from {entry.get('fetched_at')}")
                else:
                    print(f"  ERROR: no cached constituents for {station_id} and fetch failed ({e}) -- station will be skipped")
                    return
        entry = cache.get(station_id)
        if entry and entry.get("type") == "subordinate":
            ensure_one(entry["refStationId"], depth + 1)

    for station_id in STATION_IDS:
        ensure_one(station_id)

    return changed


def _harmonic_extrema(entry, t0, t1):
    """Return a list of (t_utc, height_mllw, hilo) for a harmonic-type
    entry over the UTC window [t0, t1). Pure local computation, no
    network calls."""
    tide = Tide(constituents=list(cons.noaa), amplitudes=entry["amplitudes"], phases=entry["phases_gmt"])
    offset = entry["msl_minus_mllw"]
    out = []
    for t_utc_naive, height, hilo in tide.extrema(t0, t1):
        t_utc = t_utc_naive.replace(tzinfo=timezone.utc)
        out.append((t_utc, round(height + offset, 3), hilo))
    return out


def predict_station(station_id, cache, start_date, days):
    """Compute hi/lo tide predictions for one station over
    [start_date, start_date+days) using cached constituents (harmonic) or
    a reference station + ratio/offset adjustment (subordinate)."""
    entry = cache[station_id]

    # phase_GMT is referenced to Greenwich equilibrium time, so t0/t1 and
    # the times pytides2 yields are UTC (naive datetimes representing UTC).
    # Start the UTC search a day early, and end a day late, so the full
    # local (ET) calendar day of start_date is covered even after a
    # subordinate station's time-offset shift, and even though ET midnight
    # falls a few hours after UTC midnight -- otherwise a boundary day can
    # come back with only a partial set of extrema.
    t0 = datetime(start_date.year, start_date.month, start_date.day) - timedelta(days=1)
    t1 = t0 + timedelta(days=days + 2)

    if entry.get("type") == "subordinate":
        ref_entry = cache.get(entry["refStationId"])
        if not ref_entry or ref_entry.get("type") != "harmonic":
            raise ValueError(
                f"station {station_id}: reference station {entry.get('refStationId')} "
                f"unavailable or not a harmonic station"
            )
        extrema = []
        for t_utc, height_mllw, hilo in _harmonic_extrema(ref_entry, t0, t1):
            if hilo == "H":
                dt_minutes = entry["timeOffsetHighTide"]
                ratio = entry["heightOffsetHighTide"]
            else:
                dt_minutes = entry["timeOffsetLowTide"]
                ratio = entry["heightOffsetLowTide"]
            extrema.append((t_utc + timedelta(minutes=dt_minutes), round(height_mllw * ratio, 3), hilo))
    else:
        extrema = _harmonic_extrema(entry, t0, t1)

    by_date = {}
    for t_utc, height, hilo in extrema:
        t_local = t_utc.astimezone(STATION_TZ)
        # Match the exact string shape NOAA's own datagetter (time_zone=
        # lst_ldt) returns, so the frontend's existing `new Date(tide.t)`
        # parsing (no offset marker -> browser-local) behaves identically
        # whether this record came from the live call or this backup.
        date_key = t_local.strftime("%Y-%m-%d")
        by_date.setdefault(date_key, []).append({
            "t": t_local.strftime("%Y-%m-%d %H:%M"),
            "v": height,
            "type": "High" if hilo == "H" else "Low",
        })
    return by_date


def main():
    cache = load_constituents_cache()
    if ensure_constituents(cache):
        save_constituents_cache(cache)

    start_date = datetime.now(timezone.utc).date()
    backup = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "window_days": PREDICT_DAYS,
        "source": (
            "Computed locally from NOAA CO-OPS harmonic constituents "
            "(mdapi harcon/datums), independent of the live predictions/"
            "datagetter service."
        ),
        "stations": {},
    }

    failures = []
    for station_id in STATION_IDS:
        if station_id not in cache:
            failures.append(station_id)
            continue
        try:
            backup["stations"][station_id] = predict_station(station_id, cache, start_date, PREDICT_DAYS)
        except Exception as e:
            print(f"  ERROR: prediction failed for {station_id}: {e}")
            failures.append(station_id)

    with open(BACKUP_OUTPUT_FILE, "w") as f:
        json.dump(backup, f, indent=2)

    print(f"Wrote {BACKUP_OUTPUT_FILE}: {len(backup['stations'])}/{len(STATION_IDS)} stations")
    if failures:
        print(f"WARNING: no data for stations: {failures}")
        # Don't fail the whole workflow over a handful of stations -- a
        # partial refresh (or a full reuse of the existing cache) is still
        # far better than no backup file at all.


if __name__ == "__main__":
    main()
