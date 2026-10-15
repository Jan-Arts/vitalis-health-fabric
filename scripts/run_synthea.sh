#!/usr/bin/env bash
# Generate Synthea patients (CSV + FHIR). Requires Java 17+ and git.
# Output: data/synthea_raw/csv/*.csv and data/synthea_raw/fhir/*.json
# Reproducible: fixed seeds and a fixed reference date.
set -euo pipefail

POP="${POP:-30000}"            # alive patients to generate
SEED="${SEED:-42}"
REF="${REF:-20260101}"         # reference date YYYYMMDD
STATE="${STATE:-Massachusetts}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$ROOT/data/synthea_work"
OUT="$ROOT/data/synthea_raw"
mkdir -p "$WORK" "$OUT"

[ -d "$WORK/synthea" ] || git clone --depth 1 https://github.com/synthetichealth/synthea.git "$WORK/synthea"
cd "$WORK/synthea"

./run_synthea -p "$POP" -s "$SEED" -cs "$SEED" -r "$REF" \
  --exporter.baseDirectory="$OUT" \
  --exporter.csv.export=true \
  --exporter.fhir.export=true \
  --exporter.years_of_history=5 \
  "$STATE"

echo "Done. CSVs in $OUT/csv"
