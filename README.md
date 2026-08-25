# WA Mine Rehabilitation Spectral Monitor

A statewide, descriptive spectral-chronology monitor for mine sites carried
in the Western Australian MINEDEX register, built on Digital Earth
Australia's Fractional Cover and SWIR-based indices.

## What this is

For each MINEDEX site that falls inside this project's monitoring frame,
the monitor extracts a per-site optical time series (DEA Fractional Cover
bare/pv/npv and SWIR-based indices) and derives a descriptive spectral
chronology from it. The output is a statewide register and versioned
GeoParquet data releases, consumed through a private QGIS project — not
a public website and not a compliance report (amendment A8,
`docs/decisions/2026-08-25-public-web-page-descope.md`).

## Claim boundary

This project publishes descriptive spectral chronologies for MINEDEX sites
in the monitoring frame. Every onset reported by the monitor is a spectral
detection, presented as a detection year or interval — it is never
presented as an event date, never as an operational rehabilitation date,
and never as a compliance or performance finding. A spectral detection
records that an optical metric changed in the observed time series; it does
not record what caused the change, when any physical or operational
activity occurred on the ground, or whether a site meets any regulatory or
performance standard. Readers who need an operational rehabilitation date
or a compliance determination should consult the primary regulatory record
for the site in question, not this monitor.

## Status

Current state, build sequence, and the architectural decision record
are summarised in `docs/ROADMAP.md`.

This repository is private. It stays private until the Tier 0 release
candidate gate defined in
`docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md`, section 8, item
D2, is met.

## Acknowledgements

This project's method is descended from Geoscience Australia's Digital
Earth Australia "Tracking rehabilitation of mines" notebook, which
demonstrates the same Fractional Cover trajectory approach for a single
site. This project extends that approach to a statewide register with
persistent per-site chronologies; the notebook is its direct methodological
ancestor.

## Licence

MIT. See `LICENSE`. Source data licences are tracked separately per source
and are not implied by this project's own licence — see
`docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md` for the source
licence gate.
