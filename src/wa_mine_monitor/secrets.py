# Ported from jarrah-rehab src/jarrah_rehab/secrets.py at commit cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed by the same author.
"""Secret-redaction primitives shared across the CLI, config and manifest
writers.

Anything that serialises a `ProjectConfig` (or a raw config mapping) to a
log, terminal, or persisted file must run it through `redact_secrets` first.
`ProjectConfig` sets `extra="allow"` at the top level, so arbitrary
credential-shaped keys (e.g. a SLIP API token) can land in the resolved
config; this module is the single place that strips them before output.

`redact_secrets` is key-name based: it only catches secrets that live under a
credential-shaped dict key. Three other shapes carry secrets by *value*, not
by key, and need the scrubbers below instead:

- `argv` sequences, where a token follows a credential-shaped `--flag`;
- URL-like strings (a `SourceAsset.uri`), where the secret sits in userinfo
  or a query parameter;
- free text (a git diff), where the secret sits in an embedded URL or an
  assignment-shaped `key=value` / `key: value` fragment.

`scrub_argv_secrets`, `scrub_url_secrets` and `scrub_text_secrets` cover
those. Everything that writes a manifest, log, or persisted artefact must run
`argv`, any URI, and any free-text blob (diffs, stdout captures) through the
matching scrubber -- never reimplement redaction elsewhere.

`scrub_string_leaves` is the composition for a whole nested structure: it walks
a mapping/list tree and runs `scrub_text_secrets` over every string leaf, so a
secret carried BY VALUE under a non-credential-shaped key is caught too. Pair it
with `redact_secrets` -- `scrub_string_leaves(redact_secrets(...))` -- at every
boundary that echoes or persists a config, so the terminal echo and the manifest
on disk get the identical treatment rather than one being weaker than the other.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: The credential vocabulary. Every matcher in this module derives from this
#: one tuple so the list cannot drift between them.
_SECRET_WORDS = (
    "password",
    "passwd",
    "pwd",
    "pass",
    "token",
    "secret",
    "key",
    "credential",
    "login",
    "username",
    "user",
    "auth",
    "session",
)

#: Field/flag names matching this pattern are redacted before any config is
#: printed or persisted. Substring match (not word-bounded) so it also
#: catches names like `api_token`, `slip_password`, `slip_login` or
#: `--slip-token`. Over-matching here is cheap: redacting one extra *config
#: value* is visible in the output and costs nothing. Free text is different --
#: see `_is_credential_key`.
_SECRET_NAME_PATTERN = re.compile("|".join(_SECRET_WORDS), re.IGNORECASE)
_REDACTED = "***REDACTED***"

#: Splits an identifier into its words: on any non-alphanumeric run
#: (`api_key`, `slip-token`, `db.auth.token`) and at camelCase boundaries
#: (`apiKey`).
_KEY_SEGMENT_PATTERN = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def _is_credential_key(key: str) -> bool:
    """True when a *whole word* of `key` is a credential word.

    Free text gets a stricter test than dict keys do. `_SECRET_NAME_PATTERN`
    is a substring match, so `sort_keys` matches on `key` and `bypass` matches
    on `pass`; applied to a git diff embedded in a run manifest that silently
    rewrites source lines, and the manifest then no longer reconstructs the
    tree it claims to. Requiring a whole segment keeps every realistic
    credential name (`api_key`, `SLIP_TOKEN`, `apiKey`, `db.auth.token`) while
    dropping the false positives.

    Known gap: an unseparated all-lowercase compound such as `apikey` is one
    segment and is not matched. The URL scrubber still covers the common case
    of a credential embedded in a link, and any residual scrubbing of a diff
    is disclosed by the manifest's `git.diff_scrubbed` field.
    """
    return any(
        segment.lower() in _SECRET_WORDS for segment in _KEY_SEGMENT_PATTERN.split(key) if segment
    )


#: Matches a URL-like substring embedded in free text, e.g. inside a git diff
#: line, so it can be routed through `scrub_url_secrets`.
_EMBEDDED_URL_PATTERN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")

#: Characters that end a sentence or a quoted string rather than a URL. `\S+`
#: above runs to the next whitespace, so it swallows these; they are trimmed
#: back off before the URL is parsed.
_URL_TRAILING_PUNCTUATION = "\"'>)]},.;:!?"

#: Fallback for a URL-shaped token `urlsplit` cannot parse (e.g. a `[` in the
#: netloc trips Python 3.12's stricter IPv6 bracket parsing). Matches a
#: `scheme://userinfo@` authority prefix -- the `@` must land before the
#: first `/`, `?` or `#`, so a bare `user@host/path?a=b@c` query fragment
#: does not falsely register as userinfo. Deliberately conservative: it only
#: ever matches the exact shape it names (and keeps the `scheme://` group so
#: the URL structure survives the strip), so it can redact but never corrupt
#: a line holding no secret.
_UNPARSEABLE_USERINFO_PATTERN = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/?#@]*@")


def _url_carries_credentials(url: str) -> bool:
    """True when a URL actually has userinfo or a credential-shaped query key.

    Free text is only rewritten when there is something to remove.
    `scrub_url_secrets` round-trips through `urlencode`, which re-encodes
    otherwise-legal characters (`bbox=1,2,3,4` -> `bbox=1%2C2%2C3%2C4`), and
    in a git diff that rewrite is corruption of a line holding no secret.

    `urlsplit` itself raises `ValueError` ("Invalid IPv6 URL") on a URL-SHAPED
    token that is not a real URL -- most reachably a regex literal such as
    ``https://[^...]*/path`` embedded in a git diff, because a `[` in the netloc
    trips Python 3.12's stricter IPv6 bracket parsing. This scrubber runs over
    every run manifest's diff, so it must NEVER crash on ordinary provenance
    text. But a token `urlsplit` cannot parse can still carry real userinfo
    ahead of the part that breaks parsing (`https://admin:hunter2@[evilhost/path`),
    so unlike the "nothing to remove" outcome for a genuinely credential-free
    token, an unparseable token is checked with a regex userinfo probe and
    treated as carrying credentials whenever that probe matches: under-
    redaction, not a parse failure, is the failure mode this module exists to
    avoid.
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        return bool(_UNPARSEABLE_USERINFO_PATTERN.search(url))
    if not parsed.scheme or not parsed.netloc:
        return False
    if "@" in parsed.netloc:
        return True
    return any(
        _SECRET_NAME_PATTERN.search(key)
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    )


#: Matches a `key=value` / `key: value` / `key："value"'` assignment whose
#: key is credential-shaped, so the value half can be redacted in free text
#: that isn't a structured mapping (e.g. a git diff line like
#: `+SLIP_TOKEN=abc123` or `+    "api_key": "abc123",`).
#: The key half is a cheap prefilter only -- `_is_credential_key` makes the
#: actual decision, and a match whose key fails it is left untouched.
_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<key>[\w.-]*(?:" + "|".join(_SECRET_WORDS) + r")[\w.-]*)"
    r"(?P<keyquote>[\"']?)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\s\"'&,;]+)"
    r"(?P=quote)",
    re.IGNORECASE,
)


def redact_secrets(value: Any) -> Any:
    """Recursively replace values of credential-shaped keys with a redaction marker."""
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _SECRET_NAME_PATTERN.search(str(key)) else redact_secrets(inner))
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def scrub_url_secrets(value: str) -> str:
    """Strip credential-shaped userinfo and query parameters from a URL-like string.

    Strings without both a scheme and a netloc (i.e. anything that isn't
    actually a URL -- a `dea://` scene reference, a bare local path) are
    returned unchanged. A URL-shaped token `urlsplit` cannot parse at all (a
    bracketed regex literal in a diff raises `ValueError: Invalid IPv6 URL`
    under Python 3.12) is NOT automatically returned unchanged: if it still
    matches a `scheme://userinfo@` authority prefix ahead of whatever broke
    the parse, that userinfo is stripped via a conservative regex fallback
    (see `_UNPARSEABLE_USERINFO_PATTERN`) rather than passed through verbatim
    -- this function fails toward redaction, never toward leaking a
    credential into a persisted run-manifest diff. This function must not
    crash on the provenance text a run manifest carries.
    """
    try:
        parsed = urlsplit(value)
    except ValueError:
        return _UNPARSEABLE_USERINFO_PATTERN.sub(r"\g<scheme>", value, count=1)
    if not parsed.scheme or not parsed.netloc:
        return value

    netloc = parsed.netloc
    if "@" in netloc:
        # Drop userinfo (user:pass@host) entirely rather than partially
        # redacting it -- a username alone can still be credential material.
        netloc = netloc.rsplit("@", 1)[1]

    scrubbed_query = urlencode(
        [
            (key, _REDACTED if _SECRET_NAME_PATTERN.search(key) else val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, scrubbed_query, parsed.fragment))


def scrub_argv_secrets(argv: Sequence[str]) -> list[str]:
    """Redact the token following a credential-shaped `--flag` in an argv sequence.

    Handles both `--flag value` (the value is the next token) and
    `--flag=value` (the value is embedded in the same token) forms.
    """
    scrubbed: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            scrubbed.append(_REDACTED)
            redact_next = False
            continue
        if token.startswith("-"):
            flag, sep, _value = token.partition("=")
            flag_name = flag.lstrip("-")
            if _SECRET_NAME_PATTERN.search(flag_name):
                if sep:
                    scrubbed.append(f"{flag}={_REDACTED}")
                else:
                    scrubbed.append(token)
                    redact_next = True
                continue
        scrubbed.append(token)
    return scrubbed


def scrub_text_secrets(text: str) -> str:
    """Best-effort redaction of credential-shaped values embedded in free text.

    For content that `redact_secrets` cannot reach because it isn't a
    structured mapping -- most notably a git diff, which can contain an
    added line introducing a token in a URL or a `KEY=value` assignment.
    Scrubs embedded URL-like substrings via `scrub_url_secrets`, then
    redacts the value half of any `key=value` / `key: value` assignment
    whose key is credential-shaped.
    """

    def _scrub_embedded_url(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(_URL_TRAILING_PUNCTUATION)
        trailing = match.group(0)[len(url) :]
        if not _url_carries_credentials(url):
            return match.group(0)
        return scrub_url_secrets(url) + trailing

    text = _EMBEDDED_URL_PATTERN.sub(_scrub_embedded_url, text)

    def _redact_assignment(match: re.Match[str]) -> str:
        if not _is_credential_key(match.group("key")):
            return match.group(0)
        return (
            f"{match.group('key')}{match.group('keyquote')}{match.group('sep')}"
            f"{match.group('quote')}{_REDACTED}{match.group('quote')}"
        )

    return _ASSIGNMENT_PATTERN.sub(_redact_assignment, text)


def scrub_string_leaves(value: Any) -> Any:
    """Recursively apply `scrub_text_secrets` to every `str` leaf.

    `redact_secrets` only catches secrets that live under a credential-shaped
    *key* (e.g. `slip_api_token`); a secret carried by *value* under an
    innocuous key (e.g. `stac_endpoint: "https://user:pw@host/path"`) survives
    it untouched. This walks whatever structure `redact_secrets` already
    replaced credential-shaped keys in and scrubs every remaining string.

    Lives here, beside the scrubbers it composes, rather than in any one
    consumer: the manifest writer and the CLI's `config-check` echo both need
    it, and a second definition is how a terminal echo ends up strictly weaker
    than the record persisted from the same mapping.
    """
    if isinstance(value, dict):
        return {key: scrub_string_leaves(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [scrub_string_leaves(item) for item in value]
    if isinstance(value, str):
        return scrub_text_secrets(value)
    return value
