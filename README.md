# Vitalis Health Data Platform 

End-to-end healthcare analytics project on Microsoft Fabric for **Vitalis Health Network**, a 
network of 3 hospitals and 8 clinics. All data is synthetic. No real PHI is used anywhere.

**Fabric workloads covered:** Data Factory, Lakehouse (medallion), Warehouse, Direct Lake semantic model,
Power BI, Real-Time Intelligence (Eventstream, Eventhouse, KQL), Data Activator, Data Science (MLflow),
governance and CI/CD.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Environment, Git, naming | Kit ready (`docs/phase0-runbook.md`) |
| 1 | Synthetic data generation + landing in Bronze | Generators ready and tested |
| 2 | Silver, identity resolution, data quality | Not started |
| 3 | Gold star schema + finance Warehouse | Not started |
| 4 | Semantic model + reports | Not started |
| 5 | Real-time layer + Activator | Not started |
| 6 | Readmission ML | Not started |
| 7 | Security, CI/CD, monitoring, docs | Not started |

## Repo layout

```
fabric/                 One folder per Fabric workspace (each workspace syncs to its own folder)
  ingest/ lakehouse/ warehouse/ realtime/ ds/ reports/
generators/             Synthetic data generators (Python, seeded and reproducible)
  config.yaml           Seed, facilities, volumes, DQ defect rates
  generate_reference.py facility / department / bed / provider
  simulate_adt_stream.py ADT + vitals events (history, live, Eventstream)
  inject_dq_defects.py  Controlled defects + ground-truth manifest
  generate_claims.py    Claims with payer-specific denials
scripts/                run_synthea.sh, run_all.sh, upload_to_onelake.py
tests/                  pytest suite for the generators
docs/                   naming-conventions.md, phase0-runbook.md
deployment/             CI/CD (Phase 7)
```

## Quick start (local, no Fabric needed)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                                              # 6 tests, ~10 seconds

scripts/run_synthea.sh                                 # needs Java 17+, git; ~30k patients
scripts/run_all.sh                                     # reference data, DQ defects, claims, 30 days of ADT
```

`run_all.sh` works without Synthea too (it generates reference data and the ADT stream, and skips DQ and claims).
Set `AS_OF=2026-01-31T00:00:00Z` for byte-identical ADT output between runs. Same seed, config, and as-of give identical files.

## What gets generated

| Output | Path | Lands in Bronze as |
|---|---|---|
| Synthea CSVs (clean) | `data/synthea_raw/csv/` | `bronze.synthea_*` |
| Synthea FHIR JSON | `data/synthea_raw/fhir/` | raw JSON in Files (optional path) |
| Same CSVs with injected defects | `data/synthea_dq/` | the Bronze source we actually ingest |
| DQ ground truth | `data/synthea_dq/dq_defect_manifest.csv` | `ref_dq_defect_manifest` (to score DQ rules) |
| Claims + denials | `data/claims/claims.csv` | `bronze.claims` |
| Facilities, departments, beds, providers | `data/reference/` | `bronze.ref_*` |
| ADT + vitals history | `data/adt/dt=YYYY-MM-DD/events.jsonl` | `bronze.adt_events_history` |
| Deteriorating-patient truth | `data/adt/sim_ground_truth.csv` | validates the early-warning alert |

## Streaming to Fabric 

```bash
export EVENTHUB_CONNECTION_STRING="<from Eventstream custom endpoint>"
export EVENTHUB_NAME="<entity name>"
pip install -r requirements-optional.txt
python generators/simulate_adt_stream.py --live --sink eventhub --speed 30 --skip-history
```

One flat event schema (`schema_version 1.0`) covers ADT and vitals, which keeps Eventstream and KQL mappings simple.

## Design decisions and known limitations

- **ADT stream vs Synthea patients.** Synthea has no real-time events. The ADT simulator generates its own
  encounters. `run_all.sh` reuses Synthea patient IDs so live events can join `dim_patient`,
  but ADT encounters are *not* Synthea encounters.
- **Crosswalks are Phase 2 work.** Synthea has its own organization and payer IDs. Mapping them to
  Vitalis facilities (and the Synthea state-wide providers to our provider table) is a deliberate
  Silver-layer exercise.
- **Simulator is calibrated, not clinical.** Defaults give ~55-90% inpatient occupancy, ED door-to-room median
  ~15 min and p90 ~1 hour. The system is tightly coupled: `ed_arrivals_per_bed_per_day` of 0.48 gives a surge
  demo, and 0.50 or above saturates and produces multi-hour ED waits. Early-warning data is for illustrating
  alerting, not a clinical tool.
- **Timestamps are UTC**, and the hour-of-day arrival curve is applied to the UTC hour.
- **Not yet exercised against live services:** `run_synthea.sh` (needs Java/network), the `eventhub` sink, and
  `upload_to_onelake.py`. Verify each on first use with small volumes.
