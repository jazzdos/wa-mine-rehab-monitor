"""Tests for `wa_mine_monitor.tables` -- generic declared-Arrow-schema
parquet write/read helpers.

No `pytest.importorskip` anywhere in this file, and no fixture that could
make a case silently not run: CLAUDE.md's drift-guard rule is that a check
which only runs when an optional extra is installed, or only at a toy grid
size, is not a check -- it must fail under a bare `uv run pytest -q`. The
drift-guard case here (`test_all_null_column_keeps_its_declared_arrow_type`)
is the load-bearing test in this file: it is a regression test for exactly
the failure CLAUDE.md's schema-declaration rule names -- an all-null column
silently inferring Arrow `null` instead of pinning to its declared type.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wa_mine_monitor.tables import read_table, write_table

_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),
        pa.field("observed_at", pa.date32()),
        pa.field("count", pa.int64()),
    ]
)


def test_write_table_round_trips_a_clean_frame(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "site_id": ["A001", "A002"],
            "observed_at": [pd.Timestamp("2020-01-15").date(), pd.Timestamp("2021-06-01").date()],
            "count": [3, 7],
        }
    )
    path = tmp_path / "result.parquet"

    write_table(df, path, _SCHEMA)
    back = read_table(path)

    assert list(back["site_id"]) == ["A001", "A002"]
    assert list(back["count"]) == [3, 7]


def test_write_table_refuses_a_frame_missing_a_declared_column(tmp_path: Path) -> None:
    df = pd.DataFrame({"site_id": ["A001"], "count": [3]})
    path = tmp_path / "result.parquet"

    with pytest.raises(ValueError, match="observed_at"):
        write_table(df, path, _SCHEMA)


def test_write_table_refuses_a_frame_with_an_undeclared_extra_column(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "site_id": ["A001"],
            "observed_at": [pd.Timestamp("2020-01-15").date()],
            "count": [3],
            "mystery_column": ["not declared anywhere"],
        }
    )
    path = tmp_path / "result.parquet"

    with pytest.raises(ValueError, match="mystery_column"):
        write_table(df, path, _SCHEMA)


def test_write_table_names_every_missing_and_extra_column_in_one_message(tmp_path: Path) -> None:
    """Both directions are named in a single refusal, not just the first
    mismatch found -- a caller fixing one at a time should not have to
    re-run the write repeatedly to discover the next problem."""
    df = pd.DataFrame({"site_id": ["A001"], "unexpected": ["x"]})
    path = tmp_path / "result.parquet"

    with pytest.raises(ValueError) as excinfo:
        write_table(df, path, _SCHEMA)

    message = str(excinfo.value)
    assert "observed_at" in message
    assert "count" in message
    assert "unexpected" in message


def test_all_null_column_keeps_its_declared_arrow_type(tmp_path: Path) -> None:
    """THE drift-guard case: an entirely-null `date32` column must pin to
    `date32[day]` on disk, never infer to Arrow `null`.

    CLAUDE.md: 'A table written to disk declares its column types; it never
    infers them from the rows... DuckDB reads an Arrow `null` column as
    `INTEGER`, so two valid registries land as mutually incompatible
    tables.' `observed_at` here is entirely `None` -- e.g. a site whose
    detection date is not yet computable -- and `write_table` is given the
    schema explicitly, which is the only thing that prevents pyarrow's own
    type inference from collapsing that column to `null`.

    Deliberately reads back with `pyarrow` directly (not through
    `read_table`, and not via `pandas.read_parquet`), because the property
    under test is the type recorded IN THE PARQUET FILE ITSELF -- what any
    downstream reader (DuckDB included) actually sees on disk -- not merely
    what pandas happens to infer on its own read-back path.
    """
    df = pd.DataFrame(
        {
            "site_id": ["A001", "A002"],
            "observed_at": [None, None],
            "count": [0, 0],
        }
    )
    path = tmp_path / "result.parquet"

    write_table(df, path, _SCHEMA)

    on_disk_schema = pq.read_table(path).schema
    field_type = on_disk_schema.field("observed_at").type

    assert field_type == pa.date32()
    assert not pa.types.is_null(field_type)
