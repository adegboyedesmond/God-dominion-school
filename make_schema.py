"""Writes supabase_schema.sql — the full table structure for Postgres/Supabase.
You don't need to run it: the app creates the tables itself on first start.
It is here if you prefer to create tables in the Supabase SQL editor, or to see the structure."""
import os
os.environ.setdefault("DATABASE_URL", "")
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import postgresql
from app import db
out = ["-- God Dominion School portal: Supabase / Postgres schema", "-- Paste into Supabase → SQL Editor → Run (optional; the app also creates these automatically)", ""]
for t in db.metadata.sorted_tables:
    out.append(str(CreateTable(t, if_not_exists=True).compile(dialect=postgresql.dialect())).strip() + ";\n")
    for ix in t.indexes:
        out.append(str(CreateIndex(ix).compile(dialect=postgresql.dialect())).strip() + ";\n")
open("supabase_schema.sql", "w").write("\n".join(out))
print("wrote supabase_schema.sql with", len(db.metadata.sorted_tables), "tables")
