"""Create the database (if missing), apply schema.sql and optionally seed.sql.
Usage:  python scripts/init_db.py            (schema + demo data)
        python scripts/init_db.py --no-seed  (schema only)
        python scripts/init_db.py --reset    (DROP all tables first -- demo reset, asks for confirmation)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2
from psycopg2 import sql

import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_file(conn, name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f, conn.cursor() as cur:
        cur.execute(f.read())
    conn.commit()
    print(f"applied {name}")


def main():
    args = set(sys.argv[1:])
    name = config.db_params()["dbname"]
    if str(config.get("DB_SKIP_CREATE", "0")) != "1":   # set DB_SKIP_CREATE=1 for hosted databases that already exist
        admin = psycopg2.connect(**config.db_params("postgres"))
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
            if not cur.fetchone():
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                print(f"created database {name}")
        admin.close()
    conn = psycopg2.connect(**config.db_params())
    if "--reset" in args:
        if input(f"This DELETES ALL DATA in '{name}'. Type YES to continue: ") != "YES":
            print("cancelled"); return
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
    run_file(conn, "schema.sql")
    if "--no-seed" not in args:
        run_file(conn, "seed.sql")
    conn.close()
    print("done")


if __name__ == "__main__":
    main()
