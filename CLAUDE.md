# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Mandatory development rules (do/don't) are in [`docs/rules.md`](docs/rules.md) — read it before writing or changing any code.** This file (`CLAUDE.md`) is the source of truth for product context and architecture; `docs/rules.md` is the normative rulebook and points back here for the concrete patterns (e.g. `Container`/DI).

## Project overview

FoundCore is a Telegram Mini App + bot for networking. It connects people across two domain blocks:

- **Tech & Product** — co-founders, developers, designers, investors, marketers, sales/bizdev, finance/legal.
- **Edu & Growth** — study mates, language partners, hackathon/pet-project teammates, mentors.

The original product spec (a `.docx` kept outside this repo) is summarized in this file; if a product or architecture decision isn't covered here, ask rather than guess.

**The backend is built in `app/`; the Telegram Mini App frontend lives in [`frontend/`](frontend/)** and has its own [`CLAUDE.md`](frontend/CLAUDE.md), [`docs/rules.md`](frontend/docs/rules.md) and plans ([`docs/plans/overview.md`](frontend/docs/plans/overview.md)). Unless a section says otherwise, everything below describes the backend.

**Key pivot — no swipes.** The original swipe/mutual-match concept (Like/Skip, contact revealed only on mutual like) is cancelled. The current mechanic is a **feed with filters and search**: a user browses recommendations and/or applies filters; a contact opens immediately on button press, with no reciprocity required. The only feedback loop is a notification to the profile owner that "someone opened your contact" — there are no likes or matches.

Explicitly out of scope: a social network/chat/content platform as the core product, gamification, subscriptions, public reputation/levels, fully autonomous AI-matching (AI assists scoring but is never the sole judge of compatibility).

## Repository state

Stage 1 (`skeleton`), stage 2 (`auth-telegram`) and stage 3 (`taxonomy`) are fully implemented. Stage 2 shipped `POST /api/v1/auth/telegram` plus the JWT-verification dependency (`get_current_user`), which stage 3 is the first to actually consume — every taxonomy endpoint requires a valid token, there's no anonymous read access to the reference data. Stage 3 added the `categories`/`roles`/`role_fields`/`tags`/`tag_scopes` tables, all 5 taxonomy endpoints (see "API" below), an idempotent seed script (`scripts/dev_seed_taxonomy.py`), and Redis cache-aside for the read-heavy reference tables. Stage 4 (`profiles`) is implemented and merged (see "Profiles" below): `profiles`/`profile_tags` tables, 9 endpoints under `/api/v1/profiles`, `profiles` and `user.active_profile_id` in the `/auth/telegram` response. Stage 5 (`embeddings`) is implemented on `feat/embeddings` (see "Embeddings" below; the plan with every decision is [`docs/plans/stage-5-embeddings.md`](docs/plans/stage-5-embeddings.md)): the `profile_embeddings` table with an HNSW index, stub and OpenAI providers, a Celery worker and a beat sweep. No API endpoint exposes embeddings yet (stage 6 reads them). Stage 6 (`feed`) is implemented and merged to `main` (PR #6; see "Feed algorithm" below; the plan with every decision and the list of deviations from it is [`docs/plans/stage-6-feed.md`](docs/plans/stage-6-feed.md)): the `profile_views` table, `GET /api/v1/feed`, `GET /api/v1/feed/cards/{id}`, `POST /api/v1/feed/views`, feed sessions in Redis, the `require_username` dependency and the ~100-profile dev seed (`scripts/dev_seed_profiles.py`). The `redis` Python client is pinned to 6.4.0 (see "Tech stack"). Stage 7 (`contacts-notifications`) is implemented on `feat/contacts-notifications` (see "Contacts and notifications" below; the plan with every decision and the list of deviations from it is [`docs/plans/stage-7-contacts.md`](docs/plans/stage-7-contacts.md)): the `contact_opens` table, `POST /api/v1/profiles/{id}/contact`, a daily limit of 20 opens per user, `is_contact_opened` on feed cards, and bot notifications through a separate Celery queue (`notification_worker`) with a sweep. The Celery broker variable is now `APP_CONFIG__WORKER__BROKER_URL` (it was `APP_CONFIG__EMBEDDING__BROKER_URL`). Everything else in "Development stages" below is not started yet; the next stage is 8 (`moderation`). Open follow-ups from stage 6: the embedding audit and improvement ideas under "Risks and open questions". The schema is managed by Alembic (see "Migrations"); the app no longer creates tables itself. Tests: a pytest suite split into `tests/unit` (no Postgres/Redis, in-memory fakes) and `tests/integration` (real Postgres and Redis, guarded so it can only touch `*_test_db` and a non-default Redis DB). It covers the auth flow (`initData` and JWT hostile-input cases, the first-login race, ban/`token_version` over real HTTP, user repository, mappers), stage 3 (tag normalizer/validator, taxonomy use cases, `SqlAlchemyTaxonomyRepository`, the self-healing Redis cache, request bounds on all 5 taxonomy endpoints, the dev seed), transaction behavior (`IUnitOfWork`), `Settings` (prod fail-fast) and stage 5 (embedding input and hash, providers against a mocked transport, embed and sweep use cases, the Celery queue and tasks, the embeddings repository and worker against real Postgres, the broker) and stage 6 (scorer, ranker, session fingerprints, filter validator, cursor, feed use cases with fakes; the feed SQL, view repository, Redis sessions and all three endpoints against real Postgres and Redis; the profile seed and its fixture) and stage 7 (the open-contact use case with fakes, the notification text, the send and sweep use cases, the Telegram provider against a mocked transport, the Celery queue and tasks; the endpoint over real HTTP with the limit and the parallel-open races, the notification worker and sweep against real Postgres and the broker, the broker being down); GitHub Actions runs it with coverage on every PR.

## User flow

- **Auth**: user opens the bot → Mini App → frontend gets Telegram `initData` → backend verifies the signature locally (HMAC-SHA256 from the bot secret, no call to Telegram's servers) → issues a JWT (24h, no refresh token — the Mini App just re-authenticates from fresh `initData` on expiry).
- A user with no Telegram username cannot be contacted by other users (no way to open a chat with them), so: (a) that user cannot view the feed themselves until they set a username, and (b) other users never see that user as a recommendation, since a contact they couldn't actually reach would be useless. The auth endpoint itself does not enforce this — it's checked once the profile form (anketa) is filled, which is where a username becomes required to proceed.
- **Profile/onboarding**: on first login the user accepts terms and fills a form built from the taxonomy reference tables (category → role → role-specific dynamic fields) plus the common fields described by `GET /profiles/form-config` (country, timezone, bio, goals, tags). Taxonomy-driven parts are never hardcoded on the frontend; the common fields are a fixed typed contract (see "Profiles").
- **Multi-profile**: a user can hold multiple profiles (one per category+role pair, e.g. a developer profile and a separate language-partner profile) and switch the active one via a menu. The active profile determines the feed, available filters, what counts as "already viewed," and whose name a contact is opened under.
- **Feed has two modes**: Discovery (no filters — semantic similarity + tag/role match within the same category) and Search (filters, three-tier cascade so the screen is never empty).
- **Viewed profiles ("grey cards")**: a profile the user already opened stays in place in the ranking but gets `is_viewed = true`; the frontend renders it grey. The flag is set on opening the full card, not on scrolling past it.
- **Contact opening**: opens immediately, no mutuality. The profile owner gets a bot notification naming who opened their contact (full name, category · role of both profiles, `@username`, and a button that opens the opener's profile in the Mini App). Opening the same profile again is free and silent; a daily limit applies to first-time opens (see "Contacts and notifications"). No random-profile backfill when filters exhaust — an explicit "nobody yet" state is shown instead.

## Taxonomy

Categories/roles/tags/fields live in Postgres and are cached in Redis; the taxonomy is data-driven and expands without backend code changes. The roles (and their fields/tags) are seeded by `scripts/dev_seed_taxonomy.py` (`founder`, `product_project`, `engineering`, `design`, `marketing`, `sales_bizdev`, `finance_legal` under Tech & Product; `study_mate`, `language_buddy`, `pet_project_partner`, `mentor_mentee` under Edu & Growth).

**`role_fields` vs `tags` — deliberate split.** `role_fields` only holds single-choice *structural* attributes that gate filtering (`grade`, `project_stage`, `specialization`, language `level`, `workload`, `format`, `frequency`) — always `field_type = select`. Skills, tools, stack, domain and similar open-ended or multi-value attributes are **tags**, not `role_fields` — they're unbounded and grow from user input, which `role_fields` (a fixed reference table maintained by admins) isn't designed for. `RoleFieldType` has `multi_select`/`number`/`text`/`boolean` members for future use, but the stage 3 seed only ever emits `select`.

**Common fields are duplicated per role, not shared by reference.** Tech & Product roles each get their own `workload` + `format` rows in `role_fields`; Edu & Growth roles each get their own `frequency` + `format` rows (`language_buddy` overrides `format` with its own call/chat/in-person options instead of the generic online/offline one, since it's a better fit for that role specifically). This is plain row duplication across roles, not a shared/inherited field — `role_fields` has no such concept, and adding one wasn't justified for 11 roles. See `scripts/dev_seed_taxonomy.py` for the exact per-role field list.

**Redis cache-aside: two independent repositories, orchestrated by the use case** (see `docs/rules.md`, "Repository responsibility" / "Caching (Redis)"):

```
                   Use Case
                  /        \
 ITaxonomyRepository      ITaxonomyCacheRepository
          ↓                         ↓
 SqlAlchemyTaxonomyRepository   RedisTaxonomyCacheRepository
          ↓                         ↓
      PostgreSQL                  Redis
```

`SqlAlchemyTaxonomyRepository` (`infrastructure/repositories/sqlalchemy_taxonomy.py`) only talks to Postgres. `ITaxonomyCacheRepository` (`domain/interfaces/taxonomy_cache.py`) is typed: `get_categories`/`set_categories`, `get_roles_by_category`/`set_roles_by_category`, `get_role_fields_by_role`/`set_role_fields_by_role`, all taking and returning domain entities. Its implementation `RedisTaxonomyCacheRepository` (`infrastructure/repositories/redis_taxonomy_cache.py`) owns everything Redis-specific: the keys (`taxonomy:categories`, `taxonomy:roles:{category_id}`, `taxonomy:role_fields:{role_id}`), entity ↔ JSON (de)serialization (including rebuilding `RoleFieldType` from its string value), the TTL (`TaxonomyConfig.cache_ttl_seconds`, 6h, passed to its constructor by `container.py`) and `RedisError` handling. For the use case, a cache miss, an unreadable cached value and a Redis outage all look the same: `None`. A corrupt or outdated value (bad JSON, missing/unknown field, renamed enum member) is logged and treated as a miss, and the use case's following `set_*` overwrites it, so the cache heals itself instead of failing for the whole TTL. The repository instance lives for one request: after its first `RedisError` it skips Redis for the rest of that request (one timeout at worst, not one per call), and `RedisConfig.socket_timeout` is 0.3 s. The `roles`/`fields` use cases check the cache **first** and only on a miss verify that the category/role exists (404) — a hit costs no Postgres query, and a deleted category may be served from cache until the TTL expires (accepted). The seed script clears `taxonomy:*` keys (own Redis client, errors only logged) after committing. All list queries have a deterministic tie-breaker (`sort_order, id`; `suggest`: `usage_count DESC, title, id`). So the use case is pure orchestration:

```python
async def execute(self) -> list[CategoryEntity]:
    cached = await self._taxonomy_cache_repository.get_categories()
    if cached is not None:
        return cached
    categories = await self._taxonomy_repository.get_categories()
    await self._taxonomy_cache_repository.set_categories(categories)
    return categories
```

A generic `ICacheRepository` (`get(key) -> Any` / `set(key, value, ttl)`) was tried and rejected in review: it leaked cache keys, `asdict`, enum rebuilding and TTL into the use cases and hid the real contract behind `Any`. `tags/suggest` is deliberately **not cached** — it's already backed by the `pg_trgm` index, and the query space (arbitrary substrings × category × role × caller) doesn't cache well.

**Tag title validation lives in a domain service and is validation only, not moderation.** `TagTitleValidator` (`domain/services/tag_title_validator.py`) takes `min_length`/`max_length`/`pattern` in its constructor (`TaxonomyConfig` values, injected by `container.py`) and checks only length, allowed characters via `re.fullmatch()` and that the title contains at least one letter or digit (so `...`, `+++`, `_` are rejected as empty of content). Request bounds are enforced earlier by pydantic/FastAPI: ids are `1..MAX_INT64` (`Int64Id` in `api/v1/schemas/common.py`, `Path`/`Query` `ge`/`le` in the router) so an out-of-range id is `422`, never a `500` from asyncpg; `title` is capped at 256 characters and the suggest `q` at 128 before any normalization runs. The default pattern `[\w\s+#./-]+` lets through IT tags like `C++`, `C#`, `.NET`, `Node.js`, `CI/CD`, `UI/UX`. There is **no stop-word check**: it was removed in review as primitive moderation with false positives. Content checks belong to the moderation flow (custom tag → `pending` → admin approves/rejects, stage 8), not to the validator.

**Tag identity: `id` is the identity, `title` is the display value, `normalized_title` is only for exact-duplicate detection.** Tags have no slug (the old lossy `slugify` was removed: `C`/`C#`/`C++` collapsed, Unicode titles could become empty, and nothing needs a public tag identifier — relations go through `tag_id`). `normalized_title` is `UNIQUE` and is produced by `normalize_tag_title` in `domain/services/tag_title_normalizer.py`: NFKC → trim/collapse whitespace → `casefold()`, nothing else — no transliteration, no stripping of `+ # . / -`. The stored `title` is `clean_tag_title` (NFKC + whitespace only, case kept). The column is `String(255)` because `casefold()` can make the string longer than `title` (64). `categories.slug`/`roles.slug` are unrelated admin-defined keys and stay.

Normalization deliberately does **not** detect semantic duplicates: `Node.js`, `NodeJS`, `Node Js` are three different tags until moderation (stage 8) merges them via a planned `merged_into_tag_id` (not implemented yet).

`POST /taxonomy/tags/custom` flow: clean title → validate → check category/role → look up by `normalized_title` → if found, reuse it (any status), otherwise insert a `PENDING` tag → idempotently add the caller's `(category, role)` scope (tag insert and scope insert share one transaction; the use case commits once at the end). A `REJECTED` match raises `TagRejectedError` → `409`. The response always carries the canonical `title`, so the frontend replaces what the user typed (`NODE.JS`) with the existing tag (`Node.js`). Race between SELECT and INSERT: `create_tag` is `INSERT ... ON CONFLICT (normalized_title) DO NOTHING RETURNING` and returns `None` on conflict; the use case then re-reads the tag. The repository never decides "duplicate means return existing" — that stays in the use case; `UNIQUE` is the final guarantee.

Known simplifications: (1) scopes are not moderated — submitting an existing tag from another category/role adds a scope there immediately; (2) `suggest` shows pending tags only to `created_by_user_id` (the original proposer), so a second user who got an existing pending tag from `POST` won't see it in `suggest` and simply re-submits the title to get the same tag again. TODO (a follow-up PR after profiles, which now exist): `suggest` can also include tags already used in the caller's profiles.

**`tag_scopes.role_id = NULL` means "scoped to the whole category"** (enforced idempotent via a `NULLS NOT DISTINCT` unique constraint on `(tag_id, category_id, role_id)`, so a repeat scope insert can't duplicate a category-wide row). The stage 3 seed only creates role-specific scopes (every seeded example tag belongs to one role), so category-wide scoping exists in the schema and in `suggest_tags`'s query logic but has no seeded example yet — the first real category-wide custom tag will be the first row to actually use it.

## Profiles

A user holds several profiles, at most one per (category, role); one of them is the **active** one (`users.active_profile_id`), which will drive the feed, the filters and whose name a contact is opened under.

**Content.** `country_code`, `timezone`, `bio`, `goals_description` (both 200..2000 characters after trimming), tags (**5..15**), and `extra_attributes` — the role's own `role_fields` (only `is_required` ones are mandatory; an empty value or `null` means "not set" and the key is dropped; only `select` is supported, other field types are rejected). There is no `languages` and no `looking_for`. All limits are `ProfileConfig` values (`core/config.py`), a plain `BaseModel`; the same values feed the validators and `form-config`, so frontend and backend limits cannot drift. Category and role cannot be changed after creation.

**Tags** live in `profile_tags(profile_id, tag_id)` with real foreign keys (both `ON DELETE CASCADE`: deleting a profile removes only the links, deleting a tag removes it from profiles). Allowed: `approved` or `pending` (also another user's pending tag, which `POST /taxonomy/tags/custom` may hand out), in the scope of the profile's category/role (a category-wide scope, `role_id IS NULL`, also fits). `rejected` → `409 TagRejectedError`; unknown/out-of-scope/duplicate ids → `400`. Duplicates are checked before the count, so five copies of one tag fail. A tag deleted later may leave a profile with fewer than 5 tags; the minimum is only enforced on save.

**Country and timezone.** All ISO 3166 countries that have a zone in `zone.tab` are accepted (`ProfileConfig.allowed_country_codes`, empty = all). `TimezoneCatalog` (`application/services/timezone_catalog.py`) reads `zone.tab`/`iso3166.tab` from the pinned `tzdata` package (the same source `ZoneInfo` uses). A timezone is valid only if it belongs to the chosen country (`RU` + `Asia/Tokyo` → `400`) and is checked only on write. Country names are English only (the frontend localizes by code); a zone label is the city from the IANA id; `utc_offset` (`+02:00`) is computed on the fly, never stored.

**`GET /profiles/form-config`** (auth required) returns `{bio, goals: {min_length, max_length}, tags: {min_count, max_count}, countries: [{code, name, timezones: [{id, label, utc_offset}]}]}`. Common fields are a fixed typed schema; a new common field means a schema change, a column, a migration and a validator.

**Lifecycle.** A new profile is `active` at once (there are no drafts; if drafts are ever needed they become a separate `profile_drafts` table). Statuses: `active | paused | hidden_by_admin`. The first profile becomes the active one; later ones do not change it.
- `activate`: only for an `active` profile; `paused` → `409`.
- `pause`: if it was the active profile, the pointer moves to the user's newest remaining `active` profile (`created_at DESC, id DESC`) or becomes `NULL`.
- `resume`: `paused → active`; becomes the active profile if the user has none.
- `delete`: physical; same pointer rule as `pause`.
- `hidden_by_admin` (set by moderation, stage 8): the owner cannot edit, resume or activate it → `409`.
- `pause`/`resume`/`activate` are idempotent (a repeat returns the profile, no error, no commit).
- A use case that moves the pointer also updates the `user.active_profile_id` it was given, so the response can report `is_active` without re-reading the user.

**Visibility.** Everything under `/profiles` belongs to the owner. Someone else's profile is `404`, never `403`. Other people's profiles will only be visible through the feed (stage 6).

**Create/update flow.** `ProfileContentValidator` (`application/services`) is shared by create and update: form rules (`ProfileFormValidator`, domain), role attributes (`ExtraAttributesValidator`, domain), then tags via `ITaxonomyRepository.get_tags_in_scope_by_ids` (returns any status; the status decision stays in the validator). Create is `INSERT ... ON CONFLICT (user_id, category_id, role_id) DO NOTHING RETURNING`; `None` → re-read → `ProfileAlreadyExistsError` → `409` whose body carries the existing profile (`{"detail": {"message", "profile"}}`). `PUT /profiles/{id}` is a full replace (`extra="forbid"`, so `category_id`/`role_id` in the body → `422`) and takes `SELECT ... FOR UPDATE` on the profile so two parallel PUTs do not collide on the `profile_tags` primary key. `IUserRepository.set_active_profile` is a narrow `UPDATE`, because `update` writes only Telegram fields.

**Contract with the feed (implemented in stage 6).** A user without an active profile gets `409 no_active_profile` from the feed (discovery and search) — distinct from `NO_CANDIDATES_YET` and `END_OF_FEED`. A paused profile never appears in other people's feeds. The frontend shows onboarding when the profile list is empty and a "resume" screen when profiles exist but all are paused.

**Follow-up PRs after profiles, in this order:** (1) the username gate: the `require_username` dependency (a layer on top of `get_current_user`, `409 username_required`) exists since stage 6 and guards the feed; what is left is putting it on `GET /taxonomy/categories` and `POST /profiles`, so the user learns about the missing username before the form; (2) `terms_accepted_at` in the auth response and an endpoint to accept the terms, the form is shown only after acceptance and with a username; (3) `tags/suggest` also returns tags used in the caller's profiles. Not in this PR: the embeddings table (stage 5), `DELETE /users/me`. (The ~100-profile seed came with stage 6.)

## Data model (`users`, taxonomy, profiles, embeddings, profile_views, contact_opens implemented; rest planned)

- **`users`** (implemented) — Telegram account: `id`, `telegram_id` (unique), `first_name`, `last_name`, `username`, `photo_url`, `language_code` (Telegram's UI language, not a profile language), `terms_accepted_at`, `is_admin`, `is_banned`, `ban_reason`, `token_version` (bumping this instantly invalidates issued JWTs on ban), `active_profile_id`, `created_at`, `last_active_at`, `deleted_at` (nullable, mapped to the entity but no logic reads or writes it yet — account deletion is planned to physically remove data, see "Moderation, security, analytics").
- **`profiles`** (implemented) — one row per (user, category, role); `user_id` FK (`ON DELETE CASCADE`) is deliberately **not unique**, which is what enables multi-profile. Columns: `category_id`/`role_id` (no `ondelete`: deleting a taxonomy row that profiles use must fail, not wipe profiles), `country_code`, `timezone`, `bio`, `goals_description`, `extra_attributes JSONB` (no server default), `status ENUM(active|paused|hidden_by_admin)`, `created_at`, `updated_at` (set by the application). `UNIQUE (user_id, category_id, role_id)` both prevents duplicate profiles and makes double-submit safe — a repeat form submission gets a 409 with the existing profile, no need to dedupe at the nginx layer; it also serves lookups by `user_id`, so there is no separate index. Other indexes: btree `(category_id, role_id, status)`, GIN on `extra_attributes`. `users.active_profile_id` is a real FK to `profiles.id` (`ON DELETE SET NULL`, `use_alter` because of the `users ↔ profiles` cycle).
- **`profile_tags`** (implemented) — `(profile_id, tag_id)` composite PK, both FKs `ON DELETE CASCADE`, index on `tag_id`. Replaces the earlier `tags TEXT[]` idea.
- **`profile_embeddings`** (implemented, stage 5; details in "Embeddings") — one row per profile, kept out of `profiles` so the 1536-float vector never travels with ordinary profile reads: `profile_id` (PK, FK `profiles.id`, `ON DELETE CASCADE`), `embedding vector(1536)` (nullable), `input_hash`, `model`, `status ENUM(pending|ready|failed)`, `source_updated_at` (copy of `profiles.updated_at` when the row was marked `pending`; guards the worker's write against a concurrent edit), `updated_at` (last change of the row; the sweep works off it).
- **Taxonomy tables** (implemented) — `categories`, `roles(category_id)`, `role_fields(role_id, key, label, field_type, options, is_required, is_filterable, sort_order)` (drive the dynamic form/filters), `tags(title, normalized_title UNIQUE, status: approved/pending/rejected, created_by_user_id, usage_count)`, `tag_scopes(tag_id, category_id, role_id)`. `tags.title` has a GIN trigram index (`ix_tags_title_trgm`, needs the `pg_trgm` extension — enabled via a `before_create` DDL event on `Base.metadata` since `create_all` doesn't create extensions) for `tags/suggest`'s substring search. See "Taxonomy" above for the `role_fields`/`tags` split and the cache.
- **`profile_views`** (implemented, stage 6) — `(viewer_profile_id, viewed_profile_id)` composite PK, both FKs to `profiles.id` `ON DELETE CASCADE` (deleting either profile removes its views), `viewed_at` (the first view; a repeat is `ON CONFLICT DO NOTHING` and changes nothing), index on `viewed_profile_id`. A view belongs to the viewer's **active profile** at the time. It is the source of `is_viewed`. Written only by `POST /feed/views`.
- **`contact_opens`** (implemented, stage 7) — `(opener_profile_id, opened_profile_id)` composite PK (one open per pair), both FKs to `profiles.id` `ON DELETE CASCADE` (deleting either profile removes the open), `created_at`, `notification_status ENUM(pending|sent|failed|skipped)`, `notification_updated_at`. The opener is the viewer's **active profile** at the time. It is the source of the daily limit (counted per user through the opener's profile), of `is_contact_opened` and of the notification state. Indexes: the PK prefix serves lookups by opener, btree `(opened_profile_id)` for the cascade and `is_contact_opened`, partial btree `(notification_updated_at) WHERE notification_status = 'pending'` for the sweep.
- **Interaction tables (planned)** — `reports`, `admin_actions`.
- **Indexes** — HNSW on `profile_embeddings.embedding` (`vector_cosine_ops`, `m=16`, `ef_construction=64`), a partial btree on `profile_embeddings(status, updated_at) WHERE status <> 'ready'` for the sweep, GIN on `tags` and `extra_attributes`, btree on `(category_id, role_id, status)` and `last_active_at`, and the `contact_opens` indexes above.

## Feed algorithm (implemented in stage 6; "As built" at the end of this section)

Everything is scored relative to the viewer's **active profile**.

```
score = (0.4 · cos_sim + 0.4 · tag_overlap + 0.2 · role_affinity) · activity_factor
tag_overlap    = |A∩B| / min(|A|, |B|)
role_affinity  = 1.0 same role · 0.5 other role, same category · 0.0 other category
activity_factor = 1.0 (active ≤7d) · 0.8 (≤30d) · 0.5 (>30d)
```

If a profile has no embedding yet, the semantic term is zeroed and its weight is redistributed between tags and role — the feed keeps working, just less precisely.

- **Discovery**: single tier, candidates are active profiles in the *same category only* (excluding self/hidden/banned), sorted by score. Never backfills from another category — if the category runs out, the feed just ends honestly.
- **Search**: only within the active profile's category (no category filter). Filters: `role_id`, filterable role attributes, `tag_ids` (any of them, `approved` only — anything else is `400 invalid_filter`), `country_code`, `same_timezone` (same **current** UTC offset as the viewer: Moscow, Kirov and Istanbul match; the zone list is computed in Python from `TimezoneCatalog`, never in Postgres, whose tz database may differ from `tzdata`). No free-text search. `mode=search` without any filter is `422`. Results are filled by a three-tier cascade so the page is never empty. Each filter is one criterion (role, each attribute separately, tags as one criterion, country, timezone; an unset attribute does not match):
  1. `EXACT_MATCH` — all criteria match; sorted by score.
  2. `PARTIAL_MATCH` — at least one criterion matches but not all; sorted by **number of matched criteria first**, then score (explicit filters outrank our score: a senior engineer without the requested tag, 2 of 3, beats a product manager with it, 1 of 3). This replaces the original "category matches, or at least one shared tag".
  3. `RECOMMENDED_BY_BIO` — any category, vector similarity ≥ a threshold only, filters ignored; not queried when tiers 1–2 already fill the cap.

  Tiers are consumed in order up to the cap; ties break on `profile_id`. Search cards carry a `matched_filters` reason (`matched`/`missed` criterion codes).
- **Feed session pagination**: on the first request (new filters, new active profile, or expired session) the backend ranks candidates up to a **cap of 200** (top-200 of the category for discovery; tiers 1→2→3 in order for search) and stores a lightweight `(profile_id, tier, score, reasons)` list in Redis for 30 minutes, keyed by profile+mode+filters+algorithm version. Each 20-card page just slices that cached list by offset and hydrates only those 20 profiles — vector computation is never repeated per page. The session resets on filter change, active-profile change, or algorithm version bump.
  - Known limitation: if a category genuinely has more than 200 active profiles, candidates ranked below #200 in that session are never shown to that viewer — an accepted tradeoff for cheap pagination, flagged as a future risk, not expected to bite at first-cohort scale.
  - The session key is split into two hashes: `params_hash` (active profile + mode + filters, what the user chose) and the session key (`params_hash` + own profile `updated_at` + "viewer has a comparable vector" + `FEED_ALGORITHM_VERSION` + `FeedConfig.scoring_fingerprint()`, what changes by itself). The fingerprint covers every weight, coefficient and threshold of the formula, so changing a number in `.env` starts new sessions instead of mixing two formulas for 30 minutes; the version covers changes of code; TTL, page sizes and the cap are not part of it. The opaque cursor carries offset, `params_hash` and session key: a different `params_hash` is `400 invalid_cursor`; a session key that is still alive in Redis keeps being sliced even if the current key differs (own vector arrived, deploy), so background events never break scrolling; an expired session is rebuilt and sliced from the same offset. The session stores its owner (`viewer_profile_id`, `params_hash`) and a session from a cursor with another owner is a miss, because the key comes from the client.
- **Empty states** — two distinct cases, don't conflate them: `NO_CANDIDATES_YET` (empty from the first page — "we'll keep looking and notify you") vs `END_OF_FEED` (cache exhausted after showing something — neutral "you've seen everything for now," no notify promise). Both return via `has_more: false` + a reason code in the API response, never as an error.
- **Recommendation card** — public profile data, `match_score` (0–100), `match_type` (tier), `is_viewed`, and a list of deterministically-computed (no AI call) reasons, e.g. "3 shared tags: python, fastapi, postgres", "same role", "same timezone". Reasons are codes with parameters, the frontend builds the text. `username` and `last_name` are not on the card (the contact opens through `POST /profiles/{id}/contact`, see "Contacts and notifications"); the card carries `is_viewed` and `is_contact_opened`. Cards, `tag_overlap` and the `shared_tags` reason use all of the profile's tags except `rejected` (pending ones included: they are the owner's own words).
- **Viewer gate** — `require_username` (a dependency on top of `get_current_user`, `409 username_required`) guards all three feed endpoints (`GET /feed`, `GET /feed/cards/{id}`, `POST /feed/views`); then `409 no_active_profile`.
- **Role affinity stays as is** (same role 1.0, other role in the category 0.5): Discovery deliberately means "people like me", including founders seeing other founders first.

**As built (stage 6)** — what the code does, beyond the design above:

- **SQL gives signals, Python gives the formula.** `SqlAlchemyFeedRepository` (`infrastructure/repositories/sqlalchemy_feed.py`, SQLAlchemy expressions like the other repositories: `select`/`outerjoin`/`case`/CTE, no `text()`) returns `FeedCandidate`s: `cos_sim` (computed in Postgres from the stored vectors, exact, no HNSW; clamped at 0; `NULL` unless both sides have a vector of the **current** model), tags without the `rejected` ones, activity, and for Search the list of matched criteria. `FeedScorer` (formula, degradation, reasons) and `FeedRanker` (tiers, ordering, cap) are pure domain services unit-tested without a database. "Who may appear in somebody's feed" is one list of conditions (`visible_to(viewer)` in `infrastructure/repositories/profile_visibility.py`) shared by the candidate queries, `get_cards` and, since stage 7, the contact repository (so "may I see this profile" and "may I open its contact" can never disagree): active profile, user not banned, non-empty username, not deleted, not one of the viewer's own profiles.
- **Without a comparable vector pair** (`cos_sim is None`) the semantic weight is split between tags and role in the proportion of their own weights (0.4/0.2 become 2/3 and 1/3). The scorer also clamps the cosine into `[0, 1]`.
- **Search criteria** (`FeedFilters.criteria()`): `role`, `attr:<key>` (one per attribute), `tags` (any of the selected), `country`, `timezone`. A candidate gets one tier: all criteria matched is `EXACT_MATCH`; some matched is `PARTIAL_MATCH`, ordered by the number matched, then score, then `profile_id`; none matched is only reachable through the bio tier. Every Search card in tiers 1–2 carries a `matched_filters` reason. The bio tier is queried only when the viewer has a comparable vector and tiers 1–2 left room under the cap.
- **`same_timezone`** compares the **current** UTC offset: `TimezoneCatalog.zone_ids_with_same_offset(zone)` lists the zones, the list goes to SQL as `timezone = ANY(...)`, and the same list drives the `same_timezone` reason. The viewer's own zone is always included.
- **Filters are validated** by `FeedFilterValidator` (role must be in the viewer's category; attributes need a role and must be filterable `select` fields with a valid option; the country must exist; tags must be `approved`, anything else, including an unknown id, is one `tag_invalid`). `mode=search` without a filter and `mode=discovery` with one are both `422`. The error body is `{"detail": {"code": "invalid_filter", "message", "reason"}}`.
- **Sessions** live in Redis (`feed:session:{session_key}`, the same logical DB as the taxonomy cache, `feed.session_ttl_seconds`) and hold `(profile_id, tier, score, reasons)` only; `shared_tags` reasons keep tag ids and the titles come from the hydrated card. `RedisFeedSessionRepository` follows the cache rules (any `RedisError` or unreadable value is a miss, the repository skips Redis for the rest of the request). A session named by a cursor is used only if its stored `viewer_profile_id` and `params_hash` match the request, because the key comes from the client. If Redis is down the feed is rebuilt per request and still answers.
- **Cursor** (`api/v1/mappers/feed.py`): url-safe base64 of `{"o": offset, "p": params_hash, "s": session_key}`; anything that is not exactly that (alphabet, JSON shape, `type(o) is int`, `0 <= o <= feed.max_cursor_offset`, `p`/`s` 64 lowercase hex) is `400 invalid_cursor`, and so is a `p` that differs from the current request. A live session named by the cursor keeps being sliced even if the current key moved on (the viewer's vector arrived, their profile was edited, a deploy); an expired one is rebuilt and sliced from the same offset.
- **Pages**: hydration (`IFeedRepository.get_cards`) re-checks visibility, so a profile paused or banned since the session was built drops out and a page may hold fewer than `limit` items while `has_more` stays true. `is_viewed` is computed from `profile_views` per request and never stored in the session; `is_contact_opened` (the viewer's active profile has opened that profile's contact, from `contact_opens`) is computed the same way, in `get_cards`. `match_score` is `round(score * 100)`.
- **Errors**: `409 username_required` (checked first, in `require_username`), then `409 no_active_profile` (no pointer, or the pointed profile is not `active`), `400 invalid_filter`, `400 invalid_cursor`, `404` for a profile that is missing, the viewer's own, or not visible (the same answer for all of them), `403` for a banned viewer (from `get_current_user`), `422` for out-of-range input.
- **Card by id** (`GET /feed/cards/{id}`) serves any visible profile of any category with a score relative to the active profile and `match_type: null`; opening a profile is recorded separately by `POST /feed/views` (a `GET` never writes).
- **Public card** (`FeedProfileSchema`): `id`, `first_name`, `photo_url`, category, role, country, timezone, `bio`, `goals_description`, `extra_attributes`, `tags`. Never `username` or `last_name`. Reasons are `{"code": "shared_tags", "tags": [{"id", "title"}]}`, `{"code": "matched_filters", "matched": [...], "missed": [...]}` or just `{"code": ...}`.
- **Local data**: `python -m scripts.dev_seed_taxonomy`, then `python -m scripts.dev_seed_profiles` (`--reset` recreates the seed users, `--reembed` recomputes their vectors after switching provider). The fixture (`scripts/data/seed_profiles/*.json`, 90 users, 100 profiles, every username `seed_…`) covers paused profiles, banned users, users without a username, profiles without a vector, two profiles per user and zones with one offset. To act as a seed user: `python scripts/dev_gen_init_data.py --telegram-id 1000000000001 --username seed_… --first-name …` (telegram id is `10**12 + key`). The seed refuses to run with `run.env == prod`.

## Tech stack

**In use now** (listed in `requirements.txt`):

- Python 3.12, FastAPI, Pydantic v2 (+ `pydantic-settings`), `uvicorn`.
- SQLAlchemy 2.0 (async, `asyncpg`), `PyJWT` for auth tokens, `redis` (asyncio client, pinned to 6.4.0: see the Redis note below), `celery[redis]` + `kombu` (background jobs), `pgvector` (Python package for the `vector` column), `httpx` (OpenAI and Telegram Bot API clients; also used by the tests), `tzdata` (pinned; country/timezone catalog for profiles).
- PostgreSQL 16 — source of truth. Local/dev image is `pgvector/pgvector:pg16` (plain `postgres` images don't have the extension). `pg_trgm` serves tag search and `vector` serves the embeddings; both are enabled by `before_create` DDL events on `Base.metadata` and by explicit `CREATE EXTENSION` in the migrations.
- Redis 7 (`redis:7-alpine` in docker-compose) — taxonomy cache (logical DB 0); the Celery broker (the `embeddings` and `notifications` queues, one `celery_app`) lives in its own logical DB (2 in dev, `APP_CONFIG__WORKER__BROKER_URL`); feed sessions (the cache's logical DB, `feed:session:*`, 30 minutes) and rate limiting later. Test DBs: 1 for the cache, 3 for the broker. The integration fixtures `FLUSHDB` them, so the guard in `tests/integration/conftest.py` refuses to run if the cache is DB 0 or the broker is DB 0 or 2. Map: 0 dev cache, 1 test cache, 2 dev broker, 3 test broker.
- The `redis` **Python client** is pinned to 6.4.0 (it was 8.1.0 before stage 5): the stable `kombu` (the transport under Celery) requires `redis<6.5`. Raise it once a stable kombu accepts redis-py 8 (see celery/kombu#2639). Only the client is downgraded; the Redis server version is unchanged.
- `pgvector==0.5.1`: the `profile_embeddings` table and, in stage 6, similarity queries. Do **not** add a `register_vector` listener to `db_helper`: with SQLAlchemy over asyncpg the vector travels as a string, and that codec expects a list and fails with `expected list or ndarray`.
- `celery[redis]`: background jobs: embeddings (stage 5), contact notifications (stage 7), later analytics. Chosen over `arq` (requires `redis<6`, maintenance-only) and SAQ (`redis<8` extra); the stage 5 plan has the full comparison.
- Docker Compose for local dev: the API, `db`, `redis`, and for stages 5 and 7 `embedding_worker`, `notification_worker` and `embedding_beat` (the same image; the one beat schedules both sweeps).
- Tests (`requirements-dev.txt`): `pytest`, `pytest-asyncio`, `pytest-cov`, `asgi-lifespan`, `freezegun`. Config is in `pyproject.toml`; the test env is `.env.test` (separate `found_core_test_db` in the dev Postgres container, Redis on localhost DB 1 so tests never flush the dev cache). CI: `.github/workflows/tests.yml` (pgvector Postgres + Redis services, `pytest -v --cov=app --cov-report=term-missing`, no coverage threshold). Run `pytest` from the host virtualenv, not through `docker compose exec`: the image installs only `requirements.txt`, and `.env.test` points at the `localhost` ports that `db` and `redis` publish.
- Migrations: Alembic (async template, `asyncpg` — no second sync driver), applied by hand with `alembic upgrade head`. See "Migrations" below.

**Planned, not added yet** (not in `requirements.txt`):

- PostHog — product analytics (stage 9).
- `ruff` + `mypy` for code quality.
- nginx in front of the app in prod (stage 10).

## Layered architecture

Clean Architecture variant, chosen specifically so a two-person team can work on different modules without git conflicts. Actual structure (as built, `app/`):

```
api/v1/
  routers/{auth,taxonomy,profiles,feed,contacts}.py  <- HTTP layer, validation + delegation only, no __init__.py
  schemas/{auth,user,taxonomy,profile,feed,contact}.py  <- pydantic request/response schemas, __init__.py re-exports
  mappers/{auth,user,taxonomy,profile,feed,contact}.py  <- domain entity -> schema (and request -> domain form entity), no __init__.py. feed.py also encodes/decodes the feed cursor and parses the `attr` query parameter: both are HTTP contract
  dependencies/auth.py             <- request-scoped Depends(): get_current_user, and require_username on top of it (409 username_required), no __init__.py
application/
  services/{telegram_init_data,jwt_service,timezone_catalog,profile_content_validator,feed_filter_validator,feed_viewer_resolver}.py  <- reusable cross-use-case services, no __init__.py. feed_filter_validator.py checks Search filters against the taxonomy and the timezone catalog; feed_viewer_resolver.py turns the user into the FeedViewer (active profile, tags without rejected, comparable-vector flag) for all three feed use cases. timezone_catalog.py reads zone.tab/iso3166.tab from tzdata; profile_content_validator.py is the validation shared by profile create and update (form + role attributes + tags via the taxonomy repository)
  use_cases/{auth,taxonomy,profile,embedding,feed,contact}.py     <- orchestration, __init__.py re-exports. feed.py holds GetFeedUseCase (session lookup/build, slicing, hydration), GetFeedCardUseCase and RecordProfileViewUseCase. embedding.py holds EmbedProfileUseCase (the worker's job) and SweepStaleEmbeddingsUseCase. contact.py holds OpenContactUseCase, SendContactNotificationUseCase (the notification worker's job) and SweepStaleNotificationsUseCase
domain/
  entities/{user,auth,taxonomy,profile,profile_embedding,feed,contact}.py    <- dataclasses, __init__.py re-exports. auth.py holds the whole Telegram-auth/JWT flow (init_data payload, decoded token payload, auth result) in one file — a deliberate one-off for this small flow, not a general "always merge" rule; see "Request-scoped auth" below
  exceptions/{user,auth,taxonomy,profile,embedding,feed,contact,notification}.py  <- __init__.py re-exports, same auth.py grouping as entities
  interfaces/{user,taxonomy,taxonomy_cache,profile,profile_embedding,embedding_provider,embedding_queue,unit_of_work,feed,feed_session,profile_view,contact_open,notification_sender,notification_queue}.py  <- repository and port ABCs (feed.py is the read model IFeedRepository; feed_session.py the Redis-backed session cache port), __init__.py re-exports. taxonomy_cache.py is ITaxonomyCacheRepository, typed on domain entities. unit_of_work.py is IUnitOfWork (`commit()` only), see "Transactions" below
  enums/{taxonomy,profile,feed,contact}.py         <- plain StrEnum members shared by entities/models/schemas (RoleFieldType, TagStatus, ProfileStatus, EmbeddingStatus, FeedMode, FeedTier, FeedEndReason, FeedReasonCode, NotificationStatus), __init__.py re-exports
  services/{tag_title_validator,tag_title_normalizer,profile_form_validator,extra_attributes_validator,embedding_input_builder,feed_scorer,feed_ranker,feed_session_fingerprint,contact_notification_text}.py  <- pure domain computation/validation (contact_notification_text.py builds the plain Russian notification message from a `ContactNotification`; feed_scorer.py is the formula, feed_ranker.py the tier cascade, feed_session_fingerprint.py the two hashes), no infra dependency, __init__.py re-exports. The profile validators take their limits (and the country → timezones mapping) in the constructor and return a cleaned copy. tag_title_normalizer.py is plain functions (no config/state to justify a class). See "Domain services vs. application services" in docs/rules.md
  mappers/telegram_user.py            <- domain-to-domain mappings only: `map_telegram_user_payload_to_new_user_entity` (no __init__.py)
infrastructure/
  helpers/{db_helper,redis_helper}.py     <- DB/Redis client setup, flat (no nested db/ subdir). __init__.py re-exports the `db_helper` and `redis_helper` singletons (`from app.infrastructure.helpers import db_helper, redis_helper`)
  models/{base,user,taxonomy,profile,profile_embedding,profile_view,contact_open}.py          <- SQLAlchemy 2.0 models, __init__.py re-exports Base + models. base.py also registers `before_create` DDL events enabling `pg_trgm` and `vector`, since `create_all` doesn't create extensions
  mappers/{user_mapper,taxonomy_mapper,profile_mapper,profile_embedding_mapper,feed_mapper,contact_open_mapper}.py  <- SQLAlchemy model (or query row) <-> domain entity, no __init__.py. Profile models have no relationships, so the profile mapper takes the tags as a separate argument
  repositories/{user,sqlalchemy_taxonomy,sqlalchemy_profile,sqlalchemy_profile_embedding,redis_taxonomy_cache,sqlalchemy_feed,sqlalchemy_profile_view,redis_feed_session,sqlalchemy_contact_open,profile_visibility}.py  <- domain interface implementations (profile_visibility.py is not a repository but the shared SQL conditions `visible_to(viewer)`; sqlalchemy_contact_open.py reads the owner's `username` in the same query as the visibility check, counts the sliding window per user, inserts with `ON CONFLICT DO NOTHING`, and serves the notification worker and the sweep; sqlalchemy_feed.py is the feed read model, built from SQLAlchemy expressions (the cosine uses pgvector's `cosine_distance` on two columns, so the vector never reaches Python and `register_vector` stays out of it); redis_feed_session.py follows the same cache rules as redis_taxonomy_cache.py), __init__.py re-exports. sqlalchemy_profile.py loads the tags of all requested profiles in one query (ordered by tag id) and locks the profile row in `update`. sqlalchemy_taxonomy.py is Postgres-only; redis_taxonomy_cache.py owns Redis keys, serialization, TTL and RedisError handling — see "Taxonomy" above and "Repository responsibility" in docs/rules.md
  unit_of_work.py                         <- SqlAlchemyUnitOfWork: IUnitOfWork over the request's AsyncSession
  providers/{stub_embedding,openai_embedding,stub_notification,telegram_notification}.py  <- INotificationSender implementations (stub logs the text; Telegram calls `sendMessage` with a `web_app` button and raises its own errors `from None` because the bot token is part of the URL) and IEmbeddingProvider implementations (stub: deterministic unit vector; OpenAI: httpx, 429/5xx/timeouts are "unavailable", other failures "rejected"), __init__.py re-exports
  queues/{celery_embedding_queue,celery_notification_queue}.py        <- IEmbeddingJobQueue and INotificationJobQueue over Celery `send_task` in a thread under a timeout; never raises, __init__.py re-exports
worker/
  celery_app.py                    <- the Celery app (one for both queues): default queue, acks_late, prefetch 1, beat schedule of both sweeps (the notification one is routed to its queue through `options`), task names
  tasks.py                         <- `embed_profile`, `sweep_stale_embeddings`, `send_contact_notification` and `sweep_stale_notifications`: sync wrappers over `asyncio.run` with a fresh NullPool engine per task
core/
  config.py                        <- Settings (pydantic-settings)
  composition/{container,di}.py    <- composition root, no __init__.py
```

Outside `app/`:

```
tests/
  conftest.py                      <- only loads .env.test BEFORE any app.* import (settings/db_helper are import-time singletons); nothing here touches a database, so unit tests need neither Postgres nor Redis
  unit/                            <- no DB/Redis: init_data validator (incl. an openssl-computed signature vector), JWT service, auth use cases, get_current_user, mappers, tag normalizer/validator, taxonomy use cases (with fakes), Settings (prod fail-fast)
  integration/                     <- real Postgres/Redis via the ASGI app. Its own conftest.py holds the guard (refuses a DB not ending in `_test_db`, Redis DB 0, or a broker in DB 0 or 2), the test DB + tables, per-test truncate, `session`, `redis_client` (fresh client per test, flushed first — the module-level `redis_helper.client` is bound to one event loop), `broker_client` (reads the test Celery broker, flushed before and after), `taxonomy_client` (app client using that Redis client), `taxonomy_data`. Tests: auth endpoint and flow (ban/`token_version` over real HTTP), protected route, user repository, unit of work, taxonomy repository, Redis cache repository (corrupt values, hung Redis), taxonomy endpoints (incl. Redis-down fallback and input bounds), the dev seed script, physical schema (indexes/constraints, the `vector` extension and the HNSW index being used), the embeddings repository, the Celery queue and the worker tasks against real Postgres and the broker, embeddings through the profile endpoints (incl. the broker being down)
  fixtures/                        <- entity factories, init_data builders, direct DB helpers, fake user/taxonomy/profile/embedding repositories, fake embedding provider and queue (the fake user repository copies on read and writes only the columns the real one writes) and `FakeUnitOfWork`, ORM taxonomy seed helpers (`taxonomy_data.py`), feed fakes (`fake_feed.py`) and DB helpers for the feed tests (`feed_data.py`: users, profiles and vectors of a known cosine), `auth.py` (user + Bearer header)
scripts/                           <- dev-only: dev_gen_init_data.py, dev_seed_taxonomy.py, dev_seed_profiles.py; data/seed_profiles/*.json is the handwritten profile fixture
alembic.ini, migrations/           <- Alembic config, async env.py (URL from settings, Base.metadata) and versions/. Infrastructure artifact, deliberately outside app/
.github/workflows/tests.yml        <- CI: pytest against pgvector Postgres + Redis
.env.template / .env.test          <- `.env.template` is only a template to copy to `.env` (never read by the app); `.env.test` holds test overrides (found_core_test_db, Redis on localhost DB 1). `.dockerignore` keeps `.env`, `.git`, `.venv`, tests out of the image
requirements.txt / requirements-dev.txt, pyproject.toml (pytest config)
```

`__init__.py` rule: added only to packages that get imported from often across layers, and it re-exports via `__all__` so the import stays short (`from app.domain.entities import UserEntity`). Packages nobody imports directly from outside (mappers, routers, services, composition, dependencies) skip it — import the full module path instead.

Naming: a module is named after what it actually implements, not the nearest domain entity. `routers/auth.py`, `schemas/auth.py`, `mappers/auth.py` and `use_cases/auth.py` all used to be named `user.py` — but their content is the Telegram-auth flow (init_data validation, token issuing/verification), not generic User CRUD, so keeping them as `user.py` would have collided with real user-only concerns as soon as any got added (e.g. `UserPublicSchema`/`map_user_entity_to_user_public_schema`, which now correctly live in the `user.py` sibling of each split pair).

### Composition root (`core/composition/`)

`Container` is a plain class with **lazy factory methods**, not an eagerly-built `@dataclass`/`__post_init__` — cheaper (only what a given request needs gets constructed) and it scales to use cases that need a runtime parameter the constructor can't know in advance:

```python
class Container:
    def __init__(self, session: AsyncSession, redis_client: Redis):
        self.session = session
        self.redis_client = redis_client

    # ---------- services ----------
    def telegram_init_data_service(self) -> TelegramInitDataValidator: ...  # AuthConfig bot token + init_data TTL
    def jwt_service(self) -> JWTService: ...

    # ---------- unit of work ----------
    def unit_of_work(self) -> SqlAlchemyUnitOfWork: ...                       # same session as the repositories

    # ---------- repositories ----------
    def user_repo(self) -> SqlAlchemyUserRepository: ...
    def taxonomy_repo(self) -> SqlAlchemyTaxonomyRepository: ...              # session + TaxonomyConfig.suggest_limit, no Redis
    def taxonomy_cache_repo(self) -> RedisTaxonomyCacheRepository: ...        # redis_client + TaxonomyConfig.cache_ttl_seconds

    def profile_embedding_repo(self) -> SqlAlchemyProfileEmbeddingRepository: ...

    # ---------- embeddings ----------
    def embedding_provider(self) -> IEmbeddingProvider: ...                   # stub or OpenAI from EmbeddingConfig
    def embedding_queue(self) -> CeleryEmbeddingJobQueue: ...                 # the Celery app + queue name + enqueue timeout

    # ---------- notifications ----------
    def notification_sender(self) -> INotificationSender: ...                 # stub, or Telegram (BotConfig.token + NotificationConfig)
    def notification_queue(self) -> CeleryNotificationJobQueue: ...           # the same Celery app, the notifications queue

    # ---------- domain services ----------
    def tag_title_validator(self) -> TagTitleValidator: ...  # built from TaxonomyConfig values

    # ---------- use cases ----------
    def auth_use_case(self) -> AuthenticateTelegramUserUseCase: ...
    def verify_access_token_use_case(self) -> VerifyAccessTokenUseCase: ...  # user_repo() + jwt_service(), used by get_current_user
    def get_categories_use_case(self) -> GetCategoriesUseCase: ...  # taxonomy_repo() + taxonomy_cache_repo()
    # ... plus get_roles_by_category / get_role_fields_by_role (same two repos), suggest_tags and create_custom_tag use cases
    # ... and for profiles: profile_repo(), timezone_catalog(), profile_form_validator() / extra_attributes_validator() (limits and the allowed-country → zones mapping come from ProfileConfig), profile_content_validator(), and the 9 profile use cases (auth_use_case() also gets profile_repo(); create and update also get profile_embedding_repo() and embedding_queue()), plus embed_profile_use_case() and sweep_stale_embeddings_use_case() for the worker, and for the feed: feed_repo(), profile_view_repo(), feed_session_repo(), current_embedding_model() (the model name without building a provider, so `openai` without a key cannot take the feed down in dev), feed_scorer(), feed_ranker(), feed_filter_validator(), feed_viewer_resolver() and the three feed use cases, and for contacts: contact_open_repo(), open_contact_use_case() (ContactConfig limit and window, the user repository for the row lock, notification_queue()), send_contact_notification_use_case() and sweep_stale_notifications_use_case() for the worker
```

`redis_client` was added to the constructor once the first Redis-backed repository (`taxonomy_cache_repo`) showed up — before that, `Container` only ever needed a session. `taxonomy_repo` and `taxonomy_cache_repo` are separate factories on purpose (see "Repository responsibility" in `docs/rules.md`): a use case that needs both caching and Postgres access gets both and orchestrates them itself, e.g. `GetCategoriesUseCase(taxonomy_repository=self.taxonomy_repo(), taxonomy_cache_repository=self.taxonomy_cache_repo())`. Settings such as the TTL are wired into the repository here, never passed into the use case.

`di.py` wires it into FastAPI with the classic `Depends()`-as-default-value style (not `Annotated[...]`):

```python
async def get_container(session: AsyncSession = Depends(db_helper.session_getter)) -> Container:
    return Container(session=session, redis_client=redis_helper.client)
```

Routers call it as `container.auth_use_case().execute(...)` — never construct a use case or repository directly.

### Transactions

Repositories never commit: they `flush`/`execute` and return. The use case that owns a scenario commits once at the end via `IUnitOfWork.commit()` (`SqlAlchemyUnitOfWork` wraps the same `AsyncSession` the repositories use; `Container.unit_of_work()` builds it). So `CreateCustomTagUseCase` creates the tag and its scope in **one** transaction — a failure between the two leaves no orphan tag. On any exception nothing is committed and closing the request-scoped session (`db_helper.session_getter`) rolls back; `IUnitOfWork` has no `rollback()` on purpose until a long-lived session needs one. The stage 5 Celery worker does not: it opens a new session per task, and closing it rolls back the same way. (Exception to "commit once": when the embedding worker finds a profile without an embeddings row it restores the row and commits at once, so a crash right after cannot leave the profile invisible to the sweep.) Read-only use cases never commit. Unit tests substitute `FakeUnitOfWork` (counts commits); `tests/integration/test_unit_of_work.py` checks the real behavior against Postgres through a second session.

### Auth input hardening

Everything that reaches the auth flow is treated as hostile until its *shape* is checked, so malformed input ends as `400`/`401`, never `500`:

- **`initData`** (`TelegramInitDataValidator`): the signature is compared as bytes (non-ASCII `hash` is just a mismatch); a parameter sent twice (including `hash`) is malformed; `auth_date` must be ASCII digits (≤15) and not further in the future than `AuthConfig.init_data_max_future_skew_seconds` (60 s) — an old one is `expired`; `user` must be a JSON object with an integer `id` in `1..MAX_INT64` (`domain/constants.py`, not a bool/string), a non-empty `first_name`, optional string fields, lengths matching the `users` columns, and no NUL characters (Postgres rejects them). The request body caps `init_data` at 8192 characters.
- **JWT** (`JWTService.decode_access_token`): `exp`, `iat`, `sub`, `telegram_id`, `token_version` are required (a signed token without `exp` would never expire); their types are checked and a bad `sub` is `TokenInvalidError`, not a raw `ValueError`.
- **First login race**: `IUserRepository.create` is `INSERT ... ON CONFLICT (telegram_id) DO NOTHING RETURNING` and returns `None` when a parallel request created the user first; `AuthenticateTelegramUserUseCase` then re-reads the user and continues as a returning login (`is_new_user=false`). Of N simultaneous first logins exactly one reports `is_new_user=true`.

### Configuration and secrets

Settings come from the process environment and `.env` only (`.env.template` is a copy-me template, not a fallback; the app container gets `.env` through `env_file` in `docker-compose.yaml`). `APP_CONFIG__RUN__ENV` is `dev` (default), `test` or `prod`. With `prod`, `Settings` refuses to start (pydantic `ValidationError` listing every problem) if `auth.secret_key` is a known placeholder or shorter than 32 characters, `bot.token` is a placeholder (`""`, `key`, `123`), or `run.reload` is true — so a forgotten secret fails the first deploy instead of silently running with public keys. The stage 5 and 7 settings are checked the same way: `embedding.provider` must not be `stub` (and `openai` needs a key), `notification.provider` must not be `stub`, `notification.mini_app_url` is required and must be `https` (Telegram refuses other `web_app` buttons), and `worker.broker_url` must not point at the cache's Redis DB. `WorkerConfig`, `ContactConfig` and `NotificationConfig` are plain `BaseModel`s like the other nested configs. `BotConfig.token` has no default. `RunConfig` is a plain `BaseModel` (not `BaseSettings`) so its default instance never reads unprefixed variables such as a shell's `ENV`. The Docker image runs as a non-root user and `.env` is excluded by `.dockerignore`.

### Migrations

Alembic, in `migrations/` at the repo root. `migrations/env.py` is the async template: it takes the URL from `settings.db.url` (never from `alembic.ini`, so credentials stay in the environment) and uses `Base.metadata` as the target, so **every new model must be exported from `app/infrastructure/models/__init__.py`**, otherwise autogenerate won't see it. `compare_type` and `compare_server_default` are on. Revision files are named `YYYY_MM_DD_HHMM-<rev>_<slug>.py`.

Migrations are applied **manually** (`docker compose exec found_core_mini_app alembic upgrade head`); the app does not touch the schema on startup (a DB without `upgrade head` fails with `relation does not exist`). Running them automatically on deploy is stage 10. After a `git pull` that brings new revisions, run `upgrade head`.

Creating a revision: `docker compose exec -u "$(id -u):$(id -g)" found_core_mini_app alembic revision --autogenerate -m "..."` — the `-u` is needed because the container user can't write into the bind-mounted `migrations/versions/`. Autogenerate output is a draft and is always reviewed by hand: it does not emit extensions (`CREATE EXTENSION`, e.g. `pg_trgm` in the initial revision, `vector` in stage 5), and `downgrade()` must drop PG enum types explicitly because `drop_table` leaves them behind.

The `before_create` DDL event in `models/base.py` that enables `pg_trgm` fires only for `create_all`, so migrations create the extension explicitly.

**Tests don't use migrations**: the integration `conftest.py` still builds the test database with `create_all`, and there is no test comparing migrations with the models. A model change without a revision is therefore not caught by CI — check with `alembic check` ("No new upgrade operations detected") and review the revision file.

Seed data (taxonomy) is not part of migrations — `scripts/dev_seed_taxonomy.py` fills it after `upgrade head`. How reference data gets into production is a stage 10 question.

### Request-scoped auth (`api/v1/dependencies/auth.py`)

Any future protected endpoint declares `current_user: UserEntity = Depends(get_current_user)` — the same explicit per-route `Depends()` style as `Depends(get_container)`, never a global middleware (a second, parallel DI mechanism is exactly what `docs/rules.md` forbids).

```python
bearer_scheme = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    container: Container = Depends(get_container),
) -> UserEntity:
    try:
        return await container.verify_access_token_use_case().execute(credentials.credentials)
    except (TokenInvalidError, TokenExpiredError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except UserBannedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.ban_reason or str(exc)) from exc
```

It lives in `api/v1/dependencies/`, not `core/composition/di.py`: `di.py` is pure composition (build a `Container` from a session, no HTTP awareness), while this dependency does HTTP-layer work — parses the `Authorization: Bearer` header (`HTTPBearer`, not `OAuth2PasswordBearer`, since there's no OAuth2 password flow here) and translates domain exceptions into status codes, exactly like a router's own `try/except` does.

`VerifyAccessTokenUseCase` (`application/use_cases/auth.py`) does the actual check, and re-reads the user from Postgres by id on **every call** rather than trusting the token payload beyond `sub`/`token_version`:

```python
async def execute(self, token: str) -> UserEntity:
    payload = self._jwt_service.decode_access_token(token)
    user = await self._user_repository.get_by_id(payload.user_id)
    if user is None or user.token_version != payload.token_version:
        raise TokenInvalidError()
    if user.is_banned:
        raise UserBannedError(user.ban_reason)
    return user
```

Two separate checks, catching two separate situations:

- **`token_version` mismatch** → `401`. Catches a token issued *before* a ban/logout bump — the classic `token_version` revocation already described under "Data model".
- **live `is_banned`** → `403` with `ban_reason` in the body. Catches a token issued *after* the ban (e.g. the user re-authenticated with a fresh `initData` post-ban and got a new, version-matching token) — `token_version` alone would let that request through, since the version matches the freshly-issued token.

`is_banned`/`ban_reason` are deliberately **not** put in the JWT payload: a signed JWT can't change after issuing, so baking ban state into it would only take effect on the *next* login, not instantly. Reading the live DB row on every request is what makes a ban instant. This costs nothing extra — it's the same single indexed lookup by `users.id` that the `token_version` check already requires.

## API (Auth, Taxonomy, Profiles and Feed implemented, rest planned)

- **Auth** (implemented): `POST /api/v1/auth/telegram { init_data }` → local HMAC-SHA256 signature check (data-check-string per Telegram's algorithm, secret = `HMAC_SHA256(key=b"WebAppData", msg=bot_token)`, `initData` rejected if `auth_date` older than **300s**) → upsert user by `telegram_id` → JWT via **PyJWT**, `HS256`, **1440 min (24h)**, no refresh token (client just re-calls this endpoint with fresh `initData` on 401 elsewhere). Bot token is a dev placeholder (`APP_CONFIG__BOT__TOKEN=123`) until a real bot exists. Response: `access_token`, `token_type`, `is_new_user`, `user` (id, telegram_id, first_name, last_name, username, photo_url, is_admin, `is_banned`, `ban_reason`), `profiles` (the user's profiles ordered by `created_at, id`, each with `is_active`) and `user.active_profile_id`. A **banned user still authenticates successfully (200)** — `is_banned`/`ban_reason` are in the response so the frontend can render a ban screen instead of a bare error; this endpoint never blocks on ban or on missing username (see "User flow" for where username actually gets enforced). JWT verification on other (protected) endpoints is implemented as the reusable `Depends(get_current_user)` dependency (see "Request-scoped auth" above) — first actually consumed by the Taxonomy endpoints below.
- **Taxonomy** (implemented, all behind `Depends(get_current_user)` — no anonymous reads): `GET /taxonomy/categories`, `GET /taxonomy/categories/{id}/roles` (404 if the category doesn't exist), `GET /taxonomy/roles/{id}/fields` (404 if the role doesn't exist), `GET /taxonomy/tags/suggest?q=&category_id=&role_id=` (case-insensitive substring match via the `pg_trgm` GIN index on `tags.title`, query escaped so `%`/`_`/`\` match literally; returns approved tags in scope plus the caller's own pending ones; not cached — see "Taxonomy" below), `POST /taxonomy/tags/custom { title, category_id, role_id? }` → technical validation only (length 1–64 — single-letter tags like `C` and `R` are valid —, allowed characters incl. `+ # . /` for IT tags; no stop-word check, content is left to moderation — see "Taxonomy" above), dedupe by `normalized_title` (NFKC + casefold + whitespace collapse, see "Taxonomy"), new tags get status `pending`, usable by their author immediately (the author's own pending tags show up in their own `suggest` results). If a tag with the same normalized title already exists, it is returned (canonical `title` included) instead of creating a new one — also across users and for pending tags; a `rejected` match returns `409`. Re-submitting is safe (mirrors the double-submit-safe pattern used for `profiles`).
- **Profiles** (implemented, all behind `Depends(get_current_user)`; rules in "Profiles" above): `GET /profiles/form-config`, `POST /profiles` (201; `400` invalid content, `404` unknown category/role, `409` duplicate with the existing profile in the body or a rejected tag), `GET /profiles/me`, `GET /profiles/{id}` (`404` for a missing or foreign profile), `PUT /profiles/{id}` (full replace), `POST /profiles/{id}/activate`, `POST /profiles/{id}/pause|resume`, `DELETE /profiles/{id}` (204). `/form-config` and `/me` are declared before `/{id}`. Request bodies use `extra="forbid"`; `tag_ids` ≤ 50, `bio`/`goals_description` ≤ 4000, `extra_attributes` ≤ 32 keys (key ≤ 64, value ≤ 128), ids `1..MAX_INT64`. Planned: `DELETE /users/me`.
- **Feed** (implemented, stage 6; all behind `require_username`, see "Feed algorithm" for the rules): `GET /feed?mode=discovery|search&role_id=&attr=key:value&tag_ids=&country_code=&same_timezone=&cursor=&limit=` (`attr` and `tag_ids` repeat; at most 10 and 15; `limit` 1..`feed.page_size_max`, default 20) → `{items, next_cursor, has_more, end_reason}` with items `{profile, match_type, match_score, is_viewed, reasons}`; `GET /feed/cards/{profile_id}`; `POST /feed/views {profile_id}` → `204`.
- **Contacts** (implemented, stage 7; behind `require_username`, rules in "Contacts and notifications"): `POST /profiles/{profile_id}/contact` → `{username, is_new_open, remaining_today}`; `201` for a first-time open, `200` for a free repeat; `404` for an own, missing or invisible profile; `409 username_required` / `409 no_active_profile`; `429 {"detail": {"code": "contact_limit_reached", "message", "limit", "resets_at"}}`. The router is `routers/contacts.py` (named after the flow) and mounts under `/profiles`.
- **Reports** (planned): `POST /reports`.
- **Admin**: user/profile/report lists, tag moderation queue, ban/unban, hide profile, approve/reject/merge tag, inspect why a specific recommendation was made. Gated on `is_admin` of the user row that `get_current_user` re-reads from Postgres on every request — never on the `is_admin` claim in the JWT (a token can't change after issuing, so a demoted admin would keep access until it expires). The token deliberately carries no admin claim (an older token that still has one is accepted and the claim ignored); clients learn `is_admin` from `user.is_admin` in the `/auth/telegram` response.

## Embeddings — must work without an OpenAI key

Profile text (category, role, tags, bio, goals) is hashed; if the hash is unchanged, the embedding call is not repeated. Embeddings sit behind an `EmbeddingProvider` interface with two implementations:

- `OpenAIProvider` — `text-embedding-3-small`, 1536 dims, enabled by an API key.
- `StubProvider` — deterministic pseudo-random vector derived from the text hash; used while no OpenAI key is configured.

Switching providers is one environment variable, no code change — this lets the whole feed (including the semantic scoring term) be developed and tested before an OpenAI key exists. Provider unavailability never blocks the product: a profile is still created and ranked by tags/role, the semantic term is just temporarily skipped.

**Stage 5 as built** (the plan with every decision, its reason and the rejected alternatives is [`docs/plans/stage-5-embeddings.md`](docs/plans/stage-5-embeddings.md)):

- **Storage.** The vector, `input_hash` (`sha256(model + ":" + text)`), `model` and `status` live in `profile_embeddings` (see "Data model"), not in `profiles`; `ProfileEntity` and the profile schemas do not change. Profiles in any status (active, paused, hidden) are embedded.
- **Flow.** Create/update commits the profile and marks its embeddings row `pending` in the same transaction, then — after the commit — enqueues a Celery task. Enqueueing never raises (a Redis outage only delays the vector) and runs in a thread under a timeout because `send_task` is synchronous. The worker reads the profile, builds the text, compares the hash and calls the provider only when it changed.
- **Race guard.** Every worker write is conditional on `source_updated_at` still being the value it read, so a vector computed from stale text can never overwrite the `pending` mark left by a concurrent edit. A lost race raises `ProfileChangedDuringEmbeddingError` and the task retries.
- **Sweep.** Celery beat (exactly one instance) runs every 10 minutes and re-enqueues rows that are `pending` (older than 5 min), `failed` (older than 1 h) or `ready` with another `model`. It claims them with one `UPDATE ... FOR UPDATE SKIP LOCKED ... RETURNING`. Duplicate tasks are harmless: the hash short-circuits them.
- **Celery.** Tasks are synchronous wrappers that run the async core with `asyncio.run` and a fresh `NullPool` engine per task (asyncpg pools are bound to their event loop). `acks_late`, prefetch 1. Containers: `embedding_worker` and `embedding_beat`, the same image as the API.
- **Config.** `EmbeddingConfig` (plain `BaseModel`). The required broker URL moved to `WorkerConfig` in stage 7 (`APP_CONFIG__WORKER__BROKER_URL`, shared by every queue) and must not point at the cache's Redis DB. `Settings` refuses to start in prod with `provider=stub`, with `openai` and no key, or with the broker in the cache DB.
- **For stage 6.** Use a vector only when `embedding IS NOT NULL` and `model` is the current one: stub and OpenAI vectors are not comparable.
- **Redis DBs.** 0 dev cache, 1 test cache, 2 dev broker, 3 test broker. The broker has no result backend (`task_ignore_result`), so nothing reads task results.
- **Text.** `build_embedding_input` joins `Category:`, `Role:`, `Tags:` (sorted), `Bio:` and `Goals:` lines. `extra_attributes` are not in the text (see the `is_embedded` TODO below).
- **Local run.** Every developer adds `APP_CONFIG__WORKER__BROKER_URL=redis://redis:6379/2` (renamed from `APP_CONFIG__EMBEDDING__BROKER_URL` in stage 7: the old name no longer works and the app refuses to start without the new one) and `APP_CONFIG__EMBEDDING__PROVIDER=stub` to `.env`, then `docker compose up -d --build` starts `embedding_worker`, `notification_worker` and `embedding_beat`. The queue name in the worker command in `docker-compose.yaml` (`-Q embeddings`) must match `EmbeddingConfig.queue_name`.
- **Rollback.** Stop `embedding_worker` and `embedding_beat`, then `alembic downgrade -1` (drops `profile_embeddings` and its enum type; the `vector` extension stays, other tables may use it later). Create and update keep working without the worker: the row stays `pending`, and a broker outage only produces a warning in the log.
- **Privacy.** Profile text (`bio`, `goals`) is sent to OpenAI; the terms of use must say so (add it in the `terms_accepted_at` follow-up).

## Contacts and notifications (implemented in stage 7)

The plan with every decision, its reason, the rejected alternatives and the list of deviations is [`docs/plans/stage-7-contacts.md`](docs/plans/stage-7-contacts.md).

**Opening a contact.** `POST /profiles/{profile_id}/contact` (behind `require_username`) opens the contact of any visible profile of any category under the viewer's **active profile**. Visibility is the feed's rule (`visible_to(viewer)`): active profile, owner not banned, not deleted, with a username, and not one of the viewer's own profiles. An opened pair is `(my active profile, their profile)`:
- the **first open** writes a `contact_opens` row, spends one of the daily limit, queues a notification and answers `201`;
- a **repeat** answers `200` with the same `username`, writes nothing, sends nothing and does not check the limit, so it works with an exhausted limit and lets the frontend retry safely;
- the response is `{username, is_new_open, remaining_today}`; the owner's `last_name` is not returned (the full name is visible in Telegram);
- order of checks: `409 username_required` → `409 no_active_profile` → `404` (own, missing, paused, banned, deleted or username-less profile — also for a pair opened earlier that became invisible) → repeat → `429` → `201`;
- opening does not write `profile_views` (a view is recorded only by `POST /feed/views`).

**Daily limit.** A sliding 24 hours per **user** (all their profiles), `ContactConfig.daily_limit` = 20 and `window_hours` = 24, counted from `contact_opens` joined to the opener's profile. Repeats do not count. When it is exhausted the answer is `429` with `{"detail": {"code": "contact_limit_reached", "message", "limit", "resets_at"}}`, where `resets_at` is the oldest open in the window plus the window, so the frontend can say "the next contact at 14:32". The window does not depend on time zones.

**Race at the limit boundary.** Two parallel opens of different profiles at the 19th contact would both count 19. `OpenContactUseCase` takes `SELECT ... FOR UPDATE` on the opener's `users` row (`IUserRepository.lock_for_update`, selecting only the id so nothing stale lands in the session) before counting; one person's opens are serialized until the commit. After the lock the pair is checked **again**: a double click at the last free slot would otherwise count the first request's row and answer `429` instead of the free repeat (a deviation from the plan, section 9). `INSERT ... ON CONFLICT DO NOTHING RETURNING` stays as the last guard and its `None` is answered as a repeat. Both races have `asyncio.gather` tests against real Postgres.

**Card flag.** `is_contact_opened` on feed cards and `GET /feed/cards/{id}` (see "Feed algorithm"): the frontend shows "Write" instead of "Open contact". The `username` itself is never on a card; the free repeat `POST` gives it.

**The notification.**
- The `contact_opens` row is written as `pending` in the open's transaction; after the commit a Celery task is queued on the `notifications` queue (`CeleryNotificationJobQueue`: a thread under a timeout, never raises, only logs). With the broker down the open is still `201` and the row stays `pending` for the sweep.
- Text (Russian only, plain text without `parse_mode`, so names need no escaping and Telegram links the `@username` itself; "открыл(а)" because the gender is unknown):
  ```
  Анна Иванова открыл(а) ваш контакт

  Анкета: Tech & Product · Founder
  Ваша анкета: Tech & Product · Engineering
  Написать: @anna_founder
  ```
  built by the pure domain service `build_contact_notification_text`. The button is an inline `web_app` button to `{mini_app_url}?profile_id={opener profile id}`; the chat id is the owner's `users.telegram_id`. Every open is its own message, no digest.
- **At send time** the worker (`SendContactNotificationUseCase`) reads the current facts and decides: either side banned or deleted, or the opener has no username any more → `skipped`; the open is older than `stale_after_seconds` (24 h) → `skipped` (late news after an outage would look like spam). A paused profile does **not** stop the message. A deleted profile has no row any more (cascade) and the job just ends.
- **Outcomes.** Telegram `200` → `sent`. `403` (the user blocked the bot or never pressed `/start`) and any other `4xx`, including `400 chat not found` → `failed` at once, no retries, no flag on `users`. `429` (with `retry_after`), `5xx`, a timeout and an answer that is not JSON → retry with the backoff `retry_base_delay_seconds * 2^n` or Telegram's `retry_after` if that is longer, up to `task_max_retries` (5); after the last attempt → `failed`, final. The sweep never touches `failed`. The result is written with `UPDATE ... WHERE notification_status = 'pending'`, so an outcome is never overwritten.
- **Sweep.** Celery beat every `sweep_interval_seconds` (60) claims `pending` rows whose `notification_updated_at` is older than `sweep_pending_after_seconds` (120) with `FOR UPDATE SKIP LOCKED`, bumps the timestamp in the same statement, takes at most `sweep_batch_size` (100), commits and only then enqueues. The 24 h staleness is measured from `created_at`, not from the bumped timestamp. Delivery is at-least-once (see "Risks").
- **Providers** (`NotificationConfig.provider`): `stub` (default outside prod) writes the text to the log, so `docker compose logs notification_worker` shows it; `telegram` calls `sendMessage` through `httpx` with `BotConfig.token`. The bot token is part of the URL, so the provider raises its own errors `from None`. Prod refuses `stub` and requires an https `mini_app_url`.

**Infrastructure.** One `celery_app` and two queues. `broker_url` lives in `WorkerConfig` (`APP_CONFIG__WORKER__BROKER_URL`). `notification_worker` is its own container (`-Q notifications`, the name must match `NotificationConfig.queue_name`), so a slow OpenAI never delays a notification; the one `embedding_beat` schedules both sweeps, and the notification sweep entry carries `options: {"queue": ...}`, otherwise beat would send it to the default `embeddings` queue where nobody of the notification worker listens. Without the worker the endpoint keeps working: rows stay `pending` and are skipped after 24 hours.

**Local run.** In `.env` replace `APP_CONFIG__EMBEDDING__BROKER_URL` with `APP_CONFIG__WORKER__BROKER_URL`, then `docker compose up -d --build`; seed with `python -m scripts.dev_seed_taxonomy` and `python -m scripts.dev_seed_profiles`; act as a seed user with `scripts/dev_gen_init_data.py`. Every seed username starts with `seed_`, so a stage 7 open can never point at a real Telegram account.

**Follow-up (not in stage 7).** Histories (`GET /contacts/opened`, `GET /contacts/received`) — the data is in `contact_opens`; a digest of notifications if owners of popular profiles complain; per-minute rate limiting together with stage 8; contact opens as a feedback signal for the feed.

## Moderation, security, analytics

- Reports, bans (banning bumps `token_version`, instantly invalidating already-issued JWTs — the read side of this, `get_current_user`, is already implemented, see "Request-scoped auth"; the admin ban action itself that sets `is_banned`/bumps `token_version` is stage 8 work), admin profile hiding, custom-tag moderation queue (approve/reject/merge).
- Rate limiting at two levels: nginx by IP, application-level per-user in Redis (contact opens, tag creation, reports, autocomplete). The daily limit of contact opens (20 per 24 h, from `contact_opens`) is a product rule that already exists since stage 7; this is the per-minute abuse limiting on top of it (stage 8).
- Custom tags: technical validation at creation (length, allowed characters, implemented in stage 3); content checks (spam, forbidden words) via the moderation queue, not a stop-word list (dropped in stage 3 review); daily creation limit via per-user rate limiting (stage 8).
- Account deletion physically removes profiles, embeddings, and views.
- Analytics events go to PostHog in the background, never blocking the request: signup, onboarding step timings/abandonment, profile create/edit, profile switch, feed open, scroll depth, empty screen, card open (with tier + score), contact open, filters applied, tag created, report, account deletion. AI-cost accounting is out of scope for this iteration.

## Development stages

Each stage is its own `feat/...` branch and its own PR for review.

| # | Stage | Content |
|---|-------|---------|
| 1 | skeleton | ✅ Project scaffold, docker-compose (Postgres+pgvector, Redis), `.env.template`. CI: GitHub Actions runs pytest (`.github/workflows/tests.yml`); lint/type checks are not set up yet. |
| 2 | auth-telegram | ✅ `initData` validation, `users` table, `POST /auth/telegram` issuing JWT. ✅ Auth *dependency* (`get_current_user`) to verify JWT — first consumed by the stage 3 taxonomy endpoints. |
| 3 | taxonomy | ✅ Reference tables (`categories`, `roles`, `role_fields`, `tags`, `tag_scopes`) + idempotent seed, all 5 endpoints, Redis cache-aside (categories/roles/role fields, 6h TTL, falls back to Postgres if Redis is down). |
| 4 | profiles | ✅ Profile CRUD, multi-profile, active-profile switching, pause/resume, form-field validation, `form-config`, `profiles` in the auth response, taxonomy seed with 8–10 tags per role |
| 5 | embeddings | ✅ `profile_embeddings` table with HNSW, stub and OpenAI providers, Celery worker + beat with a sweep, graceful degradation. Plan: `docs/plans/stage-5-embeddings.md` |
| 6 | feed | ✅ Scoring, discovery + search tier cascade, Redis feed sessions, card, `profile_views`, `require_username`, ~100-profile seed. Plan: `docs/plans/stage-6-feed.md` |
| 7 | contacts-notifications | ✅ `contact_opens`, `POST /profiles/{id}/contact`, daily limit, `is_contact_opened`, bot notifications with retries and a sweep, `notification_worker`. Plan: `docs/plans/stage-7-contacts.md` |
| 8 | moderation | **Next.** Reports, bans, profile hiding, tag moderation, rate limiting |
| 9 | analytics | PostHog events |
| 10 | deploy | nginx, prod config, migrations on deploy, README |

Stage 6 depends on stages 3–5; stage 7 depends on stage 6. All other stages can proceed in parallel.

## Acceptance criteria

- Tests run against a real Postgres with `pgvector` — feed logic lives in SQL and can't be verified with mocks.
- Unit tests on the scoring formula: weights, `tag_overlap`, `activity_factor`, degradation with no embedding.
- Integration tests: cascade correctly falls through to tier 2/3 under narrow filters; empty screen shown instead of random profiles; page order is stable across repeated requests.
- `initData` verification: valid / malformed / expired (all implemented; covered by automated tests in `tests/unit/test_telegram_init_data_validator.py` and `tests/integration/test_auth_telegram_endpoint.py`; `scripts/dev_gen_init_data.py` generates payloads for manual checks).
- Access-token verification (`get_current_user`): valid / expired / malformed / revoked (`token_version` mismatch) / banned (live `is_banned` check, `403` with `ban_reason`) — all implemented in `VerifyAccessTokenUseCase`; covered by `tests/unit/test_auth_use_cases.py`, `tests/unit/test_get_current_user_dependency.py` and `tests/integration/test_protected_route.py`. The taxonomy endpoints are the first protected routes to actually exercise this dependency over HTTP.
- Auth endpoint does not reject on missing username or on ban (see "API" — Auth). Feed-side: a user with no username can't view the feed and is excluded from other users' feeds (see "User flow"); a banned user never appears in results.
- A repeat `POST /profiles` with the same role returns 409.
- Contacts: the first open of a pair is `201` and spends one of the daily limit, a repeat is `200` and free (also at an exhausted limit); an own, missing, paused, banned or username-less profile is `404`; two parallel opens at the limit boundary make exactly `limit` rows; a double click makes one row; the open still succeeds, with a `pending` row, while the broker is down — covered by `tests/unit/test_contact_use_cases.py` and `tests/integration/test_contact_endpoints.py`.
- Notifications: sent / failed / skipped outcomes, the 24 h staleness and re-checks at send time, retries with `retry_after`, the sweep claiming only stale `pending` rows — covered by `tests/unit/test_notification_use_cases.py`, `tests/unit/test_telegram_notification_provider.py`, `tests/unit/test_notification_tasks.py` and `tests/integration/test_notification_task.py`.
- Seed script for ~100 test profiles across both categories (`scripts/dev_seed_profiles.py`, done in stage 6) — the feed can't be evaluated visually without this.
- Taxonomy: `categories/{id}/roles` and `roles/{id}/fields` 404 on an unknown id; `tags/custom` rejects an empty (after whitespace cleanup)/too-long/invalid-character title (`400`); re-submitting the same title for the same category/role doesn't create a duplicate tag or scope row; `tags/suggest` returns a caller's own pending tags but not other users' pending tags — covered by `tests/unit/test_taxonomy_use_cases.py`, `tests/integration/test_taxonomy_repository.py` and `tests/integration/test_taxonomy_endpoints.py`.

## Risks and open questions

- **Cold start** — the feed is meaningless until the DB has 50–100 profiles in the relevant category; sourcing the first closed cohort by segment matters more than algorithm quality at launch.
- **200-candidate session cap** — profiles ranked below #200 in their category are never shown to a viewer in that session. A deliberate tradeoff for cheap pagination, not an accidental gap; not expected to matter at first-cohort scale, but raising the cap or adding an exploration slot for tail profiles will be needed as the audience grows.
- **Match-percentage calibration** — formula weights and cutoff thresholds will need tuning once real usage data exists.
- **Embedding stub** — validates feed mechanics but not semantic search quality; that's only visible once a real OpenAI key is wired in.
- **Embedding quality audit (2026-11-02, OpenAI vectors on the 100-profile seed; open, nothing changed yet)** — the feed works end to end with real vectors and the ranking is semantically sensible, but three things need a decision. Full numbers and the options are in `docs/plans/stage-6-feed.md`, section 12, item 24.
  - *Double counting.* The embedding text holds `Category`, `Role` and `Tags`, which the formula already scores separately (`tag_overlap` 0.4, `role_affinity` 0.2). Cosine correlates with `tag_overlap` at r=0.77 inside a category; a `Bio + Goals`-only text gives r=0.30. The role label also gaps same-role from other-role pairs by 0.21 vs 0.08.
  - *Thresholds do not filter.* `bio_tier_min_cos_sim` 0.35 passes 97% of cross-category pairs (median cosine 0.48, floor ~0.3); `similar_bio_reason_min_cos_sim` 0.5 fires for 83% of same-category and 42% of cross-category pairs.
  - *Low `match_score` in `RECOMMENDED_BY_BIO` is by construction.* Another category means `role_affinity = 0` and `tag_overlap ≈ 0`, so the score is `0.4 · cos · activity` (observed 7–28, ceiling ~28), while Discovery reaches ~70.
  - Candidate fixes, none applied: (1) embed `Bio + Goals` only and run `--reembed`; (2) thresholds ~0.45 and ~0.6 for that text; (3) rescale the semantic term (`clamp((cos − 0.3) / (0.85 − 0.3))`) with a `FEED_ALGORITHM_VERSION` bump, after (1); (4) maybe hide the percentage for the bio tier. "Better" here means less dependent on tags/role and a wider spread, not verified relevance: there is no labelled ground truth, so final calibration needs real users.
- **Feed improvement ideas to consider (2026-11-02, none decided or applied; list with reasoning in `docs/plans/stage-6-feed.md`, section 12, item 25)** — the mechanics are sound, the weak part is the model of "who fits". Discovery finds people *like me* (same role 1.0, shared tags, similar text), while a profile's `goals_description` often asks for someone *different* (a backend developer looking for a sales co-founder). Quick: embed `Bio + Goals` only; thresholds ~0.45 / ~0.6; push already-viewed cards down instead of keeping them in place; weight tags by rarity (`usage_count`). Medium: `extra_attributes` (`grade`, `format`, `workload`) in the score; rescale the cosine; no percentage for the bio tier. Large: two vectors per profile (`bio` and `goals`) and score "my goals ↔ their bio" plus "their goals ↔ my bio", possibly different per role (study mate and language buddy want similarity, co-founder and investor want complementarity); not tested yet, a read-only experiment like the `Bio + Goals` one can check it first. Later: contact opens (stage 7) as a feedback signal. Note that the bio tier is *not* other-category only: it covers every category including the viewer's own, minus profiles already in tiers 1–2, so a same-category profile that matched no filter shows up there with a full score, while other-category ones get `0.4 · cos · activity` only.
- **Contact privacy** — contacts open without reciprocity, so protection relies entirely on rate limits, logging, and moderation rather than mutual consent.
- **Daily-limit bypass by deleting a profile (accepted, stage 7)** — `contact_opens` cascades on both profiles, so a user who deletes the profile they opened contacts with takes those opens out of the 24 h window and can open more. The alternative (keeping `opener_user_id` and `SET NULL` on the profile) was rejected as extra schema for a small gain; revisit if abuse shows up.
- **Notification stream (accepted, stage 7)** — every first-time open is its own message, no digest. It is bounded by the opener's daily limit and the once-per-pair rule, but the owner of a popular profile can still get many messages a day. A digest (one message per N minutes) is the follow-up if people complain.
- **Notifications are at-least-once** — a retry and the sweep running at the same moment can both send before the result is written (`settle_notification` only changes a `pending` row, so the outcome is never overwritten). The window is small and the cost is one duplicate message.
- **Contact opens have only the daily limit** — no per-minute limit until the stage 8 rate limiting, so a script can burn the whole 20 in a few seconds (it cannot get more).
- **The Telegram provider has never talked to the real Bot API** — there is no real bot yet; it is covered by `httpx.MockTransport` only. Before the first real run check: the bot token, that `mini_app_url` is https, that users have pressed `/start` (otherwise `403` → `failed`, which is expected and final) and that the `web_app` button opens the right profile. `failed` rows are not retried by the sweep.
- **Feed without rate limiting (accepted until rate limiting lands)** — stage 6 ships no limits: (a) every new filter combination rebuilds a feed session (category SQL plus a sequential vector scan for tier 3) and stores up to 200 items in Redis for 30 minutes, with no cap on how many a user can trigger; (b) `GET /feed/cards/{id}` returns any visible profile, so iterating ids scrapes every visible profile. Rejecting non-approved `tag_ids` in filters limits (a) partly. Must be closed together with application-level rate limiting: a per-user cap on session builds per minute and limits on `cards` and `views`.
- **Unmoderated tags on public cards** — pending tags are shown on other users' cards until stage 8 moderation; the frontend must render tags, `bio` and `goals` as plain text only.
- **Dev seed safety** — the ~100-profile seed (`scripts/dev_seed_profiles.py`, stage 6) refuses to run with `run.env == prod` (including `--reset`, which deletes users by a `telegram_id` range), and every seed username starts with `seed_` so a stage 7 "open contact" can never point at a real Telegram account.
- **Feed formula simplifications to check on the seed** — all tags weigh the same (a common `Python` counts as much as a rare `FastAPI`; `usage_count` could weight by rarity later), `extra_attributes` play no part in the score, everyone sees the same top profiles (no diversity), viewed cards keep their place and so sit at the top on every return, tier 2 criteria are equal.
- **TODO — `is_embedded` on `role_fields`** — `extra_attributes` currently plays no role in Discovery or the scoring formula (only used for Search's `EXACT_MATCH` tier filtering). A cheap future improvement: a boolean flag on `role_fields` marking which field values get folded into the embedding input text (e.g. append `"grade: middle"` alongside `category, role, tags, bio, goals`), giving Discovery a partial semantic signal from `extra_attributes` without hand-rolling a per-`field_type` comparison function. An explicit scoring term instead (comparing fields directly, e.g. exact-match bonus for same `grade`) was considered and rejected — `field_type`s are heterogeneous (`select`, `multi_select`, `number`, `text`), each needing its own similarity function, and it would mean reweighing the fixed formula without real usage data to calibrate against (see "Match-percentage calibration" above). Not scheduled for any stage yet; keep in mind when designing `role_fields` in stage 3 so adding this column later isn't a breaking migration.
