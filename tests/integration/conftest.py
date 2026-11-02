"""Fixtures for tests that need real Postgres and/or Redis."""

from collections.abc import AsyncIterator
from urllib.parse import unquote, urlsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import Depends
from httpx import ASGITransport
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.core.config import settings
from app.infrastructure.helpers import db_helper
from app.infrastructure.models import Base
from app.main import main_app
from tests.fixtures.taxonomy_data import TaxonomyIds, seed_basic_taxonomy


def _parse_db_url(url: str) -> dict:
    parts = urlsplit(url)
    return {
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "host": parts.hostname,
        "port": parts.port or 5432,
        "database": (parts.path or "/").lstrip("/"),
    }


@pytest.fixture(scope="session", autouse=True)
def _refuse_to_run_against_non_test_data() -> None:
    """The fixtures below TRUNCATE tables and FLUSHDB Redis. If `.env.test` is
    misconfigured to point at dev data, stop before anything is wiped."""
    db_name = _parse_db_url(str(settings.db.url))["database"]
    redis_db = urlsplit(str(settings.redis.url)).path.lstrip("/") or "0"

    if not db_name.endswith("_test_db"):
        pytest.exit(
            f"Refusing to run: database '{db_name}' does not end with '_test_db'. "
            "Check APP_CONFIG__DB__URL in .env.test.",
            returncode=2,
        )
    if redis_db == "0":
        pytest.exit(
            "Refusing to run: tests flush Redis, but the configured Redis DB is 0 (the dev cache). "
            "Use another DB number in APP_CONFIG__REDIS__URL in .env.test.",
            returncode=2,
        )
    # The broker fixture flushes its DB too: never the dev cache (0) or the dev broker (2).
    broker_db = urlsplit(settings.embedding.broker_url).path.lstrip("/") or "0"
    if broker_db in ("0", "2"):
        pytest.exit(
            f"Refusing to run: tests flush the Celery broker, but the configured broker DB is {broker_db} "
            "(the dev cache or the dev broker). Use another DB number in APP_CONFIG__EMBEDDING__BROKER_URL in .env.test.",
            returncode=2,
        )


@pytest_asyncio.fixture(autouse=True)
async def _test_database() -> AsyncIterator[None]:
    """Runs before/after every integration test, entirely within that test's own
    event loop. pytest-asyncio gives each test function a fresh loop by default,
    and asyncpg connections are bound to the loop they're created on -- a
    session-scoped setup fixture (opening connections once, up front) would
    leave the shared `db_helper.engine` pool bound to a loop that later tests
    don't share, causing "attached to a different loop" errors. Disposing
    the engine at the end of every test avoids that: the next test's first
    use of `db_helper.engine` reconnects fresh, in its own loop.
    """
    params = _parse_db_url(str(settings.db.url))

    conn = await asyncpg.connect(
        user=params["user"],
        password=params["password"],
        host=params["host"],
        port=params["port"],
        database="postgres",
    )
    try:
        await conn.execute(f'CREATE DATABASE "{params["database"]}"')
    except asyncpg.DuplicateDatabaseError:
        pass
    finally:
        await conn.close()

    async with db_helper.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with db_helper.engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE users, categories, roles, role_fields, tags, tag_scopes, "
                "profiles, profile_tags, profile_embeddings, profile_views RESTART IDENTITY CASCADE"
            )
        )
    await db_helper.engine.dispose()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with db_helper.session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """A Redis client created inside the test's own event loop (the module-level
    `redis_helper.client` would stay bound to the first test's loop, same as the
    asyncpg pool above). Uses the test Redis DB from `.env.test` and flushes it
    first so cache state never leaks between tests."""
    client = Redis.from_url(
        str(settings.redis.url),
        decode_responses=True,
        socket_connect_timeout=settings.redis.socket_timeout,
        socket_timeout=settings.redis.socket_timeout,
    )
    await client.flushdb()
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def broker_client() -> AsyncIterator[Redis]:
    """Reads the test Celery broker (a Redis DB of its own). Flushed before and after, so a test sees
    only the jobs it enqueued itself, and no test depends on whether Redis was running for the others."""
    client = Redis.from_url(settings.embedding.broker_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def container(session: AsyncSession, redis_client: Redis) -> Container:
    return Container(session=session, redis_client=redis_client)


@pytest_asyncio.fixture
async def taxonomy_data(session: AsyncSession) -> TaxonomyIds:
    return await seed_basic_taxonomy(session)


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    """Drives the real app through its real ASGI lifespan (table creation on
    startup, engine disposal on shutdown) against the test database."""
    async with LifespanManager(main_app):
        transport = ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def taxonomy_client(redis_client: Redis) -> AsyncIterator[httpx.AsyncClient]:
    """Like `async_client`, but the container gets the per-test `redis_client`.
    Kept separate so auth tests don't need Redis running."""

    async def _container(session: AsyncSession = Depends(db_helper.session_getter)) -> Container:
        return Container(session=session, redis_client=redis_client)

    main_app.dependency_overrides[get_container] = _container
    try:
        async with LifespanManager(main_app):
            transport = ASGITransport(app=main_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client
    finally:
        main_app.dependency_overrides.pop(get_container, None)
