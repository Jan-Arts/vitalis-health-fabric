# Naming Conventions

Prefix **`vhp`** (Vitalis Health Platform). Lowercase, snake_case or kebab-case as noted. No spaces.

## Workspaces (`vhp-{function}-{env}`)
| Workspace | Purpose |
|---|---|
| `vhp-ingest-{env}` | Pipelines, Dataflows Gen2, Eventstream |
| `vhp-lakehouse-{env}` | Bronze/Silver/Gold lakehouses and transformation notebooks |
| `vhp-warehouse-{env}` | Finance warehouse |
| `vhp-realtime-{env}` | Eventhouse, KQL, Activator |
| `vhp-ds-{env}` | ML notebooks, experiments, models |
| `vhp-reports-{env}` | Semantic models and reports |

`{env}` = `dev` | `prod` (`test` added later if time allows). Domain: **Vitalis Health**.

## Items (prefix + snake_case)
| Item | Pattern | Example |
|---|---|---|
| Lakehouse | `lh_{layer}` | `lh_bronze`, `lh_silver`, `lh_gold` |
| Warehouse | `wh_{domain}` | `wh_finance` |
| Notebook | `nb_{layer}_{entity}` | `nb_silver_encounter` |
| Pipeline | `pl_{purpose}` | `pl_orchestrate_daily`, `pl_ingest_synthea` |
| Dataflow Gen2 | `df_{purpose}` | `df_ref_facilities` |
| Eventstream | `es_{stream}` | `es_adt_vitals` |
| Eventhouse / KQL DB | `eh_{name}` / `kqldb_{name}` | `eh_operations`, `kqldb_adt` |
| Semantic model | `sm_{subject}` | `sm_vitalis_clinical` |
| Report | `rpt_{subject}` | `rpt_executive_scorecard` |
| ML experiment / model | `exp_{name}` / `mdl_{name}` | `exp_readmission`, `mdl_readmission_30d` |
| Activator | `act_{name}` | `act_bed_capacity` |

## Tables and columns
- **Bronze:** `{source}_{table}` as received, plus audit columns `_ingest_ts`, `_source_file`, `_batch_id`.
- **Silver:** singular entity names: `patient`, `encounter`, `condition`, `claim`, `patient_master`.
- **Gold:** `dim_*`, `fact_*`, `bridge_*`. Surrogate keys `{entity}_key`, business keys `{entity}_id`.
- **Data quality:** `dq_rule_catalog`, `dq_results`. **Ops:** `etl_run_log`, `etl_config`.
- Columns are `snake_case`. Booleans start with `is_`/`has_`. Timestamps end `_ts`, dates end `_date`.

## Git
- Repo: `vitalis-health-fabric`. Branches: `main` (prod), `dev` (integration), `feature/{short-name}`.
- Each workspace syncs to `fabric/{function}/` on the `dev` branch. Prod workspaces sync to `main`.
- Commits: `type(scope): message`, e.g. `feat(silver): add encounter dedupe`. Types: feat, fix, docs, refactor, test, chore.
