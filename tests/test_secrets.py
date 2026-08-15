# Ported from jarrah-rehab tests/test_secrets.py at commit
# cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed by
# the same author. Import rewritten only (jarrah_rehab.secrets ->
# wa_mine_monitor.secrets); every test compiles and behaves unchanged.
import pytest

from wa_mine_monitor.secrets import (
    redact_secrets,
    scrub_argv_secrets,
    scrub_string_leaves,
    scrub_text_secrets,
    scrub_url_secrets,
)


def test_scrub_string_leaves_reaches_a_secret_carried_by_value() -> None:
    """`scrub_string_leaves` closes what `redact_secrets` structurally cannot:
    a credential under a non-credential-shaped key. It lives here (not in a
    consumer) so the CLI echo and the manifest writer share one definition."""
    payload = {"slip_endpoint": "https://jarrod:hunter2@slip.wa.gov.au/d", "n": [1, 2]}

    assert redact_secrets(payload)["slip_endpoint"] == payload["slip_endpoint"]

    scrubbed = scrub_string_leaves(redact_secrets(payload))

    assert "hunter2" not in scrubbed["slip_endpoint"]
    assert "jarrod" not in scrubbed["slip_endpoint"]
    assert "slip.wa.gov.au" in scrubbed["slip_endpoint"]
    assert scrubbed["n"] == [1, 2]


@pytest.mark.parametrize(
    "key",
    [
        "slip_login",
        "slip_username",
        "slip_pass",
        "auth_header",
        "api_token",
        "password",
        "passwd",
        "pwd",
        "session_id",
        "credential",
        "secret_key",
        "user",
    ],
)
def test_redact_secrets_covers_login_shaped_fields(key: str) -> None:
    redacted = redact_secrets({key: "hunter2"})
    assert redacted[key] == "***REDACTED***"


def test_redact_secrets_leaves_non_secret_fields_untouched() -> None:
    redacted = redact_secrets({"data_root": "/data", "seed": 42})
    assert redacted == {"data_root": "/data", "seed": 42}


def test_redact_secrets_recurses_into_nested_structures() -> None:
    redacted = redact_secrets(
        {
            "slip": {"slip_login": "jarrod", "slip_pass": "hunter2"},
            "tokens": ["ok"],
            "api_token": "x",
        }
    )
    assert redacted["slip"]["slip_login"] == "***REDACTED***"
    assert redacted["slip"]["slip_pass"] == "***REDACTED***"
    assert redacted["api_token"] == "***REDACTED***"


# --- scrub_url_secrets ------------------------------------------------------


def test_scrub_url_secrets_strips_credential_shaped_query_param() -> None:
    scrubbed = scrub_url_secrets("https://maps.slip.wa.gov.au/wfs?api_key=SUPERSECRETTOKEN")
    assert "SUPERSECRETTOKEN" not in scrubbed
    assert "REDACTED" in scrubbed


def test_scrub_url_secrets_strips_userinfo() -> None:
    scrubbed = scrub_url_secrets("https://svc_user:hunter2@maps.slip.wa.gov.au/wfs")
    assert "svc_user" not in scrubbed
    assert "hunter2" not in scrubbed


def test_scrub_url_secrets_leaves_non_secret_query_params_untouched() -> None:
    scrubbed = scrub_url_secrets("https://example.com/wfs?bbox=1,2,3,4&format=geojson")
    assert scrubbed == "https://example.com/wfs?bbox=1%2C2%2C3%2C4&format=geojson"


def test_scrub_url_secrets_leaves_non_url_strings_untouched() -> None:
    assert scrub_url_secrets("dea://scene/LS8_001") == "dea://scene/LS8_001"
    assert scrub_url_secrets("/local/path/to/file.tif") == "/local/path/to/file.tif"


def test_scrub_url_secrets_leaves_unparseable_bracketed_token_untouched() -> None:
    # A regex literal is URL-SHAPED (scheme://rest) but not a real URL: the `[`
    # in the netloc position makes urlsplit raise "Invalid IPv6 URL" under
    # Python 3.12. The scrubber must return it unchanged, never crash -- this
    # exact token reaches the scrubber via a run manifest's git diff when
    # scripts/fetch_alcoa_monthly.py's `spatialfiles` regex is an untracked
    # (diffed-against-/dev/null) file.
    token = r"https://[^\"'\s]*/spatialfiles/[^\"'\s]*?\.zip"
    assert scrub_url_secrets(token) == token


def test_scrub_url_secrets_redacts_userinfo_in_unparseable_url() -> None:
    # `urlsplit` raises on the unbalanced `[` (Invalid IPv6 URL), but this
    # token still carries a `scheme://user:pass@` authority ahead of the
    # broken bracket -- unlike the credential-free regex-literal case above,
    # this one must fail TOWARD redaction, not pass through verbatim.
    leaky = "https://admin:hunter2@[evilhost/path"
    scrubbed = scrub_url_secrets(leaky)
    assert "hunter2" not in scrubbed
    assert "admin:hunter2@" not in scrubbed


# --- scrub_argv_secrets ------------------------------------------------------


def test_scrub_argv_secrets_redacts_value_following_credential_flag() -> None:
    scrubbed = scrub_argv_secrets(["jarrah-rehab", "detect", "--slip-token", "ANOTHERSECRET"])
    assert "ANOTHERSECRET" not in scrubbed
    assert scrubbed[:3] == ["jarrah-rehab", "detect", "--slip-token"]
    assert "REDACTED" in scrubbed[3]


def test_scrub_argv_secrets_redacts_inline_equals_form() -> None:
    scrubbed = scrub_argv_secrets(["jarrah-rehab", "detect", "--slip-token=ANOTHERSECRET"])
    joined = " ".join(scrubbed)
    assert "ANOTHERSECRET" not in joined
    assert "REDACTED" in joined


def test_scrub_argv_secrets_leaves_non_secret_flags_untouched() -> None:
    argv = ["jarrah-rehab", "detect", "--config", "pilot.yaml"]
    assert scrub_argv_secrets(argv) == argv


# --- scrub_text_secrets ------------------------------------------------------


def test_scrub_text_secrets_redacts_url_embedded_in_diff_text() -> None:
    diff_text = (
        "diff --git a/config/pilot.yaml b/config/pilot.yaml\n"
        "+slip_endpoint: https://maps.slip.wa.gov.au/wfs?api_key=SUPERSECRETTOKEN\n"
    )
    scrubbed = scrub_text_secrets(diff_text)
    assert "SUPERSECRETTOKEN" not in scrubbed
    assert "REDACTED" in scrubbed


def test_scrub_text_secrets_redacts_assignment_shaped_secret() -> None:
    diff_text = '+    "api_key": "SUPERSECRETTOKEN",\n'
    scrubbed = scrub_text_secrets(diff_text)
    assert "SUPERSECRETTOKEN" not in scrubbed
    assert "REDACTED" in scrubbed


def test_scrub_text_secrets_leaves_non_secret_text_untouched() -> None:
    diff_text = "diff --git a/foo.py b/foo.py\n-old line\n+new line\n"
    assert scrub_text_secrets(diff_text) == diff_text


def test_scrub_text_secrets_survives_a_bracketed_url_regex_in_a_diff() -> None:
    # A diff line adding a URL-shaped regex literal must not crash the scrubber:
    # urlsplit raises "Invalid IPv6 URL" on the `[`, and scrub_text_secrets runs
    # over every run manifest's diff. The line carries no credential, so it is
    # returned unchanged. (Regression for the Python 3.12 urlsplit crash.)
    diff_text = (
        "diff --git a/scripts/fetch_alcoa_monthly.py b/scripts/fetch_alcoa_monthly.py\n"
        "+_SPATIALFILES_ZIP_RE = re.compile("
        'r"https://[^\\"\'\\s]*/spatialfiles/[^\\"\'\\s]*?\\.zip")\n'
    )
    assert scrub_text_secrets(diff_text) == diff_text


def test_scrub_text_secrets_redacts_userinfo_in_unparseable_url() -> None:
    # Same shape as test_scrub_url_secrets_redacts_userinfo_in_unparseable_url
    # above, but through the free-text path (`scrub_text_secrets` ->
    # `_url_carries_credentials` -> `scrub_url_secrets`) that a persisted
    # run-manifest git diff actually goes through.
    leaky = "https://admin:hunter2@[evilhost/path"
    scrubbed = scrub_text_secrets(leaky)
    assert "hunter2" not in scrubbed
    assert "admin:hunter2@" not in scrubbed


@pytest.mark.parametrize(
    "text",
    [
        # The observed corruption: `sort_keys` merely *contains* `key`, so a
        # substring match rewrote this project's own manifest-writing line.
        "+    manifest_json = json.dumps(manifest, sort_keys=True)\n",
        "+keyword=search\n",
        "+keystone=enabled\n",
        "+if bypass=True:\n",
        "+passes=3\n",
        "+tokens=[1, 2, 3]\n",
    ],
)
def test_scrub_text_secrets_leaves_words_merely_containing_a_secret_word(text: str) -> None:
    """A credential word buried inside a longer word is not a credential.

    `scrub_text_secrets` runs over git diffs embedded in run manifests. A false
    positive silently rewrites a line of the recorded diff, so the manifest no
    longer reconstructs the tree it claims to.
    """
    assert scrub_text_secrets(text) == text


@pytest.mark.parametrize(
    "text",
    [
        '+    url = "https://example.com/wfs?bbox=1,2,3,4&format=geojson"\n',
        "+see https://example.com/docs (the endpoint).\n",
        "+endpoint: <https://example.com/stac>\n",
    ],
)
def test_scrub_text_secrets_does_not_swallow_punctuation_after_a_url(text: str) -> None:
    """The embedded-URL matcher must stop at the URL, not at the next space.

    `\\S+` ran past the closing quote/bracket, so `scrub_url_secrets`
    percent-encoded it and rewrote a diff line that contained no secret at all.
    """
    assert scrub_text_secrets(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "+SLIP_TOKEN=SUPERSECRETTOKEN\n",
        '+    "api_key": "SUPERSECRETTOKEN",\n',
        "+apiKey: SUPERSECRETTOKEN\n",
        "+db.auth.token = SUPERSECRETTOKEN\n",
        "+slip-password=SUPERSECRETTOKEN\n",
        "+SECRET: SUPERSECRETTOKEN\n",
    ],
)
def test_scrub_text_secrets_still_catches_separated_credential_keys(text: str) -> None:
    scrubbed = scrub_text_secrets(text)
    assert "SUPERSECRETTOKEN" not in scrubbed
    assert "REDACTED" in scrubbed
