"""Public-facing wording checks for the public-RC lane (D11).

README.md and docs/licensing-matrix.md are the two documents a public
reader sees first. These tests pin the exact claim-boundary sentence (D11),
the operator/owner distinction (D8), and the internal-vs-public-fallback
framing (D7) as literal, greppable strings — not paraphrase-checked, so a
future edit cannot drift the wording without failing here.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
MATRIX_PATH = REPO_ROOT / "docs" / "licensing-matrix.md"

CLAIM_BOUNDARY = (
    "Descriptive spectral change chronologies; not a compliance or performance assessment."
)


def _find_all(haystack: str, needle: str) -> list[int]:
    """Return every start index of `needle` in `haystack` (non-overlapping scan)."""
    indices = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        indices.append(idx)
        start = idx + 1
    return indices


def test_readme_carries_the_exact_d11_sentence_at_first_reference():
    text = README_PATH.read_text(encoding="utf-8")
    non_empty_lines = [line for line in text.splitlines() if line.strip()]
    first_five = non_empty_lines[:5]
    assert any(CLAIM_BOUNDARY in line for line in first_five), (
        "expected the exact D11 claim-boundary sentence, on one line, among "
        f"the first 5 non-empty lines of README.md; got: {first_five!r}"
    )


def test_readme_never_calls_owners_operators():
    text = README_PATH.read_text(encoding="utf-8")
    lower = text.lower()
    for idx in _find_all(lower, "operator"):
        window = lower[max(0, idx - 60) : idx + 60]
        assert "not operators" in window or "never" in window, (
            f"'operator' occurrence at {idx} lacks a disclaiming window: {window!r}"
        )


def test_readme_distinguishes_internal_frame_from_public_fallbacks():
    text = README_PATH.read_text(encoding="utf-8")
    assert "internal MINEDEX" in text
    assert "tier0-tenements" in text
    assert "tier0-maus-wa" in text


def test_readme_never_implies_minedex_rows_are_distributed():
    text = README_PATH.read_text(encoding="utf-8")
    assert "MINEDEX-derived rows are not distributed" in text


def test_no_compliance_or_performance_claim_language():
    text = README_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "compliance finding",
        "performance finding",
        "recovery has",
        "equivalent to rehabilitation",
    ):
        assert forbidden not in text, f"README.md contains forbidden phrase: {forbidden!r}"


def test_licensing_matrix_names_the_two_packages():
    text = MATRIX_PATH.read_text(encoding="utf-8")
    assert "tier0-tenements" in text
    assert "tier0-maus-wa" in text
