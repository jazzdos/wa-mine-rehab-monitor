# DBCA-060 mirror route declined; authoritative package only (2026-08-29)

**Status: authorised by the owner 2026-08-29.** This is amendment **A10**
in `docs/amendments-and-limitations.md`.

## Decision

The ArcGIS Online mirror route for DBCA-060 stays DECLINED for product
ingestion, permanently for v1. D13 §6's F1 ("Adjudicate DBCA-060 mirror
provenance and licence evidence",
`decisions/2026-08-16-d13-batches-c-g-detailing.md:742-790`) is dissolved
as objectless: F1 exists solely to authorise the mirror, and an
AUTHORITATIVE Data WA package is already on disk at
`~/data/jarrah-rehab/raw/dbca-060/2026-07-20/` (zip digests in its
`SHA256SUMS.txt`; custodian CRS-provenance in `metadata.txt`). Precedent:
the 2026-08-26 SILO decision
(`decisions/2026-08-26-silo-gridded-feed.md`) dissolving D13's
credentials precondition when the actual route made it objectless — here
the actual route is the authoritative package already staged, so the
mirror-adjudication precondition has nothing left to authorise.

## Why the mirror was suspect (recorded for the file)

The mirror is a third-party ArcGIS Online service (org id
`DN2fPfpggEPlLhP6`, identified as "Stantec" only in a sibling project's
config comment); no item-owner capture, no licence-text capture, no
authoritative-versus-mirror diff was ever run. None of that evidence is
needed when the authoritative package itself is the input — F1's entire
evidence list (item owner/publisher identity, redistribution grant,
reproducible authoritative-versus-mirror comparison, freshness policy)
adjudicates a route this project does not take.

## F3 obligations this decision assigns

1. Compute and record the sha256 of the unzipped `.gpkg` — the source
   `SHA256SUMS.txt` covers only the two zips, not the extracted
   GeoPackage that F3/F4 actually read.
2. Close the licence-evidence gap. `src/wa_mine_monitor/licence.py`'s
   `dbca_060_fire` entry holds `licence_id="open"`, `licence_url=""`.
   The CKAN record (dataset id
   `3ce8a891-b050-4c38-952b-c40ca8bdc042`, verified in jarrah-rehab
   `docs/research/data-source-verification_2026-07-20.md`) says
   `license_id: cc-by`, so the entry becomes `CC-BY-4.0` with the
   catalogue URL, and the live F3 run captures the catalogue page as a
   digested evidence file.

## Frozen F4 coverage window

**`[1937, snapshot_year - 1]`**, calendar years per `fih_year1`. 1937 is
the dataset's documented earliest systematic records; the snapshot year
itself is excluded because the record for the extract year is incomplete
at extract time. Frozen here, never inferred from the data.

## Limitation L18 (declared here, registered in the amendments file)

DBCA-060's own scope is fires on DBCA-managed land or where DBCA
incurred costs; known gaps exist and spatial completeness is not
modelled. `not_recorded` is therefore a statement about the RECORD for a
covered year, never about the ground. No output ever treats it as a
known-negative.

## Consequence

Dissolves D13 §6 F1 as objectless. F2 (ArcGIS client/pager) is likewise
unneeded and stays unbuilt — the mirror route it would serve is
declined. Batch F proceeds on the authoritative on-disk GeoPackage only.
