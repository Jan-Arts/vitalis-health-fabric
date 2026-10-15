"""ADT (admit/discharge/transfer) + vitals event simulator for Vitalis hospitals.

Patients arrive at the ED following an hour-of-day curve, wait for an ED space,
are treated, then are either discharged or admitted to an inpatient unit
(sometimes ICU -> step-down). Beds are finite, so full units create ED
boarding delays. Inpatients emit periodic vitals; a small share deteriorate.

All events share ONE flat schema so Eventstream/KQL mappings stay simple.
Timestamps are UTC; the hour-of-day curve is applied to the UTC hour.

Modes
  history   --sink file     JSONL partitioned dt=YYYY-MM-DD (land in Bronze)
  live      --live          history up to "now", then future events paced in real time
  streaming --sink eventhub send to a Fabric Eventstream custom endpoint
            (env: EVENTHUB_CONNECTION_STRING, EVENTHUB_NAME)

Examples
  python simulate_adt_stream.py --as-of 2026-01-31T00:00:00Z --backfill-days 30
  python simulate_adt_stream.py --live --sink eventhub --speed 30 --skip-history
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from common import DATA_DIR, get_rng, load_config

SCHEMA_VERSION = "1.0"
OVERFLOW_WAIT_S = 4 * 3600   # wait this long for a bed before overflowing to another general unit
HOUR_MULT = np.array([.45, .35, .30, .28, .30, .40, .55, .75, .95, 1.15, 1.30, 1.35,
                      1.35, 1.30, 1.25, 1.25, 1.25, 1.30, 1.35, 1.30, 1.10, .90, .70, .55])
HOUR_MULT = HOUR_MULT / HOUR_MULT.mean()

ACUITY_P = [.03, .12, .40, .33, .12]          # ESI-like levels 1..5
ADMIT_P = [.90, .60, .30, .10, .03]           # P(admit | acuity)
ED_DISP = ["HOME", "AMA", "TRANSFER_OUT", "EXPIRED"]
ED_DISP_P = [.935, .03, .03, .005]
DISP = ["HOME", "SNF", "HOME_HEALTH", "REHAB", "AMA", "EXPIRED"]
DISP_P = [.70, .11, .10, .04, .02, .03]
UNITS = ["MED_SURG", "CARDIOLOGY", "ICU", "ORTHOPEDICS", "ONCOLOGY", "PEDIATRICS", "MATERNITY"]
UNIT_P = [.40, .16, .08, .10, .08, .08, .10]
UNIT_P_SICK = [.30, .14, .30, .06, .06, .06, .08]
MEDIAN_LOS_DAYS = {"MED_SURG": 3.5, "CARDIOLOGY": 3.0, "ICU": 2.2, "ORTHOPEDICS": 3.0,
                   "ONCOLOGY": 5.0, "PEDIATRICS": 2.5, "MATERNITY": 2.0}

FIELDS = ["schema_version", "event_id", "event_ts", "event_type", "hl7_code", "facility_id", "unit",
          "bed_id", "patient_id", "encounter_id", "acuity", "disposition",
          "heart_rate", "resp_rate", "spo2", "systolic_bp", "diastolic_bp", "temp_c"]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_pools(bed_df: pd.DataFrame) -> dict:
    return {key: {"ids": g.bed_id.tolist(), "free_at": np.zeros(len(g))}
            for key, g in bed_df.groupby(["facility_id", "unit"], sort=True)}


def vitals_rows(rng, t0, t1, interval, onset):
    """Periodic vitals between t0 and t1; ramps toward instability after onset."""
    if t1 - t0 < interval * 0.5:
        return []
    times = np.arange(t0 + interval * rng.uniform(0.2, 0.9), t1, interval)
    if len(times) == 0:
        return []
    f = np.zeros(len(times))
    if onset is not None:
        f = np.clip((times - onset) / max((t1 - onset) * 0.66, 1.0), 0, 1)
    m = len(times)
    hr = rng.normal(80, 9) + 35 * f + rng.normal(0, 3, m)
    rr = rng.normal(16, 1.8) + 10 * f + rng.normal(0, 1, m)
    spo2 = rng.normal(97, 1.1) - 8 * f + rng.normal(0, 0.6, m)
    sbp = rng.normal(125, 13) - 35 * f + rng.normal(0, 4, m)
    temp = rng.normal(36.8, 0.25) + 1.6 * f + rng.normal(0, 0.1, m)
    out = []
    for k in range(m):
        out.append((float(times[k]), dict(
            heart_rate=int(np.clip(hr[k], 30, 200)), resp_rate=int(np.clip(rr[k], 6, 45)),
            spo2=int(np.clip(spo2[k], 70, 100)), systolic_bp=int(np.clip(sbp[k], 60, 220)),
            diastolic_bp=int(np.clip(sbp[k] * 0.62 + rng.normal(0, 3), 35, 130)),
            temp_c=round(float(np.clip(temp[k], 34, 41)), 1))))
    return out


def simulate(cfg, bed_df, as_of, backfill_days, warmup_days, horizon_hours, patient_ids):
    a = cfg["adt"]
    rng = get_rng(cfg["seed"], "adt")
    t_end = as_of + horizon_hours * 3600
    t_emit = as_of - backfill_days * 86400
    t_start = t_emit - warmup_days * 86400
    turn = a["turnaround_min"] * 60
    pools = build_pools(bed_df)
    cap = bed_df.groupby("facility_id").size()

    # --- arrivals (non-homogeneous Poisson), processed in time order ---
    arrivals = []
    for fac, nbeds in cap.items():
        base = a["ed_arrivals_per_bed_per_day"] * nbeds / 24.0
        h0 = int(t_start // 3600) * 3600
        for hs in np.arange(h0, t_end, 3600):
            n = rng.poisson(base * HOUR_MULT[int((hs % 86400) // 3600)])
            for t in hs + rng.uniform(0, 3600, n):
                if t_start <= t < t_end:
                    arrivals.append((float(t), fac))
    arrivals.sort()

    journeys, truth = [], []
    for n, (arr, fac) in enumerate(arrivals):
        pid = patient_ids[int(rng.integers(len(patient_ids)))]
        enc = f"SIMENC-{n:08d}"
        acuity = int(rng.choice(5, p=ACUITY_P)) + 1
        ev = []

        def add(ts, hl7, et, unit, bed, disposition=None, vit=None):
            ev.append(dict(event_ts=ts, event_type=et, hl7_code=hl7, facility_id=fac, unit=unit,
                           bed_id=bed, patient_id=pid, encounter_id=enc, acuity=acuity,
                           disposition=disposition, **(vit or {})))

        ed = pools[(fac, "ED")]
        i = int(np.argmin(ed["free_at"]))
        ed_start = max(arr + rng.uniform(300, 1200), ed["free_at"][i])
        ed_end = ed_start + float(np.clip(rng.lognormal(np.log(3 * 3600), 0.5), 2700, 14 * 3600))
        add(arr, "A04", "ARRIVAL", "ED", None)
        add(ed_start, "A02", "ED_ROOMED", "ED", ed["ids"][i])
        ed_iv = 1800 if acuity <= 2 else 3600

        if rng.random() >= min(0.95, ADMIT_P[acuity - 1] * a["ed_admit_scale"]):
            for ts, v in vitals_rows(rng, ed_start, ed_end, ed_iv, None):
                add(ts, "R01", "VITALS", "ED", ed["ids"][i], vit=v)
            add(ed_end, "A03", "DISCHARGE", "ED", ed["ids"][i],
                disposition=str(rng.choice(ED_DISP, p=ED_DISP_P)))
            ed["free_at"][i] = ed_end + turn
            journeys.append(ev)
            continue

        # --- admitted: one or two inpatient segments ---
        unit = UNITS[int(rng.choice(len(UNITS), p=UNIT_P_SICK if acuity <= 2 else UNIT_P))]
        segs = [unit]
        if unit == "ICU" and rng.random() < 0.6:
            segs.append("MED_SURG")
        prev_pool, prev_i, prev_start, prev_unit, prev_iv = ed, i, ed_start, "ED", ed_iv
        requested, deteriorating = ed_end, rng.random() < a["deterioration_probability"]
        prev_onset = None
        for s_idx, u in enumerate(segs):
            pool = pools[(fac, u)]
            j = int(np.argmin(pool["free_at"]))
            start = max(requested, pool["free_at"][j])
            if u not in ("ICU", "PEDIATRICS", "MATERNITY") and start - requested > OVERFLOW_WAIT_S:
                # unit is full: overflow to whichever general unit frees a bed first
                alt = min((float(pools[(fac, x)]["free_at"].min()), x)
                          for x in ("MED_SURG", "CARDIOLOGY", "ONCOLOGY", "ORTHOPEDICS"))
                if alt[0] < pool["free_at"][j]:
                    u = alt[1]
                    pool = pools[(fac, u)]
                    j = int(np.argmin(pool["free_at"]))
                    start = max(requested, pool["free_at"][j])
            # close previous segment now that we know when the patient actually moved
            for ts, v in vitals_rows(rng, prev_start, start, prev_iv, prev_onset):
                add(ts, "R01", "VITALS", prev_unit, prev_pool["ids"][prev_i], vit=v)
            prev_pool["free_at"][prev_i] = start + turn
            add(start, "A01" if s_idx == 0 else "A02", "ADMIT" if s_idx == 0 else "TRANSFER",
                u, pool["ids"][j])
            los = float(np.clip(rng.lognormal(np.log(MEDIAN_LOS_DAYS[u] * 86400), 0.55), 21600, 30 * 86400))
            end_planned = start + los
            pool["free_at"][j] = end_planned + turn
            if deteriorating and s_idx == 0:
                prev_onset = start + los * rng.uniform(0.75, 0.92)
                truth.append(dict(encounter_id=enc, patient_id=pid, facility_id=fac, unit=u,
                                  onset_ts=iso(prev_onset)))
            prev_pool, prev_i, prev_start, prev_unit = pool, j, start, u
            prev_iv = 3600 if u == "ICU" else a["vitals_interval_min_inpatient"] * 60
            requested = end_planned
        for ts, v in vitals_rows(rng, prev_start, requested, prev_iv, prev_onset):
            add(ts, "R01", "VITALS", prev_unit, prev_pool["ids"][prev_i], vit=v)
        add(requested, "A03", "DISCHARGE", prev_unit, prev_pool["ids"][prev_i],
            disposition=str(rng.choice(DISP, p=DISP_P)))
        journeys.append(ev)

    # keep journeys that overlap the emit window; truncate at t_end
    events = []
    for ev in journeys:
        if max(e["event_ts"] for e in ev) < t_emit:
            continue
        events.extend(e for e in ev if e["event_ts"] <= t_end)
    events.sort(key=lambda e: e["event_ts"])
    for k, e in enumerate(events):
        e["schema_version"] = SCHEMA_VERSION
        e["event_id"] = f"EVT-{k:09d}"
    return events, truth


def to_json(e: dict) -> str:
    rec = {f: e.get(f) for f in FIELDS}
    rec["event_ts"] = iso(e["event_ts"])
    return json.dumps(rec, separators=(",", ":"))


# ------------------------------- sinks --------------------------------
class FileSink:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        if out_dir.exists():
            shutil.rmtree(out_dir)          # re-runs replace output rather than duplicate it
        out_dir.mkdir(parents=True)

    def send(self, events):
        by_day = {}
        for e in events:
            by_day.setdefault(iso(e["event_ts"])[:10], []).append(to_json(e))
        for day, lines in by_day.items():
            d = self.out_dir / f"dt={day}"
            d.mkdir(exist_ok=True)
            with open(d / "events.jsonl", "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")

    def close(self):
        pass


class StdoutSink:
    def send(self, events):
        for e in events:
            print(to_json(e), flush=True)

    def close(self):
        pass


class EventHubSink:
    def __init__(self):
        from azure.eventhub import EventHubProducerClient  # optional dependency
        self.producer = EventHubProducerClient.from_connection_string(
            os.environ["EVENTHUB_CONNECTION_STRING"], eventhub_name=os.environ.get("EVENTHUB_NAME"))

    def send(self, events):
        from azure.eventhub import EventData
        batch = self.producer.create_batch()
        for e in events:
            data = EventData(to_json(e))
            try:
                batch.add(data)
            except ValueError:
                self.producer.send_batch(batch)
                batch = self.producer.create_batch()
                batch.add(data)
        if len(batch):
            self.producer.send_batch(batch)

    def close(self):
        self.producer.close()


def emit_chunks(sink, events, size=500):
    for i in range(0, len(events), size):
        sink.send(events[i:i + size])


def emit_live(sink, events, sim_now, speed):
    wall0 = time.time()
    i = 0
    while i < len(events):
        wait = wall0 + (events[i]["event_ts"] - sim_now) / speed - time.time()
        if wait > 0:
            time.sleep(wait)
        due = []
        while i < len(events) and wall0 + (events[i]["event_ts"] - sim_now) / speed <= time.time():
            due.append(events[i])
            i += 1
        sink.send(due or [events[i]])
        if not due:
            i += 1


def parse_as_of(s):
    if not s:
        return time.time()
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="ISO timestamp for 'now' (use for reproducible runs). Default: current time")
    ap.add_argument("--backfill-days", type=int, default=30)
    ap.add_argument("--warmup-days", type=int, default=10, help="simulated before the window so units start 'full'")
    ap.add_argument("--sink", choices=["file", "stdout", "eventhub"], default="file")
    ap.add_argument("--out-dir", default=str(DATA_DIR / "adt"))
    ap.add_argument("--reference-dir", default=str(DATA_DIR / "reference"))
    ap.add_argument("--patients-csv", help="optional Synthea patients.csv; uses its Id column as patient_id")
    ap.add_argument("--live", action="store_true", help="after history, pace future events in real time")
    ap.add_argument("--live-hours", type=float, default=24)
    ap.add_argument("--speed", type=float, default=1.0, help="live-mode time compression")
    ap.add_argument("--skip-history", action="store_true", help="live mode: don't emit history, only future events")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    bed_df = pd.read_csv(Path(args.reference_dir) / "bed.csv")
    if args.patients_csv:
        patient_ids = pd.read_csv(args.patients_csv, usecols=["Id"], dtype=str)["Id"].tolist()
    else:
        patient_ids = [f"SIM-P{i:06d}" for i in range(cfg["adt"]["patient_pool_size"])]

    as_of = parse_as_of(args.as_of)
    horizon = args.live_hours if args.live else 0
    events, truth = simulate(cfg, bed_df, as_of, args.backfill_days, args.warmup_days, horizon, patient_ids)
    hist = [e for e in events if e["event_ts"] <= as_of]
    future = [e for e in events if e["event_ts"] > as_of]

    sink = {"file": lambda: FileSink(Path(args.out_dir)), "stdout": StdoutSink, "eventhub": EventHubSink}[args.sink]()
    try:
        if not (args.live and args.skip_history):
            emit_chunks(sink, hist)
        print(f"history events: {len(hist):,} | future events: {len(future):,} | "
              f"deteriorating encounters: {len(truth)}", file=sys.stderr)
        if args.live:
            emit_live(sink, future, as_of, args.speed)
    finally:
        sink.close()
    if args.sink == "file":
        pd.DataFrame(truth).to_csv(Path(args.out_dir) / "sim_ground_truth.csv", index=False)


if __name__ == "__main__":
    main()
