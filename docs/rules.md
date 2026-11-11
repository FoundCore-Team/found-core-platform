# FoundCore Development Rules

These are mandatory development and code style rules.

## 1. Architecture

Follow the architecture and patterns defined in `CLAUDE.md`.

### Domain entities

* Domain entities belong only in `app/domain/entities/`.
* Do not define domain entities or equivalent dataclasses in `api/`, `application/`, or `infrastructure/`.
* Keep unrelated entities in separate modules. Whether a group of entities/exceptions is "related" enough to share one module (e.g. a whole small flow in one file) or needs its own file is a per-case call, not a fixed formula — see `CLAUDE.md` for the current file map and reasoning.

### Module naming

* Name a module after what it actually implements, not after the nearest domain entity — e.g. a router/use case/mapper that is really about the auth flow belongs in `auth.py`, not `user.py`, even where it touches the `User` entity.

### Entity identity

* `NewXEntity` must not have an `id` field.
* `XEntity` must have a required `id: int`.
* `IRepository.create()` accepts `NewXEntity` and returns `XEntity`.
* Exception: when concurrent requests can hit a unique constraint (`users.telegram_id`, `tags.normalized_title`), `create()` uses `INSERT ... ON CONFLICT DO NOTHING RETURNING` and returns `XEntity | None`. `None` only means "another request inserted it first"; the use case re-reads and decides what to do.
* Do not use `id: int | None` instead.

### Optional fields

Use `| None` only when the value can actually be absent according to business rules or an external contract.

Do not add optional fields or defaults for convenience.

### Domain exceptions

Use this pattern:

```python
class SomeError(Exception):

    message = "Something went wrong."

    def __init__(self) -> None:
        super().__init__(self.message)
```

* Inherit directly from `Exception`.
* Keep the message as a class attribute.
* Raise without passing a message.
* `__init__` may take extra parameters to carry context the caller needs (e.g. a ban reason) — only the `message` class attribute stays fixed.

### Dependency Injection

Use the `Container` and `Depends()` patterns defined in `CLAUDE.md`.

Do not introduce another DI or composition pattern.

### Repository responsibility

* A repository is responsible for exactly one data source/resource (one Postgres table family, one cache, etc.). Do not combine two infrastructure sources (e.g. Postgres and Redis) in a single repository class — split them and use both from the use case.
* A repository must not make use-case-level decisions and must not raise domain/application exceptions. It returns an entity, `None`, a list of entities, or the result of an operation — nothing more.
* Whether `None` means "not found" (and what to do about it, e.g. raise `CategoryNotFoundError`) is decided by the use case, not the repository.

### Transactions

* A repository never commits. It only prepares changes (`add`/`flush`/`execute`); the use case that owns the scenario commits **once, at the end**, through `IUnitOfWork` (`domain/interfaces/unit_of_work.py`).
* Use cases depend on `IUnitOfWork`, never on `AsyncSession`. The implementation (`SqlAlchemyUnitOfWork`) is built by `Container` from the same session as the repositories.
* Every admin action writes its `admin_actions` row (`IAdminActionRepository.add`, which never commits) in the same transaction as the change it records, and the use case commits once. The journal must never disagree with the state: either both are saved or neither. A repeat of an action that changes nothing writes no row and does not commit. A `reason` is mandatory for every admin action.
* Read-only use cases do not commit. On failure nothing is committed and the request-scoped session is closed by `db_helper.session_getter`, which rolls back — there is deliberately no `rollback()` on `IUnitOfWork` until a real need appears (e.g. a long-lived session in a background job).

### Concurrency and queries

* "SELECT, and if missing INSERT" is a race: two simultaneous requests both see nothing and both insert. Wherever a unique key can be hit by concurrent requests, insert with `ON CONFLICT DO NOTHING RETURNING` and treat the empty result as "someone else got there first"; the `UNIQUE` constraint is the final guarantee, the preceding `SELECT` is only an optimization.
* Every such path needs an integration test that fires the requests in parallel (`asyncio.gather`) against real Postgres and asserts exactly one row and no error.
* A change that must hold against concurrent writers is a conditional `UPDATE ... WHERE <old state> RETURNING` (not a `SELECT`, a check and an unconditional write); an empty result means "someone else got there first" and the use case re-reads and answers for the current state.
* Row locks are taken in one fixed order, ascending id within a table: **tags → profiles → `profile_embeddings`**. Code that reads tags in order to save something that refers to them (the profile validator) takes `FOR KEY SHARE` and holds it until its commit; code that changes a tag's meaning (moderation) takes `FOR UPDATE`; a counter update on tags takes `FOR NO KEY UPDATE`, never `FOR UPDATE` (it would deadlock with the `KEY SHARE` holders). Rows read after waiting for a lock are loaded with `populate_existing`, otherwise the session returns the stale object.
* A lock or a race fix comes with a test that fires the requests in parallel and **fails when the lock is removed** (check by temporarily removing it); a race test that passes either way proves nothing.
* Scenarios of one person that must not interleave (counting and spending a limit, a ban and an unban) take `IUserRepository.lock_for_update` on the user row before they read, and the use case re-reads after it. Its test holds a lock that conflicts with `FOR UPDATE` but not with a plain `UPDATE` (`FOR KEY SHARE`) from a second session and expects the call to wait: with a plain `UPDATE` in the way the call would block anyway and prove nothing.
* Every list query that is returned to a client orders by a unique tie-breaker as its last key (`ORDER BY sort_order, id`), otherwise equal rows may come back in a different order on each call.

### Migrations

* Any change to a model (table, column, index, constraint, enum) ships with an Alembic revision in the same branch. The app never creates or alters tables itself — no `create_all` outside the test fixtures.
* Autogenerate output is a draft: review it by hand before committing. It does not emit extensions (add `CREATE EXTENSION IF NOT EXISTS ...` explicitly) and does not drop PG enum types in `downgrade()`.
* Every revision has a working `downgrade()`.
* Never edit a revision that is already merged or applied by someone else — fix it with a new revision.
* A new model must be exported from `app/infrastructure/models/__init__.py`, otherwise autogenerate silently skips it.
* Revisions contain schema only, no dev/seed data (the taxonomy is seeded by `scripts/dev_seed_taxonomy.py`).
* Before merging, `git pull` from `main`. If someone else's revision came in, change `down_revision` of yours to point at theirs — otherwise there are two heads and `alembic upgrade head` fails with "Multiple head revisions". After any pull that brings new revisions, run `alembic upgrade head`.
* Generate revisions with `docker compose exec -u "$(id -u):$(id -g)" ...` (see `CLAUDE.md`, "Migrations"); never put credentials in `alembic.ini`.

### Configuration values

* Do not hardcode tunable values (TTLs, limits, size thresholds, regex patterns, etc.) as module-level constants inside a repository, use case, or domain service. They belong in `core/config.py`.
* A repository/use case/domain service receives these values through its constructor, wired up in `container.py` — the same way `jwt_service()`/`telegram_init_data_service()` already inject `AuthConfig` values. Do not import `settings` directly inside a use case or domain service body.

### Secrets and environment

* A secret (JWT key, bot token, DB/Redis credentials) has **no default value** in `core/config.py` and no fallback file. `.env.template` is a template to copy, never a source the app reads.
* A new secret must also be added to the `prod` fail-fast check in `Settings` (placeholder or too weak → refuse to start), so a forgotten value fails the first deploy instead of running with a public key.
* A nested config used as a field default (`x: XConfig = XConfig()`) must be a plain `BaseModel`, not `BaseSettings`: a default instance of `BaseSettings` reads unprefixed environment variables (a shell's `ENV`, `HOST`, `PORT`).
* The Docker image runs as a non-root user and `.dockerignore` keeps `.env`, `.git`, `.venv` and tests out of it. A service gets its settings through `env_file`/environment at run time, never baked into the image.

### Domain services vs. application services

* `domain/services/` — pure domain computation and business rules with **no infrastructure dependency**: slug generation, discount calculation, validating a domain value, etc. Usable from both use cases and application services.
* `application/services/` — logic reused across multiple use cases that **does** depend on an implementation/infrastructure detail (secrets, HMAC, an external format). Do not move something into `application/services` just because a use case happens to use it — decide first whether it's actually a pure domain rule (→ `domain/services`) before reaching for this layer.

### Request-scoped auth

* Protect an endpoint with `Depends(get_current_user)` (see `CLAUDE.md`, "Request-scoped auth") — never with middleware.
* Every route under `/api/v1/admin` sits behind `Depends(require_admin)` declared on the `APIRouter` itself (a new route cannot forget it); a route that needs the admin's id also declares it as a parameter. The admin role is checked against the live `users.is_admin`, never against the token, and is granted only by `scripts/make_admin.py`.
* Never trust mutable user state (ban status, `is_admin`/roles, token validity) from the JWT payload alone. Re-check it against the current DB row inside the auth dependency on every request.

### Request input

* Every id taken from a path, query or body is bounded to `1..MAX_INT64` (`Int64Id` in `api/v1/schemas/common.py`, or `Path`/`Query` with `ge`/`le`), so an out-of-range value is `422`, never a `500` from the database driver.
* Every free-text field has a `max_length` in the request schema, set above the business limit (which stays in the domain validator) so oversized bodies are rejected before any processing.
* Malformed external input (Telegram `initData`, JWT) ends as a domain error mapped to `400`/`401`, never an unhandled exception.
* Check the shape of anything parsed from an external source before using it: the container type (a JSON object, not a list), exact types (`type(x) is int` — `bool` is an `int` subclass), string lengths matching the DB column, no NUL characters in text bound for Postgres, and `isascii()` together with `isdigit()` before `int()` on a string (`"²"` and Arabic digits pass `isdigit()` alone).
* A valid signature does not make a payload well-formed: a signed JWT must still declare its required claims (`options={"require": [...]}`, otherwise a token without `exp` never expires) and have them type-checked.
* Compare secrets and signatures with `hmac.compare_digest` on `bytes`; on `str` it raises `TypeError` for non-ASCII input, which would surface as a `500`.

### Rate limiting

* An endpoint that writes, costs money (an embedding job) or can be scraped gets a per-user limit through `rate_limited(action)` (`api/v1/dependencies/rate_limit.py`), never through middleware and never inside the handler. One rule is one dependency; a route with two rules lists two.
* Declare it in the decorator's `dependencies=[...]`: those run before the parameters, which keeps the order token → limit → `require_username` → validation. Do not move it into a parameter.
* A new limited action is a member of `RateLimitAction` and a field of `RateLimitConfig` with the same name; both numbers come from config (no hardcoded limits). The test that compares the fields with the enum fails if one is forgotten.
* The limiter decides and never raises: `IRateLimiter.hit` returns a `RateLimitDecision`, `RateLimitGuard` raises `RateLimitExceededError`, a router maps it with `too_many_requests`. A Redis failure allows the request (fail-open).
* A limit that depends on what the use case finds (counting only a cache miss) is checked in the use case through `RateLimitGuard`, not in a dependency.
* Its test lowers only that limit with `monkeypatch.setattr(settings.rate_limit, ...)` (the autouse fixture in `tests/integration/conftest.py` lifts all of them for the other tests) and checks that request N passes, request N+1 is `429` with `Retry-After`, and another user or action is not affected.

### Mappers

Use directional names:

```text
map_<source>_to_<target>
```

### Caching (Redis)

* Caching is a separate concern from data access. A data repository (e.g. `SqlAlchemyTaxonomyRepository`) must not know about Redis, cache keys, or TTLs. Use a separate cache repository typed on domain entities instead (`ITaxonomyCacheRepository` → `RedisTaxonomyCacheRepository`); it owns cache keys and serialization.
* Cache-aside orchestration (check cache → miss → read the data repository → populate the cache) lives in the **use case**, which holds both the data repository and the cache repository — not inside either repository.
* `RedisTaxonomyCacheRepository` catches `redis.exceptions.RedisError` internally, logs a warning, and returns `None`/no-ops on failure — a Redis outage may degrade latency, it must never break the request or raise up to the use case.
* A cached value that cannot be parsed or rebuilt into entities (corrupt JSON, outdated shape) is a miss, never an exception. After the first `RedisError` a cache repository instance stops calling Redis for the rest of its lifetime (one request).
* In a read use case, check the cache before any existence check against the data repository, so a cache hit costs no database query.
* Cache TTLs are config values (see "Configuration values" above), not hardcoded constants. The TTL is passed to the cache repository in `container.py`, not to the use case.

## 2. Code style

* Follow the style of the surrounding code.
* Use clear and explicit names.
* Keep functions and classes focused.
* Keep business logic out of HTTP handlers.
* Do not duplicate business logic between layers.
* Do not introduce abstractions, helpers, or patterns without a real need. A helper is justified only when it removes real, repeated logic or hides something hard to read; do not split a few repeated lines into several tiny helpers — inline them where they are used.
* In a class or module, place private helper methods/functions above the public ones that use them, right after `__init__`, so a reader meets them before the code that calls them.
* Clarity beats brevity: terse code is good, but not at the cost of being easy to understand.
* Do not add docstrings or comments unless they explain something non-obvious.
* Write all comments and docstrings in English, regardless of the language used in chat or commit messages.
* Do not rewrite working code without a reason related to the current task.
* Prefer simple, readable solutions over clever ones.

## 3. Execution and validation

Claude must **never run commands, tests, linters, formatters, type checkers, builds, migrations, Docker commands, or other project operations on its own**.

After making changes:

* briefly explain what was changed;
* provide the exact commands the user should run to test or verify the changes manually;
* do not claim that the changes were tested or verified unless the user ran the commands and provided the results.

### Files

* Read files only with the dedicated read tool and change them only with the edit/write tools. Do not read or modify files through shell commands (`cat`, `head`, `sed`, `awk`, `echo >`, heredocs, inline `python`/`perl` scripts), and do not rewrite several files with one script: every change is its own visible edit.

### Git

* Commit or push only when the user asks. Split the work into logical commits (config, domain, infrastructure, use cases and tests), each one importable on its own.
* Commit messages follow the repository style (`feat(scope): ...`, `refactor(scope): ...`, `docs(scope): ...`, `test(scope): ...`): a short subject, and a bullet list when it helps.
* **No `Co-Authored-By` trailer and no other attribution line** ("Generated with ...") in commit messages or pull request descriptions.

## 4. Tests

* `tests/unit` must not need Postgres or Redis (use in-memory fakes); anything that touches them lives in `tests/integration`. Fixtures that `TRUNCATE`/`FLUSHDB` keep the guard that refuses to run against a database not ending in `_test_db` or Redis DB 0 — never weaken it.
* A bug fix comes with a test that fails on the old code. Do not write tests that restate the implementation (assert what the code does by re-deriving it) or that pin current behavior without checking it is correct.
* Take the expected value of a security-critical algorithm (signatures, hashes) from an independent source such as `openssl`, not from the code under test — every other test signs with the same algorithm it verifies and cannot notice a misread spec.
* A fake stands in for the real implementation on the whole contract, not just the happy path: it implements the real ABC, returns copies instead of the stored object, writes only the columns the real repository writes, and returns `None`/raises where the real one does. A fake that is more forgiving than the real thing hides bugs.
* Every external-input path is tested with hostile input and asserts a `4xx`, never a `5xx`: wrong type, oversized, out of range, non-ASCII, duplicated parameters, missing required fields.
* Auth behavior (ban, `token_version`, expiry) is tested over real HTTP against a real protected route, not only against the use case.
* Seed and reference-data tests assert meaning (options are non-empty and unique, keys are unique per role), not only row counts that merely equal the source.
* A test helper that writes data read by another session (e.g. an HTTP request) must `commit()` explicitly — repositories no longer commit.
* An integration test does not call `session.rollback()` just to "see fresh data" (it expires every ORM object the test still holds and the next attribute access fails with `MissingGreenlet`); a `text()` query already sees committed data. Where a rollback is needed, copy the ids first.
* `create_all` in the test fixtures does not alter an existing table: after a model change the test database `found_core_test_db` has to be dropped so the fixture creates it again.
