"""Event Log: the append-only, addressable, lossless history substrate.

The one substrate both agent tiers share (docs/harness-design.md section 3). SQLite is
the source of truth; FTS5/BM25 gives deterministic recall with no index-time model calls.
Redis is an optional hot tier + many-writer stream; if it is down, writes go straight to
SQLite and recall still works.
"""

from core.log.store import EpisodeStore
