"""Smoke + invariant tests for the synthetic data generators. Run: pytest -q"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generators"))
import generate_claims  # noqa: E402
import generate_reference as ref  # noqa: E402
import inject_dq_defects as dq  # noqa: E402
import simulate_adt_stream as adt  # noqa: E402
from common import load_config  # noqa: E402

CFG = load_config()
AS_OF = datetime(2026, 1, 31, tzinfo=timezone.utc).timestamp()


@pytest.fixture(scope="module")
def reference():
    return ref.build_reference(CFG)


def test_reference_bed_counts_match_departments(reference):
    dept, beds = reference["department"], reference["bed"]
    inpatient = dept[dept.unit_type != "OUTPATIENT"]
    assert len(beds) == inpatient.capacity.sum()
    assert reference["facility"].facility_id.is_unique
    assert reference["provider"].npi.str.startswith("9").all()  # never a valid real NPI


def test_reference_is_deterministic():
    a, b = ref.build_reference(CFG), ref.build_reference(CFG)
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])


def _sim(reference):
    ids = [f"SIM-P{i:06d}" for i in range(500)]
    return adt.simulate(CFG, reference["bed"], AS_OF, 3, 4, 0, ids)


def test_adt_deterministic(reference):
    (e1, t1), (e2, t2) = _sim(reference), _sim(reference)
    assert [adt.to_json(e) for e in e1] == [adt.to_json(e) for e in e2]
    assert t1 == t2


def test_adt_no_bed_double_booking_and_valid_flow(reference):
    events, _ = _sim(reference)
    df = pd.DataFrame(events)
    assert df.event_id.is_unique
    assert df.event_ts.is_monotonic_increasing
    moves = df[df.event_type.isin(["ED_ROOMED", "ADMIT", "TRANSFER", "DISCHARGE"])].sort_values(["encounter_id", "event_ts"]).copy()
    moves["next_ts"] = moves.groupby("encounter_id").event_ts.shift(-1)
    stays = moves[moves.event_type != "DISCHARGE"].dropna(subset=["next_ts"]).sort_values(["bed_id", "event_ts"])
    prev_end = stays.groupby("bed_id").next_ts.shift(1)
    assert not (stays.event_ts < prev_end).any(), "a bed held two patients at once"
    v = df[df.event_type == "VITALS"]
    assert v.spo2.between(70, 100).all() and v.heart_rate.between(30, 200).all()


def _mock_synthea(d: Path, n_pat=400, n_enc=3000):
    rng = np.random.default_rng(1)
    uid = lambda: str(uuid.UUID(int=(int(rng.integers(0, 2**62)) << 64) | int(rng.integers(0, 2**62))))
    pids = [uid() for _ in range(n_pat)]
    pd.DataFrame({"Id": pids, "BIRTHDATE": "1970-05-05", "SSN": "999-11-2222",
                  "FIRST": "Anna", "LAST": "Johnson"}).to_csv(d / "patients.csv", index=False)
    pay = pd.DataFrame({"Id": [uid() for _ in range(3)], "NAME": ["Medicare", "Aetna", "NO_INSURANCE"]})
    pay.to_csv(d / "payers.csv", index=False)
    st = pd.to_datetime("2022-01-01", utc=True) + pd.to_timedelta(rng.integers(0, 1000 * 24 * 60, n_enc), unit="m")
    enc = pd.DataFrame({"Id": [uid() for _ in range(n_enc)], "START": st.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "STOP": (st + pd.Timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "PATIENT": rng.choice(pids, n_enc), "ORGANIZATION": "o1", "PAYER": rng.choice(pay.Id, n_enc),
                        "ENCOUNTERCLASS": "ambulatory", "TOTAL_CLAIM_COST": "250.00"})
    enc.to_csv(d / "encounters.csv", index=False)
    pd.DataFrame({"PATIENT": enc.PATIENT, "ENCOUNTER": enc.Id, "CODE": "44054006"}).to_csv(d / "conditions.csv", index=False)


def test_dq_injection_reconciles_with_manifest(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    _mock_synthea(src)
    man = dq.inject(src, out, CFG)
    before = pd.read_csv(src / "encounters.csv")
    after = pd.read_csv(out / "encounters.csv", dtype=str, keep_default_na=False)
    n_missing = (man.defect_type == "missing_encounter").sum()
    n_dupes = (man.defect_type == "exact_duplicate_encounter").sum()
    assert len(after) == len(before) - n_missing + n_dupes
    cond = pd.read_csv(out / "conditions.csv", dtype=str)
    assert (~cond.ENCOUNTER.isin(after.Id)).sum() == n_missing  # exactly the dropped encounters are orphaned
    assert man.defect_id.is_unique and (man.defect_type == "duplicate_patient").sum() > 0


def test_claims_consistency(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    _mock_synthea(src)
    generate_claims.main(["--in-dir", str(src), "--out-dir", str(out)])
    c = pd.read_csv(out / "claims.csv", keep_default_na=False)
    denied = c[c.claim_status == "DENIED"]
    assert len(denied) > 0 and (denied.denial_code != "").all() and (denied.paid_amount == 0).all()
    assert (c[c.claim_status != "DENIED"].denial_code == "").all()
    assert (c[c.claim_status == "SELF_PAY"].paid_amount == 0).all()
