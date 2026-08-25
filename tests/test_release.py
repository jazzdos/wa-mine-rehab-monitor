import pandas as pd
import pytest

from wa_mine_monitor import release


def test_footprint_areas_package_is_registered() -> None:
    spec = release.PACKAGES["footprint-areas"]
    assert spec.curated_dir == "maus_footprint_areas"
    assert spec.filename == "footprint_areas.parquet"
    assert spec.source_id == "maus_v2"
    assert spec.output_licence == "CC-BY-SA-4.0"
    assert spec.share_alike is True
    # CC-BY-SA requires a modification statement in the released package
    # (licence.py maus_v2 notes: "attribution, source link and modification
    # statement"); it is package-specific, so it lives on the spec.
    assert "Maus" in spec.modification_statement


def test_attribution_block_carries_the_full_grant() -> None:
    # The attribution artefact is assembled from the licence registry, never
    # hand-written per release: attribution text, source link, licence link,
    # and the package's modification statement, all non-empty.
    block = release.attribution_block(release.PACKAGES["footprint-areas"])
    assert "Maus" in block
    assert "PANGAEA" in block  # source link
    assert "creativecommons.org/licenses/by-sa" in block
    assert release.PACKAGES["footprint-areas"].modification_statement in block


def test_prepare_for_export_attaches_row_gate_from_source() -> None:
    frame = pd.DataFrame({"maus_id": ["m1"], "footprint_area_m2": [900.0]})
    prepared = release.prepare_for_export(frame, release.PACKAGES["footprint-areas"])
    assert prepared["redistribute_public"].tolist() == [True]


def test_prepare_for_export_refuses_unknown_package_source() -> None:
    # A package whose source is not in licence.SOURCES must refuse, never
    # default: an unregistered licence is UNKNOWN, and unknown is not
    # permitted (same rule as the row gate's null case).
    bad = release.PackageSpec(
        curated_dir="x",
        filename="x.parquet",
        source_id="not_a_source",
        output_licence="CC-BY-4.0",
        share_alike=False,
        modification_statement="",
    )
    with pytest.raises(KeyError):
        release.prepare_for_export(pd.DataFrame({"a": [1]}), bad)
