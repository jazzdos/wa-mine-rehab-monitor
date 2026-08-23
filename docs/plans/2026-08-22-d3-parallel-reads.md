# build-d3-inputs parallel raster reads Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Cut `build-d3-inputs` wall-clock on luminosity from ~1.5–2 days to hours by reading footprint-year rasters concurrently, with outputs byte-identical to the serial path.

**Architecture:** Measured on luminosity 2026-08-22: one asset open+window read costs ~0.25 s of round-trip latency (link capacity 15 MB/s, achieved 3 MB/s, 14% CPU); GDAL env tuning (`CPL_VSIL_CURL_CHUNK_SIZE`, `GDAL_INGESTED_BYTES_AT_OPEN`, HTTP/2 multiplex) made it slower. The only lever is concurrency. Phase A and Phase B each loop, per footprint, over (year, source_id) and call `_read_footprint_year_bands` serially. The change: per footprint, submit every (year, source_id) read to a `ThreadPoolExecutor`, then consume the results **in the original serial order** — extraction rows are appended, ETags checked, and simulations run exactly as before, so every parquet table (including `extraction_assets.parquet` row order) and every refusal message is unchanged. Refusals: the first failing job **in serial order** is re-raised (not the first to fail in time). A `--read-workers` option (default 8, minimum 1) is disclosed in `resolved_args`; `--read-workers 1` goes through the same pool code. rasterio releases the GIL during reads and each thread opens its own dataset handles, so no shared state is touched in threads.

**Tech Stack:** Python 3.12, `concurrent.futures.ThreadPoolExecutor` (already used in `http.py:205`), typer, rasterio, pytest. Tests: `uv run pytest`; lint: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`.

---

### Task 1: `_run_reads_in_serial_order` helper (TDD)

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` — add the helper directly after `_read_footprint_year_bands` (~line 3475). Add `from concurrent.futures import ThreadPoolExecutor` to the imports if not present.
- Test: `tests/test_cli.py` — add a small unit test near the other build-d3-inputs tests (~line 1657).

**Step 1: Write the failing test**

```python
def test_run_reads_in_serial_order_preserves_order_and_first_error():
    from wa_mine_monitor import cli as cli_mod

    calls: list[int] = []

    def make(i):
        def job():
            calls.append(i)
            if i in (2, 4):
                raise d3_inputs.D3InputsError(f"job {i} failed")
            return i * 10
        return job

    # All succeed: results come back in submission order regardless of workers.
    out = cli_mod._run_reads_in_serial_order([make(0), make(1), make(3)], workers=4)
    assert out == [0, 10, 30]

    # Two failures: the FIRST IN SERIAL ORDER (job 2) is raised, not job 4.
    with pytest.raises(d3_inputs.D3InputsError, match="job 2 failed"):
        cli_mod._run_reads_in_serial_order([make(0), make(2), make(4)], workers=4)

    # workers=1 uses the same path.
    assert cli_mod._run_reads_in_serial_order([make(5)], workers=1) == [50]
    with pytest.raises(ValueError, match="read_workers"):
        cli_mod._run_reads_in_serial_order([make(5)], workers=0)
```

(`d3_inputs` is importable in tests/test_cli.py — add `from wa_mine_monitor import d3_inputs` at the top if missing.)

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k run_reads_in_serial_order -q`
Expected: FAIL, `AttributeError: module ... has no attribute '_run_reads_in_serial_order'`

**Step 3: Implement**

```python
def _run_reads_in_serial_order(
    jobs: Sequence[Callable[[], T]], *, workers: int
) -> list[T]:
    """Run `jobs` concurrently on a thread pool and return their results in
    SUBMISSION order. If any job raised, the exception of the FIRST failing
    job in submission order is re-raised (after every job has finished), so
    refusal text never depends on thread timing. `workers=1` takes the same
    path. Used for raster reads, which are round-trip-latency bound."""
    if workers < 1:
        raise ValueError(f"read_workers must be >= 1, got {workers}")
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futures = [pool.submit(job) for job in jobs]
        results: list[T] = []
        first_error: BaseException | None = None
        for future in futures:
            try:
                results.append(future.result())
            except BaseException as exc:  # noqa: BLE001 -- re-raised below in serial order
                if first_error is None:
                    first_error = exc
    if first_error is not None:
        raise first_error
    return results
```

Use `from collections.abc import Callable, Sequence` and `T = TypeVar("T")` (`from typing import TypeVar`) — follow whatever typing idiom cli.py already uses (check its imports first).

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli.py -k run_reads_in_serial_order -q` → PASS. `uv run mypy src` clean.

---

### Task 2: `--read-workers` option and parallel Phase A / Phase B reads (TDD)

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` — `build_d3_inputs_cmd` signature (~line 3477), Phase A loop (~lines 4011–4053), Phase B loop (~lines 4157–4260), `resolved_args` (~line 4298).
- Test: `tests/test_cli.py` — add after `test_build_d3_inputs_end_to_end_over_fixtures`.

**Step 1: Write the failing test**

```python
def test_build_d3_inputs_parallel_reads_are_byte_identical_to_serial(tmp_path, monkeypatch):
    """--read-workers changes wall-clock only: every output table must be
    byte-identical between 1 and 4 workers, and the manifest discloses it."""
    import hashlib

    digests: dict[int, dict[str, str]] = {}
    for workers in (1, 4):
        root = tmp_path / f"w{workers}"
        root.mkdir()
        seed = _seed_d3_inputs_chain(root, monkeypatch)
        result = runner.invoke(
            app,
            ["build-d3-inputs", "--config", str(seed.cfg_file),
             "--protocol-config", str(seed.d3_yaml_path), "--date", "2026-08-18",
             "--read-workers", str(workers)],
        )
        assert result.exit_code == 0, result.output
        out_dir = root / "data" / "curated" / "d3-inputs" / "2026-08-18"
        digests[workers] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out_dir.glob("*.parquet"))
        }
        manifest = json.loads(
            (out_dir / "footprint_support.parquet.run_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["resolved_args"]["read_workers"] == workers
    assert len(digests[1]) == 5
    assert digests[1] == digests[4]


def test_build_d3_inputs_refuses_read_workers_below_one(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        ["build-d3-inputs", "--config", str(seed.cfg_file),
         "--protocol-config", str(seed.d3_yaml_path), "--date", "2026-08-18",
         "--read-workers", "0"],
    )
    assert result.exit_code != 0
    assert not (tmp_path / "data" / "curated" / "d3-inputs" / "2026-08-18").exists()
```

Check the real run-manifest filename the command writes (`manifest_paths` ~line 4366 / `tables.write_table` calls ~line 4360) and adjust the path if it differs. If `_seed_d3_inputs_chain` cannot be called twice with the same `monkeypatch` (it sets `_REPO_ROOT`), that is fine — the second call simply re-sets it. If parquet bytes legitimately differ between runs because of embedded timestamps/metadata, compare `tables.read_table(...).to_dict("records")` per file instead and say so in the test docstring.

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k "parallel_reads or read_workers_below" -q`
Expected: FAIL (`No such option: --read-workers`).

**Step 3: Implement**

(a) Option. Next to `ProtocolConfigOption` add:

```python
ReadWorkersOption = typer.Option(
    8,
    "--read-workers",
    min=1,
    help="Concurrent raster reads per footprint (round-trip-latency bound; wall-clock only, outputs identical).",
)
```

and add `read_workers: int = ReadWorkersOption` to `build_d3_inputs_cmd`. typer enforces `min=1` (exit code 2 before any gate runs), which satisfies the refusal test.

(b) Phase A. Replace the inner `for year in sorted(candidate_years): ... for source_id, kind in ...: ... _read_footprint_year_bands(...)` structure with: first build the ordered job list, then run it, then consume in the same order:

```python
            read_keys: list[tuple[int, str, str]] = []
            read_jobs: list[Callable[[], tuple[dict[str, np.ndarray], list[dict[str, object]]]]] = []
            for year in sorted(candidate_years):
                for source_id, kind in d3_inputs.D3_COLLECTION_KIND.items():
                    if not all((source_id, tile_id, year) in item_index for tile_id in touched):
                        continue
                    read_keys.append((year, source_id, kind))
                    read_jobs.append(
                        functools.partial(
                            _read_footprint_year_bands,
                            source_id=source_id,
                            kind=kind,
                            year=year,
                            touched_tiles=touched,
                            members=members,
                            item_index=item_index,
                            phase="a",
                        )
                    )
            try:
                read_results = _run_reads_in_serial_order(read_jobs, workers=read_workers)
            except (rasterio.errors.RasterioError, OSError, d3_inputs.D3InputsError) as exc:
                typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
                raise typer.Exit(1) from None
            by_year_source: dict[int, dict[str, bool]] = {}
            for (year, source_id, kind), (raw_bands, extraction_rows) in zip(
                read_keys, read_results, strict=True
            ):
                phase_a_extraction_rows.extend(extraction_rows)
                decoded = _decode_d3_bands(raw_bands, kind=kind)
                by_year_source.setdefault(year, {})[source_id] = d3_inputs.year_computable(
                    decoded, kind=kind
                )
            for year in sorted(candidate_years):
                by_source = by_year_source.get(year, {})
                computable_by_footprint[maus_id][year] = by_source
                fc_ok = by_source.get("dea_fc_pc", False)
                gm_ok = any(by_source.get(s, False) for s in _GEOMEDIAN_SOURCES)
                if fc_ok and gm_ok:
                    n_full += 1
                else:
                    n_footprint_years_not_computable += 1
```

Keep the surrounding `if effective_support[maus_id] >= ... and candidate_years:` guard and the `n_full_support_by_id[maus_id] = n_full` tail exactly as they are. Note the original code sets `computable_by_footprint[maus_id][year] = by_source` for EVERY candidate year, including years with no readable source (empty dict) — the rewrite above preserves that via `by_year_source.get(year, {})`.

(c) Phase B. Same pattern: build `read_keys`/`read_jobs` over `for year in full_support_years: for source_id, kind in D3_COLLECTION_KIND.items(): if by_source.get(source_id): ...` with `phase="b"`, run `_run_reads_in_serial_order`, then iterate `zip(read_keys, read_results, strict=True)` running the existing body unchanged (ETag check → `phase_b_extraction_rows.extend` → `_decode_d3_bands` → `simulate_footprint_year` → rows/reduced_series bookkeeping). The ETag refusal (`typer.Exit(1)`) stays inside the consuming loop.

(d) Disclosure: add `"read_workers": read_workers,` to `resolved_args` (~line 4298) and to the stdout payload dict (~line 4355) next to `n_candidate_footprints`.

Imports: `functools`, `Callable` — add if missing. Do not change `_read_footprint_year_bands` itself.

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli.py -k "d3 or run_reads" -q` → all pass, including the byte-identity test.
Then: `uv run ruff check src tests && uv run ruff format src/wa_mine_monitor/cli.py tests/test_cli.py && uv run mypy src`.

---

### Task 3: Runbook note

**Files:**
- Modify: `docs/plans/2026-08-21-batch-d-live-run-and-batch-e-e3.md` — in section B4, after the `build-d3-inputs` command line (~line 110), add one sentence: "Pass `--read-workers 8` (default) on luminosity; measured 2026-08-22: serial reads are ~0.25 s round-trip each (≈1.5–2 days for Phase A+B), link capacity 15 MB/s. Required env for the public DEA bucket: `AWS_NO_SIGN_REQUEST=YES AWS_REGION=ap-southeast-2`; use `CPL_VSIL_CURL_CACHE_SIZE=1073741824 GDAL_CACHEMAX=1024` — the curl cache is RAM (50 GB OOM-killed the first run)." Plain prose, match the file's line width.

**Step: Verify** — `sed -n 104,122p docs/plans/2026-08-21-batch-d-live-run-and-batch-e-e3.md`.

---

### Task 4: Full battery

Run: `uv run ruff check . && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean; pytest = 691 + 3 new = 694 passed.
