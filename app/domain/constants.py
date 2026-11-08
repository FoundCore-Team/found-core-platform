# Largest value a Postgres BIGINT column (every id here) can hold.
MAX_INT64 = 2**63 - 1

# Bump together with any change to the scoring formula or ranking rules: it is part of the feed session key, so
# sessions built by the old code are never served by the new one. A constant, not config, because it changes with code.
FEED_ALGORITHM_VERSION = 1

# Longest reason an admin can give for an action. Mirrors the size of `admin_actions.reason` (and `users.ban_reason`
# receives the same text on a ban), so the request schema and the column cannot drift apart.
ADMIN_REASON_MAX_LENGTH = 1000

# Longest optional comment on a report. Mirrors the size of `reports.comment`.
REPORT_COMMENT_MAX_LENGTH = 1000

# Length of every stored embedding. The `vector(1536)` column type depends on it, so it is not configurable.
EMBEDDING_DIMENSIONS = 1536
