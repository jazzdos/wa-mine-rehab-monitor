# Tier 1 product framing: site-keyed, private-only, disclosed sharing (2026-08-25)

**Trigger.** Limitation L17 (`docs/amendments-and-limitations.md`) and
review finding F4 (`docs/reviews/2026-08-25-batch-e-findings.md`;
reproduce with `diag_batch_e_readiness.py --check sharing`): 10,185 of
the 10,372 sites eligible at the forced-144 threshold (98.2%) sit on a
Maus footprint shared with at least one other MINEDEX site (mean 10.5
sites per footprint, max 324), and sites sharing a footprint have
byte-identical trajectories by construction — the value is a function
of `maus_id`, not `site_id`. A per-site card showing that series under
a site name without saying so asserts something the data does not
support, which is the class of claim the project's boundary (AGENTS.md,
design §1) exists to prevent. Jointly at stake: finding F5 — D7 as
operationalized by D13 §1 blocks any public payload whose row selection
derives from MINEDEX, so Tier 1 as selected cannot ship publicly, and
the only route to a public artefact is a Maus-selected, MINEDEX-free
lane, which a footprint-keyed product would deliver as a side effect.

**Options considered.** (1) Keep `site_id` as the key, add a mandatory
`shared_footprint_site_count` disclosure, require every rendered card
and table to state it; Batch G ships private-only. (2) Re-key the
Tier 1 product to `maus_id`, present MINEDEX sites as attributes of the
footprint, and define a Maus-selected (CC-BY-SA-4.0) public lane
carrying no MINEDEX identifier, lineage, or selection. (3) Hybrid:
private register stays site-keyed with the disclosure; a
footprint-keyed public derivative is built alongside.

**Decision.** (1), decided by the owner 2026-08-25: the project's value
does not depend on a public deliverable; a private pipeline plus the
documented record is sufficient. The register remains the spine and the
unit of analysis stays the MINEDEX site, with the sharing disclosed
rather than re-keyed. Consequences accepted with it: no non-MINEDEX
public selection is built; Batch G finishes its private implementation
and the D5 Pages gate is recorded failed exactly as D13 §2
pre-registers (open item O5 stands unchanged); the portfolio artefact
is the repository, decision record, and private product, not a public
site.

**Consequence.** Before Batch E writes its partition schema, the Tier 1
row schema gains a `shared_footprint_site_count` field (the number of
eligible sites on the row's `maus_id`, ≥ 1), carried on every
trajectory row alongside `d3_forced_threshold`. Every rendered surface
in Batch G — site cards, tables, the map — must state it whenever it
exceeds 1, in terms equivalent to "footprint-level series shared with
N other MINEDEX sites". L17's "any per-site presentation must say so"
becomes enforceable at the schema level rather than editorial. Closes
open item **O6**.
