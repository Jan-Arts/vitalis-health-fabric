"""Upload a local folder to a Fabric Lakehouse Files area via the OneLake ADLS Gen2 endpoint.

Prereqs: pip install -r requirements-optional.txt ; az login (or any DefaultAzureCredential source)

Example:
    python scripts/upload_to_onelake.py --src data/synthea_dq --workspace vhp-lakehouse-dev \
        --lakehouse lh_bronze --dest landing/synthea
    python scripts/upload_to_onelake.py --src data/adt --workspace vhp-lakehouse-dev \
        --lakehouse lh_bronze --dest landing/adt_history

NOTE: not yet exercised against a live tenant - verify on first run with a small folder.
Alternatives that need no code: OneLake file explorer, or drag-and-drop in the Lakehouse UI.
"""
import argparse
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--workspace", required=True, help="workspace name or GUID")
    ap.add_argument("--lakehouse", required=True, help="lakehouse name, e.g. lh_bronze")
    ap.add_argument("--dest", required=True, help="folder under Files/, e.g. landing/synthea")
    args = ap.parse_args()

    svc = DataLakeServiceClient("https://onelake.dfs.fabric.microsoft.com", credential=DefaultAzureCredential())
    fs = svc.get_file_system_client(args.workspace)
    src = Path(args.src)
    files = [p for p in src.rglob("*") if p.is_file()]
    for n, p in enumerate(files, 1):
        rel = p.relative_to(src).as_posix()
        target = f"{args.lakehouse}.Lakehouse/Files/{args.dest.strip('/')}/{rel}"
        with open(p, "rb") as fh:
            fs.get_file_client(target).upload_data(fh, overwrite=True)
        print(f"[{n}/{len(files)}] {target}")


if __name__ == "__main__":
    main()
