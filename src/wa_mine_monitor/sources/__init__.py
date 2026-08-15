"""External data-source fetchers: one module per source, each pairing a
pinned endpoint with its own validation of what came back.

Every module here owns exactly one source's fetch-and-validate contract; the
licence facts for that source live in `wa_mine_monitor.licence.SOURCES`, not
here, so a source's licence status has a single definition even though its
fetch mechanics are declared separately.
"""

from __future__ import annotations
