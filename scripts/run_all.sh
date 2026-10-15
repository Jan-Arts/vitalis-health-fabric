#!/usr/bin/env bash
# Generate all synthetic data. Usage: scripts/run_all.sh [--with-synthea]
# AS_OF controls the simulated "now" for the ADT stream (set it for reproducible output).
set -euo pipefail
cd "$(dirname "$0")/.."
AS_OF="${AS_OF:-2026-01-31T00:00:00Z}"

python generators/generate_reference.py

if [[ "${1:-}" == "--with-synthea" ]]; then
  scripts/run_synthea.sh
fi

PATIENT_ARG=()
if [[ -d data/synthea_raw/csv ]]; then
  python generators/inject_dq_defects.py     # -> data/synthea_dq (defective copy + manifest)
  python generators/generate_claims.py       # -> data/claims
  PATIENT_ARG=(--patients-csv data/synthea_raw/csv/patients.csv)
else
  echo "No Synthea output found (data/synthea_raw/csv). Skipping DQ injection and claims."
  echo "Run: scripts/run_synthea.sh   (needs Java 17+)"
fi

python generators/simulate_adt_stream.py --as-of "$AS_OF" --backfill-days 30 "${PATIENT_ARG[@]}"
echo "All done. Output is in ./data"
