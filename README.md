# data-challenge

## Overview

This project ingests daily gaming data from CSVs into PostgreSQL using Python, then uses
dbt Core to transform it into curated tables for reporting on venue turnover, EGM revenue,
and daily performance. The Python script handles raw data, while dbt manages everything
from staging onwards.

## Tech Stack

- **Python** – Data ingestion from CSV files into PostgreSQL
- **PostgreSQL** – Local database for storing raw and transformed data
- **dbt Core** – Data transformation and modelling

## Setup process

**Prerequisites**: Python 3.8+, and a PostgreSQL instance you can reach (defaults to
`localhost:5432`, database `postgres`, user `postgres` - see below to point elsewhere).
The `raw` schema and `raw.game_performance_audit` table are created automatically on
first ingestion run if they don't already exist.

1. **Install dependencies** (pandas, psycopg2-binary, dbt-core, dbt-postgres):

   ```bash
   pip install -r requirements.txt
   ```

2. **Set up the dbt connection profile** (one-time, per machine). `profiles.yml` is
   gitignored - a fresh clone won't have it, and dbt can't connect without it:

   ```bash
   cp profiles.yml.example profiles.yml
   ```

3. **Credentials**: host/port/db/user are read from optional environment variables -
   `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER` - falling back to `localhost` / `5432` /
   `postgres` / `postgres` if unset. The password is never stored in a file or env var
   by default; it's passed per run via `--password` to `run_pipeline.py` (or
   `ingest_csv_to_postgres.py` directly), which forwards it to both the ingestion
   script and dbt (as `PG_PASSWORD`) for that run only.

## Project structure

- **Ingestion**: Python reads CSV files and loads them into a raw schema table in PostgreSQL.
- **Staging**: dbt reads the raw data and applies cleaning and data type casting in the staging model.
- **Aggregation**: Three fact models are built from the staging model for reporting.
- **Data Quality**: Generic dbt tests and two custom tests validate the transformed data.

## Running the pipeline

[`scripts/run_pipeline.py`](scripts/run_pipeline.py)

Trigger `run_pipeline.py` locally and pass the required variables. File path and password are required:

```bash
python run_pipeline.py --file <path to a CSV> --password <postgres password>
```

Optional environment variables: `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER`

### run_pipeline execution order

1. **Parse args** — requires `--file` and `--password`, no defaults.
2. **Sanity check** — confirms `profiles.yml` exists in the dbt project dir (`DBT_PROJECT_DIR`, derived from the script's own location: `scripts/../`); exits early with a clear message if it's missing.
3. **Build an environment dict** for the dbt steps: copies your current env, then sets `DBT_PROFILES_DIR` (so dbt finds that `profiles.yml`) and `PG_PASSWORD` (from `--password`).
4. **Runs each step via `subprocess.run`**, in order, printing a `=== step name ===` header and the exact command before each:
   - `python ingest_csv_to_postgres.py --file ... --password ...`
   - `dbt run --select stg_game_performance`
   - `dbt run --select fact_venue_turnover`
   - `dbt run --select fact_egm_venue_revenue`
   - `dbt run --select fact_daily_summary`
   - `dbt docs generate`
   - `dbt test --exclude check_positive_turnover check_positive_games_played`
   - `dbt test --select check_positive_turnover check_positive_games_played`

The `check_positive_*` tests are excluded from the main test run because they are expected
to fail against the current source data. In order to complete execution of all models, these
tests are run at the end instead, for this challenge's purposes only.

## Data transformations & quality checks

### Source data

[`data/`](data)

- Original file provided: `Data Engineer Challenge_input.csv`
- Created a dummy data file to test incremental ingestion: `Incremental_Data Engineer Challenge_input.csv`

The `Incremental_Data Engineer Challenge_input.csv` file covers 4 cases:

- Unchanged existing rows (these rows are ignored during ingestion)
- Addition of completely new rows (this inserts new rows into the existing table)
- Addition of a back-dated row (inserts it as a new record with the latest timestamp)
- Change in a value of an older row (inactivates the old row and creates a new active row)

This replicates SCD2-style ingestion, with audit columns added to trace the data timestamp.

**Primary key**: composite PK of `bus_date`, `venue_code`, `manufacturer`, and `fp`.

### Data checks in the ingestion script

[`scripts/ingest_csv_to_postgres.py`](scripts/ingest_csv_to_postgres.py)

- Ensures the schema and table exist
- Drops rows that are exact duplicates
- Checks for duplicate rows based on the PKs and throws an error if any are found
- Takes in the entire raw data as text (data types are handled in dbt)
- Checks against the existing data in Postgres to apply the CDC pattern above

### Data checks for dbt models

[`models/`](models)

Two schemas are created: `staging` and `aggregation`.

**Staging**

- Contains an incremental model that brings in only the active data from the raw schema in Postgres (CSV data is loaded into `raw`)
- This table includes casting the data into the correct data types
- Any data type mismatch is caught here, before it can populate dependent tables

**Aggregation (transform in dbt)**

Contains 3 models, as per the data challenge doc:

1. `fact_venue_turnover` — sum of turnover per venue
2. `fact_egm_venue_revenue` — sum of revenue per EGM per venue
3. `fact_daily_summary` — sum of turnover and revenue per day (incremental model)

There are no data transformations in this layer, only aggregations.

### Tests

[`tests/`](tests)

- Two custom tests:
  1. `check_positive_games_played`
  2. `check_positive_turnover`
- Not-null tests covered in [`models/staging/schema.yml`](models/staging/schema.yml)
- Did not implement a date test, since the date is already cast in the staging table
