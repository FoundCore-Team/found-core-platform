"""Guards the physical schema. The test database is built by `create_all` from the models,
so a model change that silently drops an index or constraint would otherwise go unnoticed."""

from sqlalchemy import text


async def _index_defs(session, table: str) -> list[str]:
    result = await session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = :t"),
        {"t": table},
    )
    return [row[0] for row in result.all()]


async def _columns(session, table: str) -> set[str]:
    result = await session.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
        {"t": table},
    )
    return {row[0] for row in result.all()}


async def test_pg_trgm_extension_is_installed(session):
    result = await session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))

    assert result.scalar_one_or_none() == 1


async def test_tags_has_normalized_title_and_no_slug(session):
    columns = await _columns(session, "tags")

    assert "normalized_title" in columns
    assert "slug" not in columns


async def test_tags_normalized_title_is_unique(session):
    defs = await _index_defs(session, "tags")

    assert any("UNIQUE" in d and "(normalized_title)" in d for d in defs)


async def test_tags_title_has_a_gin_trigram_index(session):
    defs = await _index_defs(session, "tags")

    assert any("ix_tags_title_trgm" in d and "gin" in d and "gin_trgm_ops" in d for d in defs)


async def test_tag_scopes_unique_constraint_treats_null_role_as_a_value(session):
    defs = await _index_defs(session, "tag_scopes")

    assert any(
        "UNIQUE" in d and "tag_id, category_id, role_id" in d and "NULLS NOT DISTINCT" in d for d in defs
    )


async def test_categories_and_roles_keep_their_slug_keys(session):
    assert "slug" in await _columns(session, "categories")
    assert "slug" in await _columns(session, "roles")


async def test_pgvector_extension_is_installed(session):
    result = await session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))

    assert result.scalar_one_or_none() == 1


async def test_profile_embeddings_has_an_hnsw_cosine_index(session):
    defs = await _index_defs(session, "profile_embeddings")

    assert any("hnsw" in d and "vector_cosine_ops" in d and "m='16'" in d and "ef_construction='64'" in d for d in defs)


async def test_profile_embeddings_sweep_index_covers_only_unfinished_rows(session):
    defs = await _index_defs(session, "profile_embeddings")

    assert any("ix_profile_embeddings_status_updated_at" in d and "ready" in d and "WHERE" in d for d in defs)


async def test_contact_opens_has_a_pair_primary_key_and_an_index_on_the_opened_profile(session):
    defs = await _index_defs(session, "contact_opens")

    assert any("pk_contact_opens" in d and "(opener_profile_id, opened_profile_id)" in d for d in defs)
    assert any("ix_contact_opens_opened_profile_id" in d and "(opened_profile_id)" in d for d in defs)


async def test_contact_opens_sweep_index_covers_only_pending_rows(session):
    defs = await _index_defs(session, "contact_opens")

    assert any(
        "ix_contact_opens_pending_updated_at" in d and "(notification_updated_at)" in d and "pending" in d and "WHERE" in d
        for d in defs
    )


async def test_notification_status_enum_has_the_four_states(session):
    result = await session.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'notification_status' ORDER BY e.enumsortorder"
        )
    )

    assert [row[0] for row in result.all()] == ["pending", "sent", "failed", "skipped"]


async def test_nearest_neighbour_query_uses_the_hnsw_index(session):
    zeros = "[" + ",".join(["0"] * 1536) + "]"
    # On an empty table the planner would prefer a plain scan, which says nothing about the index.
    await session.execute(text("SET LOCAL enable_seqscan = off"))

    plan = await session.execute(
        text(
            "EXPLAIN SELECT profile_id FROM profile_embeddings "
            f"ORDER BY embedding <=> CAST('{zeros}' AS vector) LIMIT 5"
        )
    )

    assert "ix_profile_embeddings_embedding_hnsw" in "\n".join(row[0] for row in plan.all())
