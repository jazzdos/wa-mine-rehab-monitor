"""Release package specs: what `export-release` may publish, and under what
licence lineage. One spec per package; the registry is the closed list of
things this project releases -- a package absent here cannot be exported.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from wa_mine_monitor import export_gate, licence


@dataclass(frozen=True)
class PackageSpec:
    curated_dir: str  # under <data_root>/curated/
    filename: str  # artefact inside the dated directory
    source_id: str  # key into licence.SOURCES for the row gate
    output_licence: str  # recorded in the release manifest
    share_alike: bool  # recorded in the release manifest
    modification_statement: str  # CC-BY-SA obligation; package-specific


PACKAGES: dict[str, PackageSpec] = {
    "footprint-areas": PackageSpec(
        curated_dir="maus_footprint_areas",
        filename="footprint_areas.parquet",
        source_id="maus_v2",
        output_licence="CC-BY-SA-4.0",
        share_alike=True,
        modification_statement=(
            "Modified from the Maus et al. v2 polygons: WA extract, "
            "reprojected to EPSG:3577, per-footprint areas computed; "
            "no polygon geometry is included in this table."
        ),
    ),
}


def attribution_block(spec: PackageSpec) -> str:
    """The licence notice shipped WITH the released package.

    Assembled from `licence.SOURCES` (attribution text, source URL, licence
    URL) plus the package's modification statement -- the three CC-BY-SA
    obligations the maus_v2 registry entry names. Never hand-written per
    release; the registry is the single source.
    """
    source = licence.SOURCES[spec.source_id]
    return "\n\n".join(
        [
            source.attribution_text,
            f"Source: {source.source_url}",
            f"Licence: {source.licence_id} ({source.licence_url})",
            spec.modification_statement,
        ]
    )


def prepare_for_export(frame: pd.DataFrame, spec: PackageSpec) -> pd.DataFrame:
    """Attach the row gate from the source's own licence registry entry.

    `export_public` then enforces it: absent, null, or False refuses the
    whole frame. The licence decision is made once, in `licence.SOURCES`,
    and carried here -- never asserted per-call.
    """
    source = licence.SOURCES[spec.source_id]  # KeyError = unknown source, refuse
    prepared = frame.copy()
    prepared[export_gate.REDISTRIBUTE_COLUMN] = bool(source.redistribute_public)
    return prepared
