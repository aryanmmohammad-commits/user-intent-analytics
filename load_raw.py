from pathlib import Path
import duckdb

DATA = Path("data")  # the folder that holds source/ and raw_circleci/
TABLES = {  # schema name -> (folder, files)
    "source": ("source", ["users", "organizations", "product_events"]),
    "circleci_raw": ("raw_circleci", ["projects", "pipelines", "workflows", "jobs"]),
}

con = duckdb.connect("dev.duckdb")
for schema, (folder, files) in TABLES.items():
    con.execute(f"create schema if not exists {schema}")
    for name in files:
        path = (DATA / folder / f"{name}.csv").resolve().as_posix()
        con.execute(
            f"create or replace table {schema}.{name} as "
            f"select * from read_csv('{path}', header=true, all_varchar=true)"
        )
        n = con.execute(f"select count(*) from {schema}.{name}").fetchone()[0]
        print(f"{schema}.{name}: {n} rows")
con.close()
