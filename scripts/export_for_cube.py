"""Export the marts that Cube reads to Parquet files.

Run after every dbt build, from the dbt project folder:
    python scripts/export_for_cube.py

Why Parquet instead of letting Cube open dev.duckdb:
- DuckDB allows one writer per file. dbt keeps that role; Cube never locks the file.
- Cube ships its own DuckDB version. Parquet reads the same in every version.
"""
import pathlib
import duckdb

MARTS = [
    "fct_org_intent_score",
    "fct_user_intent_score",
    "fct_org_signal_reasons",
    "user_intent_events",
]
OUT = pathlib.Path("cube/data")
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect("dev.duckdb", read_only=True)
for name in MARTS:
    found = con.execute(
        "select table_schema from information_schema.tables "
        "where table_name = ? and table_type = 'BASE TABLE'",
        [name],
    ).fetchall()
    if len(found) != 1:
        raise SystemExit(f"{name}: expected 1 table, found {len(found)}")
    schema = found[0][0]
    target = (OUT / f"{name}.parquet").as_posix()
    con.execute(f"copy {schema}.{name} to '{target}' (format parquet)")
    rows = con.execute(f"select count(*) from read_parquet('{target}')").fetchone()[0]
    print(f"{name}: {rows} rows -> {target}")
con.close()