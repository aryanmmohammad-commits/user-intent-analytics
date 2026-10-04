"""Load the call-outcomes log into dev.duckdb as raw text: the 'load' step of ELT.

Run before dbt build, from the dbt project folder:
    python scripts/load_outcomes.py

log_outcome (the MCP tool) appends rows to outcomes/outcomes.csv. This script copies that
file, untouched and as text, into sales_ops.pqa_outcomes; dbt types and checks it. With no
file yet, it creates the empty table, so dbt still builds. In a company, an ELT tool would
land Salesforce task results here instead.
"""
import csv
import pathlib
import sys

import duckdb

SRC = pathlib.Path("outcomes/outcomes.csv")
COLUMNS = ["outcome_id", "logged_at_utc", "organization_id", "run_date", "outcome",
           "note", "is_test", "logged_by"]

rows = 0
if SRC.exists() and SRC.stat().st_size > 0:
    with SRC.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        if header != COLUMNS:
            sys.exit(f"STOP: {SRC} header is {header}, expected {COLUMNS}")
        rows = sum(1 for _ in reader)

con = duckdb.connect("dev.duckdb")
con.execute("create schema if not exists sales_ops")
con.execute("create or replace table sales_ops.pqa_outcomes ("
            + ", ".join(f"{c} varchar" for c in COLUMNS) + ")")
if rows:
    cols = ", ".join(COLUMNS)
    path = SRC.resolve().as_posix().replace("'", "''")
    con.execute(f"insert into sales_ops.pqa_outcomes select {cols} "
                f"from read_csv('{path}', header=true, all_varchar=true)")
loaded = con.execute("select count(*) from sales_ops.pqa_outcomes").fetchone()[0]
tests = con.execute("select count(*) from sales_ops.pqa_outcomes where lower(is_test) = 'true'").fetchone()[0]
con.close()
if loaded != rows:
    sys.exit(f"STOP: file has {rows} rows but {loaded} were loaded")
print(f"sales_ops.pqa_outcomes: {loaded} rows ({tests} test rows) from {SRC}")
