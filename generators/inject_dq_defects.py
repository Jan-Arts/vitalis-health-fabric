"""Inject realistic data-quality defects into Synthea CSV output.

Reads Synthea CSVs from --in-dir, writes defective copies to --out-dir, and
writes dq_defect_manifest.csv: the ground truth of every defect injected.
Phase 2 DQ rules should detect these; the manifest lets us measure how many.

Defects (rates in config.yaml -> dq):
  duplicate_patient     near-duplicate patient row (typo/case/DOB shift/missing SSN) and a
                        share of the original's encounters re-pointed to the duplicate ID
  missing_encounter     encounter rows removed -> orphans in conditions/procedures/etc.
  invalid_code          malformed CODE values in conditions.csv (and procedures.csv)
  invalid_date          STOP before START, or START far in the future
  negative_cost         negative TOTAL_CLAIM_COST
  null_fk               blank PATIENT on an encounter
  exact_duplicate_enc   encounter row repeated verbatim

Usage:
    python generators/inject_dq_defects.py --in-dir data/synthea_raw/csv --out-dir data/synthea_dq
"""
from __future__ import annotations

import argparse
import shutil
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from common import DATA_DIR, get_rng, load_config

BAD_CODES = ["", "UNKNOWN", "000000", "N/A", "ABC123", "99999999"]


def new_id(rng) -> str:
    return str(uuid.UUID(int=(int(rng.integers(0, 2**62)) << 64) | int(rng.integers(0, 2**62))))


def typo(s: str, rng) -> str:
    if not isinstance(s, str) or len(s) < 4:
        return s
    k = int(rng.integers(1, len(s) - 2))
    return s[:k] + s[k + 1] + s[k] + s[k + 2:]


def sample_idx(rng, n, rate, exclude=None):
    pool = np.arange(n) if exclude is None else np.setdiff1d(np.arange(n), exclude)
    k = min(int(round(n * rate)), len(pool))
    return rng.choice(pool, k, replace=False)


def shift_ts(series: pd.Series, delta: pd.Timedelta) -> pd.Series:
    return (pd.to_datetime(series, utc=True, errors="coerce") + delta).dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def inject(in_dir: Path, out_dir: Path, cfg: dict) -> pd.DataFrame:
    rng = get_rng(cfg["seed"], "dq")
    r = cfg["dq"]
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    def log(dtype, table, rid, related="", detail=""):
        manifest.append((dtype, table, rid, related, detail))

    pat = pd.read_csv(in_dir / "patients.csv", dtype=str, keep_default_na=False)
    enc = pd.read_csv(in_dir / "encounters.csv", dtype=str, keep_default_na=False)

    # 1) duplicate patients (+ split encounter history across the pair)
    dup_rows = []
    enc_pat_idx = enc.groupby("PATIENT").indices
    for pos in sample_idx(rng, len(pat), r["duplicate_patient_rate"]):
        row = pat.iloc[pos].copy()
        orig_id, nid = row["Id"], new_id(rng)
        row["Id"] = nid
        variant = int(rng.integers(0, 4))
        if variant == 0 and "LAST" in row:
            row["LAST"] = typo(row["LAST"], rng)
            detail = "last-name typo"
        elif variant == 1:
            for c in ("FIRST", "LAST"):
                if c in row:
                    row[c] = row[c].upper()
            detail = "names upper-cased"
        elif variant == 2 and "BIRTHDATE" in row:
            row["BIRTHDATE"] = shift_ts(pd.Series([row["BIRTHDATE"]]), pd.Timedelta(days=int(rng.choice([-1, 1]))))[0][:10]
            detail = "birthdate off by one day"
        else:
            if "SSN" in row:
                row["SSN"] = ""
            detail = "SSN missing"
        dup_rows.append(row)
        log("duplicate_patient", "patients", nid, orig_id, detail)
        if orig_id in enc_pat_idx:
            idxs = enc_pat_idx[orig_id]
            moved = idxs[rng.random(len(idxs)) < 0.4]
            enc.iloc[moved, enc.columns.get_loc("PATIENT")] = nid
            if len(moved):
                log("split_history", "encounters", nid, orig_id, f"{len(moved)} encounters re-pointed")
    if dup_rows:
        pat = pd.concat([pat, pd.DataFrame(dup_rows)], ignore_index=True)

    # 2) encounter-level defects (use disjoint row sets so each defect stays attributable)
    n = len(enc)
    used = np.array([], dtype=int)

    def take(rate):
        nonlocal used
        idx = sample_idx(rng, n, rate, exclude=used)
        used = np.concatenate([used, idx])
        return idx

    drop = take(r["missing_encounter_rate"])
    for i in drop:
        log("missing_encounter", "encounters", enc.at[i, "Id"], "", "row removed; child tables now orphaned")

    bad_dates = take(r["invalid_date_rate"])
    half = len(bad_dates) // 2
    if len(bad_dates):
        a, b = bad_dates[:half], bad_dates[half:]
        enc.loc[a, "STOP"] = shift_ts(enc.loc[a, "START"], pd.Timedelta(days=-1))
        enc.loc[b, "START"] = shift_ts(enc.loc[b, "START"], pd.Timedelta(days=365 * 5))
        for i in a:
            log("invalid_date", "encounters", enc.at[i, "Id"], "", "STOP before START")
        for i in b:
            log("invalid_date", "encounters", enc.at[i, "Id"], "", "START 5 years in the future")

    neg = take(r["negative_cost_rate"])
    if "TOTAL_CLAIM_COST" in enc.columns:
        for i in neg:
            enc.at[i, "TOTAL_CLAIM_COST"] = "-" + enc.at[i, "TOTAL_CLAIM_COST"].lstrip("-")
            log("negative_cost", "encounters", enc.at[i, "Id"], "", "TOTAL_CLAIM_COST negated")

    nulls = take(r["null_fk_rate"])
    for i in nulls:
        log("null_fk", "encounters", enc.at[i, "Id"], enc.at[i, "PATIENT"], "PATIENT blanked")
        enc.at[i, "PATIENT"] = ""

    dups = take(r["exact_duplicate_encounter_rate"])
    for i in dups:
        log("exact_duplicate_encounter", "encounters", enc.at[i, "Id"], "", "row repeated verbatim")
    enc = pd.concat([enc, enc.iloc[dups]], ignore_index=True)
    enc = enc.drop(index=drop).reset_index(drop=True)
    enc.to_csv(out_dir / "encounters.csv", index=False)
    pat.to_csv(out_dir / "patients.csv", index=False)

    # 3) invalid clinical codes
    for fname, key in (("conditions.csv", "Id"), ("procedures.csv", "Id")):
        f = in_dir / fname
        if not f.exists():
            continue
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
        if "CODE" not in df.columns:
            continue
        idx = sample_idx(rng, len(df), r["invalid_code_rate"])
        orig = df.loc[idx, "CODE"].copy()
        df.loc[idx, "CODE"] = [BAD_CODES[int(k)] for k in rng.integers(0, len(BAD_CODES), len(idx))]
        for i, o in zip(idx, orig):
            log("invalid_code", fname.replace(".csv", ""), f"{df.at[i, 'ENCOUNTER']}|{o}", o, f"CODE -> '{df.at[i, 'CODE']}'")
        df.to_csv(out_dir / fname, index=False)

    # copy every CSV we did not rewrite straight through
    for f in in_dir.glob("*.csv"):
        if not (out_dir / f.name).exists():
            shutil.copy(f, out_dir / f.name)

    man = pd.DataFrame(manifest, columns=["defect_type", "table", "record_id", "related_record_id", "detail"])
    man.insert(0, "defect_id", [f"DQ{i:06d}" for i in range(len(man))])
    man.to_csv(out_dir / "dq_defect_manifest.csv", index=False)
    return man


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default=str(DATA_DIR / "synthea_raw" / "csv"))
    ap.add_argument("--out-dir", default=str(DATA_DIR / "synthea_dq"))
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    man = inject(Path(args.in_dir), Path(args.out_dir), load_config(args.config))
    print(man.defect_type.value_counts().to_string())


if __name__ == "__main__":
    main()
