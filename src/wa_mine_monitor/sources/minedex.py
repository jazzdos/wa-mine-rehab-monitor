"""DMIRS-001 MINEDEX: pinned DASC bundle endpoints, licence-evidence capture, fetch and validation.

MINEDEX is the one source in this project's registry with a genuine licence
CONFLICT (`wa_mine_monitor.licence`'s module docstring): Data WA's own
catalogue record states CC-BY-NC-4.0, while WA's Digital Atlas of
Sustainability and Conservation (DASC) carries a blanket "unless otherwise
noted" CC-BY-4.0 statement -- and the Data WA label IS such a note. This
module's fetch-side job is deliberately narrow: download the exact DASC
bundles, extract the exact in-bundle licence PDF and CAPTURE the Data WA
catalogue's own metadata record, without ever ADJUDICATING them.
`capture_licence_evidence` always writes `explicit_grant: null,
contrary_notice: null, adjudicated: false` -- the D7 ruling
(`docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`)
is applied by a SEPARATE step, the CLI's `adjudicate-minedex-licence`
command, against an already-finalized snapshot;
`licence.minedex_redistribution_allowed` reads the adjudication record and
stays `False` until that command has run.

**D6 acquisition route (2026-08-16).** The SLIP direct-download GeoPackage
endpoint this module used to pin --
`https://data-downloads.slip.wa.gov.au/DMIRS-001/Geopackage` -- was measured
auth-gated on 2026-08-16 (Task 11 live acceptance run): it returns a Landgate
SSO login page (`sso.slip.wa.gov.au`), not data. Per D6, this module now
downloads TWO DMIRS DASC (`dasc.dmirs.wa.gov.au`) statewide GDA2020 bundles
instead -- an ESRI Shapefile zip (id 3978) and a CSV database zip (id 3981),
both unauthenticated -- and finalizes them ATOMICALLY: any download,
capture or validation failure in either bundle leaves the whole snapshot
unfinalized. Each zip is preserved byte-for-byte; validation reads members
directly, with no capture-time conversion.

Fixture-first rule: nothing in this module's unit tests touches the network.
`download_minedex_zip` and `fetch_datawa_package_show`'s request-shaping are
each checked against a fake `requests.get`; a real download or metadata
fetch only ever happens when an operator runs the `fetch-minedex` CLI
command, which monkeypatches both functions out entirely in its own tests.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from wa_mine_monitor.licence import EVIDENCE_FILENAME
from wa_mine_monitor.provenance import sha256_file

#: Pinned DMIRS-001 DASC ESRI Shapefile bundle download endpoint -- DASC
#: file id 3978, "MINEDEX - [GDA2020]" SHP product, measured by direct
#: download 2026-08-16 (D6). A bare constant, not a function wrapping it --
#: see `sources/tenements.py`'s `DASC_TENEMENTS_SHP_URL` for the identical
#: convention and its rationale.
DASC_MINEDEX_SHP_URL = "https://dasc.dmirs.wa.gov.au/Download/File/3978"

#: Pinned DMIRS-001 DASC CSV database bundle download endpoint -- DASC file
#: id 3981, measured by direct download 2026-08-16 (D6).
DASC_MINEDEX_CSV_URL = "https://dasc.dmirs.wa.gov.au/Download/File/3981"

#: Data WA CKAN `package_show` metadata-record endpoint for MINEDEX,
#: measured 2026-08-16 -- the exact resource `capture_licence_evidence`'s
#: `datawa_package_show.json` is captured from, per CLAUDE.md's sourcing
#: rule (the exact resource, never a general agency page).
DATAWA_PACKAGE_SHOW_URL = (
    "https://catalogue.data.wa.gov.au/api/3/action/package_show?id=minedex-dmirs-001"
)

#: The exact CKAN package `name` a genuine `package_show` response for this
#: dataset must carry -- `fetch_datawa_package_show` checks the fetched body
#: against this before it is ever captured as evidence, so an HTTP 200
#: carrying an SSO login page, an empty body, or an unrelated CKAN error
#: object (this project's D6 finding, on the SLIP endpoint) cannot be
#: recorded as though it were "a captured Data WA metadata record".
DATAWA_PACKAGE_NAME = "minedex-dmirs-001"

#: Filenames the two downloaded DASC zips are written to inside a snapshot
#: directory.
MINEDEX_SHP_ZIP_FILENAME = "minedex_gda2020_shp.zip"
MINEDEX_CSV_ZIP_FILENAME = "minedex_gda2020_csv.zip"

#: The exact set of members the DASC MINEDEX SHP zip is expected to contain,
#: measured by direct download 2026-08-16.
MINEDEX_SHP_REQUIRED_MEMBERS: tuple[str, ...] = (
    "Minedex.cpg",
    "Minedex.dbf",
    "Minedex.prj",
    "Minedex.shp",
    "Minedex.shx",
    "MINEDEX_Database_DataDictionary_GDA2020.pdf",
    "Minedex_Database_Metadata_GDA2020.pdf",
    "Licence_CCBY4.pdf",
)

#: The MINIMUM set of members the DASC MINEDEX CSV zip must contain. The
#: real bundle carries several more CSVs (`SiteTenements.csv`,
#: `SiteProduction.csv`, `ResourceEstimates.csv`, ...) that this project
#: does not currently read; those are reported in the validation summary's
#: `csv_members` list but never required, so a future DASC bundle revision
#: adding or dropping an unused member does not spuriously refuse a fetch.
MINEDEX_CSV_REQUIRED_MEMBERS: tuple[str, ...] = (
    "Licence_CCBY4.pdf",
    "Sites.csv",
    "ProjectsOwners.csv",
)

#: The shapefile's basename inside the SHP zip, without extension -- used to
#: build the `/vsizip/` GDAL virtual-filesystem path, without ever
#: extracting the zip to a temporary directory.
MINEDEX_SHAPEFILE_BASENAME = "Minedex"

#: Expected CRS of the DASC MINEDEX shapefile, measured by direct download
#: 2026-08-16 -- GDA2020.
MINEDEX_EXPECTED_CRS = "EPSG:7844"

#: Filename `capture_licence_evidence` extracts the in-bundle licence PDF to
#: -- the exact member name inside both DASC zips.
LICENCE_PDF_FILENAME = "Licence_CCBY4.pdf"

#: Filename `capture_licence_evidence` writes the captured Data WA
#: `package_show` JSON text to, when the fetch succeeded.
DATAWA_METADATA_FILENAME = "datawa_package_show.json"

#: User-Agent sent with every request, identifying this project rather than
#: falling back to `requests`' default -- some endpoints treat an
#: unidentified client as a bot and block it.
_USER_AGENT = "wa-mine-rehab-monitor/0.1 (github.com/jazzdos/wa-mine-rehab-monitor)"

#: Streaming download timeout in seconds. Explicit because `requests` has no
#: default timeout at all: an unresponsive endpoint would otherwise hang a
#: fetch indefinitely.
_DOWNLOAD_TIMEOUT_SECONDS = 60.0

#: Timeout for the (much smaller, non-streamed) Data WA metadata fetch.
_METADATA_FETCH_TIMEOUT_SECONDS = 30.0

#: Chunk size for the streamed zip download. A MINEDEX bundle can run to
#: tens of megabytes; this never buffers the whole response in memory.
_DOWNLOAD_CHUNK_SIZE = 1_048_576  # 1 MiB

#: Maximum number of example orphan `ProjectCode` values named in a
#: `validate_minedex_bundles` refusal -- enough to diagnose the defect
#: without dumping an unbounded list into a structured refusal.
_MAX_ORPHAN_EXAMPLES = 5

#: Maximum number of example duplicated `SITE_CODE`/`SiteCode` values named
#: in a `validate_minedex_bundles` refusal, same reasoning as
#: `_MAX_ORPHAN_EXAMPLES`.
_MAX_DUPLICATE_EXAMPLES = 5

#: `dtype` pin for `pd.read_csv` over MINEDEX's `Sites.csv`/
#: `ProjectsOwners.csv` -- `ProjectCode`/`SiteCode`/`OwnerCode` are join and
#: identity KEYS shared across the two independently-read frames, and
#: pandas infers each frame's dtype separately. A numeric `ProjectCode`
#: column with no null cell in one frame infers `int64` ("1234") while the
#: SAME column, in the OTHER frame, carrying even one null cell (`Sites.csv`
#: carries 5,822 on the real 2026-08-14 extract) infers `float64`
#: ("1234.0") -- `register.owners_by_project`/`owner_join_disclosures` join
#: on `astype(str)`, which stringifies whichever value each frame ALREADY
#: inferred, not the source digits, so every owner for that project
#: silently fails to match and the loss is reported as an unmatched-project
#: count rather than a read-time schema drift. Pinned to pandas' nullable
#: `"string"` dtype so a blank cell reads back as `pd.NA` (caught by
#: `.notna()` the same way a float `NaN` was) while every code column reads
#: as its own literal digits regardless of what the OTHER frame's column
#: happens to contain. A column named here that a given CSV member does not
#: carry (e.g. `Sites.csv` has no `OwnerCode`) is silently ignored by
#: `pd.read_csv` rather than raising -- verified against pandas 3.0.5, the
#: version this project pins.
MINEDEX_CODE_COLUMN_DTYPES: dict[str, str] = {
    "ProjectCode": "string",
    "SiteCode": "string",
    "OwnerCode": "string",
}


class SnapshotValidationError(Exception):
    """A downloaded DMIRS-001 DASC MINEDEX bundle pair failed validation.

    Always names the offending zip path (or the bundle pair as a whole, for
    a cross-bundle check) in its message, so a caller does not have to
    reconstruct which snapshot failed from a bare traceback.
    """


class LicenceEvidenceCaptureError(Exception):
    """Capturing MINEDEX licence evidence from a downloaded bundle failed.

    Always names the offending zip path and the specific defect, so a
    caller does not have to reconstruct which capture step failed from a
    bare traceback. Distinct from `SnapshotValidationError`: this covers
    only the evidence-capture step (extracting the licence PDF), not the
    bundle-content validation `validate_minedex_bundles` performs.
    """


class DataWaMetadataValidationError(Exception):
    """A fetched Data WA `package_show` body does not describe MINEDEX.

    Raised by `fetch_datawa_package_show` before the fetched text is ever
    returned -- an HTTP 200 status is not evidence the body is the metadata
    record it claims to be (D6's own finding: the SLIP endpoint returned an
    HTTP 200 Landgate SSO login page, not data). `fetch-minedex` catches
    this the same broad way it catches every other `fetch_datawa_package_
    show` failure (the fetch is best-effort by design), so this exception
    never needs its own CLI-layer handling -- it converts a would-be false
    positive into the SAME `datawa_json_text = None` /
    `datawa_fetch_failed: true` outcome a network failure already produces.
    """


def download_minedex_zip(
    url: str,
    dest_path: Path,
    *,
    timeout: float = _DOWNLOAD_TIMEOUT_SECONDS,
    user_agent: str = _USER_AGENT,
) -> Path:
    """Stream-download the DASC zip at `url` to `dest_path`.

    One function shared by both MINEDEX bundle downloads (SHP and CSV) --
    `fetch-minedex` calls it twice, once per `url`, so a test can
    monkeypatch a single seam and dispatch on `url` to decide which fixture
    zip to hand back, rather than needing two separately-patched functions.

    Streams in fixed-size chunks rather than buffering the whole response,
    with an explicit timeout (`requests` has none by default) and an
    explicit User-Agent identifying this project. Raises `requests.HTTPError`
    on a non-2xx response (`raise_for_status`) before anything is written to
    `dest_path` beyond the (empty) file `open(..., "wb")` creates.

    Never exercised against a real endpoint by a unit test -- see this
    module's fixture-first rule.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        url, stream=True, timeout=timeout, headers={"User-Agent": user_agent}
    ) as response:
        response.raise_for_status()
        with open(dest_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    handle.write(chunk)
    return dest_path


def fetch_datawa_package_show(
    url: str,
    *,
    timeout: float = _METADATA_FETCH_TIMEOUT_SECONDS,
    user_agent: str = _USER_AGENT,
) -> str:
    """Fetch the Data WA CKAN `package_show` record at `url` and return its raw text.

    A plain (non-streamed) GET with the same explicit-timeout,
    explicit-User-Agent discipline as `download_minedex_zip`. Raises
    `requests.HTTPError` on a non-2xx response, and propagates any other
    `requests` exception (connection failure, timeout) to the caller
    unchanged. This function is deliberately NOT best-effort itself -- the
    `fetch-minedex` CLI command is what treats this fetch as best-effort, by
    catching whatever this raises and recording `datawa_fetch_failed` in the
    captured evidence rather than aborting the whole run; a bare fetch
    helper that silently swallowed its own errors would hide that decision
    from its caller.

    An HTTP 200 status is NOT evidence the body is a genuine `package_show`
    record for this dataset -- D6's own finding, on the sibling SLIP
    endpoint, was an HTTP 200 auth-gated login page. So before returning,
    this function also checks the body: it must parse as a JSON object,
    carry `result.name == DATAWA_PACKAGE_NAME`, and carry a non-empty
    `result.license_id`. Any of those failing raises `DataWaMetadataValidationError`
    -- which `fetch-minedex`'s existing best-effort `except Exception` catches
    exactly like a network failure, so a non-record body is treated as a
    FAILED fetch (`datawa_fetch_failed: true`) rather than being captured
    and listed as evidence.

    Returns the raw response TEXT, not a parsed object: `capture_licence_
    evidence` writes it verbatim to `datawa_package_show.json`, so the
    captured evidence is byte-for-byte what Data WA actually returned, not
    this project's own re-serialisation of a parsed structure.

    Never exercised against a real endpoint by a unit test -- see this
    module's fixture-first rule.
    """
    response = requests.get(url, timeout=timeout, headers={"User-Agent": user_agent})
    response.raise_for_status()
    text = str(response.text)
    _validate_datawa_package_show_text(text)
    return text


def _validate_datawa_package_show_text(text: str) -> None:
    """Raise `DataWaMetadataValidationError` unless `text` parses as a
    genuine CKAN `package_show` record for `DATAWA_PACKAGE_NAME`.

    Checks, in order, naming the specific defect: `text` parses as JSON;
    the parsed value is an object; its `result` field is an object; that
    object's `name` equals `DATAWA_PACKAGE_NAME` exactly; that object's
    `license_id` is present and non-empty. A CKAN error envelope
    (`{"success": false, ...}`, no `result`) and an unrelated package's
    record both fail the `name` check, and an HTML login page fails the
    JSON-parse check first.
    """
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DataWaMetadataValidationError(
            f"Data WA package_show response does not parse as JSON ({exc})"
        ) from exc

    if not isinstance(payload, dict):
        raise DataWaMetadataValidationError(
            "Data WA package_show response does not parse as a JSON object"
        )

    result = payload.get("result")
    if not isinstance(result, dict):
        raise DataWaMetadataValidationError(
            "Data WA package_show response has no 'result' object -- not a genuine "
            f"package_show record (top-level keys: {sorted(payload)})"
        )

    if result.get("name") != DATAWA_PACKAGE_NAME:
        raise DataWaMetadataValidationError(
            f"Data WA package_show response names package {result.get('name')!r}, "
            f"expected {DATAWA_PACKAGE_NAME!r}"
        )

    if not result.get("license_id"):
        raise DataWaMetadataValidationError(
            "Data WA package_show response's result has no non-empty 'license_id'"
        )


def capture_licence_evidence(
    snapshot_dir: Path,
    csv_zip_path: Path,
    datawa_json_text: str | None,
    *,
    captured: str,
) -> Path:
    """Capture MINEDEX licence evidence into `snapshot_dir`, WITHOUT adjudicating it.

    Extracts `Licence_CCBY4.pdf` BYTE-IDENTICALLY from `csv_zip_path` into
    `snapshot_dir` -- the bytes written are read straight from the zip
    member with no transformation, and the extracted file's own sha256 is
    asserted to match the in-zip member's sha256 before this function
    returns, raising `LicenceEvidenceCaptureError` (naming `csv_zip_path`)
    on any mismatch or if the member is absent. Writes `datawa_package_show.
    json` (verbatim) when `datawa_json_text` is not `None`; when it IS
    `None`, no metadata file is written and `datawa_fetch_failed: true` is
    recorded in the evidence JSON instead -- the Data WA fetch is
    best-effort at the CLI layer (see `fetch_datawa_package_show`'s
    docstring).

    Always writes `licence_evidence.json`
    (`wa_mine_monitor.licence.EVIDENCE_FILENAME`) with:

    - `resource`: a mapping naming the EXACT resource each evidence file was
      captured from (`{"licence_pdf": DASC_MINEDEX_CSV_URL,
      "datawa_package_show": DATAWA_PACKAGE_SHOW_URL}`) -- the exact
      resource, never a general agency page, per CLAUDE.md's sourcing rule;
    - `explicit_grant: null`, `contrary_notice: null`, `adjudicated: false`
      -- ALWAYS, regardless of what either captured document says. This
      function never reads or interprets the captured PDF/JSON; the D7
      ruling is applied later by the CLI's `adjudicate-minedex-licence`
      command against an already-finalized snapshot. Capture is a
      fetch-and-record step, not a decision;
    - `captured`: the `captured` argument, verbatim -- the caller's snapshot
      date, never computed from the clock here (same discipline as
      `snapshots.py`'s module docstring);
    - `evidence_files`: the list of files this call ACTUALLY wrote -- always
      includes `Licence_CCBY4.pdf` (extraction failure raises rather than
      silently omitting it), and includes `datawa_package_show.json` only
      when `datawa_json_text` is not `None`.

    Never overwrites an existing `licence_evidence.json` whose `adjudicated`
    is `True`: an earlier `adjudicate-minedex-licence` run has already
    filled in this file's grant fields, and a re-run of this function,
    however it was reached, must never silently revert it back to a fresh,
    unadjudicated `null`/`false` capture. When that guard fires, this
    function writes NOTHING and simply returns the existing evidence path
    unchanged. This is defense in depth: the primary guard against a doomed
    re-run lives in the `fetch-minedex` CLI command, which refuses before
    ANY download or write happens once a snapshot is already finalized;
    this guard protects the same invariant at the function level.

    Returns the path to the written (or, on the adjudicated guard, existing)
    `licence_evidence.json`.

    Because `explicit_grant`/`contrary_notice` are always `None` on a fresh
    write, `licence.minedex_redistribution_allowed(evidence_dir=snapshot_dir)`
    is `False` immediately after every non-guarded call to this function,
    under EITHER `require_hashed` setting -- capture never unblocks
    anything.
    """
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    evidence_path = snapshot_dir / EVIDENCE_FILENAME
    if evidence_path.exists():
        try:
            existing_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing_payload = None
        if isinstance(existing_payload, dict) and existing_payload.get("adjudicated") is True:
            return evidence_path

    evidence_files: list[str] = []
    payload: dict[str, object] = {
        "resource": {
            "licence_pdf": DASC_MINEDEX_CSV_URL,
            "datawa_package_show": DATAWA_PACKAGE_SHOW_URL,
        },
        "explicit_grant": None,
        "contrary_notice": None,
        "adjudicated": False,
        "captured": captured,
    }

    csv_zip_path = Path(csv_zip_path)
    try:
        with zipfile.ZipFile(csv_zip_path) as zf:
            pdf_bytes = zf.read(LICENCE_PDF_FILENAME)
    except (OSError, zipfile.BadZipFile) as exc:
        raise LicenceEvidenceCaptureError(
            f"{csv_zip_path}: not a readable zip ({exc}) -- cannot capture licence evidence"
        ) from exc
    except KeyError as exc:
        raise LicenceEvidenceCaptureError(
            f"{csv_zip_path}: does not contain {LICENCE_PDF_FILENAME!r} -- "
            "cannot capture licence evidence"
        ) from exc

    expected_digest = hashlib.sha256(pdf_bytes).hexdigest()
    licence_pdf_path = snapshot_dir / LICENCE_PDF_FILENAME
    licence_pdf_path.write_bytes(pdf_bytes)
    written_digest = sha256_file(licence_pdf_path)
    if written_digest != expected_digest:
        raise LicenceEvidenceCaptureError(
            f"{licence_pdf_path}: extracted bytes sha256 {written_digest} does not "
            f"match the in-zip member's sha256 {expected_digest} -- byte-identical "
            "extraction failed"
        )
    evidence_files.append(LICENCE_PDF_FILENAME)

    if datawa_json_text is not None:
        datawa_path = snapshot_dir / DATAWA_METADATA_FILENAME
        datawa_path.write_text(datawa_json_text, encoding="utf-8")
        evidence_files.append(DATAWA_METADATA_FILENAME)
    else:
        payload["datawa_fetch_failed"] = True

    payload["evidence_files"] = evidence_files

    evidence_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence_path


def _extract_dates(values: pd.Series) -> set[str]:
    """Normalise an EXTRACT_DA/EXTRACT_DATE series to a set of ISO date strings.

    Identical normalisation to `sources.tenements._extract_dates` --
    duplicated rather than shared, matching this project's existing
    convention of a self-contained `sources/*.py` module per source (see
    `download_tenements_zip`/`download_minedex_zip`, which likewise
    duplicate near-identical streamed-download logic rather than sharing
    it). See that function's docstring for the reasoning.
    """
    parsed = pd.to_datetime(values, errors="coerce", dayfirst=True)
    normalized: set[str] = set()
    for raw_value, parsed_value in zip(values, parsed, strict=True):
        if pd.isna(parsed_value):
            normalized.add(str(raw_value))
        else:
            normalized.add(parsed_value.date().isoformat())
    return normalized


def _read_zip_members(zip_path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return set(zf.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise SnapshotValidationError(f"{zip_path}: not a readable zip ({exc})") from exc


def _read_csv_member(zip_path: Path, member_name: str) -> pd.DataFrame:
    """Read `member_name` out of `zip_path` as a CSV, or raise
    `SnapshotValidationError` naming the member and the underlying cause.

    Mirrors the shapefile read three lines above this function's original
    position (`gpd.read_file` wrapped in `except Exception`): a malformed
    CSV member inside an otherwise well-formed bundle -- ragged rows
    (`pandas.errors.ParserError`), non-UTF-8 bytes (`UnicodeDecodeError`),
    an empty member (`pandas.errors.EmptyDataError`), or the zip/member
    itself being unreadable (`OSError`, `zipfile.BadZipFile`) -- must
    become the SAME structured `SnapshotValidationError` every other check
    in `validate_minedex_bundles` raises, never an uncaught traceback: the
    CLI's `except MinedexSnapshotValidationError` clause is the only thing
    standing between this and an empty-stdout crash.

    Read under `MINEDEX_CODE_COLUMN_DTYPES` -- see that constant's
    docstring for why `ProjectCode`/`SiteCode`/`OwnerCode` must never be
    left to per-frame dtype inference.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf, zf.open(member_name) as handle:
            return pd.read_csv(handle, encoding="utf-8-sig", dtype=MINEDEX_CODE_COLUMN_DTYPES)
    except Exception as exc:
        raise SnapshotValidationError(
            f"{zip_path}: {member_name} is not readable as CSV ({exc})"
        ) from exc


def validate_minedex_bundles(shp_zip_path: Path, csv_zip_path: Path) -> dict[str, object]:
    """Validate a downloaded DASC MINEDEX SHP+CSV bundle pair TOGETHER and summarise it.

    Implements D6's atomicity requirement: the two bundles are validated as
    ONE cross-checked pair, and any single check below failing refuses the
    WHOLE pair (`SnapshotValidationError`), so `fetch-minedex` never
    finalizes a snapshot from a shapefile and a CSV set that disagree with
    each other. Checks run in this order, each naming the specific numbers
    involved in its refusal:

    (a) every member of `MINEDEX_SHP_REQUIRED_MEMBERS` is present in the SHP
        zip and every member of `MINEDEX_CSV_REQUIRED_MEMBERS` is present in
        the CSV zip -- EVERY missing member named, from both zips together;
    (b) the shapefile (read via `/vsizip/`, never extracted to a temp
        directory) is non-empty, carries Point-only geometry (a present,
        non-empty geometry that is not `"Point"` refuses; a null/empty
        geometry is tolerated -- MINEDEX carries sites with no resolvable
        coordinates), has CRS exactly `MINEDEX_EXPECTED_CRS`, and carries
        `SITE_CODE` and `EXTRACT_DA` columns;
    (c) `Sites.csv`'s `Stage` value counts reconcile EXACTLY against its own
        row total. NOTE what this actually guards: `pandas.Series.
        value_counts(dropna=False)` always sums to `len(series)` by
        construction, so this cannot catch any genuine defect in
        `Sites.csv` itself -- it guards this FUNCTION's own rendering of
        the count-table keys through `str()`, which collapses two distinct
        raw values onto the same string key (e.g. a literal `NaN` and the
        literal string `"nan"` both stringifying to `"nan"`) and would
        silently undercount `stage_total` if that ever happened;
    (d) exactly one distinct normalised extract date (`_extract_dates`)
        across the shapefile's `EXTRACT_DA`, `Sites.csv`'s `EXTRACT_DATE`
        and `ProjectsOwners.csv`'s `EXTRACT_DATE` -- naming every distinct
        value found, per source, on refusal;
    (e) the shapefile's `SITE_CODE` set is a SUBSET of `Sites.csv`'s
        `SiteCode` set -- refused when it is not (a code the shapefile
        carries and `Sites.csv` does not is unexplainable either way).
        `Sites.csv` is EXPECTED to carry additional codes the shapefile does
        not: `Sites.csv` is `MINEDEX`'s full site register, while the
        shapefile is a POINT format that structurally cannot carry a
        coordinate-less site. That excess (`Sites.csv`-only codes) is
        refused only when it does NOT reconcile exactly against the
        distinct `SiteCode`s of `Sites.csv`'s own null-`Latitude`/
        `Longitude` rows -- measured against the real 2026-08-14 DASC
        extract, the two sets are IDENTICAL (351 == 351). A `Sites.csv`-only
        code that is NOT among the null-coordinate rows is refused by name,
        because it would mean the shapefile is missing a site `Sites.csv`
        says HAS coordinates. This replaces a former row-count-equality
        check between the shapefile and `Sites.csv` that refused on the
        healthy product (48,402 shapefile features against 50,164 `Sites.
        csv` rows is the NORMAL shape of a real extract, driven by the
        coordinate-less sites above, not a defect) and a former exact
        set-equality check that refused on the identical healthy product for
        the same reason;
    (f) `SITE_CODE` is unique within the shapefile -- refused when it is
        not, naming the duplicate count and up to `_MAX_DUPLICATE_EXAMPLES`
        example codes. A point-shapefile row IS a register row, so a
        duplicated `SITE_CODE` there breaks the one-site-one-feature
        invariant outright; measured against the real 2026-08-14 extract
        the shapefile carries zero duplicates, so this stays a hard refusal.
        `Sites.csv`'s `SiteCode` duplication is a DIFFERENT, disclosed
        quantity -- see `n_duplicate_site_code_values`/
        `n_site_code_rows_duplicated` below, never refused on: the real
        extract carries 1,327 duplicated `SiteCode` values (1,411 excess
        rows) and refusing on that would block the healthy product exactly
        as the former row-count-equality check did.

    Two further cross-bundle counts are DISCLOSED in the summary rather than
    refused on, per the same reasoning -- each is a real, expected shape of
    the healthy 2026-08-14 extract, not evidence of a corrupted download:
    `n_duplicate_site_code_values`/`n_site_code_rows_duplicated` (`Sites.csv`
    `SiteCode` duplication -- see (f) above) and
    `n_orphan_owner_project_codes` (the number of distinct `ProjectsOwners.
    csv` `ProjectCode` values absent from `Sites.csv`'s `ProjectCode` column
    -- the real extract carries 179). A future bundle revision where these
    climb far outside the measured range is a question for whoever reads the
    manifest, not something this function can adjudicate from inside a
    single snapshot with no prior extract to compare against.

    Returns a summary dict with (at least): `shp_members`, `csv_members`,
    `feature_count`, `crs`, `sites_row_count`, `stage_counts`,
    `extract_date`, `n_sites_null_project_code`, `n_sites_null_coordinates`,
    `coordinate_columns_present`, `n_projects`, `n_projects_with_current_owner`
    (current = `EndDate` null or blank), `n_sites_absent_from_shapefile`
    (distinct `Sites.csv`-only `SiteCode`s, i.e. (e)'s reconciled excess),
    `n_duplicate_site_code_values`, `n_site_code_rows_duplicated` and
    `n_orphan_owner_project_codes`. `n_sites_null_coordinates` is `None` --
    never a number -- when `coordinate_columns_present` is `False`: unlike
    every refusal above, a site genuinely lacking coordinates is not a
    validation failure, so this cannot refuse when the `Latitude`/
    `Longitude` columns are absent; it must instead report "not computed"
    distinctly from "every site lacks coordinates", per CLAUDE.md's rule
    that a diagnostic that could not be computed is not one that fired. The
    same rule is why (e)'s reconciliation REFUSES (rather than silently
    skipping) when `Sites.csv`-only codes exist but the coordinate columns
    needed to explain them are absent.
    """
    shp_zip_path = Path(shp_zip_path)
    csv_zip_path = Path(csv_zip_path)

    # (a) required members.
    shp_members = _read_zip_members(shp_zip_path)
    csv_members = _read_zip_members(csv_zip_path)
    missing_shp_members = [m for m in MINEDEX_SHP_REQUIRED_MEMBERS if m not in shp_members]
    missing_csv_members = [m for m in MINEDEX_CSV_REQUIRED_MEMBERS if m not in csv_members]
    if missing_shp_members or missing_csv_members:
        raise SnapshotValidationError(
            "MINEDEX bundle missing required member(s) -- "
            f"SHP zip {shp_zip_path} missing {missing_shp_members}, "
            f"CSV zip {csv_zip_path} missing {missing_csv_members}"
        )

    # (b) shapefile checks.
    try:
        gdf = gpd.read_file(f"/vsizip/{shp_zip_path}/{MINEDEX_SHAPEFILE_BASENAME}.shp")
    except Exception as exc:
        raise SnapshotValidationError(f"{shp_zip_path}: shapefile not readable ({exc})") from exc

    if len(gdf) == 0:
        raise SnapshotValidationError(f"{shp_zip_path}: shapefile is empty (0 features)")

    present_geometry = ~(gdf.geometry.isna() | gdf.geometry.is_empty)
    non_point = present_geometry & (gdf.geometry.geom_type != "Point")
    if non_point.any():
        raise SnapshotValidationError(
            f"{shp_zip_path}: {int(non_point.sum())} feature(s) carry a non-Point "
            "geometry (null/empty geometry is tolerated; a present geometry that is "
            "not a Point is not)"
        )

    crs_str = str(gdf.crs)
    if crs_str != MINEDEX_EXPECTED_CRS:
        raise SnapshotValidationError(
            f"{shp_zip_path}: shapefile CRS is {crs_str!r}, expected {MINEDEX_EXPECTED_CRS!r}"
        )

    missing_shp_columns = [c for c in ("SITE_CODE", "EXTRACT_DA") if c not in gdf.columns]
    if missing_shp_columns:
        raise SnapshotValidationError(
            f"{shp_zip_path}: missing required column(s) {missing_shp_columns} "
            f"(columns present: {sorted(gdf.columns)})"
        )

    # (c) Sites.csv stage-count reconciliation.
    sites_df = _read_csv_member(csv_zip_path, "Sites.csv")
    owners_df = _read_csv_member(csv_zip_path, "ProjectsOwners.csv")

    if "Stage" not in sites_df.columns:
        raise SnapshotValidationError(f"{csv_zip_path}: Sites.csv has no 'Stage' column")
    stage_counts = {
        str(key): int(count) for key, count in sites_df["Stage"].value_counts(dropna=False).items()
    }
    sites_total = len(sites_df)
    stage_total = sum(stage_counts.values())
    if stage_total != sites_total:
        raise SnapshotValidationError(
            f"{csv_zip_path}: Sites.csv stage counts ({stage_total}) do not "
            f"reconcile against the row total ({sites_total})"
        )

    # (d) extract-date consistency across all three sources.
    for column, frame, label in (
        ("EXTRACT_DATE", sites_df, "Sites.csv"),
        ("EXTRACT_DATE", owners_df, "ProjectsOwners.csv"),
    ):
        if column not in frame.columns:
            raise SnapshotValidationError(f"{csv_zip_path}: {label} has no {column!r} column")

    shp_extract_dates = _extract_dates(gdf["EXTRACT_DA"])
    sites_extract_dates = _extract_dates(sites_df["EXTRACT_DATE"])
    owners_extract_dates = _extract_dates(owners_df["EXTRACT_DATE"])
    all_extract_dates = shp_extract_dates | sites_extract_dates | owners_extract_dates
    if len(all_extract_dates) != 1:
        raise SnapshotValidationError(
            "MINEDEX bundle extract dates do not agree across sources: "
            f"shapefile EXTRACT_DA={sorted(shp_extract_dates)}, "
            f"Sites.csv EXTRACT_DATE={sorted(sites_extract_dates)}, "
            f"ProjectsOwners.csv EXTRACT_DATE={sorted(owners_extract_dates)}"
        )
    extract_date = next(iter(all_extract_dates))

    # `n_sites_null_coordinates`'s coordinate-columns-present guard (below)
    # is needed by (e)'s reconciliation too, so it is computed once, here,
    # ahead of (e) -- never duplicated.
    coordinate_columns_present = "Latitude" in sites_df.columns and "Longitude" in sites_df.columns

    # (e) shapefile SITE_CODE containment in Sites.csv SiteCode, with the
    # excess (Sites.csv-only codes) reconciled against Sites.csv's own
    # null-coordinate rows. See the docstring's (e) paragraph for why this
    # replaced a set-equality/row-count-equality pair that refused the
    # healthy 2026-08-14 extract.
    if "SiteCode" not in sites_df.columns:
        raise SnapshotValidationError(f"{csv_zip_path}: Sites.csv has no 'SiteCode' column")
    shp_site_codes = set(gdf["SITE_CODE"].astype(str))
    csv_site_codes = set(sites_df["SiteCode"].astype(str))
    only_in_shp = sorted(shp_site_codes - csv_site_codes)
    if only_in_shp:
        raise SnapshotValidationError(
            f"MINEDEX shapefile carries {len(only_in_shp)} SITE_CODE value(s) absent "
            f"from Sites.csv, e.g. {only_in_shp[:_MAX_ORPHAN_EXAMPLES]} -- a point "
            "shapefile carrying a code the full site register does not know about "
            "cannot be reconciled"
        )

    only_in_csv = csv_site_codes - shp_site_codes
    n_sites_absent_from_shapefile = len(only_in_csv)
    if only_in_csv:
        if not coordinate_columns_present:
            raise SnapshotValidationError(
                f"{csv_zip_path}: {n_sites_absent_from_shapefile} SiteCode value(s) in "
                "Sites.csv are absent from the shapefile, and Sites.csv carries no "
                "Latitude/Longitude columns to check whether they reconcile against "
                "coordinate-less sites -- cannot verify a point shapefile's expected "
                "shortfall against Sites.csv without them"
            )
        null_coordinate_site_codes = set(
            sites_df.loc[
                sites_df["Latitude"].isna() | sites_df["Longitude"].isna(), "SiteCode"
            ].astype(str)
        )
        unreconciled = sorted(only_in_csv - null_coordinate_site_codes)
        if unreconciled:
            raise SnapshotValidationError(
                f"MINEDEX Sites.csv carries {n_sites_absent_from_shapefile} SiteCode "
                "value(s) absent from the shapefile, but only "
                f"{n_sites_absent_from_shapefile - len(unreconciled)} of them are "
                f"explained by Sites.csv's own null-coordinate rows: {len(unreconciled)} "
                f"code(s) do NOT reconcile, e.g. {unreconciled[:_MAX_ORPHAN_EXAMPLES]} -- "
                "Sites.csv says these sites HAVE coordinates, but the shapefile does "
                "not carry them"
            )

    # (f) SITE_CODE uniqueness WITHIN the shapefile -- a point-shapefile row
    # IS a register row, so a duplicate there is refused outright. Sites.csv
    # SiteCode duplication is a different, disclosed quantity below, never
    # refused on -- see the docstring.
    shp_site_code_counts = gdf["SITE_CODE"].astype(str).value_counts()
    shp_duplicate_codes = sorted(shp_site_code_counts[shp_site_code_counts > 1].index)
    if shp_duplicate_codes:
        raise SnapshotValidationError(
            f"{shp_zip_path}: {len(shp_duplicate_codes)} SITE_CODE value(s) appear "
            f"more than once, e.g. {shp_duplicate_codes[:_MAX_DUPLICATE_EXAMPLES]}"
        )

    # Disclosed, not refused: Sites.csv SiteCode duplication -- the real
    # 2026-08-14 extract carries 1,327 duplicated values across 1,411 excess
    # rows (50,164 rows, 48,753 distinct). `n_duplicate_site_code_values` is
    # the count of DISTINCT SiteCode values appearing more than once;
    # `n_site_code_rows_duplicated` is the total ROW count across those
    # duplicated values (i.e. every row sharing a duplicated value, not just
    # the excess beyond the first).
    csv_site_code_counts = sites_df["SiteCode"].astype(str).value_counts()
    csv_duplicated_values = csv_site_code_counts[csv_site_code_counts > 1]
    n_duplicate_site_code_values = len(csv_duplicated_values)
    n_site_code_rows_duplicated = int(csv_duplicated_values.sum())

    # Disclosed, not refused: every ProjectsOwners.csv ProjectCode value
    # absent from Sites.csv ProjectCode -- the real 2026-08-14 extract
    # carries 179. See the docstring.
    if "ProjectCode" not in owners_df.columns:
        raise SnapshotValidationError(
            f"{csv_zip_path}: ProjectsOwners.csv has no 'ProjectCode' column"
        )
    if "ProjectCode" not in sites_df.columns:
        raise SnapshotValidationError(f"{csv_zip_path}: Sites.csv has no 'ProjectCode' column")

    sites_project_codes = set(sites_df["ProjectCode"].dropna().astype(str))
    owners_project_codes = set(owners_df["ProjectCode"].dropna().astype(str))
    n_orphan_owner_project_codes = len(owners_project_codes - sites_project_codes)

    if "EndDate" not in owners_df.columns:
        raise SnapshotValidationError(f"{csv_zip_path}: ProjectsOwners.csv has no 'EndDate' column")

    n_sites_null_project_code = int(sites_df["ProjectCode"].isna().sum())

    # `n_sites_null_coordinates` is a real, expected count in a healthy
    # bundle (some MINEDEX sites genuinely carry no resolvable coordinates)
    # -- NOT a validation refusal, unlike every other required-column check
    # in this function. But it must still distinguish "computed: N sites
    # lack coordinates" from "not computed: this DASC bundle revision
    # dropped/renamed the Latitude/Longitude columns", or a schema change
    # would silently report every site as coordinate-less, the not-computed
    # diagnostic read as though it had fired
    # (CLAUDE.md: "a diagnostic that could not be computed is not a
    # diagnostic that FIRED"). So the two coordinate columns' presence is
    # its own disclosed boolean, and the count itself is `None` -- never a
    # number standing in for "unknown" -- when they are absent.
    n_sites_null_coordinates: int | None
    if coordinate_columns_present:
        n_sites_null_coordinates = int(
            (sites_df["Latitude"].isna() | sites_df["Longitude"].isna()).sum()
        )
    else:
        n_sites_null_coordinates = None
    n_projects = int(sites_df["ProjectCode"].dropna().nunique())

    end_date_blank = owners_df["EndDate"].isna() | (
        owners_df["EndDate"].astype(str).str.strip() == ""
    )
    n_projects_with_current_owner = int(
        owners_df.loc[end_date_blank, "ProjectCode"].dropna().nunique()
    )

    return {
        "shp_members": sorted(shp_members),
        "csv_members": sorted(csv_members),
        "feature_count": len(gdf),
        "crs": crs_str,
        "sites_row_count": sites_total,
        "stage_counts": stage_counts,
        "extract_date": extract_date,
        "n_sites_null_project_code": n_sites_null_project_code,
        "n_sites_null_coordinates": n_sites_null_coordinates,
        "coordinate_columns_present": coordinate_columns_present,
        "n_projects": n_projects,
        "n_projects_with_current_owner": n_projects_with_current_owner,
        "n_sites_absent_from_shapefile": n_sites_absent_from_shapefile,
        "n_duplicate_site_code_values": n_duplicate_site_code_values,
        "n_site_code_rows_duplicated": n_site_code_rows_duplicated,
        "n_orphan_owner_project_codes": n_orphan_owner_project_codes,
    }
