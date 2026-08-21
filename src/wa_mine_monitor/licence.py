"""Source licence registry: one pinned record per external data source.

This is the project's own citation for every data-licence fact used
downstream (the export gate in `export_gate.py`, the licensing matrix at
`docs/licensing-matrix.md`, and any attribution rendered on a public page).
Every entry is pinned as measured on 2026-08-15 against the primary record
named in its `source_url` -- the design doc's §6 table, not an inference from
the operating agency (CLAUDE.md's sourcing rule: read the exact resource,
never the agency's general reputation).

MINEDEX is the one source with a genuine licence CONFLICT: Data WA's own
catalogue record for `minedex-dmirs-001` states `license_id: cc-nc`
(CC-BY-NC-4.0), while WA's Digital Atlas of Sustainability and Conservation
(DASC) carries a blanket "unless otherwise noted" CC-BY-4.0 statement -- and
the Data WA catalogue label IS such a note. `SOURCES["dmirs_001_minedex"]`
is therefore pinned `redistribute_public=False` unconditionally: no runtime
mechanism in this module ever flips that static entry. What CAN unblock a
public *export* of MINEDEX-derived data is a separate, narrower question --
"did this run capture explicit, uncontradicted CC-BY-4.0 evidence for the
exact DASC resource it downloaded" -- and that is `minedex_redistribution_
allowed`, which reads a per-run evidence file rather than trusting the
catalogue label. The two are deliberately not the same mechanism: the
registry entry describes what is true of MINEDEX in general (conflicted,
closed), and the evidence check describes what is true of one captured
download (open only if it says so explicitly, in writing, with no contrary
notice).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from wa_mine_monitor.provenance import sha256_file
from wa_mine_monitor.snapshots import snapshot_entries

#: Filename `minedex_redistribution_allowed` reads from `evidence_dir`. Fixed
#: rather than parameterised: this is the one file shape the MINEDEX binding
#: rule (design doc §6) recognises, so a caller cannot silently point the
#: check at a differently-shaped file and get a different answer.
EVIDENCE_FILENAME = "licence_evidence.json"

#: The only `explicit_grant` value that can unblock MINEDEX redistribution.
_REQUIRED_GRANT = "CC-BY-4.0"


@dataclass(frozen=True)
class SourceLicence:
    """One external data source's pinned licence record.

    Every field is required and non-placeholder -- see
    `test_every_source_has_required_fields` -- because a licence record with
    an empty attribution string or a missing source URL is not a licence
    record, it is a name with a decision nobody can check.
    """

    #: Stable key this project uses internally (matches the `SOURCES` dict key).
    source_id: str
    #: Human-readable name for docs and attribution rendering.
    title: str
    #: The exact resource this project measured, not a general agency page.
    source_url: str
    #: Short licence identifier, e.g. `"CC-BY-4.0"`. `dmirs_001_minedex`
    #: carries the sentinel `"CONFLICT:cc-nc-vs-cc-by"` instead, so the
    #: conflict is visible in the identifier itself and not just in prose.
    licence_id: str
    #: URL to the licence text itself, where one exists.
    licence_url: str
    #: Exact text to render wherever this source's data is displayed or cited.
    attribution_text: str
    #: Whether data derived from this source may be redistributed publicly.
    #: This is the static, general-purpose answer for the source as a whole;
    #: `dmirs_001_minedex` is pinned `False` here regardless of what any
    #: single run's captured evidence shows -- see this module's docstring.
    redistribute_public: bool
    #: Free-text notes: scope limits, transformation notices, and the
    #: reasoning behind the redistribution decision.
    notes: str


_HANSEN_DISPLAY_CREDIT = "Source: Hansen/UMD/Google/USGS/NASA"
_HANSEN_CITATION_CREDIT = (
    "Hansen, M. C., P. V. Potapov, R. Moore, M. Hancher, S. A. Turubanova, "
    "A. Tyukavina, D. Thau, S. V. Stehman, S. J. Goetz, T. R. Loveland, "
    "A. Kommareddy, A. Egorov, L. Chini, C. O. Justice, and J. R. G. Townshend. "
    '2013. "High-Resolution Global Maps of 21st-Century Forest Cover Change." '
    "Science 342 (15 November): 850-53. Data available on-line from: "
    "https://glad.earthengine.app/view/global-forest-change."
)

SOURCES: dict[str, SourceLicence] = {
    "dea_gm_ls5t": SourceLicence(
        source_id="dea_gm_ls5t",
        title="DEA Landsat 5 TM Geomedian (ga_ls5t_gm_cyear_3)",
        source_url="https://explorer.dea.ga.gov.au/stac/collections/ga_ls5t_gm_cyear_3",
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=(
            "Contains Digital Earth Australia data (Geoscience Australia), "
            "collection ga_ls5t_gm_cyear_3, licensed under CC-BY-4.0. Landsat "
            "data courtesy of the U.S. Geological Survey."
        ),
        redistribute_public=True,
        notes=(
            "Licence read from the collection's own STAC JSON `license` field, "
            "per the design doc's measurement, not inferred from Geoscience "
            "Australia's general reputation. `source_url` pins the exact "
            "collection record (not the STAC catalog root), so the CC-BY-4.0 "
            "pin is directly re-verifiable at this URL."
        ),
    ),
    "dea_gm_ls7e": SourceLicence(
        source_id="dea_gm_ls7e",
        title="DEA Landsat 7 ETM+ Geomedian (ga_ls7e_gm_cyear_3)",
        source_url="https://explorer.dea.ga.gov.au/stac/collections/ga_ls7e_gm_cyear_3",
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=(
            "Contains Digital Earth Australia data (Geoscience Australia), "
            "collection ga_ls7e_gm_cyear_3, licensed under CC-BY-4.0. Landsat "
            "data courtesy of the U.S. Geological Survey."
        ),
        redistribute_public=True,
        notes="Licence read from the collection's own STAC JSON `license` field.",
    ),
    "dea_gm_ls8cls9c": SourceLicence(
        source_id="dea_gm_ls8cls9c",
        title="DEA Landsat 8/9 Geomedian (ga_ls8cls9c_gm_cyear_3)",
        source_url="https://explorer.dea.ga.gov.au/stac/collections/ga_ls8cls9c_gm_cyear_3",
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=(
            "Contains Digital Earth Australia data (Geoscience Australia), "
            "collection ga_ls8cls9c_gm_cyear_3, licensed under CC-BY-4.0. "
            "Landsat data courtesy of the U.S. Geological Survey."
        ),
        redistribute_public=True,
        notes="Licence read from the collection's own STAC JSON `license` field.",
    ),
    "dea_fc_pc": SourceLicence(
        source_id="dea_fc_pc",
        title="DEA Fractional Cover Percentiles (ga_ls_fc_pc_cyear_3)",
        source_url="https://explorer.dea.ga.gov.au/stac/collections/ga_ls_fc_pc_cyear_3",
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=(
            "Contains Digital Earth Australia data (Geoscience Australia), "
            "collection ga_ls_fc_pc_cyear_3, licensed under CC-BY-4.0. Landsat "
            "data courtesy of the U.S. Geological Survey."
        ),
        redistribute_public=True,
        notes="Licence read from the collection's own STAC JSON `license` field.",
    ),
    "dmirs_003_tenements": SourceLicence(
        source_id="dmirs_003_tenements",
        title="Mining Tenements (DMIRS-003)",
        source_url="https://catalogue.data.wa.gov.au/dataset/mining-tenements-dmirs-003",
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=(
            "Contains data sourced from the Department of Mines, Industry "
            "Regulation and Safety (DMIRS), Mining Tenements (DMIRS-003), "
            "licensed under CC-BY-4.0."
        ),
        redistribute_public=True,
        notes="Data WA catalogue `license_id: cc-by`, read 2026-08-15.",
    ),
    "dmirs_001_minedex": SourceLicence(
        source_id="dmirs_001_minedex",
        title="MINEDEX (DMIRS-001)",
        source_url="https://catalogue.data.wa.gov.au/dataset/minedex-dmirs-001",
        licence_id="CONFLICT:cc-nc-vs-cc-by",
        licence_url="https://creativecommons.org/licenses/by-nc/4.0/",
        attribution_text=(
            "Contains data sourced from the Department of Mines, Industry "
            "Regulation and Safety (DMIRS), MINEDEX (DMIRS-001). Attribution "
            "only; not for public redistribution pending licence resolution."
        ),
        redistribute_public=False,
        notes=(
            "Data WA's own catalogue record for minedex-dmirs-001 states "
            "`license_id: cc-nc` (CC-BY-NC-4.0). WA's Digital Atlas of "
            "Sustainability and Conservation (DASC) separately carries a "
            "blanket 'unless otherwise noted' CC-BY-4.0 statement -- and the "
            "Data WA catalogue label IS such a note, so the blanket statement "
            "cannot be read as overriding it. Every DASC bundle (measured "
            "2026-08-16) carries its own `Licence_CCBY4.pdf`, operative "
            'sentence verbatim: "With the exception of the Western '
            "Australia's Coat of Arms of State and other logos, and where "
            "otherwise noted, these data are provided under a Creative "
            'Commons Attribution 4.0 International Licence." D7 (delegated '
            "director ruling, "
            "docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md) "
            "adjudicated this exact conflict: the bundled PDF is explicit "
            "evidence of a CC-BY-4.0 grant, but `contrary_notice: false` "
            "cannot be recorded while Data WA -- an official government "
            "catalogue, not third-party commentary -- labels the same "
            "dataset CC-BY-NC-4.0, and DASC's own grant is qualified by "
            "'where otherwise noted'. Outcome: MINEDEX public redistribution "
            "STAYS CLOSED. The `adjudicate-minedex-licence` CLI command "
            "applies this ruling to a finalized snapshot's "
            '`licence_evidence.json` (`explicit_grant: "CC-BY-4.0"`, '
            "`contrary_notice: true`, `adjudicated: true`), and "
            "`minedex_redistribution_allowed` reads that record and stays "
            "False -- `contrary_notice: true` is exactly what keeps the gate "
            "closed. This registry entry itself stays False regardless of "
            "any run's captured or adjudicated evidence; the evidence check "
            "governs the export path, not this pinned record. The "
            "written-clarification route (emailing DMIRS) is unavailable "
            "under the project's standing directive against external "
            "data-request emails."
        ),
    ),
    "maus_v2": SourceLicence(
        source_id="maus_v2",
        title="Maus et al. global mining polygons, v2",
        source_url="https://doi.pangaea.de/10.1594/PANGAEA.942325",
        licence_id="CC-BY-SA-4.0",
        licence_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution_text=(
            'Contains data derived from Maus, V. et al., "An update on global '
            'mining land use" (PANGAEA.942325), licensed under CC-BY-SA-4.0. '
            "This derived work is licensed under CC-BY-SA-4.0."
        ),
        redistribute_public=True,
        notes=(
            "ShareAlike applies to the whole Maus-derived package (the "
            "design's D1: WA extract, crosswalk, trajectories over the Maus "
            "mask), applied conservatively -- no scalar-field carve-outs are "
            "asserted. That package publishes SEPARATELY under CC-BY-SA-4.0 "
            "with attribution, source link and modification statement; it is "
            "never folded into the project's own MIT-licensed code or into a "
            "CC-BY export of another source. See `export_gate.py`'s geometry "
            "drop, which keeps Maus geometry out of every other export path."
        ),
    ),
    "hansen_gfc": SourceLicence(
        source_id="hansen_gfc",
        title="Hansen/UMD/Google/USGS/NASA Global Forest Change (GFC-2024-v1.12)",
        source_url="https://glad.earthengine.app/view/global-forest-change",
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=f"{_HANSEN_DISPLAY_CREDIT}\n\n{_HANSEN_CITATION_CREDIT}",
        redistribute_public=True,
        notes=(
            "CC-BY-4.0, conditional on attaching BOTH credit strings verbatim "
            "-- they are NOT interchangeable. The display string "
            f"('{_HANSEN_DISPLAY_CREDIT}') is for anywhere the data are shown; "
            "the citation string (Hansen et al. 2013, Science 342:850-53) is "
            "for anywhere the source is cited in text. `attribution_text` "
            "carries both, separated by a blank line, so a caller that "
            "renders the whole field satisfies the grant either way. "
            "Unresolved and not adopted: the source page's own prose says "
            "`lossyear` has 'range 1-20' while the product claims 2001-2024 "
            "coverage -- a source-internal inconsistency, so any year bound "
            "is verified empirically against the granule, never read off the "
            "page alone."
        ),
    ),
    "dbca_060_fire": SourceLicence(
        source_id="dbca_060_fire",
        title="DBCA-060 Fire History",
        source_url="https://catalogue.data.wa.gov.au/dataset/fire-history-dbca-060",
        licence_id="open",
        licence_url="",
        attribution_text=(
            "Contains fire history data from the Department of Biodiversity, "
            "Conservation and Attractions (DBCA), Fire History (DBCA-060)."
        ),
        redistribute_public=True,
        notes=(
            "Open, jarrah-verified. Context-only use: the record is "
            "incomplete statewide, and its own metadata says it can include "
            "burns in mining-rehabilitation areas. 'Not recorded' is NEVER a "
            "known-negative fire label -- absence from the record never "
            "establishes absence of fire, per CLAUDE.md's absence-of-a-record "
            "rule. Every site-year this project derives from DBCA-060 carries "
            "`fire_status in {recorded, not_recorded, unknown}`, never a bare "
            "boolean."
        ),
    ),
    "silo": SourceLicence(
        source_id="silo",
        title="SILO Climate Database",
        source_url="https://www.longpaddock.qld.gov.au/silo/",
        licence_id="open-with-account",
        licence_url="https://www.longpaddock.qld.gov.au/silo/access-data/",
        attribution_text=(
            "Contains climate data sourced from the Queensland Government's "
            "SILO climate database (longpaddock.qld.gov.au/silo)."
        ),
        redistribute_public=True,
        notes=(
            "Open access, but each fetch is gated behind a registered email "
            "address (an API-key-shaped account credential, not a payment or "
            "a use restriction) rather than being anonymous. Derived rainfall "
            "context is redistributable."
        ),
    ),
    "wa_rdc_regions": SourceLicence(
        source_id="wa_rdc_regions",
        title="WA Regional Development Commission Boundaries (DPIRD-020)",
        source_url=(
            "https://catalogue.data.wa.gov.au/dataset/regional-development-commission-boundaries"
        ),
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=(
            "Contains Regional Development Commission Boundaries (DPIRD-020) "
            "data © Department of Primary Industries and Regional Development "
            "(WA), licensed under CC-BY-4.0."
        ),
        redistribute_public=True,
        notes=(
            "The nine regions defined under the Regional Development "
            "Commissions Act 1993, pinned as the design doc D4's 'official "
            "Pilbara and Goldfields-Esperance boundaries'. Licence read from "
            "the Data WA catalogue record for DPIRD-020 (CC-BY-4.0); "
            "re-verified 2026-08-21 via the CKAN API when the fetch was "
            "re-pinned from data-downloads.slip.wa.gov.au (now SSO-gated) to "
            "the SLIP public ArcGIS REST layer "
            "public-services.slip.wa.gov.au/.../Boundaries/MapServer/25, "
            "served as GeoJSON in GDA2020 / EPSG:4326. Pinned 2026-08-16, "
            "re-pinned 2026-08-21."
        ),
    ),
}


def licence_for_collection(collection_id: str) -> SourceLicence:
    """Return the SourceLicence whose `source_url` pins `collection_id`.

    Used by the DEA catalogue fetch to compare a captured collection's own
    `license` field against the pinned record: the licence gate re-reads
    the licence from the captured JSON rather than trusting this table
    (D13 Batch C gate: "DEA licences must be re-read from the captured
    collection JSON").
    """
    for record in SOURCES.values():
        if record.source_url.endswith(f"/collections/{collection_id}"):
            return record
    raise KeyError(f"no pinned licence record for collection {collection_id!r}")


def minedex_redistribution_allowed(evidence_dir: Path, *, require_hashed: bool = True) -> bool:
    """True only when captured evidence explicitly places MINEDEX under CC-BY-4.0.

    Reads `evidence_dir / EVIDENCE_FILENAME`, per the design doc's MINEDEX
    binding rule (§6): a public export of MINEDEX-derived data is blocked
    unless the exact DASC download's captured metadata explicitly places
    that resource under CC-BY-4.0 with no contrary notice. Returns `True`
    only when ALL of:

    - the evidence file exists and parses as a JSON object;
    - `explicit_grant == "CC-BY-4.0"` exactly;
    - `contrary_notice is False` exactly (not merely falsy -- a missing or
      differently-typed key is not evidence of absence);
    - `evidence_files` is a non-empty list of strings, EACH of which names a
      real file that actually exists inside `evidence_dir` -- so the
      "captured" claim is checkable against named files rather than
      asserted bare. A name is resolved against `evidence_dir` and the
      resolved path must stay inside `evidence_dir` (containment check
      against `Path(evidence_dir).resolve()`): otherwise an entry such as
      `"../../etc/hosts"` or an absolute path elsewhere on disk could name a
      file this function was never meant to vouch for;
    - (with `require_hashed=True`, the default) every `evidence_files`
      entry also appears, with a matching digest, in the snapshot's
      `SHA256SUMS.txt` -- see `minedex_evidence_is_hashed`. This is what
      stops a file dropped into an already-finalized snapshot from flipping
      this answer after the fact: `SHA256SUMS.txt` has no opinion on files
      it never hashed, so a newcomer named by a dropped-in evidence file
      fails the lookup even though it exists on disk. Pass
      `require_hashed=False` only for a pre-finalize check DURING capture,
      before `snapshots.finalize_snapshot` has run; a `True` obtained that
      way covers nothing once the snapshot is finalized.

    Fails closed on every other outcome, including a missing file, invalid
    JSON, a non-object JSON value, missing keys, an `evidence_files` entry
    that is not a string, one that is missing, or one that resolves outside
    `evidence_dir` -- this function NEVER raises. An absent or malformed
    evidence file is not evidence of permission; it is the default, closed
    state this module's docstring describes for MINEDEX as a whole. This
    function governs one run's captured evidence for an export path; it
    never mutates `SOURCES["dmirs_001_minedex"]`, which stays pinned
    `redistribute_public=False` regardless of what any evidence file says.
    """
    evidence_dir = Path(evidence_dir)
    evidence_path = evidence_dir / EVIDENCE_FILENAME
    try:
        raw = evidence_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, ValueError):
        return False

    if not isinstance(payload, dict):
        return False

    explicit_grant = payload.get("explicit_grant")
    contrary_notice = payload.get("contrary_notice")
    evidence_files = payload.get("evidence_files")

    if explicit_grant != _REQUIRED_GRANT:
        return False
    if contrary_notice is not False:
        return False
    if not isinstance(evidence_files, list) or len(evidence_files) == 0:
        return False
    if not all(_evidence_file_is_real(evidence_dir, name) for name in evidence_files):
        return False
    return not require_hashed or minedex_evidence_is_hashed(evidence_dir)


def minedex_evidence_is_hashed(evidence_dir: Path) -> bool:
    """True when `EVIDENCE_FILENAME` AND every `evidence_files` entry appear,
    with matching digests, in `SHA256SUMS.txt`.

    Two distinct checks, both required. First, `EVIDENCE_FILENAME` itself
    (`licence_evidence.json`) must be listed in the snapshot's
    `SHA256SUMS.txt` AND its current sha256 must match the recorded digest.
    The listing half rejects an evidence JSON that did not exist at finalize
    time at all: without it, a `licence_evidence.json` dropped in alone --
    naming files finalize already hashed, such as `metadata.txt` or a
    downloaded zip -- flips this function to True, because those named files
    genuinely do appear, OK, in `SHA256SUMS.txt`. The digest half rejects an
    UNSIGNED edit of the evidence JSON inside a finalized snapshot: an edit
    that, say, flips `contrary_notice` to false without re-signing would
    otherwise pass here and flip `minedex_redistribution_allowed` open,
    contradicting that function's post-finalize guarantee. No exemption is
    needed for the legitimate adjudication flow -- `adjudicate-minedex-
    licence` rewrites this file and then re-signs its one `SHA256SUMS.txt`
    line via `snapshots.update_snapshot_entry` (the declared, narrow
    exception to post-finalize immutability), so in every legitimate state
    the digest matches. An earlier revision of this function checked listing
    only, justified by the adjudication edit; that justification lapsed the
    moment the adjudication flow started re-signing, and the digest check
    closed the gap.

    Second, for every `evidence_files` entry: the entry's path is listed in
    the snapshot's `SHA256SUMS.txt` (via `snapshots.snapshot_entries`), AND
    the file's current sha256 matches the recorded digest -- a
    listed-but-since-tampered file is as unhashed as an unlisted one. This
    is the mechanism behind `minedex_redistribution_allowed`'s post-finalize
    guarantee: a `licence_evidence.json` plus the files it names, dropped
    into an already-`finalize_snapshot`-d directory, name files
    `SHA256SUMS.txt` never hashed, so they fail the lookup here even though
    they exist.

    Fails closed and never raises: a missing or unparseable
    `SHA256SUMS.txt` (snapshot never finalized), a missing or malformed
    evidence JSON, an evidence JSON absent from `SHA256SUMS.txt` (dropped in
    after finalize) or whose content no longer matches its recorded digest
    (edited without re-signing), an empty `evidence_files`, an entry that is
    not a string, escapes `evidence_dir`, is unlisted, or no longer matches
    its recorded digest -- all return False.
    """
    evidence_dir = Path(evidence_dir)
    try:
        payload = json.loads((evidence_dir / EVIDENCE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    evidence_files = payload.get("evidence_files")
    if not isinstance(evidence_files, list) or len(evidence_files) == 0:
        return False

    try:
        recorded = snapshot_entries(evidence_dir)
    except (OSError, ValueError):
        return False

    recorded_evidence_digest = recorded.get(EVIDENCE_FILENAME)
    if recorded_evidence_digest is None:
        return False
    try:
        if sha256_file(evidence_dir / EVIDENCE_FILENAME) != recorded_evidence_digest:
            return False
    except OSError:
        return False

    evidence_root = evidence_dir.resolve()
    for name in evidence_files:
        if not _evidence_file_is_real(evidence_dir, name):
            return False
        assert isinstance(name, str)  # narrowed by _evidence_file_is_real
        relative_path = (evidence_dir / name).resolve().relative_to(evidence_root).as_posix()
        digest = recorded.get(relative_path)
        if digest is None:
            return False
        try:
            if sha256_file(evidence_dir / relative_path) != digest:
                return False
        except OSError:
            return False
    return True


def _evidence_file_is_real(evidence_dir: Path, name: object) -> bool:
    """True when `name` is a string naming a real file that stays inside `evidence_dir`.

    `evidence_dir` is already the resolved directory `minedex_redistribution_
    allowed` reads from. A relative entry such as `"../../etc/hosts"`, or an
    absolute entry naming a file elsewhere on disk, resolves outside
    `evidence_dir` and is rejected by the containment check -- so an
    `evidence_files` entry can only ever vouch for a file actually captured
    alongside the evidence JSON itself.
    """
    if not isinstance(name, str) or not name:
        return False
    evidence_root = evidence_dir.resolve()
    try:
        candidate = (evidence_dir / name).resolve()
    except (OSError, RuntimeError):
        return False
    if evidence_root not in (candidate, *candidate.parents):
        return False
    return candidate.is_file()
