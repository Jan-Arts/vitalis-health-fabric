"""Generate claims with payer-specific denial behaviour from Synthea encounters.

Reads encounters.csv (and payers.csv if present) and writes claims.csv with
claim status, CARC-style denial codes, appeals, and payment timing. Denial rate
depends on payer (stable, seeded per payer ID) and encounter class.

Usage:
    python generators/generate_claims.py --in-dir data/synthea_raw/csv --out-dir data/claims
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
import pandas as pd

from common import DATA_DIR, get_rng, load_config

# CARC group + reason codes (real X12 code values)
DENIALS = [("CO-16", "Claim lacks information needed for adjudication", .22),
           ("CO-18", "Duplicate claim/service", .08),
           ("CO-29", "Timely filing limit expired", .07),
           ("CO-50", "Not deemed a medical necessity by the payer", .20),
           ("CO-97", "Benefit included in payment for another service", .13),
           ("CO-197", "Precertification/authorization absent", .22),
           ("CO-4", "Procedure code inconsistent with modifier", .08)]
CLASS_FACTOR = {"inpatient": .8, "emergency": .8, "urgentcare": 1.0, "ambulatory": 1.0,
                "outpatient": 1.0, "wellness": .5, "home": 1.1, "hospice": .6, "snf": .9}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default=str(DATA_DIR / "synthea_raw" / "csv"))
    ap.add_argument("--out-dir", default=str(DATA_DIR / "claims"))
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    seed = cfg["seed"]
    rng = get_rng(seed, "claims")
    in_dir, out_dir = Path(args.in_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    want = ["Id", "START", "STOP", "PATIENT", "ORGANIZATION", "PAYER", "ENCOUNTERCLASS", "TOTAL_CLAIM_COST"]
    enc = pd.read_csv(in_dir / "encounters.csv", dtype=str, keep_default_na=False)
    enc = enc[[c for c in want if c in enc.columns]].copy()
    n = len(enc)

    payer_name = {}
    if (in_dir / "payers.csv").exists():
        p = pd.read_csv(in_dir / "payers.csv", dtype=str, keep_default_na=False)
        payer_name = dict(zip(p["Id"], p["NAME"]))
    enc["payer_name"] = enc["PAYER"].map(payer_name).fillna("UNKNOWN")

    base = enc["PAYER"].map(lambda pid: 0.04 + random.Random(f"{seed}:{pid}").random() * 0.14).to_numpy()
    cls = enc["ENCOUNTERCLASS"].str.lower().map(CLASS_FACTOR).fillna(1.0).to_numpy()
    p_deny = np.clip(base * cls, 0, 0.6)

    self_pay = (enc["payer_name"].str.upper() == "NO_INSURANCE").to_numpy()
    denied = rng.random(n) < p_deny
    partial = rng.random(n) < 0.08
    status = np.where(self_pay, "SELF_PAY", np.where(denied, "DENIED", np.where(partial, "PARTIAL", "PAID")))

    svc = pd.to_datetime(enc["STOP"].where(enc["STOP"] != "", enc["START"]), utc=True, errors="coerce")
    submitted = svc + pd.to_timedelta(rng.integers(1, 8, n), unit="D")
    status = np.where((status != "DENIED") & (status != "SELF_PAY") &
                      (submitted > submitted.max() - pd.Timedelta(days=30)), "PENDING", status)
    adjudicated = submitted + pd.to_timedelta(np.clip(rng.lognormal(np.log(28), 0.5, n), 5, 180), unit="D")
    adjudicated = adjudicated.where(~np.isin(status, ["PENDING", "SELF_PAY"]))

    billed = pd.to_numeric(enc.get("TOTAL_CLAIM_COST", pd.Series(["0"] * n)), errors="coerce").fillna(0).abs().to_numpy()
    allowed = np.round(billed * rng.uniform(.55, .85, n), 2)
    paid = np.round(allowed * rng.uniform(.70, .95, n), 2)
    paid = np.where(status == "PARTIAL", np.round(paid * .5, 2), paid)
    paid = np.where(np.isin(status, ["DENIED", "PENDING", "SELF_PAY"]), 0.0, paid)

    codes = rng.choice(len(DENIALS), size=n, p=[d[2] for d in DENIALS])
    is_denied = status == "DENIED"
    appealed = is_denied & (rng.random(n) < 0.55)
    overturned = appealed & (rng.random(n) < 0.40)

    out = pd.DataFrame({
        "claim_id": [f"CLM{i:08d}" for i in range(n)],
        "encounter_id": enc["Id"], "patient_id": enc["PATIENT"], "facility_org_id": enc.get("ORGANIZATION", ""),
        "payer_id": enc["PAYER"], "payer_name": enc["payer_name"], "encounter_class": enc["ENCOUNTERCLASS"],
        "service_date": svc.dt.strftime("%Y-%m-%d"), "submitted_date": submitted.dt.strftime("%Y-%m-%d"),
        "adjudicated_date": adjudicated.dt.strftime("%Y-%m-%d"),
        "billed_amount": np.round(billed, 2), "allowed_amount": allowed, "paid_amount": paid,
        "patient_responsibility": np.where(np.isin(status, ["PAID", "PARTIAL"]), np.round(allowed - paid, 2), 0.0),
        "claim_status": status,
        "denial_code": np.where(is_denied, [DENIALS[k][0] for k in codes], ""),
        "denial_reason": np.where(is_denied, [DENIALS[k][1] for k in codes], ""),
        "appealed_flag": appealed.astype(int), "appeal_overturned_flag": overturned.astype(int),
    })
    out.to_csv(out_dir / "claims.csv", index=False)
    print(f"wrote claims.csv ({n:,} rows)")
    print(out.claim_status.value_counts(normalize=True).round(3).to_string())


if __name__ == "__main__":
    main()
