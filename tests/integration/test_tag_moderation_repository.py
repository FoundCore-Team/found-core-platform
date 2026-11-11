import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.domain.enums import EmbeddingStatus, TagStatus
from app.infrastructure.helpers import db_helper
from app.infrastructure.models import ProfileModel, ProfileTagModel, TagModel, TagScopeModel
from app.infrastructure.repositories import (
    SqlAlchemyProfileEmbeddingRepository,
    SqlAlchemyProfileRepository,
)
from app.infrastructure.repositories.sqlalchemy_taxonomy import SqlAlchemyTaxonomyRepository
from tests.fixtures.feed_data import add_profile, add_user
from tests.fixtures.taxonomy_data import add_tag


def _repo(session) -> SqlAlchemyTaxonomyRepository:
    return SqlAlchemyTaxonomyRepository(session, suggest_limit=10)


async def _tag_ids_of(session, profile_id: int) -> list[int]:
    result = await session.execute(
        select(ProfileTagModel.tag_id).where(ProfileTagModel.profile_id == profile_id).order_by(ProfileTagModel.tag_id)
    )
    return list(result.scalars())


async def _scopes_of(session, tag_id: int) -> set[tuple[int, int | None]]:
    result = await session.execute(select(TagScopeModel).where(TagScopeModel.tag_id == tag_id))
    return {(s.category_id, s.role_id) for s in result.scalars()}


class TestLockAndStatus:
    async def test_lock_returns_current_rows_in_id_order_and_skips_missing_ids(self, session, taxonomy_data):
        a = await add_tag(session, "A")
        b = await add_tag(session, "B")

        locked = await _repo(session).lock_tags_for_update([b.id, 999999, a.id])

        assert [t.id for t in locked] == [a.id, b.id]

    async def test_lock_rereads_a_row_changed_by_another_session(self, session, taxonomy_data):
        tag = await add_tag(session, "A", status=TagStatus.PENDING)
        assert (await _repo(session).get_tags_by_ids([tag.id]))[0].status is TagStatus.PENDING

        async with db_helper.session_factory() as other:
            await SqlAlchemyTaxonomyRepository(other, suggest_limit=10).set_tag_status(tag.id, TagStatus.APPROVED)
            await other.commit()

        locked = await _repo(session).lock_tags_for_update([tag.id])
        assert locked[0].status is TagStatus.APPROVED

    async def test_set_tag_status_changes_only_the_status(self, session, taxonomy_data):
        tag = await add_tag(session, "A", status=TagStatus.PENDING, usage_count=3)

        await _repo(session).set_tag_status(tag.id, TagStatus.REJECTED)
        await session.commit()

        (reloaded,) = await _repo(session).get_tags_by_ids([tag.id])
        assert (reloaded.status, reloaded.usage_count, reloaded.merged_into_tag_id) == (TagStatus.REJECTED, 3, None)


class TestMergeTag:
    async def test_source_becomes_an_alias_and_target_gets_its_usage(self, session, taxonomy_data):
        source = await add_tag(session, "NodeJS", status=TagStatus.PENDING, usage_count=2)
        target = await add_tag(session, "Node.js", usage_count=5)

        await _repo(session).merge_tag(source.id, target.id)
        await session.commit()

        merged_source, merged_target = await _repo(session).get_tags_by_ids([source.id, target.id])
        assert (merged_source.status, merged_source.merged_into_tag_id, merged_source.usage_count) == (
            TagStatus.MERGED,
            target.id,
            2,
        )
        assert (merged_target.status, merged_target.merged_into_tag_id, merged_target.usage_count) == (
            TagStatus.APPROVED,
            None,
            7,
        )

    async def test_aliases_of_the_source_are_redirected_to_the_target(self, session, taxonomy_data):
        # `old_alias` already points at `source`; after source -> target no alias may point at another alias.
        source = await add_tag(session, "NodeJS")
        target = await add_tag(session, "Node.js")
        old_alias = await add_tag(session, "Node Js", status=TagStatus.PENDING)
        await session.execute(
            text("UPDATE tags SET status = 'merged', merged_into_tag_id = :s WHERE id = :a"),
            {"s": source.id, "a": old_alias.id},
        )
        await session.commit()

        await _repo(session).merge_tag(source.id, target.id)
        await session.commit()

        rows = {t.id: t for t in await _repo(session).get_tags_by_ids([old_alias.id, source.id])}
        assert rows[old_alias.id].merged_into_tag_id == target.id
        assert rows[source.id].merged_into_tag_id == target.id


class TestCopyTagScopes:
    async def test_target_gets_every_scope_including_a_category_wide_one(self, session, taxonomy_data):
        t = taxonomy_data
        source = await add_tag(
            session,
            "NodeJS",
            scopes=[(t.tech_category_id, t.engineering_role_id), (t.edu_category_id, None)],
        )
        target = await add_tag(session, "Node.js", scopes=[(t.tech_category_id, t.design_role_id)])

        await _repo(session).copy_tag_scopes(source.id, target.id)
        await session.commit()

        assert await _scopes_of(session, target.id) == {
            (t.tech_category_id, t.design_role_id),
            (t.tech_category_id, t.engineering_role_id),
            (t.edu_category_id, None),
        }

    async def test_copying_twice_creates_no_duplicates(self, session, taxonomy_data):
        t = taxonomy_data
        source = await add_tag(session, "NodeJS", scopes=[(t.edu_category_id, None)])
        target = await add_tag(session, "Node.js", scopes=[(t.edu_category_id, None)])
        repo = _repo(session)

        await repo.copy_tag_scopes(source.id, target.id)
        await repo.copy_tag_scopes(source.id, target.id)
        await session.commit()

        count = (
            await session.execute(text("SELECT count(*) FROM tag_scopes WHERE tag_id = :t"), {"t": target.id})
        ).scalar_one()
        assert count == 1


class TestPendingQueue:
    async def test_lists_only_pending_oldest_first_with_ties_by_id(self, session, taxonomy_data):
        now = datetime.now(timezone.utc)
        first = await add_tag(session, "First", status=TagStatus.PENDING)
        second = await add_tag(session, "Second", status=TagStatus.PENDING)
        third = await add_tag(session, "Third", status=TagStatus.PENDING)
        await add_tag(session, "Approved")
        await add_tag(session, "Rejected", status=TagStatus.REJECTED)
        # `first` and `second` share a timestamp: the id decides. `third` is older than both.
        await session.execute(
            text("UPDATE tags SET created_at = :t WHERE id IN (:a, :b)"), {"t": now, "a": first.id, "b": second.id}
        )
        await session.execute(
            text("UPDATE tags SET created_at = :t WHERE id = :a"), {"t": now - timedelta(hours=1), "a": third.id}
        )
        await session.commit()

        queue = await _repo(session).list_pending_tags(limit=10, offset=0)

        assert [p.tag.id for p in queue] == [third.id, first.id, second.id]
        assert await _repo(session).count_pending_tags() == 3

    async def test_pages_do_not_overlap(self, session, taxonomy_data):
        tags = [await add_tag(session, f"T{i}", status=TagStatus.PENDING) for i in range(5)]
        repo = _repo(session)

        page_1 = await repo.list_pending_tags(limit=2, offset=0)
        page_2 = await repo.list_pending_tags(limit=2, offset=2)
        page_3 = await repo.list_pending_tags(limit=2, offset=4)

        assert [p.tag.id for p in page_1 + page_2 + page_3] == [t.id for t in tags]

    async def test_carries_creator_scopes_and_profile_count(self, session, taxonomy_data):
        t = taxonomy_data
        author_id = await add_user(session, 1, username="author")
        tag = await add_tag(
            session,
            "NodeJS",
            status=TagStatus.PENDING,
            created_by=author_id,
            scopes=[
                (t.tech_category_id, t.engineering_role_id),
                (t.tech_category_id, None),
                (t.edu_category_id, None),
                (t.tech_category_id, t.design_role_id),
            ],
        )
        for telegram_id in (2, 3):
            user_id = await add_user(session, telegram_id)
            await add_profile(
                session, user_id, t.tech_category_id, t.engineering_role_id, tag_ids=(tag.id,)
            )

        (item,) = await _repo(session).list_pending_tags(limit=10, offset=0)

        assert item.profiles_count == 2
        assert (item.creator.user_id, item.creator.first_name, item.creator.username) == (
            author_id,
            "User1",
            "author",
        )
        # category_id, role_id with the category-wide scope (NULL) first.
        assert [(s.category_id, s.role_id) for s in item.scopes] == [
            (t.tech_category_id, None),
            (t.tech_category_id, t.engineering_role_id),
            (t.tech_category_id, t.design_role_id),
            (t.edu_category_id, None),
        ]

    async def test_a_tag_without_an_author_has_no_creator(self, session, taxonomy_data):
        await add_tag(session, "Orphan", status=TagStatus.PENDING, created_by=None)

        (item,) = await _repo(session).list_pending_tags(limit=10, offset=0)

        assert item.creator is None
        assert item.scopes == []
        assert item.profiles_count == 0


class TestProfileLinks:
    async def _two_profiles(self, session, t, *tag_ids_per_profile: tuple[int, ...]) -> list[int]:
        ids = []
        for i, tag_ids in enumerate(tag_ids_per_profile, start=1):
            user_id = await add_user(session, i)
            ids.append(await add_profile(session, user_id, t.tech_category_id, t.engineering_role_id, tag_ids=tag_ids))
        return ids

    async def test_detach_removes_the_tag_from_every_profile_and_reports_them(self, session, taxonomy_data):
        t = taxonomy_data
        tag = await add_tag(session, "A")
        other = await add_tag(session, "B")
        p1, p2, p3 = await self._two_profiles(session, t, (tag.id, other.id), (tag.id,), (other.id,))

        affected = await SqlAlchemyProfileRepository(session).detach_tag(tag.id)
        await session.commit()

        assert affected == [p1, p2]
        assert await _tag_ids_of(session, p1) == [other.id]
        assert await _tag_ids_of(session, p2) == []
        assert await _tag_ids_of(session, p3) == [other.id]

    async def test_replace_moves_links_and_keeps_one_link_for_a_profile_with_both(self, session, taxonomy_data):
        t = taxonomy_data
        source = await add_tag(session, "NodeJS")
        target = await add_tag(session, "Node.js")
        p1, p2, p3 = await self._two_profiles(
            session, t, (source.id,), (source.id, target.id), (target.id,)
        )

        affected = await SqlAlchemyProfileRepository(session).replace_tag(source.id, target.id)
        await session.commit()

        assert affected == [p1, p2]
        assert await _tag_ids_of(session, p1) == [target.id]
        assert await _tag_ids_of(session, p2) == [target.id]
        assert await _tag_ids_of(session, p3) == [target.id]

    async def test_replace_with_no_profiles_is_a_noop(self, session, taxonomy_data):
        source = await add_tag(session, "NodeJS")
        target = await add_tag(session, "Node.js")

        assert await SqlAlchemyProfileRepository(session).replace_tag(source.id, target.id) == []

    async def test_touch_sets_one_fresh_updated_at_for_the_listed_profiles_only(self, session, taxonomy_data):
        t = taxonomy_data
        p1, p2, p3 = await self._two_profiles(session, t, (), (), ())
        before = {
            row.id: row.updated_at
            for row in (await session.execute(select(ProfileModel.id, ProfileModel.updated_at))).all()
        }

        touched = await SqlAlchemyProfileRepository(session).touch([p1, p2, 999999])
        await session.commit()

        assert set(touched) == {p1, p2}
        assert len(set(touched.values())) == 1
        after = {
            row.id: row.updated_at
            for row in (await session.execute(select(ProfileModel.id, ProfileModel.updated_at))).all()
        }
        assert after[p1] == after[p2] == touched[p1] > before[p1]
        assert after[p3] == before[p3]

    async def test_touch_of_nothing_does_nothing(self, session, taxonomy_data):
        assert await SqlAlchemyProfileRepository(session).touch([]) == {}


class TestMarkPendingMany:
    async def test_creates_and_resets_rows_for_every_profile(self, session, taxonomy_data):
        t = taxonomy_data
        ids = []
        for i in (1, 2, 3):
            user_id = await add_user(session, i)
            ids.append(await add_profile(session, user_id, t.tech_category_id, t.engineering_role_id))
        repo = SqlAlchemyProfileEmbeddingRepository(session)
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        await repo.mark_pending(ids[0], old)
        await repo.set_outcome(ids[0], EmbeddingStatus.READY, old)
        new = datetime.now(timezone.utc)

        await repo.mark_pending_many({ids[0]: new, ids[1]: new})
        await session.commit()

        rows = {i: await repo.get(i) for i in ids}
        assert rows[ids[0]].status is EmbeddingStatus.PENDING and rows[ids[0]].source_updated_at == new
        assert rows[ids[1]].status is EmbeddingStatus.PENDING and rows[ids[1]].source_updated_at == new
        assert rows[ids[2]] is None

    async def test_an_empty_batch_does_nothing(self, session, taxonomy_data):
        await SqlAlchemyProfileEmbeddingRepository(session).mark_pending_many({})


class TestLockCompatibility:
    async def test_validator_read_waits_for_a_moderator_holding_the_tag(self, session, taxonomy_data):
        t = taxonomy_data
        tag = await add_tag(session, "A", scopes=[(t.tech_category_id, t.engineering_role_id)])

        async with db_helper.session_factory() as moderator, db_helper.session_factory() as saver:
            await SqlAlchemyTaxonomyRepository(moderator, suggest_limit=10).lock_tags_for_update([tag.id])
            read = asyncio.create_task(
                SqlAlchemyTaxonomyRepository(saver, suggest_limit=10).get_tags_in_scope_by_ids(
                    [tag.id], t.tech_category_id, t.engineering_role_id
                )
            )
            await asyncio.sleep(0.3)
            assert not read.done()

            await SqlAlchemyTaxonomyRepository(moderator, suggest_limit=10).set_tag_status(tag.id, TagStatus.REJECTED)
            await moderator.commit()
            tags = await asyncio.wait_for(read, timeout=5)
            await saver.commit()

        # The waiting save sees the result of the moderation, not the state from before it.
        assert [x.status for x in tags] == [TagStatus.REJECTED]

    async def test_a_moderator_waits_for_a_save_that_holds_the_tag(self, session, taxonomy_data):
        t = taxonomy_data
        tag = await add_tag(session, "A", scopes=[(t.tech_category_id, t.engineering_role_id)])

        async with db_helper.session_factory() as saver, db_helper.session_factory() as moderator:
            await SqlAlchemyTaxonomyRepository(saver, suggest_limit=10).get_tags_in_scope_by_ids(
                [tag.id], t.tech_category_id, t.engineering_role_id
            )
            lock = asyncio.create_task(
                SqlAlchemyTaxonomyRepository(moderator, suggest_limit=10).lock_tags_for_update([tag.id])
            )
            await asyncio.sleep(0.3)
            assert not lock.done()

            await saver.commit()
            await asyncio.wait_for(lock, timeout=5)
            await moderator.commit()

    async def test_two_validator_reads_and_a_usage_increment_do_not_block_each_other(self, session, taxonomy_data):
        t = taxonomy_data
        tag = await add_tag(session, "A", scopes=[(t.tech_category_id, t.engineering_role_id)])

        async with db_helper.session_factory() as first, db_helper.session_factory() as second:
            args = ([tag.id], t.tech_category_id, t.engineering_role_id)
            await SqlAlchemyTaxonomyRepository(first, suggest_limit=10).get_tags_in_scope_by_ids(*args)
            await SqlAlchemyTaxonomyRepository(second, suggest_limit=10).get_tags_in_scope_by_ids(*args)
            # FOR NO KEY UPDATE is compatible with the other session's FOR KEY SHARE.
            await asyncio.wait_for(
                SqlAlchemyTaxonomyRepository(first, suggest_limit=10).increment_tags_usage([tag.id]), timeout=2
            )
            await first.commit()
            await second.commit()

        row = (await session.execute(select(TagModel.usage_count).where(TagModel.id == tag.id))).scalar_one()
        assert row == 1
