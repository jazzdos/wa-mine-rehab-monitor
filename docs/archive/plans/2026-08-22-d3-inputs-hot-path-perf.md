# D3 Inputs Hot-Path Performance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Cut the twelve D3 CLI/acceptance tests (currently 21–44 s each, ~300 s of a 325 s suite) to a few seconds each by removing two pure-Python hot loops in `d3_inputs.py`, with bit-identical orderings and numerically equivalent correlations.

**Architecture:** Profiling `test_build_d3_inputs_end_to_end_over_fixtures` (cProfile, 42.9 s) attributes ~15 s to `_rank_all` (40 000 calls; 11.5 M `_rank_key` f-string+sha256 evaluations through a `sorted(key=lambda)`) and ~15 s to `spearman` (72 000 calls of pandas `Series.rank().corr()` on ≤ 40-element series, ~0.2 ms each of pandas overhead). Task 1 keeps the sha256 ranking semantics exactly (same token bytes, same digest, same ascending order) but formats each member token once per footprint-year, hashes `prefix + token` per replicate, and sorts an index list by precomputed digest bytes. Task 2 replaces pandas with a numpy average-rank + Pearson, asserted equal to pandas within 1e-12 on random series with ties. Neither task changes any public signature, refusal, or None-disclosure. The frozen protocol is unaffected: `protocol_digest` covers config, not code, and the determinism/acceptance tests in `tests/test_batch_d_acceptance.py` must stay green.

**Tech Stack:** Python 3.12, numpy, pandas (tests only for the reference), pytest, ruff (line-length 100), mypy. Run `kit:code-standards` for Python before editing.

**Baseline:** `uv run pytest -q` → `714 passed` in ~325 s on main at `625f166`.

---

### Task 1: `_rank_all` — hash once per member, sort by precomputed digests

**Files:**
- Modify: `src/wa_mine_monitor/d3_inputs.py:51-73` (`_rank_key`, `_rank_all`)
- Test: `tests/test_d3_inputs.py`

**Step 1: Write the failing test** (append to `tests/test_d3_inputs.py`)

```python
import hashlib
import random


def _rank_all_reference(members, *, replicate, seed_material):
    """The pre-perf implementation, kept verbatim as the ordering oracle."""

    def key(member):
        token = f"{seed_material}|{replicate}|{member[0]},{member[1]},{member[2]}"
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    distinct = sorted(set(members))
    return tuple(sorted(distinct, key=key))


def test_rank_all_ordering_is_identical_to_reference_over_random_inputs():
    rng = random.Random(20260822)
    for trial in range(25):
        tiles = [f"x{rng.randint(0, 99)}y{rng.randint(0, 99)}" for _ in range(rng.randint(1, 3))]
        members = [
            (rng.choice(tiles), rng.randint(0, 4000), rng.randint(0, 4000))
            for _ in range(rng.randint(1, 400))
        ]
        members += members[: rng.randint(0, len(members))]  # duplicates must collapse
        seed = f"digest{trial}|maus-{trial}|src|{1990 + trial}"
        for replicate in (0, 1, 17, 99):
            got = d3_inputs._rank_all(members, replicate=replicate, seed_material=seed)
            want = _rank_all_reference(members, replicate=replicate, seed_material=seed)
            assert got == want, (trial, replicate)
            assert len(got) == len(set(members))


def test_rank_all_is_a_strict_prefix_relation_across_supports():
    members = [("t", r, c) for r in range(20) for c in range(20)]
    ranked = d3_inputs._rank_all(members, replicate=3, seed_material="s")
    assert d3_inputs.sample_support(members, 144, replicate=3, seed_material="s") == ranked[:144]
    assert d3_inputs.sample_support(members, 300, replicate=3, seed_material="s") == ranked[:300]
```

**Step 2: Run to verify the oracle test passes against the current code (it is a characterisation test)**

Run: `uv run pytest tests/test_d3_inputs.py -q -k rank_all`
Expected: `2 passed` — the reference and the current implementation are the same code. This step pins the oracle BEFORE the rewrite; the rewrite in Step 3 must keep it green.

**Step 3: Rewrite `_rank_all`** (replace lines 51–73 of `src/wa_mine_monitor/d3_inputs.py`; keep the docstring's second paragraph)

```python
def _rank_key(member: Member, replicate: int, seed_material: str) -> str:
    """Hex sha256 of `seed_material|replicate|tile,row,col` -- the ranking key.

    Retained as the readable single-member form; `_rank_all` computes the
    same digest for every member without re-formatting the prefix.
    """
    token = f"{seed_material}|{replicate}|{member[0]},{member[1]},{member[2]}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _rank_all(
    members: Sequence[Member], *, replicate: int, seed_material: str
) -> tuple[Member, ...]:
    """Every distinct member ranked by sha256(seed_material|replicate|member),
    ascending -- the full ordering `sample_support` slices a prefix of.

    The ranking depends only on `(replicate, seed_material)`, never on a
    requested support size, so a caller needing several support levels for
    the SAME replicate (`simulate_footprint_year`, sweeping the frozen
    `supports` tuple) must compute this once and slice, rather than calling
    `sample_support` once per support -- that would re-sort (and re-hash
    every member) once per support for no behavioural difference, an O(len
    (supports)) multiplier this project's fixtures make expensive at real
    pixel counts.

    Digest BYTES sort identically to their hex encoding (hex is a
    monotone byte-wise encoding), so the comparison key is the raw digest
    and the token is `prefix + member_bytes` with the prefix encoded once.
    """
    distinct = sorted(set(members))
    prefix = f"{seed_material}|{replicate}|".encode()
    sha256 = hashlib.sha256
    digests = [sha256(prefix + f"{m[0]},{m[1]},{m[2]}".encode()).digest() for m in distinct]
    order = sorted(range(len(distinct)), key=digests.__getitem__)
    return tuple(distinct[i] for i in order)
```

Note: `sorted(set(members))` first guarantees that equal digests (impossible in practice) and the tie-break fall back to the same stable order the reference used.

**Step 4: Run the oracle and the existing d3_inputs tests**

Run: `uv run pytest tests/test_d3_inputs.py -q`
Expected: all pass (existing count + 2).

**Step 5: Measure one chain test**

Run: `uv run pytest tests/test_cli.py::test_build_d3_inputs_end_to_end_over_fixtures -q --durations=1 -p no:cacheprovider`
Expected: PASS; reported duration ≤ 15 s (was ~21–43 s). If it is not materially lower, report the number — do not tune further in this task.

---

### Task 2: `spearman` — numpy average ranks + Pearson

**Files:**
- Modify: `src/wa_mine_monitor/d3_inputs.py:114-123` (`spearman`)
- Test: `tests/test_d3_inputs.py`

**Step 1: Write the failing tests** (append)

```python
def test_spearman_equals_pandas_rank_corr_within_1e12_including_ties():
    rng = np.random.default_rng(20260822)
    for _ in range(200):
        n = int(rng.integers(2, 40))
        full = pd.Series(np.round(rng.normal(size=n), int(rng.integers(0, 3))))
        reduced = pd.Series(np.round(full.to_numpy() + rng.normal(scale=0.5, size=n), 2))
        if full.nunique() < 2 or reduced.nunique() < 2:
            assert d3_inputs.spearman(full, reduced) is None
            continue
        expected = float(full.rank().corr(reduced.rank()))
        got = d3_inputs.spearman(full, reduced)
        assert got is not None
        assert got == pytest.approx(expected, abs=1e-12), (n, full.tolist(), reduced.tolist())


def test_spearman_is_exactly_one_for_identical_series():
    s = pd.Series([3.0, 1.0, 2.0, 2.0, 5.0])
    assert d3_inputs.spearman(s, s.copy()) == pytest.approx(1.0, abs=1e-12)


def test_spearman_refuses_length_mismatch():
    with pytest.raises(d3_inputs.D3InputsError, match="years"):
        d3_inputs.spearman(pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 2.0]))
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_d3_inputs.py -q -k spearman`
Expected: the three existing spearman tests pass and the three new ones ALSO pass against the pandas implementation (they characterise it). That is intended — they become the equivalence oracle for Step 3. If any new test fails here, fix the TEST, not the code, and report it.

**Step 3: Rewrite `spearman`** (replace the function body; add the helper directly above it)

```python
def _average_ranks(values: np.ndarray) -> np.ndarray:
    """1-based average ranks with ties averaged -- pandas `rank(method="average")`."""
    order = np.argsort(values, kind="stable")
    sorted_vals = values[order]
    # Boundaries of tie groups in sorted order.
    is_new_group = np.concatenate(([True], sorted_vals[1:] != sorted_vals[:-1]))
    group_id = np.cumsum(is_new_group) - 1
    group_start = np.flatnonzero(is_new_group)
    group_end = np.append(group_start[1:], len(values))  # exclusive
    # Average of 1-based positions start+1 .. end  ==  (start + 1 + end) / 2
    group_rank = (group_start + 1 + group_end) / 2.0
    ranks = np.empty(len(values), dtype=np.float64)
    ranks[order] = group_rank[group_id]
    return ranks


def spearman(full: pd.Series, reduced: pd.Series) -> float | None:
    if len(full) < MIN_SPEARMAN_YEARS or len(full) != len(reduced):
        raise D3InputsError(
            f"spearman needs >= {MIN_SPEARMAN_YEARS} paired years, got "
            f"{len(full)} vs {len(reduced)}"
        )
    a = np.asarray(full, dtype=np.float64)
    b = np.asarray(reduced, dtype=np.float64)
    if len(np.unique(a)) < 2 or len(np.unique(b)) < 2:
        return None  # undefined for a constant series -- caller discloses
    ra = _average_ranks(a)
    rb = _average_ranks(b)
    ra -= ra.mean()
    rb -= rb.mean()
    denom = math.sqrt(float(ra @ ra) * float(rb @ rb))
    return float(ra @ rb) / denom
```

Keep the `pd.Series` parameter types — `cli.py:4255` passes Series and other callers may rely on it; `np.asarray` accepts them. If `len(np.unique(...))` differs from `Series.nunique()` for NaN inputs, note that the caller never passes NaN here (`year_computable` gates it) and add a one-line comment.

**Step 4: Run the spearman tests**

Run: `uv run pytest tests/test_d3_inputs.py -q -k spearman`
Expected: `6 passed`.

**Step 5: Measure one chain test again**

Run: `uv run pytest tests/test_cli.py::test_build_d3_inputs_end_to_end_over_fixtures -q --durations=1 -p no:cacheprovider`
Expected: PASS; duration ≤ 6 s. Report the number.

---

### Task 3: Full battery and determinism evidence

**Files:**
- None modified (verification only). If ruff/mypy complain, fix in place.

**Step 1: Determinism and acceptance**

Run: `uv run pytest tests/test_batch_d_acceptance.py tests/test_cli.py -q -k "d3 or determinism or accuracy or strata or trajectory_status" --durations=15 -p no:cacheprovider`
Expected: all pass; the slowest test ≤ 10 s (was 43.7 s).

**Step 2: Battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q --durations=5 -p no:cacheprovider`
Expected: ruff clean, mypy clean, `719 passed` (714 + 5), total wall time ≤ 90 s. Report the exact tail including the durations block.

**Step 3:** Report — do not commit.
