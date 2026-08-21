# D3 outside-RDC footprint exclusion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Let `build-d3-inputs` run over the live 2026-08-16 Maus extract by excluding, with disclosure, Tier-1 footprints whose representative point is covered by no DPIRD-020 RDC polygon (all 20 live cases are Perth-metropolitan quarries/sand pits; the RDC layer excludes the metro area by construction).

**Architecture:** A new pure helper `d3_protocol.partition_uncovered_points` splits points into covered / uncovered ids (same `covered_by` sjoin as `assign_regions`). `build-d3-inputs` calls it BEFORE `assign_regions`; uncovered footprints are dropped from `footprint_geometry` and routed through the existing `support_not_computed_reason` channel with reason `OUTSIDE_RDC_REGIONS_REASON`, and the manifest/stdout disclosure gains `n_footprints_outside_rdc_regions` and `footprints_outside_rdc_regions` (sorted ids). A ceiling `MAX_UNCOVERED_FRACTION = 0.05` refuses if more than 5% of candidate footprints are uncovered (guards against a wrong/partial region layer). `assign_regions` keeps its refusal unchanged as the backstop. The frozen protocol (config/d3.yaml, digest) is NOT touched; this is code-side scoping, recorded in `docs/decisions/2026-08-21-d3-outside-rdc-exclusion.md`.

**Tech Stack:** Python 3.12, geopandas sjoin, typer CLI, pytest (tests/test_d3_protocol.py, tests/test_cli.py). Run tests with `uv run pytest`. Lint: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`.

---

### Task 1: `partition_uncovered_points` helper (TDD)

**Files:**
- Modify: `src/wa_mine_monitor/d3_protocol.py` (constants near the other module constants; function after `assign_regions`, ~line 296)
- Test: `tests/test_d3_protocol.py` (add after `test_assign_regions_works_with_footprint_representative_points`)

**Step 1: Write the failing tests**

```python
def test_partition_uncovered_points_splits_by_covered_by():
    regions = gpd.GeoDataFrame(
        {"region_name": ["Pilbara", "Goldfields-Esperance"]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(10, 0), (20, 0), (20, 10), (10, 10)]),
        ],
        crs="EPSG:3577",
    )
    points = gpd.GeoDataFrame(
        {"site_id": ["A", "B", "C", "D"]},
        geometry=[Point(5, 5), Point(99, 99), Point(15, 5), Point(10, 5)],  # D on shared border
        crs="EPSG:3577",
    )
    covered, uncovered = d3_protocol.partition_uncovered_points(points, regions)
    assert covered["site_id"].tolist() == ["A", "C", "D"]
    assert uncovered == ["B"]
    assert covered.crs == points.crs


def test_partition_uncovered_points_refuses_crs_mismatch():
    regions = gpd.GeoDataFrame(
        {"region_name": ["Pilbara"]}, geometry=[Polygon([(0, 0), (1, 0), (1, 1)])], crs="EPSG:3577"
    )
    points = gpd.GeoDataFrame({"site_id": ["A"]}, geometry=[Point(0.5, 0.2)], crs="EPSG:4326")
    with pytest.raises(d3_protocol.D3ProtocolError, match="CRS"):
        d3_protocol.partition_uncovered_points(points, regions)


def test_partition_uncovered_points_empty_input():
    regions = gpd.GeoDataFrame(
        {"region_name": ["Pilbara"]}, geometry=[Polygon([(0, 0), (1, 0), (1, 1)])], crs="EPSG:3577"
    )
    points = gpd.GeoDataFrame({"site_id": []}, geometry=[], crs="EPSG:3577")
    covered, uncovered = d3_protocol.partition_uncovered_points(points, regions)
    assert len(covered) == 0 and uncovered == []
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_d3_protocol.py -k partition_uncovered -q`
Expected: FAIL, `AttributeError: ... has no attribute 'partition_uncovered_points'`

**Step 3: Implement**

```python
# Decision 2026-08-21 (docs/decisions/2026-08-21-d3-outside-rdc-exclusion.md):
# footprints whose representative point is covered by no RDC polygon are
# excluded from D3 derivation with disclosure, bounded by this ceiling.
MAX_UNCOVERED_FRACTION = 0.05
OUTSIDE_RDC_REGIONS_REASON = (
    "representative point covered by no DPIRD-020 RDC polygon "
    "(excluded from D3 derivation; decision 2026-08-21)"
)


def partition_uncovered_points(
    points: gpd.GeoDataFrame, regions: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, list[str]]:
    """Split `points` into (covered subset, sorted uncovered site_ids).

    Uses the same `covered_by` predicate as `assign_regions`, so a point on a
    shared boundary counts as covered. Order of the covered frame is
    preserved. Refuses on CRS mismatch exactly like `assign_regions`.
    """
    if str(points.crs) != str(regions.crs):
        raise D3ProtocolError(f"points CRS {points.crs} != regions CRS {regions.crs}")
    if points.empty:
        return points.copy(), []
    joined = gpd.sjoin(
        points[["site_id", "geometry"]],
        regions[["region_name", "geometry"]],
        how="left",
        predicate="covered_by",
    )
    covered_ids = set(joined.loc[joined["region_name"].notna(), "site_id"].astype(str))
    mask = points["site_id"].astype(str).isin(covered_ids)
    uncovered = sorted(points.loc[~mask, "site_id"].astype(str))
    return points.loc[mask].copy(), uncovered
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_d3_protocol.py -q`
Expected: all pass (existing `assign_regions` tests unchanged).

---

### Task 2: Wire the pre-filter into `build-d3-inputs` with disclosure and ceiling (TDD)

**Files:**
- Modify: `src/wa_mine_monitor/cli.py:3847-3862` (region assignment block); `region_disclosure` is also emitted at `:4303` (resolved_args) and `:4366` (stdout payload) — no change needed there beyond the type.
- Modify: `tests/test_cli.py` — `_seed_d3_inputs_chain` (line ~1539) gains `n_outside_region: int = 0`; the regions payload at line ~1604 becomes `_d3_regions_geojson_bytes(specs[: len(specs) - n_outside_region])` so the Pilbara box excludes the LAST `n_outside_region` footprint specs (they stay in the register, crosswalk and Maus extract, i.e. remain Tier-1 candidates). Thread the kwarg through; do not change any other fixture.
- Test: `tests/test_cli.py` (add after `test_build_d3_inputs_end_to_end_over_fixtures`)

**Step 1: Write the failing tests**

```python
def test_build_d3_inputs_excludes_footprints_outside_rdc_regions(tmp_path, monkeypatch):
    """Decision 2026-08-21: a Tier-1 footprint covered by no RDC polygon is
    excluded with disclosure, not a refusal. 1 of 10 fixtures is outside
    (10%), so lift the 5% ceiling for this happy path."""
    monkeypatch.setattr("wa_mine_monitor.d3_protocol.MAX_UNCOVERED_FRACTION", 0.5)
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch, n_outside_region=1)
    data_root = tmp_path / "data"
    result = runner.invoke(
        app,
        ["build-d3-inputs", "--config", str(seed.cfg_file),
         "--protocol-config", str(seed.d3_yaml_path), "--date", "2026-08-18"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["region_ambiguity"]["n_footprints_outside_rdc_regions"] == 1
    assert payload["region_ambiguity"]["footprints_outside_rdc_regions"] == ["D3FP09"]

    out_dir = data_root / "curated" / "d3-inputs" / "2026-08-18"
    footprint_support = tables.read_table(out_dir / "footprint_support.parquet")
    row = footprint_support[footprint_support["maus_id"] == "D3FP09"].iloc[0]
    assert row["region"] is None or pd.isna(row["region"])
    assert row["support_not_computed_reason"] == d3_protocol.OUTSIDE_RDC_REGIONS_REASON
    assert not bool(row["selected"])
    # Manifest: find the run manifest beside footprint_support.parquet (check the
    # real filename with `ls` in a neighbouring test or grep run_manifest in tests/test_cli.py)
    manifest = json.loads((out_dir / "footprint_support.parquet.run_manifest.json").read_text())
    assert manifest["resolved_args"]["region_ambiguity"]["n_footprints_outside_rdc_regions"] == 1


def test_build_d3_inputs_refuses_when_too_many_footprints_outside_rdc_regions(tmp_path, monkeypatch):
    """1 of 10 outside = 10% > MAX_UNCOVERED_FRACTION (5%): refuse, naming the ids."""
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch, n_outside_region=1)
    result = runner.invoke(
        app,
        ["build-d3-inputs", "--config", str(seed.cfg_file),
         "--protocol-config", str(seed.d3_yaml_path), "--date", "2026-08-18"],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "outside" in payload["refusal"] and "D3FP09" in payload["refusal"]
    assert not (tmp_path / "data" / "curated" / "d3-inputs" / "2026-08-18").exists()
```

Adapt the manifest path/key to the real one the command writes (grep `run_manifest` / `resolved_args` near the other build-d3-inputs tests). Import `pandas as pd` and `from wa_mine_monitor import d3_protocol` at the top of tests/test_cli.py if not already imported.

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k outside_rdc -q`
Expected: FAIL (TypeError on unknown kwarg `n_outside_region`, then refusal "region is unclassified").

**Step 3: Implement in cli.py**

Replace the block at lines 3847–3862 with:

```python
    region_by_id: dict[str, str] = {}
    region_disclosure: dict[str, Any] = {
        "n_ambiguous_boundary_points": 0,
        "n_footprints_outside_rdc_regions": 0,
        "footprints_outside_rdc_regions": [],
    }
    if footprint_geometry:
        points_gdf = gpd.GeoDataFrame(
            {"site_id": list(footprint_geometry.keys())},
            geometry=[geometry.representative_point() for geometry in footprint_geometry.values()],
            crs=crosswalk.TARGET_CRS,
        )
        try:
            points_gdf, outside_ids = d3_protocol.partition_uncovered_points(points_gdf, regions_gdf)
        except d3_protocol.D3ProtocolError as exc:
            typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
            raise typer.Exit(1) from None
        # Decision 2026-08-21: exclude-with-disclosure, bounded by the ceiling.
        n_for_ceiling = len(footprint_geometry)
        if outside_ids and len(outside_ids) / n_for_ceiling > d3_protocol.MAX_UNCOVERED_FRACTION:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"{len(outside_ids)} of {n_for_ceiling} candidate footprints "
                            f"({len(outside_ids) / n_for_ceiling:.1%}) lie outside every RDC "
                            f"polygon, above the {d3_protocol.MAX_UNCOVERED_FRACTION:.0%} ceiling "
                            f"-- check the region snapshot: {outside_ids}"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1) from None
        for maus_id in outside_ids:
            footprint_geometry.pop(maus_id, None)
            shape_class_by_id.pop(maus_id, None)
            support_not_computed_reason[maus_id] = d3_protocol.OUTSIDE_RDC_REGIONS_REASON
        region_disclosure["n_footprints_outside_rdc_regions"] = len(outside_ids)
        region_disclosure["footprints_outside_rdc_regions"] = outside_ids
        if not points_gdf.empty:
            try:
                region_series, ambiguity = d3_protocol.assign_regions(
                    points_gdf, regions_gdf, protocol
                )
            except d3_protocol.D3ProtocolError as exc:
                typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
                raise typer.Exit(1) from None
            region_disclosure.update(ambiguity)
            region_by_id = dict(zip(points_gdf["site_id"], region_series, strict=True))
```

Requirements: the ceiling refusal must happen BEFORE any output directory is created (output dir creation is at ~line 4308, so it does). `region_disclosure`'s annotation changes from `dict[str, int]` to `dict[str, Any]`; fix any mypy fallout. Confirm that the later loops (line ~3917 over `footprint_geometry`, line ~4017 building footprint rows over Tier-1 ids, line ~4095 counting `support_not_computed_reason`) give an excluded footprint `region=None`, `selected=False`, and the reason string — adjust only if a test shows otherwise. `shape_class_by_id` is the dict built at line ~3839; pop from whatever it is actually called.

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli.py -k "d3" -q`
Expected: all build-d3-inputs / derive / apply tests pass including the two new ones.

---

### Task 3: Decision record and Batch D plan amendment

**Files:**
- Create: `docs/decisions/2026-08-21-d3-outside-rdc-exclusion.md`
- Modify: `docs/plans/2026-08-16-batch-d-implementation.md:39-43` — append after "…never a fallback for a point the boundaries do not explain.": "Amended 2026-08-21: such footprints are excluded from D3 derivation with disclosure (`n_footprints_outside_rdc_regions`), bounded by a 5% ceiling; see docs/decisions/2026-08-21-d3-outside-rdc-exclusion.md."

**Step 1: Write the decision record** (plain factual prose, no editorial lead-ins):

```markdown
# D3: footprints outside every RDC polygon (2026-08-21)

**Trigger.** First live `build-d3-inputs` (luminosity, Batch D live run,
Maus 2026-08-16 extract, DPIRD-020 snapshot 2026-08-21) refused:
"region is unclassified for site(s): [20 maus_ids] -- point(s) covered by
no RDC polygon". All 20 footprints are Perth-metropolitan quarries and
sand pits (Kwinana/Postans/Baldivis x9, Neerabup/Nowergup x2,
Gnangara/Bullsbrook x2, Upper Swan x2, Red Hill/Herne Hill x5), 4.8–23.9 km
outside the nearest RDC polygon (Peel or Wheatbelt). DPIRD-020 covers the
nine Regional Development Commission areas and excludes the Perth
metropolitan area by construction, so the refusal was the Batch D rule
(plan decision 1: "a point covered by NO RDC polygon is a refusal")
operating as written on a population the rule did not anticipate.

**Options considered.** (a) Exclude uncovered footprints from D3
derivation with disclosure; (b) classify uncovered onshore points as
`other_wa`; (c) add the Perth metropolitan boundary as a fourth stratum.
(b) and (c) change the frozen 2026-08-18 protocol's region semantics and
would require a new protocol lineage. (b) also violates the existing rule
that `other_wa` is a positive classification.

**Decision.** (a). `build-d3-inputs` partitions Tier-1 footprint
representative points with the same `covered_by` predicate as
`assign_regions`; uncovered footprints are removed before stratification,
carry `support_not_computed_reason = OUTSIDE_RDC_REGIONS_REASON`,
`region = null`, `selected = false` in `footprint_support.parquet`, and are
listed in the run manifest under
`region_ambiguity.footprints_outside_rdc_regions` with count
`n_footprints_outside_rdc_regions`. If more than `MAX_UNCOVERED_FRACTION`
(5%) of candidate footprints are uncovered the command refuses before
writing anything, naming the ids, because that pattern indicates a wrong
or partial region snapshot rather than metro-area sites. `assign_regions`
keeps its refusal as the backstop. The frozen protocol digest is
unchanged: this is input scoping, not a protocol parameter.

**Consequence for D3.** The D3 threshold is derived from footprints inside
the nine RDC regions only. Perth-metro footprints are not in any stratum
and receive no region-stratified threshold; `apply-d3-threshold` treats
them through the existing not-computed path. Live count at 2026-08-16:
20 of 1,753 Maus footprints (1.1%).
```

**Step 2: Verify** — `sed -n 36,46p docs/plans/2026-08-16-batch-d-implementation.md` shows the amendment.

---

### Task 4: Full battery

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
Expected: clean; pytest = 680 + 5 new = 685 passed.
