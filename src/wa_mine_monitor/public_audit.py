"""Public-flip payload audits (D13 §8 P4).

The public flip turns this repo's visibility from private to public. Once
that happens, everything in git history is public forever -- a bad commit
cannot be un-pushed. This module is the fail-closed gate that runs BEFORE
that flip and again over every release payload: it scans a set of relative
paths for the shapes of data that must never leave the private repo.

Two audiences, two entry points:

- `audit_tree` / `scripts/audit_public_tree.py` -- the whole tracked (plus
  untracked-but-not-ignored) working tree, checked before the flip. Nothing
  in this project should look like a bulk data artefact, a licence-evidence
  bundle, a credential, a local filesystem path, raw geometry, or a MINEDEX
  lineage marker outside code/docs.
- `audit_release_dir` / `scripts/audit_release_payload.py` -- one version
  directory of the public Tier-0 release product, checked against a
  narrower allowlist (the RC artefacts D13 actually authorises to ship)
  rather than "nothing bulk at all".

Detection is split into two passes because the cost of a full-content
scan scales with file size and this audit runs against a working tree that
may contain large legitimate build artefacts elsewhere (this project has
none tracked today, but the audit must not assume that stays true):

1. Cheap, path-only checks (extension, filename) that need no I/O.
2. A content sniff over the first 65536 bytes of the file (`errors=
   "replace"` -- audited files are not guaranteed to be valid UTF-8, and a
   decode failure must never crash the gate that is supposed to catch the
   leak). Credentials are scanned in every file's sniffed text, because a
   secret can hide inside a `.py` or `.md` file too. Local filesystem paths
   are scanned everywhere EXCEPT recognised code/docs suffixes, since a
   real absolute path in a config or data file usually means the file
   embeds a specific machine's layout, while `.py`/`.md`/etc are expected to
   discuss paths abstractly. Geometry content and MINEDEX lineage tokens
   are scanned only on data-shaped files (`.csv`/`.tsv`/`.json`/`.txt`/
   `.bin`/extensionless) -- source and docs are allowed to *mention* a field
   name like `SiteCode` when explaining the schema; only a file that looks
   like it IS the data is a finding.

Every `Finding.note` is a fixed, safe string describing which rule fired --
never the matched bytes, the matched line, or any other file content. A
credential or a home-directory username caught by this audit must not be
copied into the audit's own output, which itself may be printed to a CI
log or committed to a report.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Extensions that indicate a bulk data artefact (raster, vector, tabular,
#: or archive) rather than source or documentation. A public research repo
#: ships code and small text fixtures, never the data itself.
BULK_EXTENSIONS = frozenset(
    {
        ".zip",
        ".parquet",
        ".gpkg",
        ".tif",
        ".tiff",
        ".nc",
        ".zarr",
        ".feather",
        ".arrow",
        ".shp",
        ".shx",
        ".dbf",
        ".prj",
        ".cpg",
        ".geojson",
        ".csv",
        ".tsv",
        ".xlsx",
    }
)

#: Filenames (exact match) that are always evidence-bundle artefacts,
#: regardless of extension.
_EVIDENCE_EXACT_NAMES = frozenset({"SHA256SUMS.txt", "licence_evidence.json"})

#: Filename suffixes that mark an evidence-bundle artefact -- every
#: `<artefact>.run_manifest.json` this pipeline writes.
_EVIDENCE_SUFFIXES = (".run_manifest.json",)

#: Credential-shaped content. Deliberately over-matching (a false positive
#: here just means a human re-checks one file) -- the alternative, missing
#: a real key on the way into a public repo, is not recoverable.
CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"aws_secret", re.IGNORECASE),
    re.compile(r"aws_access_key_id", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*=", re.IGNORECASE),
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)

#: Local filesystem path shapes -- evidence the file was authored against
#: (or embeds output from) one machine's directory layout.
LOCAL_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/Users/"),
    re.compile(r"/home/"),
    re.compile(r"[A-Z]:\\"),
)

#: WKT geometry text, e.g. `POLYGON ((0 0, 1 0, 1 1, 0 0))`.
_WKT_PATTERN = re.compile(
    r"\b(POLYGON|MULTIPOLYGON|LINESTRING|MULTILINESTRING|POINT|MULTIPOINT)\s*\("
)

#: GeoJSON markers -- the literal key/value pairs that identify a file as
#: geometry rather than plain JSON.
_GEOJSON_MARKERS = ('"FeatureCollection"', '"coordinates"')

#: WKB geometry-type codes 1..7 (Point, LineString, Polygon, MultiPoint,
#: MultiLineString, MultiPolygon, GeometryCollection), little- and
#: big-endian. Byte 0 is the endianness flag (0x00 big, 0x01 little); the
#: following 4 bytes are the geometry type in that endianness.
_WKB_LITTLE_ENDIAN_HEADERS = frozenset(bytes([0x01]) + n.to_bytes(4, "little") for n in range(1, 8))
_WKB_BIG_ENDIAN_HEADERS = frozenset(bytes([0x00]) + n.to_bytes(4, "big") for n in range(1, 8))


def _is_wkb_header(data: bytes) -> bool:
    prefix = data[:5]
    return prefix in _WKB_LITTLE_ENDIAN_HEADERS or prefix in _WKB_BIG_ENDIAN_HEADERS


#: MINEDEX lineage tokens. CamelCase field names are matched case-sensitively
#: (matching them case-insensitively would fire on ordinary English words
#: like "sitecode" appearing nowhere -- kept case-sensitive anyway per spec,
#: it is the safer direction for a compound identifier); the snake_case /
#: lowercase tokens are matched case-insensitively since they have no
#: plausible unrelated reading.
_MINEDEX_CASE_SENSITIVE_TOKENS = ("SiteCode", "ProjectCode", "OwnerName")
_MINEDEX_CASE_INSENSITIVE_TOKENS = ("owners_at_snapshot", "dmirs_001", "minedex")

#: Suffixes treated as source/documentation/config -- exempt from the
#: local-path and MINEDEX-lineage content checks, since these files are
#: expected to discuss paths and field names abstractly rather than embed
#: them as data.
CODE_SUFFIXES = frozenset({".py", ".md", ".rst", ".toml", ".cfg", ".ini", ".yml", ".yaml"})

#: Suffixes (plus extensionless files) treated as data-shaped -- the only
#: files scanned for geometry content and MINEDEX lineage tokens.
_DATA_SUFFIXES = frozenset({".csv", ".tsv", ".json", ".txt", ".bin"})

#: Bytes read from the front of each file for the content sniff. Large
#: enough to catch a header row or an opening JSON key; small enough that
#: sniffing a genuinely bulk file (which the extension check has already
#: flagged) does not blow out audit runtime.
_SNIFF_BYTES = 65536

#: Release-payload allowlist (D13 §8 P4/P6): the RC artefacts a Tier-0
#: version directory is authorised to ship. Extension/evidence-name rules
#: are skipped for a matching filename; content rules (credential, local
#: path, geometry, MINEDEX lineage) still apply -- an authorised filename
#: does not authorise leaked content inside it.
_RELEASE_ALLOWED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^tier0-.*\.parquet$"),
    re.compile(r"^RELEASE_NOTES\.md$"),
    re.compile(r".*\.run_manifest\.json$"),
)

#: Fixture paths this audit permits despite looking data-shaped: committed
#: DEA STAC collection/item JSON stubs used by `tests/sources`. Each is a
#: small, hand-trimmed STAC document with no licence-restricted content --
#: audited (this list, and the files it names) at the time this module was
#: written; a new fixture must be reviewed and added deliberately, never by
#: pattern.
SYNTHETIC_FIXTURE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "tests/fixtures/dea/collection_ga_ls5t_gm_cyear_3.json",
        "tests/fixtures/dea/collection_ga_ls7e_gm_cyear_3.json",
        "tests/fixtures/dea/collection_ga_ls8cls9c_gm_cyear_3.json",
        "tests/fixtures/dea/collection_ga_ls_fc_pc_cyear_3.json",
        "tests/fixtures/dea/collection_stub.json",
        "tests/fixtures/dea/items_page_1.json",
        "tests/fixtures/dea/items_page_2.json",
    }
)

#: Files where the `credential` rule always false-positives, reviewed and
#: exempted deliberately here -- never by pattern, and never touching the
#: other rules (bulk format, evidence bundle, local path, geometry,
#: MINEDEX lineage), which still run against every one of these files.
#: Two distinct reasons put a file on this list:
#:
#: - `src/wa_mine_monitor/public_audit.py` is the module that *defines*
#:   `CREDENTIAL_PATTERNS`. Its own source text necessarily contains the
#:   strings those patterns look for (`"aws_secret"`, `"AKIA..."`,
#:   `"BEGIN ... PRIVATE KEY"`) -- it is a structural self-match that no
#:   amount of editing the file's *behaviour* can avoid, since the leak
#:   this rule exists to catch is a substring of the rule's own
#:   definition.
#: - The rest are tests (`tests/test_http.py`, `tests/test_manifests.py`,
#:   `tests/test_public_audits.py`, `tests/test_secrets.py`) and docs
#:   (`docs/plans/2026-08-29-public-rc-lane.md`,
#:   `docs/archive/plans/2026-08-16-batch-c-implementation.md`) that embed
#:   a fake `api_key=...` token on purpose, to exercise or document the
#:   `scrub_url_secrets` URL-secret-redaction behaviour. The tokens are
#:   synthetic (`SECRETTOKEN`, `SUPERSECRETTOKEN`, `LEAKED_CONFIG_TOKEN`)
#:   and never leave this repo as real credentials.
#:
#: A new file must be reviewed and added deliberately, exactly like
#: `SYNTHETIC_FIXTURE_ALLOWLIST` -- this list narrows the credential rule
#: only for these seven paths, not for any pattern of path or content.
CREDENTIAL_FALSE_POSITIVE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "src/wa_mine_monitor/public_audit.py",
        "tests/test_http.py",
        "tests/test_manifests.py",
        "tests/test_public_audits.py",
        "tests/test_secrets.py",
        "docs/plans/2026-08-29-public-rc-lane.md",
        "docs/archive/plans/2026-08-16-batch-c-implementation.md",
    }
)


@dataclass(frozen=True)
class Finding:
    """One audit hit. `note` is fixed, safe rule-description text -- never
    the matched content -- so a `Finding` can be printed or persisted
    without becoming a second copy of whatever it flagged."""

    path: str
    rule: str
    note: str


def _evidence_name_hit(name: str) -> bool:
    return name in _EVIDENCE_EXACT_NAMES or any(name.endswith(suf) for suf in _EVIDENCE_SUFFIXES)


def _is_release_allowed(name: str) -> bool:
    return any(pattern.match(name) for pattern in _RELEASE_ALLOWED_PATTERNS)


def _sniff_text(full_path: Path) -> str | None:
    try:
        with full_path.open("rb") as fh:
            raw = fh.read(_SNIFF_BYTES)
    except OSError:
        return None
    return raw.decode("utf-8", errors="replace")


def _sniff_bytes(full_path: Path) -> bytes:
    try:
        with full_path.open("rb") as fh:
            return fh.read(_SNIFF_BYTES)
    except OSError:
        return b""


def _content_looks_like_minedex(text: str) -> bool:
    if any(token in text for token in _MINEDEX_CASE_SENSITIVE_TOKENS):
        return True
    lowered = text.lower()
    return any(token in lowered for token in _MINEDEX_CASE_INSENSITIVE_TOKENS)


def _credential_findings(rel_path: str, full_path: Path) -> list[Finding]:
    """Credential scan alone -- the one content check that still applies to
    an allowlisted fixture. A reviewed fixture is trusted not to embed a
    local path or raw geometry (that review is exactly what put it on the
    allowlist), but is not a substitute for scanning it for a credential
    that could have been pasted in by accident afterwards."""
    if not full_path.is_file():
        return []
    text = _sniff_text(full_path)
    if text is None:
        return []
    for pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return [Finding(rel_path, "credential", "credential-shaped content")]
    return []


def _content_findings(
    rel_path: str, full_path: Path, *, skip_credential: bool = False
) -> list[Finding]:
    """Content-sniff checks for a non-allowlisted file: credentials
    everywhere, local paths outside code/docs, geometry and MINEDEX
    lineage tokens only on data-shaped files. Independent of the
    path-only (extension/evidence-name) rules, so it applies unchanged
    whether or not those were skipped for a release-authorised filename.

    `skip_credential` narrows only the credential rule, for a path on
    `CREDENTIAL_FALSE_POSITIVE_ALLOWLIST` -- every other check still
    runs.
    """
    if not full_path.is_file():
        return []

    text = _sniff_text(full_path)
    if text is None:
        return []

    findings: list[Finding] = []
    suffix = full_path.suffix.lower()

    if not skip_credential:
        for pattern in CREDENTIAL_PATTERNS:
            if pattern.search(text):
                findings.append(Finding(rel_path, "credential", "credential-shaped content"))
                break

    if suffix not in CODE_SUFFIXES:
        for pattern in LOCAL_PATH_PATTERNS:
            if pattern.search(text):
                findings.append(Finding(rel_path, "local_path", "local filesystem path"))
                break

    if suffix in _DATA_SUFFIXES or suffix == "":
        geometry_hit = bool(_WKT_PATTERN.search(text)) or any(
            marker in text for marker in _GEOJSON_MARKERS
        )
        if not geometry_hit:
            geometry_hit = _is_wkb_header(_sniff_bytes(full_path))
        if geometry_hit:
            findings.append(Finding(rel_path, "geometry_content", "raw geometry content"))

        if _content_looks_like_minedex(text):
            findings.append(Finding(rel_path, "minedex_lineage", "MINEDEX lineage marker"))

    return findings


def audit_file(
    root: Path,
    rel_path: str,
    *,
    release_mode: bool = False,
    credential_allowlist: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Audit one file. `rel_path` is a repo-relative POSIX path; `root` is
    the tree it is relative to.

    In `release_mode`, a filename matching `_RELEASE_ALLOWED_PATTERNS`
    skips the extension/evidence-name checks (it is one of the RC
    artefacts the release is authorised to ship) but every content check
    still runs -- an authorised filename says nothing about what got
    written inside it.

    `credential_allowlist` narrows only the credential content check --
    see `CREDENTIAL_FALSE_POSITIVE_ALLOWLIST`.
    """
    full_path = root / rel_path
    name = full_path.name

    findings: list[Finding] = []
    if not (release_mode and _is_release_allowed(name)):
        suffix = full_path.suffix.lower()
        if suffix in BULK_EXTENSIONS:
            findings.append(Finding(rel_path, "bulk_format", f"bulk data extension '{suffix}'"))
        if _evidence_name_hit(name):
            findings.append(
                Finding(rel_path, "evidence_bundle", "licence/provenance evidence artefact")
            )

    findings.extend(
        _content_findings(rel_path, full_path, skip_credential=rel_path in credential_allowlist)
    )
    return findings


def audit_tree(
    root: Path,
    files: list[str],
    allowlist: frozenset[str] = frozenset(),
    credential_false_positive_allowlist: frozenset[str] = CREDENTIAL_FALSE_POSITIVE_ALLOWLIST,
) -> list[Finding]:
    """Audit a set of repo-relative paths under `root`.

    An allowlisted path skips the extension/evidence-name rules (it is a
    known, reviewed fixture) but is still credential-scanned and otherwise
    content-scanned like every other file -- unless it is also on
    `credential_false_positive_allowlist`, in which case the credential
    rule is narrowed for it too (see that constant's docstring).
    """
    findings: list[Finding] = []
    for rel_path in files:
        if rel_path in allowlist:
            if rel_path not in credential_false_positive_allowlist:
                findings.extend(_credential_findings(rel_path, root / rel_path))
        else:
            findings.extend(
                audit_file(root, rel_path, credential_allowlist=credential_false_positive_allowlist)
            )
    return findings


def collect_repo_files(repo_root: Path) -> list[str]:
    """List every file that would actually be part of a commit from
    `repo_root`: tracked (`--cached`) plus untracked-but-not-ignored
    (`--others --exclude-standard`).

    `git ls-files` with no flags is blind to a file created during a build
    and not yet `git add`-ed -- exactly the file a pre-flip audit most
    needs to catch, since it is the one nobody has looked at yet.
    Gitignored files are excluded (`--exclude-standard`): a `.gitignore`
    covering `data/` is itself the leak-prevention mechanism this audit is
    layered on top of, not something it needs to re-flag.
    """
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


class EmptyReleaseDirError(ValueError):
    """Raised by `audit_release_dir` when `version_dir` does not exist or
    contains zero files. A missing/empty directory yields zero findings by
    construction -- indistinguishable from a genuinely clean payload -- so
    this must be a hard refusal rather than a silent "0 finding(s)" pass.
    A typo'd path or an unbuilt release must never record as clean."""


def audit_release_dir(version_dir: Path) -> list[Finding]:
    """Audit one release version directory in `release_mode` against the
    RC-artefact allowlist (D13 §8 P4/P6).

    Raises `EmptyReleaseDirError` if `version_dir` does not exist or no
    files are found under it -- see that class's docstring for why this
    must fail closed rather than report zero findings.
    """
    if not version_dir.is_dir():
        raise EmptyReleaseDirError(
            f"version_dir does not exist or is not a directory: {version_dir}"
        )

    findings: list[Finding] = []
    scanned = 0
    for full_path in sorted(version_dir.rglob("*")):
        if not full_path.is_file():
            continue
        scanned += 1
        rel_path = full_path.relative_to(version_dir).as_posix()
        findings.extend(audit_file(version_dir, rel_path, release_mode=True))

    if scanned == 0:
        raise EmptyReleaseDirError(f"version_dir contains zero files: {version_dir}")

    return findings


def render_report(findings: list[Finding], *, scanned_files: int | None = None) -> str:
    """Render findings as `<path>: <rule>` lines plus a counts summary.
    Never includes matched content -- only `Finding.path`, `Finding.rule`
    and the fixed `Finding.note` text, none of which can carry a secret or
    a local path by construction.

    `scanned_files`, when given, is included in the summary so a clean run
    (findings but zero of them) is distinguishable from a vacuous one that
    scanned nothing -- callers that walked `version_dir` themselves (e.g.
    `audit_release_dir`, which now refuses on zero files) should pass the
    count they actually saw.
    """
    lines = sorted(f"{f.path}: {f.rule} ({f.note})" for f in findings)
    summary = f"{len(findings)} finding(s) across {len({f.path for f in findings})} file(s)"
    if scanned_files is not None:
        summary += f"; {scanned_files} file(s) scanned"
    return "\n".join([*lines, summary])
