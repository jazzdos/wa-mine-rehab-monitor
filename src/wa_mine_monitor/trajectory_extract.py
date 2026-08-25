"""Resumable Parquet partition extraction for the Tier 1 trajectory table
(D13 E4).

Partitions are `collection_id/year`. A partition is IMMUTABLE once written:
`--force` adds the next `part-NNNN.parquet` beside the old one rather than
mutating it, so a re-extraction can never silently replace the numbers an
earlier manifest describes. Coverage is read from the partitions themselves
(part file present AND digest-equal to its own run manifest), never from a
progress file that could claim a partition an interrupted run never
finished.

`PartitionResult` replaces the dataplatform `LoadResult` (which models a
mutable table load) with the four outcomes an immutable Parquet partition
actually has.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from wa_mine_monitor import d3_inputs, manifests, source_catalogue, trajectories

#: `part-0000.parquet`, `part-0001.parquet`, ... Zero-padded so a plain
#: lexicographic sort is also a chronological one.
PART_FILENAME_TEMPLATE = "part-{version:04d}.parquet"
_PART_FILENAME_RE = re.compile(r"^part-(\d{4})\.parquet$")

#: Where E5 writes its verdict, and the only thing that unlocks statewide
#: extraction.
HUNTLY_VALIDATION_SUBDIR = ("curated", "huntly-validation")
HUNTLY_VERDICT_FILENAME = "validation.json"

#: Geomedian `source_id` -> the `sensor` label the trajectory schema
#: carries. FC is a multi-sensor product, so its `sensor` is NULL -- never
#: a fabricated "ls" label (the same discipline that keeps `geomad_count`
#: null for FC rows).
_SENSOR_BY_SOURCE: dict[str, str | None] = {
    "dea_gm_ls5t": "ls5t",
    "dea_gm_ls7e": "ls7e",
    "dea_gm_ls8cls9c": "ls8cls9c",
    "dea_fc_pc": None,
}


class TrajectoryExtractError(ValueError):
    """A partition write or coverage read that violates E4's contract."""


@dataclass(frozen=True)
class PartitionResult:
    """The four outcomes of an attempted partition write.

    `existing` and `refused_empty` count PARTITIONS; `inserted` and
    `not_computable` count ROWS (`not_computable` is the subset of
    `inserted` carrying `computable=False`, i.e. rows that disclose a
    `not_computable_reason` instead of a value -- they are written, never
    dropped).
    """

    existing: int = 0
    inserted: int = 0
    refused_empty: int = 0
    not_computable: int = 0

    def __add__(self, other: PartitionResult) -> PartitionResult:
        return PartitionResult(
            existing=self.existing + other.existing,
            inserted=self.inserted + other.inserted,
            refused_empty=self.refused_empty + other.refused_empty,
            not_computable=self.not_computable + other.not_computable,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "existing": self.existing,
            "inserted": self.inserted,
            "refused_empty": self.refused_empty,
            "not_computable": self.not_computable,
        }


def partition_dir(root: Path, collection_id: str, year: int) -> Path:
    """`root/collection_id=<collection_id>/year=<year>`."""
    return Path(root) / f"collection_id={collection_id}" / f"year={int(year)}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def part_files(partition: Path) -> list[Path]:
    """Every `part-NNNN.parquet` in `partition`, version-ordered. No
    verification -- see `verified_parts`."""
    partition = Path(partition)
    if not partition.is_dir():
        return []
    matched = [p for p in partition.iterdir() if _PART_FILENAME_RE.match(p.name)]
    return sorted(matched, key=lambda p: p.name)


def verified_parts(partition: Path) -> list[Path]:
    """Every part file in `partition` whose bytes still match the
    `output.sha256` its OWN run manifest records.

    A part file with no manifest, an unparseable manifest, or a changed
    digest is a REFUSAL, never "not covered": treating it as absent would
    let a corrupted partition be silently rewritten and the corruption
    would never be reported. This is the artefact-level twin of
    `cli._digest_verified_manifest`, kept here (rather than imported from
    `cli`) so the module is usable without the CLI layer.
    """
    verified: list[Path] = []
    for path in part_files(partition):
        manifest_path = Path(str(path) + manifests.MANIFEST_SUFFIX)
        if not manifest_path.exists():
            raise TrajectoryExtractError(f"no run manifest beside {path}")
        try:
            recorded = json.loads(manifest_path.read_text(encoding="utf-8"))["output"]["sha256"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TrajectoryExtractError(
                f"{manifest_path} is missing or unparseable: {exc}"
            ) from None
        actual = _sha256_file(path)
        if actual != recorded:
            raise TrajectoryExtractError(
                f"{path} has sha256 {actual[:12]}... but its manifest records "
                f"{str(recorded)[:12]}... -- the partition changed after its manifest "
                "was written"
            )
        verified.append(path)
    return verified


def next_part_version(partition: Path) -> int:
    """One past the highest `part-NNNN` version present (0 when empty)."""
    present = part_files(partition)
    if not present:
        return 0
    matches = [_PART_FILENAME_RE.match(p.name) for p in present]
    return max(int(m.group(1)) for m in matches if m is not None) + 1


def write_partition(rows: pd.DataFrame, partition: Path) -> tuple[Path, PartitionResult]:
    """Write `rows` as the NEXT `part-NNNN.parquet` under `partition`.

    Refuses an empty frame: an empty partition file is indistinguishable
    from a partition whose reads all failed, and writing one would let a
    later run read "covered, zero rows" as a measurement. A partition with
    genuinely no computable values is still written -- as
    not-computable ROWS carrying a reason (that is what
    `PartitionResult.not_computable` counts), which is never the empty
    case.

    Never mutates an existing part file. `trajectories.write_trajectories`
    validates the E3 row contract before a byte is written.
    """
    if rows.empty:
        raise TrajectoryExtractError(
            f"refusing to write an empty partition at {partition} -- an empty part file "
            "is not a measurement"
        )
    partition = Path(partition)
    partition.mkdir(parents=True, exist_ok=True)
    version = next_part_version(partition)
    path = partition / PART_FILENAME_TEMPLATE.format(version=version)
    trajectories.write_trajectories(rows, path)
    return path, PartitionResult(
        inserted=len(rows),
        not_computable=int((~rows["computable"].astype(bool)).sum()),
    )


def select_eligible_sites(register_df: pd.DataFrame) -> list[str]:
    """The sorted `site_id`s of the `trajectory_status == "eligible"` rows.

    D13 E4 acceptance: only eligible rows enter extraction. Every other
    status (including `threshold_not_computed`) is excluded here rather
    than filtered downstream, so no ineligible site can reach a raster
    read at all.
    """
    for column in ("site_id", "trajectory_status"):
        if column not in register_df.columns:
            raise TrajectoryExtractError(
                f"register frame is missing {column!r} -- run apply-d3-threshold first"
            )
    eligible = register_df.loc[register_df["trajectory_status"] == "eligible", "site_id"]
    return sorted(str(site_id) for site_id in eligible.dropna())


def collection_id_for_source(source_id: str) -> str:
    """The public DEA collection id for an internal `source_id`."""
    try:
        return source_catalogue.spec_for_source(source_id).collection_id
    except KeyError:
        raise TrajectoryExtractError(f"unknown source_id {source_id!r}") from None


def sensor_for_source(source_id: str) -> str | None:
    """The `sensor` label for `source_id`; `None` for the FC product."""
    if source_id not in _SENSOR_BY_SOURCE:
        raise TrajectoryExtractError(f"unknown source_id {source_id!r}")
    return _SENSOR_BY_SOURCE[source_id]


def transition_adjacent_years(
    covered_years_by_source: Mapping[str, set[int]],
) -> dict[int, bool]:
    """Flag every year that sits at or beside a GEOMEDIAN sensor change.

    A year is `transition_adjacent` when the SET of geomedian collections
    covering it differs from the set covering `year - 1` or `year + 1`, or
    when more than one geomedian collection covers it (a genuine overlap
    year). FC is excluded from the comparison entirely -- it is one
    multi-sensor collection and its coverage changing says nothing about a
    Landsat sensor transition.

    This is a DISCLOSURE flag, not a filter: E6 (sensor-overlap
    sensitivity) is what interprets it. Nothing here drops a flagged row.
    """
    geomedian = {
        source_id: set(years)
        for source_id, years in covered_years_by_source.items()
        if d3_inputs.D3_COLLECTION_KIND.get(source_id) == "geomedian"
    }
    all_years = sorted({year for years in geomedian.values() for year in years})

    def sources_covering(year: int) -> frozenset[str]:
        return frozenset(s for s, years in geomedian.items() if year in years)

    flags: dict[int, bool] = {}
    for year in all_years:
        here = sources_covering(year)
        neighbours = [sources_covering(year - 1), sources_covering(year + 1)]
        changed = any(other and other != here for other in neighbours)
        flags[year] = len(here) > 1 or changed
    return flags


def require_huntly_gate(data_root: Path) -> dict[str, object]:
    """Return the latest digest-verified Huntly verdict, or refuse.

    D13 E4/E5: statewide extraction is not permitted until the Huntly cube
    comparison has passed. The verdict is verified against its OWN run
    manifest first -- a hand-edited `passed: true` is refused, not
    honoured, which is the whole point of gating on an artefact rather
    than on a human's say-so.
    """
    root = Path(data_root).joinpath(*HUNTLY_VALIDATION_SUBDIR)
    dated = sorted(root.glob("????-??-??")) if root.exists() else []
    if not dated:
        raise TrajectoryExtractError(
            f"no Huntly validation verdict under {root} -- run validate-huntly before "
            "statewide extraction"
        )
    path = dated[-1] / HUNTLY_VERDICT_FILENAME
    if not path.exists():
        raise TrajectoryExtractError(f"{path} does not exist -- run validate-huntly")
    manifest_path = Path(str(path) + manifests.MANIFEST_SUFFIX)
    if not manifest_path.exists():
        raise TrajectoryExtractError(f"no run manifest beside {path}")
    try:
        recorded = json.loads(manifest_path.read_text(encoding="utf-8"))["output"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TrajectoryExtractError(f"{manifest_path} is missing or unparseable: {exc}") from None
    actual = _sha256_file(path)
    if actual != recorded:
        raise TrajectoryExtractError(
            f"{path} has sha256 {actual[:12]}... but its manifest records "
            f"{str(recorded)[:12]}... -- the verdict changed after its manifest was written"
        )
    verdict: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    if not bool(verdict.get("passed")):
        raise TrajectoryExtractError(
            f"the Huntly validation at {path} did not pass -- statewide extraction is "
            "refused until it does"
        )
    return verdict
