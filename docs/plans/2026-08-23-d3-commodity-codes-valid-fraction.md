# D3 commodity codes + valid-fraction computability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Replace the never-matching English `commodity_token_rules` with exact-token MINEDEX `commodity_code_rules`, and replace all-pixels-valid Phase A computability with a `min_valid_member_fraction: 0.95` rule whose Phase B values are computed over valid members only; then re-freeze the protocol as `2026-08-23` and rerun the chain on luminosity.

**Architecture:** `d3_protocol.py` owns the protocol dataclasses, loader validation, and `classify_commodity`; `d3_inputs.py` owns `year_computable` / `simulate_footprint_year` and the procedures-drift check; `cli.py` `build-d3-inputs` wires both. The protocol YAML is the digest source, so every change to `config/d3.yaml` changes the digest and requires the decision doc (Task 1) before `freeze-d3-protocol` is rerun (Task 8). Design: `docs/plans/2026-08-23-d3-commodity-codes-valid-fraction-design.md` (authoritative).

**Tech Stack:** Python 3.12, uv, pytest, numpy, pyyaml, ruff (line-length 100), mypy.

Conventions: run `kit:code-standards` for Python before editing. Tests are plain functions, no conftest. Errors are `ValueError` subclasses named `<Module>Error`. All commands below run from the worktree root `/Users/jarrodbaker/Documents/wa-mine-rehab-monitor/.claude/worktrees/d3-commodity-codes-valid-fraction`. Never edit `config/d3.yaml` without also editing the decision doc; never run `freeze-d3-protocol` locally against a data root that already holds `curated/d3-protocol/2026-08-18`.

Design points this plan resolves (not stated in the design doc):

1. `year_computable` keeps a `bool` return (Phase A in `cli.py` only needs the bool) and gains a required keyword `min_valid_member_fraction`. A new public `valid_member_mask(band_values, *, kind)` exposes the mask; `simulate_footprint_year` calls the mask once and applies the same threshold via `_mask_computable`.
2. Threshold arithmetic: `int(mask.sum()) >= math.ceil(round(min_valid_member_fraction * len(mask), 9))`. The `round(..., 9)` guards `0.95 * n` float noise (e.g. `0.95 * 100` is not exactly `95.0`); at 144 members the threshold is `ceil(136.8) = 137`.
3. Ranking and the valid subset: `_rank_all` hashes each member independently of the set it is ranked in, so ranking only the valid members yields exactly the full-set ranking with invalid members removed. The prefix/nesting property across supports is preserved. `simulate_footprint_year` therefore builds `valid_members` once and ranks that tuple per replicate; the `_rank_all` oracle tests are untouched.
4. Test fixture register rows in `tests/test_cli.py` use English commodity text (`"Gold"`, `"Iron Ore"`) which now classifies as `other`. They are changed to `"Au"` / `"Fe"` so the existing one-stratum adequacy fixture keeps its documented meaning.
5. `check_procedures_consistency` gains two checks: `procedures.commodity_mode` must contain the phrase `exact token` and `procedures.full_support_year` must contain `min_valid_member_fraction`. The fraction value itself is protocol data, already digest-bound, so the check names the rule, not the number.

---

### Task 1: Decision doc

**Files:**
- Create: `docs/decisions/2026-08-23-d3-commodity-codes-and-valid-fraction.md`

**Step 1: Write the decision doc** with exactly this content.

```markdown
# D3: MINEDEX commodity codes and valid-fraction computability (2026-08-23)

**Trigger.** Batch D live run (luminosity, 2026-08-22, commit b17d5ef,
`docs/checkpoints/batch-d-result.md`) returned `criteria_passed=false`,
`n_star=144`, 0 eligible sites, under the 2026-08-18 protocol freeze
(digest b2fa76f7d1dae1cfabad2f83828246e78024b1c246432eb6deeaa35598f90272).
Two protocol defects were observed, both in stratification and
computability outputs, neither in an accuracy result:

1. `config/d3.yaml` `commodity_token_rules` matched English substrings
   (`iron`, `gold`, `bauxite`, `nickel`, `mineral sands`, ...) but the
   register `commodity` column holds verbatim MINEDEX `Commodities`
   codes (`Au`, `Au, Ag`, `Fe`, `Ni, Co`, `Bx`, `HM`, ...).
   `d3_protocol.classify_commodity` returned `other` for all 1,252
   footprints; 45 of 54 strata had 0 footprints and 48 were inadequate.
   The unit test used English text, so the fixture suite stayed green.
2. Batch D plan decision 11 (all member pixels valid in FC and a
   geomedian) is unattainable after 2012. Diagnostics
   (`scripts/diag_d3_computability.py`, `scripts/diag_d3_coverage.py`,
   luminosity `/mnt/data/wa-mine-monitor/reports/diag-*.log`) show
   catalogue coverage 100% for 1987-2025, geomedian sources 99-100%
   computable, the zero-denominator rule never firing, and every failure
   caused by `dea_fc_pc` nodata (all three FC bands 255 together: water,
   pit lakes, shadow). Where FC fails, the invalid share of member
   pixels is median 0.8%, p90 5.4%, clustered interior blobs.
   `computable_fraction` was 0.75-0.89 in 5 of 6 strata against the
   0.90 floor, identical at every support level, so it blocks the
   threshold search regardless of accuracy.

**What is and is not changed.** D13
(`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` Batch D) fixes
the six commodity groups and the >= 0.90 computable site-year fraction;
it does not specify the token vocabulary or the all-pixels-valid rule.
Both are Batch D plan decisions
(`docs/plans/2026-08-16-batch-d-implementation.md` decisions 10-11).
The accuracy criteria (NBR/NDMI P90 abs error <= 0.03, FC P90 <= 5 pp,
median Spearman >= 0.95, computable fraction >= 0.90), the support set,
regions, groups, shape classes, adequacy counts, selection, and
replicates are unchanged. This change follows observation of
stratification counts and computability fractions only; no P90 error,
Spearman, or threshold value informed it, and none may inform any
future protocol change.

**Options considered.** (a) Patch `classify_commodity` in code only and
keep the 2026-08-18 digest: rejected, the rules are protocol content and
the digest must change. (b) Keep all-pixels-valid and lower the 0.90
computable fraction: rejected, that criterion is D13-frozen. (c) Replace
the token vocabulary with exact MINEDEX codes and replace
all-pixels-valid with a valid-member fraction, computing values over
valid members: adopted.

**Decision.** (c), under a new single lineage dated 2026-08-23.

- `commodity_code_rules` replaces `commodity_token_rules`. Matching:
  split the raw `commodity` on `,`, strip, case-insensitive exact token
  match (no substring, so `fe` cannot hit `Fel`), first rule wins, a
  non-empty value matching nothing is `other`, null/blank is a refusal.
  Rule order and codes (Tier 1 vocabulary from
  `curated/register/2026-08-17` intersected with the high-confidence
  crosswalk): iron_ore = Fe, FeOre, Mag, Hem, Hem-MIO, Fe-DRI,
  Fe-Pellets, FeSpec, Fe2O3; bauxite_alumina = Bx, Al2O3Bayer, Alu, Al;
  nickel = Ni, MgsNi; mineral_sands = HM, Ilm, Zrn, Leu, Rt, Mnz, Grt,
  IlmRt-syn, Xen; gold = Au. Modal group per footprint and tie handling
  are unchanged.
- `adequacy.min_valid_member_fraction: 0.95`. A footprint-year-collection
  is computable iff `valid_support_px >= ceil(0.95 * full_support_px)`
  with validity from the existing `geomedian_valid_mask` /
  `fc_valid_mask`. A year is full-support computable iff FC computable
  and at least one geomedian collection computable (unchanged).
- Phase B computes the full value and every replicate value over valid
  members only; replicate draws sample from valid members;
  `valid_support_px` carries the valid count; `full_support_px` stays
  the geometric member count. Sub-full supports (<= 100) are always
  drawable (137 >= 100). The full-support row (144) is the reference
  itself: when `valid_support_px < 144` it is emitted with the sample
  equal to all valid members, so its errors are exactly zero and its
  Spearman series equals the full series; any other support above
  `valid_support_px` is a refusal. Footprints below 144 geometric
  members are still refused.

**Disclosed limitation (statistical).** The simulation now conditions on
the valid members: support `s` draws `s` valid pixels and is compared to
the mean over all valid pixels. `apply-d3-threshold` compares `n_star`
to the geometric `effective_pixel_support_px`, so a site with geometric
support `n_star` may have as few as `ceil(0.95 * n_star)` valid pixels
in a given year. The 0.95 floor bounds this discrepancy to 5% of
support, and FC nodata is spatially clustered, so reduced-support error
on such sites may be slightly understated relative to the simulation.
Batch E extraction must apply the same `min_valid_member_fraction` rule
per site-year-collection and record `valid_support_px`, so that every
trajectory value is computed over the same population the threshold
was derived on. This is recorded rather than corrected because the
alternative (per-year valid counts in eligibility) has no D13 basis.

**Supersession.** This decision supersedes the 2026-08-18 protocol
freeze. On luminosity `curated/d3-protocol/2026-08-18` is moved to
`curated/d3-protocol.superseded-2026-08-18` (kept, not deleted) and
`freeze-d3-protocol --date 2026-08-23` creates the only dated lineage.
The 2026-08-21 `d3-inputs`, `d3-threshold`, and `register` outputs stay
under their own date as the record of the failed run. Dry run of the new
rules against the 2026-08-21 `footprint_support` (same candidates, new
labels): gold 726 / other 267 / iron_ore 136 / nickel 95 /
mineral_sands 16 / bauxite_alumina 12 footprints; 17 adequate strata,
416 selected footprints (was 6 / 180). Bauxite_alumina and mineral_sands
never reach 10 per stratum; this is disclosed in `stratum_summary`, not
blocking.

**Consequence for D3.** Strata are now commodity-stratified as D13
intended. `n_star` and `criteria_passed` from the 2026-08-21 run are not
a usable D3 result and are retained only as the record that triggered
this decision. The Batch E E4/E5 gate stays closed until the 2026-08-23
rerun reports `criteria_passed=true`.
```

**Step 2: Verify it exists**

Run: `test -s docs/decisions/2026-08-23-d3-commodity-codes-and-valid-fraction.md && echo ok`
Expected: `ok`

---

### Task 2: `config/d3.yaml`

**Files:**
- Modify: `config/d3.yaml:13-26`, `config/d3.yaml:30-32`, `config/d3.yaml:45`, `config/d3.yaml:52`

**Step 1: Replace the commodity rules block.** Current lines 13-26:

```yaml
  # Ordered, case-insensitive substring rules over the register's raw
  # MINEDEX `commodity` text. First matching rule wins; a non-empty value
  # matching nothing is `other`; null/empty is a refusal (unclassified).
  commodity_token_rules:
    - group: iron_ore
      tokens: ["iron"]
    - group: bauxite_alumina
      tokens: ["bauxite", "alumina", "aluminium"]
    - group: nickel
      tokens: ["nickel"]
    - group: mineral_sands
      tokens: ["mineral sands", "heavy mineral", "ilmenite", "rutile", "zircon", "leucoxene", "monazite", "garnet"]
    - group: gold
      tokens: ["gold"]
```

Replace with:

```yaml
  # Ordered, case-insensitive EXACT-token rules over the register's raw
  # MINEDEX `commodity` codes (split on ","; decision 2026-08-23). First
  # matching rule wins; a non-empty value matching nothing is `other`;
  # null/empty is a refusal (unclassified).
  commodity_code_rules:
    - group: iron_ore
      codes: ["Fe", "FeOre", "Mag", "Hem", "Hem-MIO", "Fe-DRI", "Fe-Pellets", "FeSpec", "Fe2O3"]
    - group: bauxite_alumina
      codes: ["Bx", "Al2O3Bayer", "Alu", "Al"]
    - group: nickel
      codes: ["Ni", "MgsNi"]
    - group: mineral_sands
      codes: ["HM", "Ilm", "Zrn", "Leu", "Rt", "Mnz", "Grt", "IlmRt-syn", "Xen"]
    - group: gold
      codes: ["Au"]
```

**Step 2: Add the adequacy key.** Current lines 30-32:

```yaml
  adequacy:
    min_footprints: 10
    min_full_support_years: 10
```

Replace with:

```yaml
  adequacy:
    min_footprints: 10
    min_full_support_years: 10
    min_valid_member_fraction: 0.95
```

**Step 3: Replace `procedures.commodity_mode`.** Current line 45:

```yaml
    commodity_mode: "First matching rule in protocol-declared token list (case-insensitive substring match); non-matching non-empty values are 'other'; null/empty/whitespace is a refusal"
```

Replace with:

```yaml
    commodity_mode: "Raw MINEDEX commodity split on ',' and stripped; first matching rule in protocol-declared code list (case-insensitive exact token match, no substring); non-matching non-empty values are 'other'; null/empty/whitespace is a refusal"
```

**Step 4: Replace `procedures.full_support_year`.** Current line 52:

```yaml
    full_support_year: "Every contributing band pixel non-null post-decode AND every geomedian metric denominator non-zero; valid_support_px==effective_pixel_support_px"
```

Replace with:

```yaml
    full_support_year: "A member pixel is valid when every contributing band is non-null post-decode AND every geomedian metric denominator is non-zero; a footprint-year-collection is computable iff valid_support_px >= ceil(adequacy.min_valid_member_fraction * full_support_px); full and replicate values are computed over valid members only and replicate draws sample valid members only"
```

**Step 5: Confirm the YAML parses** (loader tests come in Task 3)

Run: `uv run python -c "import yaml; d=yaml.safe_load(open('config/d3.yaml'))['d3']; print(len(d['commodity_code_rules']), d['adequacy']['min_valid_member_fraction'])"`
Expected: `5 0.95`

---

### Task 3: `d3_protocol.py` rule dataclass, loader, validation

**Files:**
- Modify: `src/wa_mine_monitor/d3_protocol.py:51`, `:82-85`, `:100-103`, `:112`, `:131-134`, `:172-182`, `:199-202`
- Test: `tests/test_d3_protocol.py`

**Step 1: Write the failing tests.** Append to `tests/test_d3_protocol.py` after `test_load_requires_all_procedure_keys` (line 126):

```python
def test_load_exposes_code_rules_and_valid_fraction():
    protocol = _protocol()
    assert protocol.adequacy.min_valid_member_fraction == 0.95
    assert [r.group for r in protocol.commodity_code_rules] == [
        "iron_ore",
        "bauxite_alumina",
        "nickel",
        "mineral_sands",
        "gold",
    ]
    assert protocol.commodity_code_rules[4].codes == ("Au",)
    assert "commodity_code_rules" in d3_protocol.canonical_protocol(protocol)
    assert "commodity_token_rules" not in d3_protocol.canonical_protocol(protocol)


def test_load_refuses_drifted_valid_member_fraction(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["adequacy"]["min_valid_member_fraction"] = 0.90
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="min_valid_member_fraction"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_duplicate_code_across_rules(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["commodity_code_rules"].append({"group": "other", "codes": ["au"]})
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="duplicate"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_empty_code_list(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["commodity_code_rules"].append({"group": "other", "codes": []})
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="empty"):
        d3_protocol.load_protocol(drifted)
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_d3_protocol.py -k "code_rules or valid_fraction or valid_member_fraction or duplicate_code or empty_code" -v`
Expected: 4 FAIL (`malformed d3 protocol config: 'commodity_token_rules'` from the loader, since Task 2 already renamed the YAML key).

**Step 3: Implement.** In `src/wa_mine_monitor/d3_protocol.py`:

Line 51, replace
```python
REQUIRED_ADEQUACY = {"min_footprints": 10, "min_full_support_years": 10}
```
with
```python
REQUIRED_ADEQUACY = {
    "min_footprints": 10,
    "min_full_support_years": 10,
    # Decision 2026-08-23 (docs/decisions/2026-08-23-d3-commodity-codes-and-valid-fraction.md)
    "min_valid_member_fraction": 0.95,
}
```

Lines 82-85, replace the `Adequacy` dataclass with
```python
@dataclass(frozen=True)
class Adequacy:
    min_footprints: int
    min_full_support_years: int
    min_valid_member_fraction: float
```

Lines 100-103, replace `CommodityRule` with
```python
@dataclass(frozen=True)
class CommodityRule:
    group: str
    codes: tuple[str, ...]
```

Line 112, replace `    commodity_token_rules: tuple[CommodityRule, ...]` with `    commodity_code_rules: tuple[CommodityRule, ...]`.

Lines 131-134, replace
```python
            commodity_token_rules=tuple(
                CommodityRule(group=r["group"], tokens=tuple(r["tokens"]))
                for r in d3["commodity_token_rules"]
            ),
```
with
```python
            commodity_code_rules=tuple(
                CommodityRule(group=r["group"], codes=tuple(str(c) for c in r["codes"]))
                for r in d3["commodity_code_rules"]
            ),
```

After line 182 (the `min_full_support_years` check, before `# Validate selection`), insert
```python
    if protocol.adequacy.min_valid_member_fraction != REQUIRED_ADEQUACY["min_valid_member_fraction"]:
        raise D3ProtocolError(
            f"adequacy.min_valid_member_fraction {protocol.adequacy.min_valid_member_fraction} "
            f"!= frozen {REQUIRED_ADEQUACY['min_valid_member_fraction']}"
        )
```

Lines 199-202, replace
```python
    rule_groups = {rule.group for rule in protocol.commodity_token_rules}
    unknown = rule_groups - set(protocol.commodity_groups)
    if unknown:
        raise D3ProtocolError(f"token rules name unknown groups: {sorted(unknown)}")
    return protocol
```
with
```python
    rule_groups = {rule.group for rule in protocol.commodity_code_rules}
    unknown = rule_groups - set(protocol.commodity_groups)
    if unknown:
        raise D3ProtocolError(f"code rules name unknown groups: {sorted(unknown)}")
    seen: dict[str, str] = {}
    for rule in protocol.commodity_code_rules:
        if not rule.codes:
            raise D3ProtocolError(f"code rule for group {rule.group!r} has an empty code list")
        for code in rule.codes:
            key = code.strip().lower()
            if not key:
                raise D3ProtocolError(f"code rule for group {rule.group!r} has an empty code")
            if key in seen:
                raise D3ProtocolError(
                    f"duplicate commodity code {code!r} in rules for {seen[key]!r} and "
                    f"{rule.group!r} -- first-rule-wins would be ambiguous"
                )
            seen[key] = rule.group
    return protocol
```

Also update `classify_commodity` (line 235) minimally so the module imports: replace `protocol.commodity_token_rules` with `protocol.commodity_code_rules` and `rule.tokens` with `rule.codes`. Task 4 rewrites the body.

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_d3_protocol.py -v`
Expected: all PASS except `test_classify_commodity_first_rule_wins_and_other_is_catch_all` (FAIL: English text no longer matches; rewritten in Task 4).

---

### Task 4: `classify_commodity` exact token match

**Files:**
- Modify: `src/wa_mine_monitor/d3_protocol.py:230-238`
- Test: `tests/test_d3_protocol.py:155-160`

**Step 1: Replace the failing test.** Replace lines 155-160 of `tests/test_d3_protocol.py`:

```python
def test_classify_commodity_first_rule_wins_and_other_is_catch_all():
    protocol = _protocol()
    assert d3_protocol.classify_commodity("IRON ORE - Hematite", protocol) == "iron_ore"
    assert d3_protocol.classify_commodity("Gold, Nickel", protocol) == "nickel"
    assert d3_protocol.classify_commodity("Zircon; Rutile", protocol) == "mineral_sands"
    assert d3_protocol.classify_commodity("Coal", protocol) == "other"
```

with

```python
def test_classify_commodity_matches_minedex_codes_exactly():
    protocol = _protocol()
    assert d3_protocol.classify_commodity("Au, Ag", protocol) == "gold"
    assert d3_protocol.classify_commodity("Fe, Mag", protocol) == "iron_ore"
    assert d3_protocol.classify_commodity("Ni, Cu, Co", protocol) == "nickel"
    assert d3_protocol.classify_commodity("Bx", protocol) == "bauxite_alumina"
    assert d3_protocol.classify_commodity("HM", protocol) == "mineral_sands"
    assert d3_protocol.classify_commodity("Fel", protocol) == "other"  # no substring on "Fe"
    assert d3_protocol.classify_commodity("Coal", protocol) == "other"
    assert d3_protocol.classify_commodity(" au ", protocol) == "gold"  # case + whitespace
    assert d3_protocol.classify_commodity("IRON ORE - Hematite", protocol) == "other"


def test_classify_commodity_first_rule_wins_in_protocol_order():
    protocol = _protocol()
    # iron_ore precedes nickel and gold in the frozen rule order.
    assert d3_protocol.classify_commodity("Au, Ni, Fe", protocol) == "iron_ore"
    assert d3_protocol.classify_commodity("Au, Ni", protocol) == "nickel"
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_d3_protocol.py -k classify_commodity -v`
Expected: `test_classify_commodity_matches_minedex_codes_exactly` FAIL (`"Fel"` -> `iron_ore` under substring match); the other two PASS.

**Step 3: Implement.** Replace `src/wa_mine_monitor/d3_protocol.py:230-238`:

```python
def classify_commodity(raw: str | None, protocol: D3Protocol) -> str:
    """Map raw MINEDEX commodity text to a frozen group. Refuses blank."""
    if raw is None or not str(raw).strip():
        raise D3ProtocolError("commodity is unclassified: null or blank raw value refused")
    lowered = str(raw).lower()
    for rule in protocol.commodity_token_rules:
        if any(token in lowered for token in rule.tokens):
            return rule.group
    return "other"
```

with

```python
def classify_commodity(raw: str | None, protocol: D3Protocol) -> str:
    """Map raw MINEDEX commodity codes to a frozen group. Refuses blank.

    Decision 2026-08-23: the raw value is split on ",", each token
    stripped and lower-cased, and compared for EXACT equality against the
    rule's codes (so "Fe" never matches "Fel"). First rule wins.
    """
    if raw is None or not str(raw).strip():
        raise D3ProtocolError("commodity is unclassified: null or blank raw value refused")
    tokens = {part.strip().lower() for part in str(raw).split(",") if part.strip()}
    for rule in protocol.commodity_code_rules:
        if any(code.strip().lower() in tokens for code in rule.codes):
            return rule.group
    return "other"
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_d3_protocol.py -v`
Expected: all PASS.

---

### Task 5: Repoint other test fixtures from token rules / English text

**Files:**
- Modify: `tests/test_cli.py:668`, `:1437`, `:1455`, `:1878`, `:2038`, `:2219`

References found (`grep -rn commodity_token_rules tests src config`): only `tests/test_cli.py` at 1878, 2038, 2219; no frozen `REQUIRED_*` constant names the key; `tests/test_cli.py::_write_d3_config` (line 1118) copies `config/d3.yaml` verbatim so it needs no change.

**Step 1:** At each of lines 1878, 2038, 2219 replace
```python
    raw["d3"]["commodity_token_rules"].append({"group": "gold", "tokens": ["aurum"]})
```
with
```python
    raw["d3"]["commodity_code_rules"].append({"group": "gold", "codes": ["Aurum"]})
```

**Step 2:** Line 668, replace `"commodity": ["Gold", "Iron Ore"],` with `"commodity": ["Au", "Fe"],`.

**Step 3:** Line 1455, replace `"commodity": ["Gold"] * len(specs),` with `"commodity": ["Au"] * len(specs),`. Line 1437 docstring: replace `` `commodity="Gold"` `` with `` `commodity="Au"` ``.

**Step 4: Run the affected CLI tests**

Run: `uv run pytest tests/test_cli.py -k "d3" -v`
Expected: all PASS (the `refuses_drifted_protocol` and re-freeze tests still produce a different digest because `Aurum` is a new code).

---

### Task 6: `valid_member_mask` / `year_computable` with a valid fraction

**Files:**
- Modify: `src/wa_mine_monitor/d3_inputs.py:235-238`
- Test: `tests/test_d3_inputs.py:277-281`

**Step 1: Replace the existing test** at `tests/test_d3_inputs.py:277-281` with

```python
def _bands_with_invalid(n, n_invalid):
    bands = _bands(n)
    for i in range(n_invalid):
        bands["nbart_nir"][i] = np.nan
    return bands


def test_year_computable_uses_valid_member_fraction():
    # 144 members: ceil(0.95 * 144) = 137 valid required.
    ok_96 = _bands_with_invalid(144, 5)  # 139 valid = 96.5%
    bad_94 = _bands_with_invalid(144, 9)  # 135 valid = 93.75%
    assert d3_inputs.year_computable(ok_96, kind="geomedian", min_valid_member_fraction=0.95)
    assert not d3_inputs.year_computable(bad_94, kind="geomedian", min_valid_member_fraction=0.95)
    assert d3_inputs.year_computable(_bands(144), kind="geomedian", min_valid_member_fraction=1.0)
    assert not d3_inputs.year_computable(ok_96, kind="geomedian", min_valid_member_fraction=1.0)


def test_year_computable_threshold_is_exact_at_the_boundary():
    # 100 members at 0.95 -> exactly 95 valid is computable, 94 is not
    # (guards float noise in 0.95 * 100).
    assert d3_inputs.year_computable(
        _bands_with_invalid(100, 5), kind="geomedian", min_valid_member_fraction=0.95
    )
    assert not d3_inputs.year_computable(
        _bands_with_invalid(100, 6), kind="geomedian", min_valid_member_fraction=0.95
    )


def test_valid_member_mask_for_fc_kind():
    values = {
        "bs_pc_50": np.array([10.0, np.nan, 30.0]),
        "pv_pc_50": np.array([1.0, 2.0, 3.0]),
        "npv_pc_50": np.array([1.0, 2.0, 3.0]),
    }
    assert d3_inputs.valid_member_mask(values, kind="fc").tolist() == [True, False, True]
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_d3_inputs.py -k "year_computable or valid_member_mask" -v`
Expected: 3 FAIL (`TypeError: unexpected keyword 'min_valid_member_fraction'`, `AttributeError: valid_member_mask`).

**Step 3: Implement.** Replace `src/wa_mine_monitor/d3_inputs.py:235-238`:

```python
def year_computable(band_values: Mapping[str, np.ndarray], *, kind: str) -> bool:
    """Phase A computability: every member pixel valid (design decision 11)."""
    mask = geomedian_valid_mask(band_values) if kind == "geomedian" else fc_valid_mask(band_values)
    return bool(mask.all())
```

with

```python
def valid_member_mask(band_values: Mapping[str, np.ndarray], *, kind: str) -> np.ndarray:
    """Per-member validity for `kind` ("geomedian" | "fc"), positionally
    aligned to the canonical member order the arrays were read in."""
    return geomedian_valid_mask(band_values) if kind == "geomedian" else fc_valid_mask(band_values)


def _mask_computable(mask: np.ndarray, min_valid_member_fraction: float) -> bool:
    """valid >= ceil(fraction * members). `round(.., 9)` keeps `0.95 * 100`
    (not exactly 95.0 in binary) from ceiling to 96."""
    required = math.ceil(round(min_valid_member_fraction * len(mask), 9))
    return int(mask.sum()) >= required


def year_computable(
    band_values: Mapping[str, np.ndarray], *, kind: str, min_valid_member_fraction: float
) -> bool:
    """Phase A computability (decision 2026-08-23): at least
    ceil(min_valid_member_fraction * members) member pixels valid."""
    return _mask_computable(valid_member_mask(band_values, kind=kind), min_valid_member_fraction)
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_d3_inputs.py -k "year_computable or valid_member_mask" -v`
Expected: 3 PASS. (`simulate_footprint_year` tests now error on the changed signature; Task 7 fixes them.)

---

### Task 7: `simulate_footprint_year` over valid members only

**Files:**
- Modify: `src/wa_mine_monitor/d3_inputs.py:241-323`
- Test: `tests/test_d3_inputs.py:194-275`

**Step 1: Update existing tests and add new ones.** In `tests/test_d3_inputs.py`, add `min_valid_member_fraction=0.95,` after every `protocol_digest="d" * 64,` in the four `simulate_footprint_year` calls (lines 205, 239, 255, 272). Replace `test_simulate_footprint_year_invalid_pixel_returns_none` (lines 259-274) with:

```python
def test_simulate_footprint_year_below_valid_fraction_returns_none():
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    result = d3_inputs.simulate_footprint_year(
        maus_id="M1",
        year=2005,
        source_id="dea_gm_ls5t",
        members=members,
        band_values=_bands_with_invalid(144, 9),  # 135 valid < 137
        kind="geomedian",
        supports=(9,),
        replicates=5,
        protocol_digest="d" * 64,
        min_valid_member_fraction=0.95,
    )
    assert result is None  # not computable at 93.75% valid


def test_simulate_footprint_year_uses_valid_members_only():
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    bands = _bands_with_invalid(144, 5)  # members 0..4 invalid, 139 valid
    result = d3_inputs.simulate_footprint_year(
        maus_id="M1",
        year=2005,
        source_id="dea_gm_ls5t",
        members=members,
        band_values=bands,
        kind="geomedian",
        supports=(9, 100),
        replicates=20,
        protocol_digest="d" * 64,
        min_valid_member_fraction=0.95,
    )
    assert result is not None
    rows, reduced_series = result
    frame = pd.DataFrame(rows)
    assert (frame["full_support_px"] == 144).all()
    assert (frame["valid_support_px"] == 139).all()
    # full value is the mean over the 139 valid members only
    valid = d3_inputs.valid_member_mask(bands, kind="geomedian")
    expected = d3_inputs.geomedian_metrics({b: v[valid] for b, v in bands.items()})
    nbr_rows = frame[frame["metric_id"] == "nbr"]
    assert nbr_rows["full_value"].unique().tolist() == pytest.approx([expected["nbr"]])
    # no NaN ever reached a replicate value
    assert all(np.isfinite(v).all() for v in reduced_series.values())
    assert frame["replicate_abs_errors"].map(lambda v: np.isfinite(v).all()).all()


def test_simulate_footprint_year_never_draws_an_invalid_member():
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    bands = _bands_with_invalid(144, 5)
    invalid = set(members[:5])
    seed = f"{'d' * 64}|M1|dea_gm_ls5t|2005"
    valid_members = tuple(m for m in members if m not in invalid)
    for replicate in range(100):
        sample = d3_inputs.sample_support(valid_members, 100, replicate=replicate, seed_material=seed)
        assert not (set(sample) & invalid)
        # ranking the valid subset == full ranking with invalid members removed
        full_rank = d3_inputs._rank_all(members, replicate=replicate, seed_material=seed)
        assert sample == tuple(m for m in full_rank if m not in invalid)[:100]


def test_simulate_footprint_year_refuses_sub_full_support_above_valid_count():
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    with pytest.raises(d3_inputs.D3InputsError, match="valid"):
        d3_inputs.simulate_footprint_year(
            maus_id="M1",
            year=2005,
            source_id="dea_gm_ls5t",
            members=members,
            band_values=_bands_with_invalid(144, 5),
            kind="geomedian",
            supports=(140,),  # 140 > 139 valid and 140 is not the full-support row
            replicates=5,
            protocol_digest="d" * 64,
            min_valid_member_fraction=0.95,
        )


def test_simulate_footprint_year_full_support_row_is_the_reference_when_valid_below_144():
    # Frozen supports always include 144; with 139 valid members the 144 row
    # must be emitted (not refused) as the reference itself: zero error,
    # reduced series == full series.
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    result = d3_inputs.simulate_footprint_year(
        maus_id="M1",
        year=2005,
        source_id="dea_gm_ls5t",
        members=members,
        band_values=_bands_with_invalid(144, 5),
        kind="geomedian",
        supports=(100, 144),
        replicates=5,
        protocol_digest="d" * 64,
        min_valid_member_fraction=0.95,
    )
    assert result is not None
    rows, reduced_series = result
    full_rows = [r for r in rows if r["support_px"] == 144]
    assert full_rows and all(r["valid_support_px"] == 139 for r in full_rows)
    assert all(max(r["replicate_abs_errors"]) == 0.0 for r in full_rows)
    for (metric, support), series in reduced_series.items():
        if support == 144:
            full_value = next(r["full_value"] for r in full_rows if r["metric_id"] == metric)
            assert series == [full_value] * 5
```

`_bands_with_invalid` (Task 6) must be defined above these tests; move it next to `_bands` at line 186 if needed.

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_d3_inputs.py -k simulate_footprint_year -v`
Expected: FAIL (`TypeError` on the new keyword for all; the "never draws" test passes already because it exercises `_rank_all` only, which is the point of resolution 3).

**Step 3: Implement.** In `src/wa_mine_monitor/d3_inputs.py` `simulate_footprint_year` (lines 241-323):

Add parameter after `protocol_digest: str,` (line 251):
```python
    min_valid_member_fraction: float,
```

Replace lines 273-278:
```python
    if not year_computable(band_values, kind=kind):
        return None

    metric_fn = geomedian_metrics if kind == "geomedian" else fc_metrics
    full = metric_fn(band_values)
    member_index = {m: i for i, m in enumerate(canonical)}
```
with
```python
    valid = valid_member_mask(band_values, kind=kind)
    if not _mask_computable(valid, min_valid_member_fraction):
        return None
    # Decision 2026-08-23: full and replicate values are computed over the
    # VALID members only; invalid members are never sampled.
    valid_members = tuple(m for m, ok in zip(canonical, valid, strict=True) if ok)
    valid_values = {band: values[valid] for band, values in band_values.items()}

    metric_fn = geomedian_metrics if kind == "geomedian" else fc_metrics
    full = metric_fn(valid_values)
    member_index = {m: i for i, m in enumerate(valid_members)}
```

Replace line 286 `_rank_all(canonical, replicate=replicate, seed_material=seed_material)` with `_rank_all(valid_members, replicate=replicate, seed_material=seed_material)`. (Per-member hashes are set-independent, so this equals the full ranking filtered to valid members; nesting across supports is preserved.)

Replace lines 293-296:
```python
        if support > len(canonical):
            raise D3InputsError(
                f"requested support {support} exceeds available {len(canonical)} members"
            )
```
with
```python
        if support > len(valid_members):
            if support != d3_protocol.MIN_FULL_SUPPORT_PX:
                raise D3InputsError(
                    f"requested support {support} exceeds the {len(valid_members)} valid "
                    f"members of {len(canonical)} (maus_id={maus_id}, source_id={source_id}, "
                    f"year={year})"
                )
            # The full-support row IS the reference: sample = all valid members.
            draw = len(valid_members)
        else:
            draw = support
```
and replace `sample = replicate_rankings[replicate][:support]` (line 300) with `sample = replicate_rankings[replicate][:draw]`. The row's `support_px` stays `support` (144) so the threshold table keeps its frozen support axis.

Replace line 302 `reduced = metric_fn({band: values[indices] for band, values in band_values.items()})` with `reduced = metric_fn({band: values[indices] for band, values in valid_values.items()})`.

Replace line 315 `"valid_support_px": len(canonical),` with `"valid_support_px": len(valid_members),`.

Update the docstring line 258-259 `Support below 144 is a caller error (refused); an invalid pixel is a data property (year not computable -> None).` to `Support below 144 is a caller error (refused); fewer than ceil(min_valid_member_fraction * members) valid pixels is a data property (year not computable -> None).`

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_d3_inputs.py -v`
Expected: all PASS, including the unchanged `_rank_all` oracle tests and `test_sample_support_*`.

---

### Task 8: `check_procedures_consistency` names the new rules

**Files:**
- Modify: `src/wa_mine_monitor/d3_inputs.py:480-492` (append after the decode check)
- Test: `tests/test_d3_inputs.py` (append)

**Step 1: Write the failing tests**

```python
def _procedures():
    from pathlib import Path

    import yaml

    cfg = Path(__file__).resolve().parents[1] / "config" / "d3.yaml"
    return dict(yaml.safe_load(cfg.read_text())["d3"]["procedures"])


def test_check_procedures_consistency_accepts_frozen_text():
    d3_inputs.check_procedures_consistency(_procedures())


def test_check_procedures_consistency_refuses_substring_commodity_text():
    procedures = _procedures()
    procedures["commodity_mode"] = "case-insensitive substring match"
    with pytest.raises(d3_inputs.D3InputsError, match="commodity_mode"):
        d3_inputs.check_procedures_consistency(procedures)


def test_check_procedures_consistency_refuses_all_pixels_valid_text():
    procedures = _procedures()
    procedures["full_support_year"] = "Every contributing band pixel non-null"
    with pytest.raises(d3_inputs.D3InputsError, match="full_support_year"):
        d3_inputs.check_procedures_consistency(procedures)
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_d3_inputs.py -k check_procedures_consistency -v`
Expected: first PASS, two FAIL (`DID NOT RAISE`).

**Step 3: Implement.** Append to `check_procedures_consistency` after the `missing_decode` block (after line 492):

```python
    commodity_mode = str(procedures.get("commodity_mode", ""))
    if "exact token" not in commodity_mode:
        raise D3InputsError(
            "protocol drift: procedures.commodity_mode no longer describes the exact "
            "token match d3_protocol.classify_commodity implements (decision 2026-08-23)"
        )

    full_support_year = str(procedures.get("full_support_year", ""))
    if "min_valid_member_fraction" not in full_support_year:
        raise D3InputsError(
            "protocol drift: procedures.full_support_year no longer names the "
            "min_valid_member_fraction rule year_computable implements (decision 2026-08-23)"
        )
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_d3_inputs.py -k check_procedures_consistency -v`
Expected: 3 PASS.

---

### Task 9: `cli.py` Phase A / Phase B wiring

**Files:**
- Modify: `src/wa_mine_monitor/cli.py:4092-4094`, `:4275-4285`
- Test: `tests/test_cli.py` (existing `build-d3-inputs` chain tests)

**Step 1: Run the chain tests to see the failure**

Run: `uv run pytest tests/test_cli.py -k "build_d3_inputs" -v`
Expected: FAIL with `TypeError: year_computable() missing 1 required keyword-only argument: 'min_valid_member_fraction'`.

**Step 2: Phase A.** Replace `src/wa_mine_monitor/cli.py:4092-4094`:
```python
                by_year_source.setdefault(year, {})[source_id] = d3_inputs.year_computable(
                    decoded, kind=kind
                )
```
with
```python
                by_year_source.setdefault(year, {})[source_id] = d3_inputs.year_computable(
                    decoded,
                    kind=kind,
                    min_valid_member_fraction=protocol.adequacy.min_valid_member_fraction,
                )
```

**Step 3: Phase B.** In the `d3_inputs.simulate_footprint_year(` call at `src/wa_mine_monitor/cli.py:4275-4285`, add after `protocol_digest=frozen_digest,` (line 4284):
```python
                min_valid_member_fraction=protocol.adequacy.min_valid_member_fraction,
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli.py -k "d3" -v`
Expected: all PASS (fixture rasters are fully valid, so Phase A/B outcomes are unchanged; `test_build_d3_inputs_refuses_drifted_protocol` still refuses with `drift`).

---

### Task 10: Full suite, ruff, mypy

**Step 1:** Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: no findings. Fix any line >100 chars in the edited blocks.

**Step 2:** Run: `uv run mypy src`
Expected: `Success: no issues found`.

**Step 3:** Run: `uv run pytest -q`
Expected: all pass, 0 failures. If `scripts/diag_d3_computability.py` is imported by any test and calls `year_computable`, add `min_valid_member_fraction=1.0` there (grep showed only comment references; confirm with `grep -n year_computable scripts/*.py`).

**Step 4:** Run `kit:verify`.

---

### Task 11: Luminosity runbook (shell only, after the branch is merged/pushed on Jarrod's ask)

No code. Run every command on luminosity (`ssh jarrod@luminosity`, fallback `jarrod@luminosity.local`). The Mac is on a metered hotspot: copy back only the small tables, never `support_inputs.parquet`. The disk-guard and follow-up watcher are reconstructed from `docs/plans/2026-08-21-batch-d-live-run-and-batch-e-e3.md` §B3-B4 and the `docs/checkpoints/batch-d-result.md` run note (tmux `wmm-d3`, `--read-workers 32`, logs `reports/*-<date>.log`); the checkpoint did not record the exact guard/watcher command lines, so these are equivalents.

**Step 1: Supersede the old freeze and pull the branch**

```
cd ~/wa-mine-rehab-monitor   # confirm with: git rev-parse --show-toplevel
git status --short            # must be clean
git fetch origin && git checkout feature/d3-commodity-codes-valid-fraction && git pull --ff-only
uv sync
ls /mnt/data/wa-mine-monitor/curated/d3-protocol/
mv /mnt/data/wa-mine-monitor/curated/d3-protocol/2026-08-18 /mnt/data/wa-mine-monitor/curated/d3-protocol.superseded-2026-08-18
ls /mnt/data/wa-mine-monitor/curated/d3-protocol/   # expected: empty
```

**Step 2: Freeze the new protocol**

```
D=2026-08-23
uv run wa-mine-monitor freeze-d3-protocol --config config/luminosity.yaml --date $D --protocol-config config/d3.yaml 2>&1 | tee /mnt/data/wa-mine-monitor/reports/freeze-d3-protocol-$D.log
python3 -c "import json;print(json.load(open('/mnt/data/wa-mine-monitor/curated/d3-protocol/$D/protocol.json'))['protocol_digest'])"
```
Expected: exit 0; digest differs from `b2fa76f7d1dae1cf...`. Record the digest for Task 12.

**Step 3: Verify the old-digest refusal is gone** (dry gate check; stops before reads only if a refusal fires)

```
uv run wa-mine-monitor build-d3-inputs --config config/luminosity.yaml --date $D --protocol-config config/d3.yaml --help >/dev/null && echo cli-ok
grep -l "protocol drift" /mnt/data/wa-mine-monitor/reports/build-d3-inputs-2026-08-21.log || echo "no drift refusal in prior log"
```
The real gate check is the first ~30 s of Step 4's log: it must not print `{"refusal": ...drift...}` or `...lineage violated...`.

**Step 4: Launch the run in tmux — disk guard FIRST, then build + follow-up in ONE sequential shell**

The 2026-08-22 run had two races (guard/watcher polling `pgrep` before the build existed). This layout has none: the guard is an unconditional `while true` loop started before the build, and derive/apply run in the same shell as the build, gated on its exit status, so no process-watching is needed.

```
R=/mnt/data/wa-mine-monitor/reports
tmux new-session -d -s wmm-guard -c ~/wa-mine-rehab-monitor "while true; do U=\$(du -sb /mnt/data/wa-mine-monitor | cut -f1); F=\$(df --output=avail -k /mnt/data | tail -1); echo \"\$(date -Is) used_bytes=\$U free_kb=\$F\" > $R/disk-guard.status; if [ \$F -lt 62914560 ]; then echo \"\$(date -Is) LOW DISK, killing run\" >> $R/disk-guard.status; pkill -f 'wa-mine-monitor build-d3-inputs'; fi; sleep 60; done"
sleep 2; cat $R/disk-guard.status   # must show a fresh line before continuing

cat > ~/wa-mine-rehab-monitor/scripts/run_d3_chain_2026-08-23.sh <<'EOF'
#!/usr/bin/env bash
set -u
D=2026-08-23
R=/mnt/data/wa-mine-monitor/reports
cd ~/wa-mine-rehab-monitor
export PYTHONUNBUFFERED=1 AWS_NO_SIGN_REQUEST=YES AWS_REGION=ap-southeast-2 GDAL_CACHEMAX=1024 CPL_VSIL_CURL_CACHE_SIZE=1073741824 GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR GDAL_HTTP_MAX_RETRY=5 GDAL_HTTP_RETRY_DELAY=5
df -h /mnt/data > $R/df-before-$D.log
~/.local/bin/uv run wa-mine-monitor build-d3-inputs --config config/luminosity.yaml --date $D --protocol-config config/d3.yaml --read-workers 32 >> $R/build-d3-inputs-$D.log 2>&1
rc=$?
echo "BUILD_EXIT=$rc" >> $R/build-d3-inputs-$D.log
echo "$(date -Is) build finished exit=$rc" >> $R/d3-followup-$D.log
if [ $rc -eq 0 ]; then
  ~/.local/bin/uv run wa-mine-monitor derive-d3-threshold --config config/luminosity.yaml --date $D > $R/derive-d3-threshold-$D.log 2>&1
  echo "derive_exit=$?" >> $R/d3-followup-$D.log
  ~/.local/bin/uv run wa-mine-monitor apply-d3-threshold --config config/luminosity.yaml --date $D > $R/apply-d3-threshold-$D.log 2>&1
  echo "apply_exit=$?" >> $R/d3-followup-$D.log
else
  echo "build failed; derive/apply not run" >> $R/d3-followup-$D.log
fi
echo "$(date -Is) followup done" >> $R/d3-followup-$D.log
EOF
chmod +x ~/wa-mine-rehab-monitor/scripts/run_d3_chain_2026-08-23.sh
tmux new-session -d -s wmm-d3 -c ~/wa-mine-rehab-monitor "~/wa-mine-rehab-monitor/scripts/run_d3_chain_2026-08-23.sh; sleep 3600"
sleep 60; tail -n 5 $R/build-d3-inputs-2026-08-23.log   # must NOT contain "refusal"
```

If a `wmm-guard` session from the previous run is still alive (`tmux ls`), kill it first (`tmux kill-session -t wmm-guard`) so only one guard runs.

**Step 5: Monitor** (expected ~16 h)

```
tail -n 5 /mnt/data/wa-mine-monitor/reports/build-d3-inputs-2026-08-23.log
tmux capture-pane -pt wmm-d3:0 | tail -20
```
Any `{"refusal": ...}`: stop, run `kit:debugging` against the log; do not delete curated outputs by hand.

**Step 6: Copy back the small tables only** (from lux)

```
D=2026-08-23
rsync -av --exclude='support_inputs.parquet*' jarrod@luminosity:/mnt/data/wa-mine-monitor/curated/d3-inputs/$D/ ~/Documents/wa-mine-monitor-data/curated/d3-inputs/$D/
rsync -av jarrod@luminosity:/mnt/data/wa-mine-monitor/curated/d3-threshold/$D/ ~/Documents/wa-mine-monitor-data/curated/d3-threshold/$D/
rsync -av jarrod@luminosity:/mnt/data/wa-mine-monitor/curated/register/$D/ ~/Documents/wa-mine-monitor-data/curated/register/$D/
rsync -av jarrod@luminosity:/mnt/data/wa-mine-monitor/curated/d3-protocol/$D/ ~/Documents/wa-mine-monitor-data/curated/d3-protocol/$D/
```
Do not transfer `support_inputs.parquet` (~0.5 GB) while on the hotspot.

---

### Task 12: Checkpoint update

**Files:**
- Modify: `docs/checkpoints/batch-d-result.md` (append at end)

**Step 1: Append this section**

```markdown
## Rerun 2026-08-23 — commodity codes + valid-fraction protocol

Decision: `docs/decisions/2026-08-23-d3-commodity-codes-and-valid-fraction.md` (supersedes the 2026-08-18 freeze; `curated/d3-protocol/2026-08-18` moved to `curated/d3-protocol.superseded-2026-08-18` on luminosity).

- **Frozen protocol digest:** _pending_ (freeze 2026-08-23)
- **Commit:** _pending_
- **Candidate footprint counts per stratum:** _pending_ (dry-run expectation: gold 726 / other 267 / iron_ore 136 / nickel 95 / mineral_sands 16 / bauxite_alumina 12; 17 adequate strata)
- **Selected footprint counts per stratum:** _pending_ (dry-run expectation: 416 selected)
- **Footprint-years simulated:** _pending_
- **Footprint-years not computable:** _pending_
- **computable_fraction per adequate stratum:** _pending_
- **n_star (threshold):** _pending_
- **criteria_passed:** _pending_
- **Per-criterion margins with counts:** _pending_
- **Eligibility counts by trajectory_status:** _pending_
- **Run timing:** _pending_ (start/end AWST, `--read-workers 32`, tmux `wmm-d3`, logs `reports/{freeze-d3-protocol,build-d3-inputs,derive-d3-threshold,apply-d3-threshold}-2026-08-23.log`)
- **Copied to lux:** _pending_ (small tables only; `support_inputs.parquet` left on luminosity)

Batch E E4/E5 gate: reopen only if `criteria_passed=true`.
```

**Step 2: Update the status line** at the top of the file to: `**Status:** 2026-08-21 live run FAILED (commodity rules never matched); protocol re-frozen 2026-08-23 per decision doc — rerun _pending_`.

**Step 3: Verify** Run: `grep -c "_pending_" docs/checkpoints/batch-d-result.md`
Expected: `13` or more.
