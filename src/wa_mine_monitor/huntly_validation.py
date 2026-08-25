"""Validate the monitor's own zonal engine against the jarrah-rehab Huntly
pilot cube (D13 E5).

E5 does NOT compare monitor output against DEA data, and it does NOT
compare against the jarrah plot footprint mean the draft of this plan
assumed. The 2026-08-25 owner decision
(`docs/decisions/2026-08-25-e5-engine-parity-rescope.md`, amendment A7 in
`docs/amendments-and-limitations.md`) found that comparison geometrically
impossible: a single Maus footprint of ~370 km^2 (411,895 px) covers the
jarrah Huntly plots, so a whole-footprint mean and a 9-pixel plot mean
cannot agree at `|Delta| <= 1e-6` under any parameter choice.

Instead E5 is an ENGINE test on the PILOT CUBE, exactly as design doc
Sec.10 describes: `sample_pilot_cube` reads jarrah's own annual composite
COGs -- the SAME rasters `HUNTLY_REFERENCE_SCHEMA`'s reference table was
built from -- at jarrah's own site points, with the monitor's own
3x3-window zonal mean. Same rasters, same pixels, same formula, so the
D13 E5 tolerances (`Tolerances`) are correct by construction and are
confirmed, not merely declared, by that decision. Both sides key on
jarrah's own `site_id` directly -- there is no cross-project site
mapping to supply -- and fractional-cover values compare UNSCALED: same
rasters, same units on both sides. A failure this module reports is
therefore a real defect in the monitor's zonal reduction, which is the
only thing this gate can usefully protect.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

#: The declared schema of the jarrah reference table
#: (`probe-out/detection_estimand/series_incumbent_w1.parquet` as at
#: 2026-08-22). Declared here rather than inferred so a reference whose
#: shape changed is a refusal, not a silently different comparison.
HUNTLY_REFERENCE_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.large_string(), nullable=False),
        pa.field("year", pa.int64(), nullable=False),
        pa.field("bare", pa.float64(), nullable=True),
        pa.field("pv", pa.float64(), nullable=True),
        pa.field("npv", pa.float64(), nullable=True),
        pa.field("nbr", pa.float64(), nullable=True),
        pa.field("ndmi", pa.float64(), nullable=True),
        pa.field("ndvi", pa.float64(), nullable=True),
    ]
)

#: Monitor metric name (`trajectories.METRICS`) -> reference/sampled column.
#: `ndvi` is present in the reference and deliberately NOT compared: the
#: monitor's frozen D3 metric vocabulary does not contain it, and
#: comparing a metric this pipeline never computes would be theatre.
REFERENCE_METRIC_COLUMNS: dict[str, str] = {
    "nbr": "nbr",
    "ndmi": "ndmi",
    "bare_soil": "bare",
    "photosynthetic_vegetation": "pv",
    "non_photosynthetic_vegetation": "npv",
}

#: FC metrics only: the tolerance for these is `Tolerances.fc_abs`
#: (percentage points), not `Tolerances.spectral_abs`. Reference and
#: extracted FC values are otherwise treated identically -- unscaled.
_FC_METRICS = frozenset({"bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"})

#: Bands read from `nbart/nbart_<year>.tif`, by GeoTIFF description name.
_NBART_BANDS: tuple[str, ...] = ("nbr", "ndmi", "ndvi")

#: Bands read from `fractional_cover/fractional_cover_<year>.tif`, by
#: GeoTIFF description name.
_FC_BANDS: tuple[str, ...] = ("bare", "pv", "npv")


class HuntlyValidationError(ValueError):
    """A reference cube, pilot-cube sample, or comparison that cannot be
    trusted."""


def read_reference_cube(path: Path) -> pd.DataFrame:
    """Read the jarrah per-site-year series table, refusing any table that
    does not carry every declared column."""
    resolved = Path(path)
    if not resolved.exists():
        raise HuntlyValidationError(f"reference cube {resolved} does not exist")
    try:
        table = pq.read_table(resolved)
    except (OSError, pa.ArrowInvalid) as exc:
        raise HuntlyValidationError(f"cannot read reference cube {resolved}: {exc}") from None
    missing = [name for name in HUNTLY_REFERENCE_SCHEMA.names if name not in table.column_names]
    if missing:
        raise HuntlyValidationError(
            f"reference cube {resolved} is missing column(s) {missing}; expected "
            f"{HUNTLY_REFERENCE_SCHEMA.names}"
        )
    frame = table.select(HUNTLY_REFERENCE_SCHEMA.names).to_pandas()
    frame["site_id"] = frame["site_id"].astype(str)
    frame["year"] = frame["year"].astype("int64")
    return frame


def _years_available(directory: Path, prefix: str) -> set[int]:
    """Years with a `<prefix><year>.tif` file under `directory`. An absent
    directory has no years -- never an error, since a metric group is
    allowed to be missing entirely for a given composites tree."""
    if not directory.exists():
        return set()
    years: set[int] = set()
    for candidate in directory.glob(f"{prefix}*.tif"):
        stem = candidate.name[len(prefix) : -len(".tif")]
        if stem.isdigit():
            years.add(int(stem))
    return years


def _band_index(dataset: object, band_name: str, path: Path) -> int:
    """The 1-based rasterio band index whose GeoTIFF description matches
    `band_name`. Bands are addressed by NAME, never position: a band
    reorder upstream must not silently corrupt the comparison."""
    descriptions = dataset.descriptions  # type: ignore[attr-defined]
    try:
        return descriptions.index(band_name) + 1
    except ValueError:
        raise HuntlyValidationError(
            f"{path} has no band described {band_name!r}; band descriptions are "
            f"{list(descriptions)}"
        ) from None


def _sample_band(
    dataset: object, band_name: str, path: Path, x: float, y: float, window: int
) -> tuple[float, int, int]:
    """Sample one band's `window x window` block centred on the pixel
    containing `(x, y)`, clipped to the raster bounds (never padded).

    Returns `(value, n_member_pixels, n_valid_pixels)`. `value` is the
    mean over non-NaN members, or NaN if every member is NaN -- that
    band's metric is not-computable, independent of every other band.
    """
    from rasterio.windows import Window  # type: ignore[import-untyped]

    band_index = _band_index(dataset, band_name, path)
    row, col = dataset.index(x, y)  # type: ignore[attr-defined]
    half = window // 2
    row_lo = max(0, row - half)
    row_hi = min(dataset.height - 1, row + half)  # type: ignore[attr-defined]
    col_lo = max(0, col - half)
    col_hi = min(dataset.width - 1, col + half)  # type: ignore[attr-defined]
    if row_hi < row_lo or col_hi < col_lo:
        raise HuntlyValidationError(f"site point ({x}, {y}) falls entirely outside {path}'s bounds")
    read_window = Window(
        col_off=col_lo,
        row_off=row_lo,
        width=col_hi - col_lo + 1,
        height=row_hi - row_lo + 1,
    )
    block = dataset.read(band_index, window=read_window).astype(np.float64)  # type: ignore[attr-defined]
    n_member = int(block.size)
    valid = ~np.isnan(block)
    n_valid = int(valid.sum())
    value = float(np.nanmean(block)) if n_valid > 0 else float("nan")
    return value, n_member, n_valid


def sample_pilot_cube(
    composites_dir: Path,
    sites: pd.DataFrame,
    *,
    window: int = 3,
) -> pd.DataFrame:
    """Sample the jarrah pilot cube with the monitor's own zonal engine.

    One row per (site_id, year) present in `sites` and the composite
    years found on disk, carrying `HUNTLY_REFERENCE_SCHEMA`'s value
    columns plus `n_member_pixels` / `n_valid_pixels`.

    Reads `nbart/nbart_<year>.tif` (bands `nbr`/`ndmi`/`ndvi`) and
    `fractional_cover/fractional_cover_<year>.tif` (bands
    `bare`/`pv`/`npv`) under `composites_dir`, EPSG:3577, 30 m, masked
    pixels `NaN`, **bands addressed by their GeoTIFF description name**
    -- never by index, so a band reorder upstream cannot silently corrupt
    the comparison.

    Member set: the `window x window` block centred on the pixel
    containing `(x, y)`, clipped to the raster bounds. Value: the mean
    over the block's non-`NaN` members. An all-`NaN` block is
    not-computable for THAT metric only, never for the whole row.

    A year with no composite file on disk (neither `nbart_<year>.tif`
    nor `fractional_cover_<year>.tif`) is an absent year, never a
    synthesised or zero row -- the same discipline the reference table
    itself applies. `n_member_pixels` is the window's clipped pixel
    count (geometric, shared by every band read for the row, since both
    composite trees share the pilot cube's 30 m grid); `n_valid_pixels`
    is the count of pixels valid across every band actually read for
    that row -- a conservative, single, row-level count that coexists
    with per-metric not-computable values.

    `sites` must carry `site_id`, `x`, `y` in EPSG:3577
    (`crosswalk.TARGET_CRS`).
    """
    import rasterio  # type: ignore[import-untyped]

    composites_dir = Path(composites_dir)
    nbart_dir = composites_dir / "nbart"
    fc_dir = composites_dir / "fractional_cover"
    years = sorted(
        _years_available(nbart_dir, "nbart_") | _years_available(fc_dir, "fractional_cover_")
    )

    rows: list[dict[str, object]] = []
    for year in years:
        nbart_path = nbart_dir / f"nbart_{year}.tif"
        fc_path = fc_dir / f"fractional_cover_{year}.tif"
        nbart_ds = rasterio.open(nbart_path) if nbart_path.exists() else None
        fc_ds = rasterio.open(fc_path) if fc_path.exists() else None
        try:
            for site in sites.itertuples(index=False):
                row: dict[str, object] = {
                    "site_id": str(site.site_id),
                    "year": year,
                    "bare": float("nan"),
                    "pv": float("nan"),
                    "npv": float("nan"),
                    "nbr": float("nan"),
                    "ndmi": float("nan"),
                    "ndvi": float("nan"),
                    "n_member_pixels": 0,
                    "n_valid_pixels": 0,
                }
                member_counts: list[int] = []
                valid_counts: list[int] = []
                if nbart_ds is not None:
                    for band_name in _NBART_BANDS:
                        value, n_member, n_valid = _sample_band(
                            nbart_ds, band_name, nbart_path, site.x, site.y, window
                        )
                        row[band_name] = value
                        member_counts.append(n_member)
                        valid_counts.append(n_valid)
                if fc_ds is not None:
                    for band_name in _FC_BANDS:
                        value, n_member, n_valid = _sample_band(
                            fc_ds, band_name, fc_path, site.x, site.y, window
                        )
                        row[band_name] = value
                        member_counts.append(n_member)
                        valid_counts.append(n_valid)
                if member_counts:
                    row["n_member_pixels"] = member_counts[0]
                    row["n_valid_pixels"] = min(valid_counts)
                rows.append(row)
        finally:
            if nbart_ds is not None:
                nbart_ds.close()
            if fc_ds is not None:
                fc_ds.close()

    return pd.DataFrame(
        rows,
        columns=[
            "site_id",
            "year",
            "bare",
            "pv",
            "npv",
            "nbr",
            "ndmi",
            "ndvi",
            "n_member_pixels",
            "n_valid_pixels",
        ],
    )


def melt_sampled_frame(sampled: pd.DataFrame) -> pd.DataFrame:
    """Melt `sample_pilot_cube`'s wide (site_id, year, <metric...>) frame
    into the long rows `compare()` iterates: one row per
    (site_id, year, metric) in the monitor's metric vocabulary
    (`REFERENCE_METRIC_COLUMNS`), carrying `value`, the row's
    `n_member_pixels` / `n_valid_pixels`, and `computable` /
    `not_computable_reason`.

    `computable` is `False`, with `not_computable_reason` set to
    `"zero_valid_pixels"` (the `spectral_metrics.NOT_COMPUTABLE_REASONS`
    vocabulary), exactly when the sampled value is NaN -- an all-NaN
    block for that one metric, never for the whole site-year. This is
    the shape the `validate-huntly` CLI feeds as `compare()`'s left-hand
    side.
    """
    rows: list[dict[str, object]] = []
    for record in sampled.to_dict("records"):
        for metric, column in REFERENCE_METRIC_COLUMNS.items():
            value = record[column]
            computable = bool(not pd.isna(value))
            rows.append(
                {
                    "site_id": record["site_id"],
                    "year": int(record["year"]),
                    "metric": metric,
                    "value": float(value) if computable else None,
                    "n_member_pixels": record["n_member_pixels"],
                    "n_valid_pixels": record["n_valid_pixels"],
                    "computable": computable,
                    "not_computable_reason": None if computable else "zero_valid_pixels",
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "site_id",
            "year",
            "metric",
            "value",
            "n_member_pixels",
            "n_valid_pixels",
            "computable",
            "not_computable_reason",
        ],
    )


@dataclass(frozen=True)
class Tolerances:
    """The E5 gate, as parameters.

    Confirmed by the 2026-08-25 engine-parity re-scope decision: because
    `sample_pilot_cube` reads the same rasters the reference table was
    built from, at the same points, with the same formula, `1e-6`
    absolute for the identical-formula spectral metrics (NBR, NDMI) and
    `0.1` percentage points for fractional cover are exact-agreement
    tolerances for a like-for-like comparison, not a fudge for a mismatch.
    Widen them only by an explicit, recorded flag -- never in code.

    `require_pixel_counts` defaults to `False`: the jarrah reference
    table (`HUNTLY_REFERENCE_SCHEMA`, as `read_reference_cube` actually
    reads it) carries no `n_member_pixels` / `n_valid_pixels` columns --
    it was never built with them -- so `compare()` against the only
    reference this module reads would REFUSE outright were the default
    `True`. Exact value agreement (`spectral_abs`, `fc_abs`) is still
    enforced unconditionally; pixel-count agreement is additional
    strictness for a reference that happens to carry counts, opted into
    explicitly with `Tolerances(require_pixel_counts=True)`, not part of
    the real `validate-huntly` path today.
    """

    spectral_abs: float = 1e-6
    fc_abs: float = 0.1
    require_pixel_counts: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "spectral_abs": self.spectral_abs,
            "fc_abs": self.fc_abs,
            "require_pixel_counts": self.require_pixel_counts,
        }


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    n_compared: int
    n_sites: int
    failures: list[dict[str, object]]
    tolerances: Tolerances

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "n_compared": self.n_compared,
            "n_sites": self.n_sites,
            "n_failures": len(self.failures),
            "failures": self.failures,
            "tolerances": self.tolerances.as_dict(),
        }


#: Reference columns carrying no per-site pixel counts at all. Named so the
#: refusal can say WHICH requirement the reference cannot satisfy.
_PIXEL_COUNT_COLUMNS: tuple[str, ...] = ("n_member_pixels", "n_valid_pixels")


def compare(
    extracted: pd.DataFrame,
    reference: pd.DataFrame,
    tolerances: Tolerances,
) -> ValidationReport:
    """Compare every extracted row against its reference site-year.

    Both sides key on jarrah's own `site_id` directly -- `sample_pilot_cube`
    samples AT jarrah's site points, so there is no cross-project site
    mapping to supply. Fractional-cover values compare UNSCALED: same
    rasters, same units on both sides.

    Every extracted row is accounted for: it either matches inside
    tolerance or appears in `failures` with a named `reason`
    (`reference_row_missing`, `pixel_count_mismatch`, `computability_mismatch`,
    `value_outside_tolerance`). A row is never dropped for being
    inconvenient, and `passed` is `False` the moment any failure exists.

    REFUSES (rather than reporting a failure) when
    `require_pixel_counts=True` against a reference that carries no count
    columns -- the comparison itself is not well-formed in that case. When
    the reference DOES carry count columns, `require_pixel_counts=True`
    enforces exact `n_member_pixels` / `n_valid_pixels` agreement per row:
    a mismatch is reported as `pixel_count_mismatch` (and the value
    comparison for that row is skipped, since a pixel-count disagreement
    already means the two sides did not reduce the same pixels).
    """
    if tolerances.require_pixel_counts:
        absent = [c for c in _PIXEL_COUNT_COLUMNS if c not in reference.columns]
        if absent:
            raise HuntlyValidationError(
                f"require_pixel_counts is set but the reference carries no pixel count "
                f"column(s) {absent}. D13 E5 requires exact member/valid pixel agreement; "
                "supply a reference that carries them, or pass "
                "Tolerances(require_pixel_counts=False) and record why."
            )

    indexed = reference.set_index(
        [reference["site_id"].astype(str), reference["year"].astype("int64")]
    )
    failures: list[dict[str, object]] = []
    n_compared = 0

    for row in extracted.to_dict("records"):
        metric = str(row["metric"])
        column = REFERENCE_METRIC_COLUMNS.get(metric)
        if column is None:
            # A metric the reference does not carry (none today) is skipped
            # explicitly rather than silently counted as agreement.
            continue
        n_compared += 1
        key = (str(row["site_id"]), int(row["year"]))
        base = {
            "site_id": row["site_id"],
            "year": int(row["year"]),
            "metric": metric,
            "collection_id": row.get("collection_id"),
        }
        try:
            reference_row = indexed.loc[key]
        except KeyError:
            failures.append({**base, "reason": "reference_row_missing"})
            continue
        if tolerances.require_pixel_counts:
            mismatched = {
                col: {"extracted": row[col], "reference": reference_row[col]}
                for col in _PIXEL_COUNT_COLUMNS
                if row[col] != reference_row[col]
            }
            if mismatched:
                failures.append(
                    {**base, "reason": "pixel_count_mismatch", "mismatched": mismatched}
                )
                continue
        reference_value = reference_row[column]
        computable = bool(row["computable"])
        if pd.isna(reference_value):
            if computable:
                failures.append({**base, "reason": "computability_mismatch"})
            continue
        if not computable:
            failures.append(
                {
                    **base,
                    "reason": "computability_mismatch",
                    "not_computable_reason": row.get("not_computable_reason"),
                }
            )
            continue
        reference_scalar = float(reference_value)
        tolerance = tolerances.fc_abs if metric in _FC_METRICS else tolerances.spectral_abs
        difference = abs(float(row["value"]) - reference_scalar)
        if difference > tolerance:
            failures.append(
                {
                    **base,
                    "reason": "value_outside_tolerance",
                    "extracted": float(row["value"]),
                    "reference": reference_scalar,
                    "abs_difference": difference,
                    "tolerance": tolerance,
                }
            )

    return ValidationReport(
        passed=not failures,
        n_compared=n_compared,
        n_sites=int(extracted["site_id"].nunique()),
        failures=failures,
        tolerances=tolerances,
    )
