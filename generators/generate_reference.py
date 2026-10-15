"""Generate Vitalis reference data: facility, department, bed, provider.

Payers are intentionally NOT generated here - Synthea produces payers.csv and
the claims generator builds denial behaviour on top of those payer IDs.

Usage:
    python generators/generate_reference.py [--out-dir data/reference]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from common import DATA_DIR, get_rng, load_config

UNIT_LABEL = {
    "ED": "Emergency Department", "ICU": "Intensive Care Unit",
    "MED_SURG": "Medical-Surgical", "CARDIOLOGY": "Cardiology",
    "ONCOLOGY": "Oncology", "ORTHOPEDICS": "Orthopedics",
    "PEDIATRICS": "Pediatrics", "MATERNITY": "Maternity",
}
UNIT_TYPE = {"ED": "EMERGENCY", "ICU": "CRITICAL_CARE"}  # default INPATIENT
BED_TYPE = {"ED": "ED_SPACE", "ICU": "ICU"}              # default INPATIENT

FIRST = ["Alex", "Maria", "James", "Priya", "Wei", "Omar", "Elena", "David", "Aisha", "Tom",
         "Sofia", "Kenji", "Grace", "Luis", "Hannah", "Raj", "Nina", "Marcus", "Chloe", "Ibrahim"]
LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Okafor", "Silva", "Cohen", "Murphy", "Ahmed", "Rossi",
        "Larsen", "Tanaka", "Dubois", "Khan", "Walsh", "Costa", "Novak", "Reyes", "Haddad", "Fischer"]
HOSP_SPECIALTIES = ["Emergency Medicine", "Hospitalist", "Internal Medicine", "Cardiology", "Oncology",
                    "Orthopedics", "Pediatrics", "Obstetrics", "Critical Care", "General Surgery",
                    "Radiology", "Anesthesiology"]
CLINIC_SPECIALTIES = ["Family Medicine", "Internal Medicine", "Pediatrics", "Endocrinology",
                      "Cardiology", "Behavioral Health"]


def build_reference(cfg: dict) -> dict[str, pd.DataFrame]:
    ref = cfg["reference"]
    rng = get_rng(cfg["seed"], "reference")
    shares = ref["unit_share"]
    assert abs(sum(shares.values()) - 1.0) < 1e-6, "unit_share must sum to 1.0"

    facilities, departments, beds = [], [], []
    for h in ref["hospitals"]:
        facilities.append(dict(
            facility_id=h["facility_id"], facility_name=h["name"], facility_type="HOSPITAL",
            city=h["city"], state="MA", licensed_beds=h["licensed_beds"],
            opened_year=int(rng.integers(1955, 2008))))
        for unit, share in shares.items():
            cap = max(4, round(h["licensed_beds"] * share))
            dept_id = f'{h["facility_id"]}-{unit}'
            departments.append(dict(
                department_id=dept_id, facility_id=h["facility_id"], unit=unit,
                department_name=UNIT_LABEL[unit], unit_type=UNIT_TYPE.get(unit, "INPATIENT"),
                capacity=cap))
            for i in range(1, cap + 1):
                beds.append(dict(
                    bed_id=f"{dept_id}-{i:03d}", facility_id=h["facility_id"],
                    department_id=dept_id, unit=unit, bed_type=BED_TYPE.get(unit, "INPATIENT")))
    for c in ref["clinics"]:
        facilities.append(dict(
            facility_id=c["facility_id"], facility_name=c["name"], facility_type="CLINIC",
            city=c["city"], state="MA", licensed_beds=0, opened_year=int(rng.integers(1995, 2024))))
        for unit, label, rooms in (("PRIMARY_CARE", "Primary Care", 8), ("SPECIALTY", "Specialty Care", 6)):
            departments.append(dict(
                department_id=f'{c["facility_id"]}-{unit}', facility_id=c["facility_id"], unit=unit,
                department_name=label, unit_type="OUTPATIENT", capacity=rooms))

    fac_df = pd.DataFrame(facilities)
    dept_df = pd.DataFrame(departments)
    bed_df = pd.DataFrame(beds)

    # Providers: weighted toward bigger facilities.
    w = np.where(fac_df.facility_type == "HOSPITAL", fac_df.licensed_beds, 80).astype(float)
    w /= w.sum()
    provs = []
    for i in range(1, ref["provider_count"] + 1):
        fac = fac_df.iloc[int(rng.choice(len(fac_df), p=w))]
        specs = HOSP_SPECIALTIES if fac.facility_type == "HOSPITAL" else CLINIC_SPECIALTIES
        fdepts = dept_df[dept_df.facility_id == fac.facility_id].department_id.to_numpy()
        provs.append(dict(
            provider_id=f"PRV{i:05d}",
            # NPIs starting with 9 are not valid real NPIs - safe for synthetic data.
            npi="9" + "".join(str(int(x)) for x in rng.integers(0, 10, 9)),
            first_name=FIRST[int(rng.integers(len(FIRST)))],
            last_name=LAST[int(rng.integers(len(LAST)))],
            specialty=specs[int(rng.integers(len(specs)))],
            facility_id=fac.facility_id,
            department_id=str(fdepts[int(rng.integers(len(fdepts)))]),
            hire_date=(pd.Timestamp("2005-01-01") + pd.Timedelta(days=int(rng.integers(0, 7500)))).date().isoformat()))
    return {"facility": fac_df, "department": dept_df, "bed": bed_df, "provider": pd.DataFrame(provs)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(DATA_DIR / "reference"))
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in build_reference(cfg).items():
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"wrote {name}.csv  ({len(df):,} rows)")


if __name__ == "__main__":
    main()
