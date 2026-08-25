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
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from wa_mine_monitor import d3_inputs, manifests, source_catalogue, trajectories
from wa_mine_monitor.provenance import SourceAsset

#: `part-0000.parquet`, `part-0001.parquet`, ... Zero-padded so a plain
#: lexicographic sort is also a chronological one.
PART_FILENAME_TEMPLATE = "part-{version:04d}.parquet"
_PART_FILENAME_RE = re.compile(r"^part-([0-9]{4})\.parquet$")

#: The only other entry a partition directory may legitimately hold: a part
#: file's own run manifest sidecar (`manifests.write_run_manifest` writes
#: `<part path><MANIFEST_SUFFIX>` beside it). Built from `_PART_FILENAME_RE`'s
#: source plus `manifests.MANIFEST_SUFFIX` (escaped) rather than a
#: separately hand-written pattern, so the two cannot drift apart, and
#: ASCII-digit-only for the same reason `_PART_FILENAME_RE` is: `\d` also
#: accepts Unicode digits Python's `int()` treats as equal to their ASCII
#: counterparts.
_PART_MANIFEST_FILENAME_RE = re.compile(
    r"^part-([0-9]{4})\.parquet" + re.escape(manifests.MANIFEST_SUFFIX) + r"$"
)

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


#: The reverse of `partition_dir`'s two path segments. Kept next to
#: `partition_dir` so the two cannot drift apart.
_COLLECTION_ID_DIR_RE = re.compile(r"^collection_id=(.+)$")
_YEAR_DIR_RE = re.compile(r"^year=(0|[1-9][0-9]*)$")


def existing_partitions(out_dir: Path) -> list[tuple[str, int]]:
    """Every `(collection_id, year)` partition already on disk under
    `out_dir`, parsed from the `collection_id=<id>/year=<year>` layout
    `partition_dir` writes.

    `out_dir` also holds top-level, non-partition files -- `extraction_
    summary.json` and its run manifest -- written directly in `out_dir`,
    never inside a `collection_id=.../year=...` subdirectory. Those, and
    any other top-level FILE, are ignored here: only directories are
    partition-shaped at all.

    A top-level DIRECTORY not named `collection_id=<value>`, or an entry
    inside one that is not a directory named `year=<int>`, is refused
    rather than silently skipped: this function is the only thing standing
    between an unrecognised directory and a caller that assumes every
    existing partition it does not see is simply absent from the run. A
    directory this function cannot parse must be surfaced, never guessed
    past.
    """
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return []
    partitions: list[tuple[str, int]] = []
    for collection_entry in sorted(out_dir.iterdir()):
        if collection_entry.is_file():
            continue
        collection_match = _COLLECTION_ID_DIR_RE.match(collection_entry.name)
        if collection_match is None:
            raise TrajectoryExtractError(
                f"{collection_entry} does not match the collection_id=<value>/year=<int> "
                "partition layout -- refusing to guess whether it is a partition"
            )
        collection_id = collection_match.group(1)
        for year_entry in sorted(collection_entry.iterdir()):
            year_match = _YEAR_DIR_RE.match(year_entry.name) if year_entry.is_dir() else None
            if year_match is None:
                raise TrajectoryExtractError(
                    f"{year_entry} does not match the collection_id=<value>/year=<int> "
                    "partition layout -- refusing to guess whether it is a partition"
                )
            partitions.append((collection_id, int(year_match.group(1))))
    return partitions


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def part_files(partition: Path) -> list[Path]:
    """Every `part-NNNN.parquet` in `partition`, version-ordered. No
    per-part verification -- see `verified_parts` for that.

    Every entry in `partition` is required to be either a matched
    `part-NNNN.parquet` file or that part's `.run_manifest.json` sidecar; any
    other entry (a stray file, a directory, or a part-shaped name that only
    matches under Unicode-digit folding) is a REFUSAL naming the entry, never
    a silent skip. Tightening `_PART_FILENAME_RE` to ASCII digits means a
    part file named with Unicode digits (which old `\\d`-based matching
    would have accepted) no longer matches -- without this gate such a file
    would simply vanish from `part_files`' and `verified_parts`' view and
    then finalize, unverified, inside a directory the ledger's fail-closed
    convention requires to be empty of anything the run did not check. This
    is the partition-directory twin of `existing_partitions`' stray-file and
    stray-partition gates.

    The sidecar/part correspondence is also required to be a BIJECTION: a
    sidecar with no matching `part-NNNN.parquet` (an "orphan sidecar") is a
    REFUSAL naming the orphan, not a silent accept. `verified_parts` already
    refuses the opposite direction -- a part with no sidecar ("no run
    manifest beside <path>") -- so that case is not duplicated here. The
    orphan-sidecar direction has no such downstream check, and it is not
    merely inert clutter: `part_files` is called from `verified_parts`
    (which would otherwise verify only the parts that happen to have a
    sidecar, silently ignoring the orphan), from `next_part_version` (which
    picks one past the highest present PART version -- an orphan sidecar for
    a version whose part is absent does not shift that choice, but leaves
    the version `next_part_version` picks free for a new part to reuse),
    and from `write_partition` (which writes that new part at the version
    `next_part_version` chose and then has `manifests.write_run_manifest`
    write its OWN sidecar beside it -- landing exactly on the orphan's
    path and silently overwriting a stale, unverified manifest with a
    fresh one that happens to still validate, since it was written for the
    part now sitting there). Refusing the orphan up front, before version
    selection or verification ever runs, is correct for all three callers:
    the remedy is the same in every case -- the partition is inconsistent
    and must be re-extracted under a NEW dated output directory, never
    finalized or written into as-is."""
    partition = Path(partition)
    if not partition.is_dir():
        return []
    matched: list[Path] = []
    sidecars: list[tuple[Path, str]] = []
    for entry in partition.iterdir():
        if _PART_FILENAME_RE.match(entry.name):
            matched.append(entry)
            continue
        manifest_match = _PART_MANIFEST_FILENAME_RE.match(entry.name)
        if manifest_match is not None:
            sidecars.append((entry, manifest_match.group(1)))
            continue
        raise TrajectoryExtractError(
            f"{entry} does not match the part-NNNN.parquet (or its run manifest sidecar) "
            "layout -- refusing to guess whether it is a verified part"
        )
    matched_versions = {
        m.group(1) for m in (_PART_FILENAME_RE.match(p.name) for p in matched) if m is not None
    }
    for sidecar_path, sidecar_version in sidecars:
        if sidecar_version not in matched_versions:
            raise TrajectoryExtractError(
                f"{sidecar_path} is a run manifest sidecar with no corresponding "
                f"part-{sidecar_version}.parquet -- the partition is inconsistent and must "
                "be re-extracted under a NEW dated output directory"
            )
    return sorted(matched, key=lambda p: p.name)


def _schema_field_mismatches(schema: pa.Schema, expected: pa.Schema) -> list[str]:
    """Field-name/type/nullability differences between `schema` and
    `expected`, ignoring schema-level and field-level METADATA (pandas
    round-trips attach its own `pandas` metadata blob, which is never a
    contract violation).

    Nullability is checked because it is part of the declared trajectory
    row contract (`TRAJECTORY_SCHEMA` marks key fields non-nullable), not
    schema metadata: a part whose names and types match but that relaxes a
    non-nullable field to nullable could carry null keys or missing
    geometry and must be refused, not silently accepted.

    Duplicate field names in `schema` are checked FIRST, before either
    schema is collapsed into a name-keyed dict: Parquet permits two
    fields sharing one name (a foreign writer could produce this), and a
    dict keyed by field name silently collapses such a duplicate down to
    one entry, reporting no mismatch at all even though the resulting
    dataset is ambiguous to read. Each duplicated name is named in the
    mismatch list along with its repeat count."""
    if len(schema.names) != len(set(schema.names)):
        counts: dict[str, int] = {}
        for name in schema.names:
            counts[name] = counts.get(name, 0) + 1
        return [
            f"field {name!r} appears {count} times (duplicate field names)"
            for name, count in counts.items()
            if count > 1
        ]
    actual_by_name = {f.name: f for f in schema}
    expected_by_name = {f.name: f for f in expected}
    mismatches: list[str] = []
    for name, expected_field in expected_by_name.items():
        if name not in actual_by_name:
            mismatches.append(f"missing field {name!r} (expected {expected_field.type})")
            continue
        actual_field = actual_by_name[name]
        if actual_field.type != expected_field.type:
            mismatches.append(
                f"field {name!r} has type {actual_field.type}, expected {expected_field.type}"
            )
        if actual_field.nullable != expected_field.nullable:
            actual_desc = "nullable" if actual_field.nullable else "non-nullable"
            expected_desc = "nullable" if expected_field.nullable else "non-nullable"
            mismatches.append(f"field {name!r} is {actual_desc}, expected {expected_desc}")
    for name in actual_by_name:
        if name not in expected_by_name:
            mismatches.append(f"unexpected field {name!r}")
    return mismatches


def verified_parts(partition: Path, expected_schema: pa.Schema | None = None) -> list[Path]:
    """Every part file in `partition` whose bytes still match the
    `output.sha256` its OWN run manifest records, AND (when `expected_schema`
    is given) whose Parquet schema still matches `expected_schema`.

    A part file with no manifest, an unparseable manifest, or a changed
    digest is a REFUSAL, never "not covered": treating it as absent would
    let a corrupted partition be silently rewritten and the corruption
    would never be reported. This is the artefact-level twin of
    `cli._digest_verified_manifest`, kept here (rather than imported from
    `cli`) so the module is usable without the CLI layer.

    The digest check alone lets a partition written by an OLDER build --
    before the trajectory row contract gained a column, or tightened a
    rule -- pass unchanged, since its own manifest still matches its own
    bytes. `expected_schema` closes that hole: each verified part's schema
    is read from the Parquet FOOTER only (`pyarrow.parquet.read_schema`,
    no row data touched) and compared to `expected_schema` by field name,
    type, AND nullability (never by metadata -- pandas round-trips attach
    a `pandas` metadata blob that carries no contract meaning).
    Nullability is included because it is part of the declared trajectory
    row contract, not incidental metadata: a part whose names and types
    match but that relaxed a non-nullable field (a key column, or
    `geometry`) to nullable would otherwise pass unchanged. Any difference is a
    REFUSAL naming the part file, the differing fields, and the remedy:
    the partition predates the current trajectory row contract and must
    be re-extracted under a NEW dated output directory, never finalized
    as-is into a mixed-schema dataset.
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
        if expected_schema is not None:
            mismatches = _schema_field_mismatches(pq.read_schema(path), expected_schema)
            if mismatches:
                raise TrajectoryExtractError(
                    f"{path} does not match the current trajectory row contract: "
                    f"{'; '.join(mismatches)} -- this partition predates the current "
                    "schema and must be re-extracted under a NEW dated output directory, "
                    "never finalized as-is"
                )
        verified.append(path)
    return verified


#: The resume-binding fields `resume_binding_mismatches` checks, in the
#: order they are appended to its result -- kept as a tuple so the CLI's
#: refusal message and this module's tests enumerate them identically.
RESUME_BINDING_FIELDS: tuple[str, ...] = (
    "date",
    "scope",
    "site_ids",
    "inputs",
    "config",
    "git",
    "package_versions",
)

#: One recorded/current input asset's identity for resume-binding purposes:
#: sha256 + collection + snapshot_date, WITHOUT `uri`. `uri` is deliberately
#: excluded -- an absolute path legitimately differs across machines and
#: checkouts, so it carries no provenance signal; byte identity (`sha256`)
#: plus `collection` plus `snapshot_date` is what actually identifies "the
#: same external asset", and is what a NEWER Maus/catalogue/register/
#: crosswalk/footprints snapshot directory containing byte-identical bytes
#: would otherwise slip past a bare sha256 comparison.
_InputIdentity = tuple[str, str | None, _date | None]


def _input_identity(asset: SourceAsset) -> _InputIdentity | None:
    """`asset`'s resume-binding identity, or `None` when it has no sha256
    (matching the recorded side dropping `None`-sha256 entries below)."""
    if asset.sha256 is None:
        return None
    return (asset.sha256, asset.collection, asset.snapshot_date)


def _recorded_input_identity(asset: Mapping[str, Any]) -> _InputIdentity | None:
    """The same identity as `_input_identity`, read off a manifest's
    already-serialized `inputs[]` entry (`snapshot_date` parsed back from
    its canonical `YYYY-MM-DD` JSON string -- the manifest's own recorded
    form -- rather than compared as a raw string against a `date` object)."""
    sha256 = asset.get("sha256")
    if not sha256:
        return None
    snapshot_date = asset.get("snapshot_date")
    return (
        sha256,
        asset.get("collection"),
        _date.fromisoformat(snapshot_date) if snapshot_date else None,
    )


def _format_input_identity(identity: _InputIdentity) -> str:
    sha256, collection, snapshot_date = identity
    return (
        f"(sha256={sha256}, collection={collection}, "
        f"snapshot_date={snapshot_date.isoformat() if snapshot_date else None})"
    )


def resume_binding_mismatches(
    part_manifest: Mapping[str, Any],
    *,
    date: str,
    scope: str,
    site_ids: Sequence[str],
    input_assets: Iterable[SourceAsset],
    config: Mapping[str, Any],
    git_state: Mapping[str, Any],
    package_versions: Mapping[str, str],
) -> list[str]:
    """The fields on which `part_manifest` -- an already digest- and
    schema-verified partition's OWN run manifest -- differs from the
    CURRENT invocation, or an empty list when none do.

    `verified_parts` only proves a partition's bytes still match its own
    manifest and that its schema still matches the current row contract --
    neither says anything about whether THIS run is the one that wrote it.
    The skip decision in the extraction loop binds only on
    `(collection_id, year)`, so a partition left by an earlier, interrupted
    run against a DIFFERENT `--site-id` scope, a different catalogue/
    register/crosswalk/footprints/Maus snapshot, a different config,
    different code, or a drifted dependency environment would otherwise be
    silently absorbed, and the final summary would then claim the CURRENT
    invocation's scope/sites/inputs over rows produced under the old ones.
    A resumed run must be the SAME run, or refuse -- this is the check that
    enforces it.

    Seven fields are compared, each named in the returned list when it
    differs:

    - `"date"`/`"scope"`/`"site_ids"` -- `part_manifest["resolved_args"]`
      against the CURRENT `date`, `scope` and `site_ids` (the caller's
      `extracted_sites`, compared sorted -- the same order the CLI records
      them in and writes them to the batch summary).
    - `"inputs"` -- the recorded input assets' identities (`(sha256,
      collection, snapshot_date)`, `None`-sha256 entries dropped, `uri`
      deliberately excluded -- see `_InputIdentity`) against `input_assets`'
      identities, compared as a MULTISET (duplicate sha256 values with
      different collections/snapshot dates are not collapsed): the same
      catalogue snapshot, register, crosswalk, footprint areas, Maus
      snapshot and frozen protocol artefact, never a newer or older one
      silently substituted even when its bytes happen to be
      byte-identical to the old one (a bare sha256 SET comparison would
      miss exactly this: a newer Maus snapshot directory containing
      byte-identical bytes, which still changes `source_snapshot_date` on
      every row the CLI derives from it).
    - `"config"`/`"git"` -- `part_manifest["config"]`/`["git"]` against
      `manifests.canonical_config(config)`/`manifests.canonical_git_state
      (git_state)` -- the SAME normalised form `write_run_manifest`
      recorded, so a raw config/git_state is never compared against a
      scrubbed one. `git` is what catches different CODE: a rule change
      with an unchanged row schema (the defect this function exists to
      close) is invisible to `verified_parts`' digest+schema gate, but it
      changes `git.sha` (a committed change) or `git.diff` (an
      uncommitted one).
    - `"package_versions"` -- `part_manifest["package_versions"]` against
      `package_versions` (the CURRENT invocation's, from the same
      `manifests.installed_package_versions()` a real
      `write_run_manifest` call would use when none is supplied). Cheap
      and, under a synced `uv.lock`, deterministic across a resume -- so
      it never causes a spurious refusal, but it catches an environment
      that drifted without any code change. A manifest recording no
      `package_versions` at all (an older or hand-built manifest) compares
      unequal to any non-empty current environment and so is a mismatch:
      this check is fail-closed, never lenient about an absent field.
    """
    mismatches: list[str] = []
    resolved_args = part_manifest.get("resolved_args") or {}
    if resolved_args.get("date") != date:
        mismatches.append("date")
    if resolved_args.get("scope") != scope:
        mismatches.append("scope")
    if resolved_args.get("site_ids") != sorted(site_ids):
        mismatches.append("site_ids")

    recorded_inputs = Counter(
        identity
        for asset in part_manifest.get("inputs") or []
        if (identity := _recorded_input_identity(asset)) is not None
    )
    current_inputs = Counter(
        identity for asset in input_assets if (identity := _input_identity(asset)) is not None
    )
    if recorded_inputs != current_inputs:
        missing = sorted(
            _format_input_identity(i) for i in (recorded_inputs - current_inputs).elements()
        )
        extra = sorted(
            _format_input_identity(i) for i in (current_inputs - recorded_inputs).elements()
        )
        detail_parts = []
        if missing:
            detail_parts.append(f"recorded but not in this run: [{', '.join(missing)}]")
        if extra:
            detail_parts.append(f"in this run but not recorded: [{', '.join(extra)}]")
        mismatches.append(f"inputs ({'; '.join(detail_parts)})")

    if part_manifest.get("config") != manifests.canonical_config(config):
        mismatches.append("config")
    if part_manifest.get("git") != manifests.canonical_git_state(git_state):
        mismatches.append("git")

    recorded_package_versions = part_manifest.get("package_versions") or {}
    current_package_versions = dict(package_versions)
    if recorded_package_versions != current_package_versions:
        all_packages = set(recorded_package_versions) | set(current_package_versions)
        changed_packages = sorted(
            pkg
            for pkg in all_packages
            if recorded_package_versions.get(pkg) != current_package_versions.get(pkg)
        )
        mismatches.append(f"package_versions ({', '.join(changed_packages)})")
    return mismatches


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
