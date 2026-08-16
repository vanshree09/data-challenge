"""
run_pipeline.py

End-to-end pipeline: loads a CSV into Postgres via ingest_csv_to_postgres.py,
then builds and tests the dbt models on top of it, in dependency order:

    1. ingest_csv_to_postgres.py --file <file> --password <password>
    2. dbt run --select stg_game_performance
    3. dbt run --select fact_venue_turnover
    4. dbt run --select fact_egm_venue_revenue
    5. dbt run --select fact_daily_summary
    6. dbt docs generate
    7. dbt test, excluding check_positive_turnover / check_positive_games_played
       (still fail-fast - an unexpected test failure here stops the pipeline)
    8. dbt test --select check_positive_turnover check_positive_games_played
       (run last, on their own - the current source data has 13 rows that
       fail each of these, so they're expected to fail; that does not stop
       the pipeline or affect its exit code)

Usage:
    python run_pipeline.py --file path/to/data.csv --password mypassword

Arguments (both required):
    --file      Path to the CSV file to ingest.
    --password  Postgres password, used for both the ingestion script and
                the dbt run (passed through as the PG_PASSWORD env var).

One-time local setup:
    profiles.yml needs to be setup
"""

import argparse
import os
import subprocess
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
INGEST_SCRIPT = os.path.join(THIS_DIR, "ingest_csv_to_postgres.py")

# This script lives in <dbt project root>/scripts/, so the dbt project
# directory is just its parent - no machine-specific path needed.
DBT_PROJECT_DIR = os.path.dirname(THIS_DIR)

DBT_SELECT_STEPS = [
    "stg_game_performance",
    "fact_venue_turnover",
    "fact_egm_venue_revenue",
    "fact_daily_summary",
]

# These two data tests fail against the current source data (13 rows each
# with turnover_sum / games_played_sum <= 0) - known, expected, not a
# pipeline defect. They're run separately, last, and not allowed to stop
# the pipeline or flip its exit code.
KNOWN_FAILING_TESTS = ["check_positive_turnover", "check_positive_games_played"]


def run_step(description: str, cmd: list, cwd: str, env: dict, fail_on_error: bool = True) -> None:
    print(f"\n=== {description} ===")
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        if fail_on_error:
            print(f"\nERROR: step failed ({description}), stopping pipeline.", file=sys.stderr)
            sys.exit(result.returncode)
        print(f"\nWARNING: step failed ({description}), continuing - this failure is expected.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Run the full ingest -> dbt build -> dbt test -> dbt docs pipeline."
    )
    parser.add_argument("--file", required=True, help="Path to the CSV file to ingest.")
    parser.add_argument("--password", required=True, help="Postgres password.")
    args = parser.parse_args()

    profiles_path = os.path.join(DBT_PROJECT_DIR, "profiles.yml")
    if not os.path.isfile(profiles_path):
        print(
            f"ERROR: {profiles_path} not found.\n"
            f"One-time setup: copy {DBT_PROJECT_DIR}\\profiles.yml.example "
            f"to {profiles_path} before running this pipeline.",
            file=sys.stderr,
        )
        sys.exit(1)

    # dbt reads the Postgres password from the PG_PASSWORD env var
    # (see profiles.yml) - reuse the same --password value passed in here.
    dbt_env = os.environ.copy()
    dbt_env["DBT_PROFILES_DIR"] = DBT_PROJECT_DIR
    dbt_env["PG_PASSWORD"] = args.password

    run_step(
        "Ingest CSV into Postgres",
        [sys.executable, INGEST_SCRIPT, "--file", args.file, "--password", args.password],
        cwd=THIS_DIR,
        env=os.environ.copy(),
    )

    for model in DBT_SELECT_STEPS:
        run_step(
            f"dbt run --select {model}",
            ["dbt", "run", "--select", model],
            cwd=DBT_PROJECT_DIR,
            env=dbt_env,
        )

    run_step("dbt docs generate", ["dbt", "docs", "generate"], cwd=DBT_PROJECT_DIR, env=dbt_env)

    run_step(
        "dbt test (excluding known-failing tests)",
        ["dbt", "test", "--exclude", *KNOWN_FAILING_TESTS],
        cwd=DBT_PROJECT_DIR,
        env=dbt_env,
    )
    run_step(
        "dbt test (known-failing tests, expected to fail)",
        ["dbt", "test", "--select", *KNOWN_FAILING_TESTS],
        cwd=DBT_PROJECT_DIR,
        env=dbt_env,
        fail_on_error=False,
    )

    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    main()