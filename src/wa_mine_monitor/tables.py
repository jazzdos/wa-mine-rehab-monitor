# Adapted from jarrah-rehab src/jarrah_rehab/envelope/io.py (the
# declared-schema write path) and src/jarrah_rehab/envelope/schemas.py (the
# schema-declaration pattern) at commit
# cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed by
# the same author. This is generic machinery only -- jarrah's envelope-domain
# schemas (ENVELOPE_REFERENCE_UNITS_SCHEMA and friends), its per-artefact
# filename vocabulary, and its vocabulary-column validation are jarrah-domain
# and are NOT ported here. Callers of this module declare their own
# `pyarrow.Schema` per artefact and call `write_table`/`read_table` directly.
"""Declared-Arrow-schema parquet write/read helpers.

CLAUDE.md's rule (this project inherits it, not just the code): a table
written to disk declares its column types; it never infers them from the
rows. Passing `schema=` explicitly to `pyarrow.Table.from_pandas` is what
makes that true in practice -- an entirely-null column pins to the schema's
declared Arrow type (e.g. `date32[day]`) instead of inferring Arrow `null`,
which downstream engines can read as an unrelated type (jarrah-rehab
measured DuckDB reading an inferred-null column as `INTEGER`).

`write_table` also names every column mismatch loudly rather than silently
selecting a subset or padding with nulls: a frame missing a declared column,
or carrying a column the schema does not declare, fails the write with both
lists named, so a caller sees exactly what to fix rather than discovering a
silently dropped or silently null-padded column downstream.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def write_table(df: pd.DataFrame, path: Path, schema: pa.Schema) -> None:
    """Write `df` to `path` as parquet, conformed to `schema`.

    Refuses (`ValueError`) when `df`'s columns and `schema`'s declared
    columns are not exactly the same set, naming every missing and every
    extra column in the message -- a partial match is not accepted
    silently in either direction.

    `df` is reordered to `schema`'s declared column order and passed to
    `pyarrow.Table.from_pandas` WITH `schema=` given explicitly, which is
    what pins an entirely-null column to its declared Arrow type instead of
    letting pyarrow infer `null` from an all-missing pandas column.
    """
    declared = list(schema.names)
    declared_set = set(declared)
    present_set = set(df.columns)

    missing = sorted(declared_set - present_set)
    extra = sorted(present_set - declared_set)
    if missing or extra:
        raise ValueError(
            f"frame does not match the declared schema for {Path(path).name}: "
            f"missing column(s) {missing}, extra (undeclared) column(s) {extra}; "
            f"declared columns are {declared}"
        )

    ordered = df.loc[:, declared]
    table = pa.Table.from_pandas(ordered, schema=schema, preserve_index=False)

    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, resolved)


def read_table(path: Path) -> pd.DataFrame:
    """Read a parquet file written by `write_table` back into a DataFrame."""
    return pq.read_table(Path(path)).to_pandas()
