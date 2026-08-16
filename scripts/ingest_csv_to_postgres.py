"""
ingest_csv_to_postgres.py

Reads a CSV file containing gaming performance data and loads it into a local PostgreSQL database (raw.game_performance_audit table), using SCD2-style
incremental merge logic:

  - Exact duplicate rows within the same file are dropped.
  - Duplicate primary keys (bus_date, venue_code, manufacturer, fp) with
    differing data within the same file raise an error (ambiguous input).
  - New primary keys are inserted as new active rows.
  - Existing primary keys with changed business data expire the old active
    row (active_status = 'N') and insert a new active row.
  - Existing primary keys with unchanged data are left alone.

Usage:
    python ingest_csv_to_postgres.py --file "C:\path\with spaces\data.csv" --password "Password"

Arguments (both required):
    --file      Path to a single CSV file to load. Directories are not
                scanned - point this at exactly the file to process.
    --password  Postgres password for this run. No fallback - if omitted,
                the script exits with an argparse error before connecting.

Environment variables:
    PG_HOST     (default: localhost)
    PG_PORT     (default: 5432)
    PG_DB       (default: postgres)
    PG_USER     (default: postgres)
"""

import argparse
import hashlib
import os
import sys
import traceback
import uuid
from datetime import datetime

import pandas as pd
import psycopg2


PK_COLUMNS = ["bus_date", "venue_code", "manufacturer", "fp"]
BUSINESS_COLUMNS = [
    "bus_date", "venue_code", "egm_description", "manufacturer",
    "fp", "turnover_sum", "gmp_sum", "games_played_sum",
]
TABLE_NAME = "raw.game_performance_audit"


def get_connection(password: str):
    """Open a connection to the local Postgres instance.

    Host/port/db/user come from env vars (or their defaults). The password
    is required and always comes from --password - no env var fallback, so
    a run either gets an explicit password or fails at the argparse level.
    """
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "localhost"),
        port=os.getenv("PG_PORT", "5432"),
        dbname=os.getenv("PG_DB", "postgres"),
        user=os.getenv("PG_USER", "postgres"),
        password=password,
    )


def ensure_schema_and_table(conn):
    # Create the raw schema and audit table if they don't already exist.

    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS raw;")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                bus_date              DATE,
                venue_code            TEXT,
                egm_description       TEXT,
                manufacturer          TEXT,
                fp                    TEXT,
                turnover_sum          TEXT,
                gmp_sum               TEXT,
                games_played_sum      TEXT,
                ingestion_id          TEXT,
                active_from_timestamp TIMESTAMP,
                active_to_timestamp   TIMESTAMP,
                active_status         TEXT
            );
            """
        )
    conn.commit()


def clean_int_column(series: pd.Series) -> pd.Series:
    """Normalize int-like values (e.g. 20.0, '20.0') to a plain string ('20')
    so the same real-world value always stringifies identically across loads,
    regardless of whether pandas inferred the column as int64 or float64."""
    return series.apply(lambda x: '' if pd.isna(x) else str(int(float(x))))

# generates a 16 digit ingestion_id for each unique row ingested
def generate_ingestion_id() -> str:
    """16-digit unique numeric ID via hash of a UUID."""
    raw = uuid.uuid4().hex
    digest = hashlib.sha256(raw.encode()).hexdigest()
    numeric = int(digest, 16) % (10 ** 16)
    return str(numeric).zfill(16)

#added audit fields to track the timestamps and CDC pattern
def add_audit_fields(df: pd.DataFrame) -> pd.DataFrame:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df["ingestion_id"] = [generate_ingestion_id() for _ in range(len(df))]
    df["active_from_timestamp"] = now_str
    df["active_to_timestamp"] = "9999-12-31 00:00:00"
    df["active_status"] = "Y"
    return df

#drops exact row matches across all business columns
def drop_exact_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=BUSINESS_COLUMNS, keep="first").reset_index(drop=True)
    dropped = before - len(df)
    if dropped > 0:
        print(f"Dropped {dropped} exact duplicate row(s).")
    return df

#raises an error if any genuine duplicates across PK combination are present
def check_pk_duplicates(df: pd.DataFrame):
    dup_mask = df.duplicated(subset=PK_COLUMNS, keep=False)
    if dup_mask.any():
        dup_rows = df.loc[dup_mask, PK_COLUMNS]
        raise ValueError(
            f"Duplicate records found for primary key {PK_COLUMNS}:\n"
            f"{dup_rows.to_string(index=False)}"
        )

#Fetch current active (active_status = 'Y') records, keyed by PK.
def fetch_active_records(conn) -> dict:
    cols = BUSINESS_COLUMNS
    query = f"SELECT {', '.join(cols)} FROM {TABLE_NAME} WHERE active_status = 'Y'"
    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()

    active_df = pd.DataFrame(rows, columns=cols)
    active_lookup = {}
    for _, row in active_df.iterrows():
        key = tuple(str(row[c]) for c in PK_COLUMNS)
        active_lookup[key] = tuple(str(row[c]) for c in BUSINESS_COLUMNS)
    return active_lookup

#inserts new rows
def insert_row(cur, row):
    cur.execute(
        f"""
        INSERT INTO {TABLE_NAME}
        (bus_date, venue_code, egm_description, manufacturer, fp,
         turnover_sum, gmp_sum, games_played_sum,
         ingestion_id, active_from_timestamp, active_to_timestamp, active_status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            row["bus_date"], row["venue_code"], row["egm_description"], row["manufacturer"],
            row["fp"], row["turnover_sum"], row["gmp_sum"], row["games_played_sum"],
            row["ingestion_id"], row["active_from_timestamp"], row["active_to_timestamp"], row["active_status"],
        ),
    )

#deactivates changed rows
def expire_row(cur, row, now_str):
    cur.execute(
        f"""
        UPDATE {TABLE_NAME}
        SET active_status = 'N', active_to_timestamp = %s
        WHERE active_status = 'Y' AND bus_date = %s AND venue_code = %s AND manufacturer = %s AND fp = %s
        """,
        (now_str, row["bus_date"], row["venue_code"], row["manufacturer"], row["fp"]),
    )


def merge_incremental(df: pd.DataFrame, conn) -> None:
    active_lookup = fetch_active_records(conn)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur = conn.cursor()
    new_count = 0
    changed_count = 0
    unchanged_count = 0

    for _, row in df.iterrows():
        key = tuple(str(row[c]) for c in PK_COLUMNS)
        incoming_values = tuple(str(row[c]) for c in BUSINESS_COLUMNS)

        if key not in active_lookup:
            insert_row(cur, row)
            new_count += 1
        elif incoming_values != active_lookup[key]:
            expire_row(cur, row, now_str)
            insert_row(cur, row)
            changed_count += 1
        else:
            unchanged_count += 1

    conn.commit()
    cur.close()
    print(f"Incremental load: {new_count} new, {changed_count} changed, {unchanged_count} unchanged.")


def load_csv(file_path: str, conn) -> int:
    """Read the CSV, clean/validate it, and merge it into raw.game_performance_audit."""
    df = pd.read_csv(file_path)
    df.columns = [c.strip().lower() for c in df.columns]

    missing = [c for c in BUSINESS_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    df = df[BUSINESS_COLUMNS].copy()

    int_columns = ["venue_code", "fp", "games_played_sum"]
    for col in int_columns:
        df[col] = clean_int_column(df[col])

    for col in BUSINESS_COLUMNS:
        if col != "bus_date" and col not in int_columns:
            df[col] = df[col].astype(str)

    df = drop_exact_duplicates(df)
    check_pk_duplicates(df)

    df = add_audit_fields(df)
    df = df[BUSINESS_COLUMNS + ["ingestion_id", "active_from_timestamp", "active_to_timestamp", "active_status"]]

    merge_incremental(df, conn)
    return len(df)


def main():
    parser = argparse.ArgumentParser(description="Ingest a gaming performance CSV into local Postgres (SCD2 audit merge).")
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the CSV file to load.",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Postgres password.",
    )
    args = parser.parse_args()

    # Fail fast on a bad path before opening a DB connection.
    if not os.path.isfile(args.file):
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    conn = get_connection(args.password)
    try:
        ensure_schema_and_table(conn)
        row_count = load_csv(args.file, conn)
        print(f"Processed {row_count} rows from '{args.file}' into {TABLE_NAME}")
    except Exception as e:
        conn.rollback()
        print(f"ERROR: ingestion failed, no data committed: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()