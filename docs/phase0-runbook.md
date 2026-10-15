# Phase 0 Runbook: Environment Setup

**Rule:** do the pre-flight list *before* starting the 60-day trial. Everything Fabric-side is rebuilt from Git.

## Pre-flight (no Fabric needed)
- [ ] Tenant settings enabled in the Admin portal: users can create Fabric items; users can try Fabric paid features; Git integration (and GitHub variant); Activator; XMLA endpoints / Analyze in Excel.
- [ ] GitHub repo `vitalis-health-fabric` created and this project pushed (branches `main` and `dev`).
- [ ] Python 3.10+ and **Java 17+** installed locally.
- [ ] `pytest -q` passes; `scripts/run_synthea.sh` and `scripts/run_all.sh` completed; `data/` populated.
- [ ] GitHub fine-grained personal access token ready (Contents: read and write, repo-scoped) for Fabric Git integration.

```bash
git init -b main && git add . && git commit -m "chore: initial project scaffold"
git remote add origin https://github.com/<you>/vitalis-health-fabric.git
git push -u origin main && git switch -c dev && git push -u origin dev
```

## Day 0: Fabric
1. Start the trial (account menu -> Start trial). Note the capacity name and region. **The 60-day clock starts now.**
2. Create the domain **Vitalis Health** (Admin portal -> Domains).
3. Create six workspaces named per `naming-conventions.md` (`vhp-*-dev`), each assigned to the trial capacity and the domain.
4. Connect each workspace to Git: Workspace settings -> Git integration -> GitHub -> repo `vitalis-health-fabric`, branch `dev`, **folder `fabric/<function>`** (matching the workspace).
5. In `vhp-lakehouse-dev` create three lakehouses: `lh_bronze`, `lh_silver`, `lh_gold`. Commit from the workspace Git panel.

## Phase 1: land data in Bronze
```bash
az login
python scripts/upload_to_onelake.py --src data/synthea_dq --workspace vhp-lakehouse-dev --lakehouse lh_bronze --dest landing/synthea
python scripts/upload_to_onelake.py --src data/claims    --workspace vhp-lakehouse-dev --lakehouse lh_bronze --dest landing/claims
python scripts/upload_to_onelake.py --src data/reference --workspace vhp-lakehouse-dev --lakehouse lh_bronze --dest landing/reference
python scripts/upload_to_onelake.py --src data/adt       --workspace vhp-lakehouse-dev --lakehouse lh_bronze --dest landing/adt_history
```
(Or drag-and-drop in the Lakehouse UI, or use OneLake file explorer.)

**Phase 1 exit criteria**
- [ ] All landing folders visible under `lh_bronze` Files.
- [ ] Bronze Delta tables created from landing files with audit columns (next build step).
- [ ] Row counts reconcile to the generator output (record them in `docs/`).
- [ ] Workspace items committed to `dev`.
