# Context for the SILO plan attack (2026-08-26)

Fixed owner decisions (not open for re-litigation, see
`2026-08-26-silo-climate-feed-design.md` §"Owner decisions"):
ingestion lives in THIS repo, not env-health; the anonymous gridded
AWS bucket is the route (no account, no credential); storage is
`<data_root>/raw/silo/<date>/`; whole annual NetCDF files, not daily
GeoTIFFs and not streamed range-reads; no real download happens during
implementation (owner is on a metered connection).

Authority on schema and claim boundary:
`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` §5 F5
(lines 878-930). Deviations from D13 must be recorded, not silently
applied.

Verified on disk at commit 355b3be (do not re-derive; these were
checked directly this session):
- `requests` is NOT imported in `cli.py`; downloads live in
  `sources/<name>.py` (`download_tenements_zip`, tenements.py:111) and
  CLI tests monkeypatch `cli_module.<download_fn>`.
- Acquisition CLI tests live in `tests/sources/test_<source>.py`;
  curated-build CLI tests live beside their module's test file.
- Each test module defines its own `_write_config(tmp_path, data_root)`.
- Site->Maus tie-break to reproduce verbatim: `cli.py:5941-5945`.
- Maus geometry is reprojected to `crosswalk.TARGET_CRS` (EPSG:3577).
- Baseline suite: 855 passed, ~9 minutes.
- netCDF4 is NOT yet a dependency; `secrets.py` has no SILO entry.
- `http.py`'s module docstring still claims "SILO's API key travels as
  a query param" — stale under the anonymous route.

Known-missing: no SILO snapshot exists on any data root yet; O7 in
`docs/amendments-and-limitations.md:293` is still open.
