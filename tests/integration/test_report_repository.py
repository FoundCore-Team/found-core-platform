"""SqlAlchemyReportRepository and the physical `reports` schema against real Postgres: the partial unique index, the
cascades, the visibility rule, the closing updates and the admin queue with its ordering."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.entities import NewReportEntity
from app.domain.enums import ProfileStatus, ReportReason, ReportStatus
from app.infrastructure.repositories import SqlAlchemyReportRepository
from tests.fixtures.feed_data import add_profile, add_user

T0 = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def new_report(reporter_user_id, profile_id, reason=ReportReason.SPAM, comment=None, minutes=0) -> NewReportEntity:
    return NewReportEntity(
        reporter_user_id=reporter_user_id,
        profile_id=profile_id,
        reason=reason,
        comment=comment,
        created_at=T0 + timedelta(minutes=minutes),
    )


async def make_target(session, taxonomy, telegram_id, *, role_id=None, user_kwargs=None, **profile_kwargs):
    """A user with one profile; returns (user_id, profile_id)."""
    user_id = await add_user(session, telegram_id, **(user_kwargs or {}))
    profile_id = await add_profile(
        session, user_id, taxonomy.tech_category_id, role_id or taxonomy.engineering_role_id, **profile_kwargs
    )
    return user_id, profile_id


async def count_reports(session, **where) -> int:
    clause = " AND ".join(f"{column} = {value!r}" for column, value in where.items()) or "true"
    return (await session.execute(text(f"SELECT count(*) FROM reports WHERE {clause}"))).scalar_one()


# ---------- create and the one-open-report rule ----------


async def test_create_returns_an_open_report_that_is_not_resolved(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporter = await add_user(session, 1001)
    _, profile_id = await make_target(session, taxonomy_data, 1002)

    report = await repository.create(new_report(reporter, profile_id, ReportReason.FAKE, "bot"))

    assert report is not None
    assert (report.reporter_user_id, report.profile_id) == (reporter, profile_id)
    assert (report.reason, report.comment) == (ReportReason.FAKE, "bot")
    assert report.status is ReportStatus.OPEN
    assert report.resolved_at is None
    assert report.created_at == T0


async def test_a_second_open_report_of_the_same_reporter_on_the_same_profile_is_refused(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporter = await add_user(session, 1011)
    _, profile_id = await make_target(session, taxonomy_data, 1012)
    first = await repository.create(new_report(reporter, profile_id, ReportReason.SPAM, "first"))

    second = await repository.create(new_report(reporter, profile_id, ReportReason.OFFENSIVE, "second"))

    assert first is not None and second is None
    assert await count_reports(session) == 1
    found = await repository.get_open_by_reporter_and_profile(reporter, profile_id)
    assert (found.id, found.reason, found.comment) == (first.id, ReportReason.SPAM, "first")


async def test_other_reporters_and_other_profiles_are_not_blocked(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporter, other_reporter = await add_user(session, 1021), await add_user(session, 1022)
    _, profile_id = await make_target(session, taxonomy_data, 1023)
    _, other_profile_id = await make_target(session, taxonomy_data, 1024)

    results = [
        await repository.create(new_report(reporter, profile_id)),
        await repository.create(new_report(other_reporter, profile_id)),
        await repository.create(new_report(reporter, other_profile_id)),
    ]

    assert all(result is not None for result in results)
    assert await count_reports(session) == 3


async def test_a_closed_report_does_not_block_a_new_one_and_stays_for_history(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporter = await add_user(session, 1031)
    _, profile_id = await make_target(session, taxonomy_data, 1032)
    old = await repository.create(new_report(reporter, profile_id))
    await repository.close_open_for_profile(profile_id, ReportStatus.DISMISSED, NOW)

    new = await repository.create(new_report(reporter, profile_id))

    assert new is not None and new.id != old.id
    assert await count_reports(session) == 2
    assert await count_reports(session, status="dismissed") == 1
    assert (await repository.get_open_by_reporter_and_profile(reporter, profile_id)).id == new.id


async def test_the_database_itself_refuses_two_open_reports_for_one_pair(session, taxonomy_data):
    reporter = await add_user(session, 1041)
    _, profile_id = await make_target(session, taxonomy_data, 1042)
    insert = (
        "INSERT INTO reports (reporter_user_id, profile_id, reason, status, created_at) "
        f"VALUES ({reporter}, {profile_id}, 'spam', '{{status}}', now())"
    )
    await session.execute(text(insert.format(status="open")))

    with pytest.raises(IntegrityError):
        await session.execute(text(insert.format(status="open")))
    await session.rollback()


async def test_closed_reports_do_not_count_for_the_unique_index(session, taxonomy_data):
    reporter = await add_user(session, 1051)
    _, profile_id = await make_target(session, taxonomy_data, 1052)
    insert = (
        "INSERT INTO reports (reporter_user_id, profile_id, reason, status, created_at) "
        f"VALUES ({reporter}, {profile_id}, 'spam', '{{status}}', now())"
    )

    for status in ("dismissed", "resolved", "dismissed", "open"):
        await session.execute(text(insert.format(status=status)))
    await session.commit()

    assert await count_reports(session) == 4


async def test_status_defaults_to_open_and_resolved_at_to_null(session, taxonomy_data):
    reporter = await add_user(session, 1061)
    _, profile_id = await make_target(session, taxonomy_data, 1062)

    row = (
        await session.execute(
            text(
                "INSERT INTO reports (reporter_user_id, profile_id, reason, created_at) "
                f"VALUES ({reporter}, {profile_id}, 'other', now()) RETURNING status, resolved_at, comment"
            )
        )
    ).one()

    assert (row.status, row.resolved_at, row.comment) == ("open", None, None)


async def test_the_comment_column_holds_exactly_the_longest_allowed_comment(session, taxonomy_data):
    reporter = await add_user(session, 1071)
    _, profile_id = await make_target(session, taxonomy_data, 1072)
    repository = SqlAlchemyReportRepository(session)

    report = await repository.create(new_report(reporter, profile_id, comment="я" * 1000))
    await session.commit()

    assert len(report.comment) == 1000


async def test_the_expected_indexes_exist(session):
    rows = (
        await session.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'reports'")
        )
    ).all()
    definitions = {row.indexname: row.indexdef for row in rows}

    assert "UNIQUE" in definitions["uq_reports_open_per_reporter_profile"]
    assert "status = 'open'" in definitions["uq_reports_open_per_reporter_profile"]
    assert "(profile_id, status)" in definitions["ix_reports_profile_id_status"]


# ---------- cascades ----------


async def test_deleting_the_reporter_deletes_their_reports_and_nobody_elses(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporter, other = await add_user(session, 1081), await add_user(session, 1082)
    _, profile_id = await make_target(session, taxonomy_data, 1083)
    await repository.create(new_report(reporter, profile_id))
    await repository.create(new_report(other, profile_id))
    await session.commit()

    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": reporter})
    await session.commit()

    assert await count_reports(session) == 1
    assert await count_reports(session, reporter_user_id=other) == 1


async def test_deleting_the_profile_deletes_its_reports(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporter = await add_user(session, 1091)
    _, profile_id = await make_target(session, taxonomy_data, 1092)
    _, other_profile_id = await make_target(session, taxonomy_data, 1093)
    await repository.create(new_report(reporter, profile_id))
    await repository.create(new_report(reporter, other_profile_id))
    await session.commit()

    await session.execute(text("DELETE FROM profiles WHERE id = :id"), {"id": profile_id})
    await session.commit()

    assert await count_reports(session) == 1
    assert await count_reports(session, profile_id=other_profile_id) == 1


# ---------- visibility ----------


async def test_a_report_can_target_exactly_what_the_feed_would_show(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    viewer = await add_user(session, 1101)
    own_profile = await add_profile(session, viewer, taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)
    _, visible = await make_target(session, taxonomy_data, 1102)
    _, paused = await make_target(session, taxonomy_data, 1103, status=ProfileStatus.PAUSED)
    _, hidden = await make_target(session, taxonomy_data, 1104, status=ProfileStatus.HIDDEN_BY_ADMIN)
    _, banned_owner = await make_target(session, taxonomy_data, 1105, user_kwargs=dict(is_banned=True))
    _, no_username = await make_target(session, taxonomy_data, 1106, user_kwargs=dict(username=None))
    _, empty_username = await make_target(session, taxonomy_data, 1107, user_kwargs=dict(username=""))
    _, deleted_owner = await make_target(session, taxonomy_data, 1108, user_kwargs=dict(deleted=True))

    answers = {
        name: await repository.is_profile_visible_to(viewer, profile_id)
        for name, profile_id in dict(
            visible=visible,
            own=own_profile,
            paused=paused,
            hidden=hidden,
            banned_owner=banned_owner,
            no_username=no_username,
            empty_username=empty_username,
            deleted_owner=deleted_owner,
            missing=999_999,
        ).items()
    }

    assert answers == dict(
        visible=True,
        own=False,
        paused=False,
        hidden=False,
        banned_owner=False,
        no_username=False,
        empty_username=False,
        deleted_owner=False,
        missing=False,
    )


async def test_a_viewer_without_any_profile_or_username_may_still_see_a_profile(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    viewer = await add_user(session, 1111, username=None)
    _, profile_id = await make_target(session, taxonomy_data, 1112)

    assert await repository.is_profile_visible_to(viewer, profile_id) is True


# ---------- reading and closing ----------


async def test_list_open_by_profile_is_oldest_first_open_only_and_one_profile_only(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporters = [await add_user(session, 1120 + i) for i in range(4)]
    _, profile_id = await make_target(session, taxonomy_data, 1130)
    _, other_profile_id = await make_target(session, taxonomy_data, 1131)
    late = await repository.create(new_report(reporters[0], profile_id, minutes=5))
    early = await repository.create(new_report(reporters[1], profile_id, minutes=1))
    closed = await repository.create(new_report(reporters[2], profile_id, minutes=2))
    await repository.create(new_report(reporters[3], other_profile_id))
    await session.execute(text("UPDATE reports SET status = 'dismissed' WHERE id = :id"), {"id": closed.id})

    listed = await repository.list_open_by_profile(profile_id)

    assert [r.id for r in listed] == [early.id, late.id]


async def test_list_open_by_profile_breaks_ties_by_id(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporters = [await add_user(session, 1140 + i) for i in range(3)]
    _, profile_id = await make_target(session, taxonomy_data, 1150)
    created = [await repository.create(new_report(reporter, profile_id, minutes=3)) for reporter in reporters]

    listed = await repository.list_open_by_profile(profile_id)

    assert [r.id for r in listed] == sorted(r.id for r in created)


async def test_closing_a_profile_marks_every_open_report_and_returns_how_many(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporters = [await add_user(session, 1160 + i) for i in range(3)]
    _, profile_id = await make_target(session, taxonomy_data, 1170)
    _, other_profile_id = await make_target(session, taxonomy_data, 1171)
    await repository.create(new_report(reporters[0], profile_id))
    await repository.create(new_report(reporters[1], profile_id))
    await repository.create(new_report(reporters[2], other_profile_id))

    closed = await repository.close_open_for_profile(profile_id, ReportStatus.DISMISSED, NOW)

    assert closed == 2
    rows = (await session.execute(text("SELECT profile_id, status, resolved_at FROM reports ORDER BY id"))).all()
    assert [(r.profile_id, r.status) for r in rows] == [
        (profile_id, "dismissed"),
        (profile_id, "dismissed"),
        (other_profile_id, "open"),
    ]
    assert rows[0].resolved_at == NOW and rows[2].resolved_at is None
    assert await repository.close_open_for_profile(profile_id, ReportStatus.DISMISSED, NOW) == 0  # nothing left


async def test_closing_does_not_touch_reports_that_were_already_closed(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporters = [await add_user(session, 1180 + i) for i in range(2)]
    _, profile_id = await make_target(session, taxonomy_data, 1190)
    first = await repository.create(new_report(reporters[0], profile_id))
    await repository.close_open_for_profile(profile_id, ReportStatus.RESOLVED, T0)
    await repository.create(new_report(reporters[1], profile_id))

    await repository.close_open_for_profile(profile_id, ReportStatus.DISMISSED, NOW)

    row = (await session.execute(text("SELECT status, resolved_at FROM reports WHERE id = :id"), {"id": first.id})).one()
    assert (row.status, row.resolved_at) == ("resolved", T0)


async def test_resolving_for_a_user_covers_all_of_their_profiles_and_nobody_elses(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporter, other_reporter = await add_user(session, 1200), await add_user(session, 1201)
    owner, first_profile = await make_target(session, taxonomy_data, 1202)
    second_profile = await add_profile(
        session, owner, taxonomy_data.tech_category_id, taxonomy_data.design_role_id, status=ProfileStatus.PAUSED
    )
    _, someone_elses = await make_target(session, taxonomy_data, 1203)
    await repository.create(new_report(reporter, first_profile))
    await repository.create(new_report(reporter, second_profile))
    await repository.create(new_report(other_reporter, second_profile))
    await repository.create(new_report(reporter, someone_elses))

    resolved = await repository.resolve_open_for_user(owner, NOW)

    assert resolved == 3
    assert await count_reports(session, status="resolved") == 3
    assert await count_reports(session, status="open", profile_id=someone_elses) == 1
    assert await repository.resolve_open_for_user(owner, NOW) == 0


# ---------- the admin queue ----------


async def test_the_queue_is_ordered_by_count_then_oldest_then_profile_id(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    r1, r2, r3 = [await add_user(session, 1300 + i) for i in range(3)]
    _, p1 = await make_target(session, taxonomy_data, 1310)
    _, p2 = await make_target(session, taxonomy_data, 1311)
    _, p3 = await make_target(session, taxonomy_data, 1312)
    _, p4 = await make_target(session, taxonomy_data, 1313, status=ProfileStatus.PAUSED)
    _, p5 = await make_target(session, taxonomy_data, 1314, status=ProfileStatus.HIDDEN_BY_ADMIN)
    _, p6 = await make_target(session, taxonomy_data, 1315)  # only a closed report: not in the queue
    await repository.create(new_report(r1, p1, minutes=10))
    await repository.create(new_report(r2, p1, minutes=11))
    await repository.create(new_report(r1, p2, minutes=1))
    await repository.create(new_report(r2, p2, minutes=2))
    await repository.create(new_report(r1, p3, minutes=5))
    await repository.create(new_report(r2, p4, minutes=5))  # same oldest as p3: the lower profile id goes first
    await repository.create(new_report(r3, p5, minutes=20))
    await repository.create(new_report(r1, p6))
    await repository.close_open_for_profile(p6, ReportStatus.DISMISSED, NOW)

    groups = await repository.list_open_groups(limit=50, offset=0)

    # p2 (2 reports, oldest at +1) before p1 (2 reports, oldest at +10); then the single reports by age and id.
    assert [g.profile_id for g in groups] == [p2, p1, p3, p4, p5]
    assert await repository.count_open_groups() == 5
    assert groups[3].profile_status is ProfileStatus.PAUSED  # an admin sees paused and hidden profiles too
    assert groups[4].profile_status is ProfileStatus.HIDDEN_BY_ADMIN


async def test_a_group_carries_the_owner_the_counts_and_the_dates(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporters = [await add_user(session, 1320 + i) for i in range(3)]
    owner, profile_id = await make_target(session, taxonomy_data, 1330, user_kwargs=dict(is_banned=True))
    await repository.create(new_report(reporters[0], profile_id, minutes=3))
    await repository.create(new_report(reporters[1], profile_id, minutes=9))
    await repository.create(new_report(reporters[2], profile_id, minutes=6))

    [group] = await repository.list_open_groups(limit=50, offset=0)

    assert group.profile_id == profile_id
    assert (group.category_id, group.role_id) == (taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)
    assert group.owner_user_id == owner
    assert (group.owner_first_name, group.owner_username, group.owner_is_banned) == ("User1330", "someone", True)
    assert group.open_count == 3
    assert group.oldest_at == T0 + timedelta(minutes=3)
    assert group.newest_at == T0 + timedelta(minutes=9)


async def test_the_breakdown_by_reason_always_has_all_five_reasons(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporters = [await add_user(session, 1340 + i) for i in range(4)]
    _, spammed = await make_target(session, taxonomy_data, 1350)
    _, mixed = await make_target(session, taxonomy_data, 1351)
    await repository.create(new_report(reporters[0], spammed, ReportReason.SPAM))
    await repository.create(new_report(reporters[1], spammed, ReportReason.SPAM))
    await repository.create(new_report(reporters[0], mixed, ReportReason.FAKE))
    await repository.create(new_report(reporters[1], mixed, ReportReason.OTHER))
    await repository.create(new_report(reporters[2], mixed, ReportReason.OTHER))
    await repository.create(new_report(reporters[3], mixed, ReportReason.INAPPROPRIATE_CONTACT))

    groups = {g.profile_id: g for g in await repository.list_open_groups(limit=50, offset=0)}

    assert set(groups[spammed].reasons) == set(ReportReason)
    assert groups[spammed].reasons == {
        ReportReason.SPAM: 2,
        ReportReason.FAKE: 0,
        ReportReason.OFFENSIVE: 0,
        ReportReason.INAPPROPRIATE_CONTACT: 0,
        ReportReason.OTHER: 0,
    }
    assert groups[mixed].reasons == {
        ReportReason.SPAM: 0,
        ReportReason.FAKE: 1,
        ReportReason.OFFENSIVE: 0,
        ReportReason.INAPPROPRIATE_CONTACT: 1,
        ReportReason.OTHER: 2,
    }
    assert sum(groups[mixed].reasons.values()) == groups[mixed].open_count


async def test_closed_reports_are_left_out_of_the_counts_and_the_breakdown(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporters = [await add_user(session, 1360 + i) for i in range(3)]
    _, profile_id = await make_target(session, taxonomy_data, 1370)
    await repository.create(new_report(reporters[0], profile_id, ReportReason.SPAM))
    await repository.close_open_for_profile(profile_id, ReportStatus.RESOLVED, NOW)
    await repository.create(new_report(reporters[0], profile_id, ReportReason.FAKE, minutes=30))
    await repository.create(new_report(reporters[1], profile_id, ReportReason.FAKE, minutes=40))

    [group] = await repository.list_open_groups(limit=50, offset=0)

    assert group.open_count == 2
    assert group.reasons[ReportReason.SPAM] == 0 and group.reasons[ReportReason.FAKE] == 2
    assert group.oldest_at == T0 + timedelta(minutes=30)


async def test_pages_of_the_queue_do_not_overlap_and_add_up_to_the_whole(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)
    reporter = await add_user(session, 1380)
    profile_ids = []
    for i in range(5):
        _, profile_id = await make_target(session, taxonomy_data, 1390 + i)
        profile_ids.append(profile_id)
        await repository.create(new_report(reporter, profile_id, minutes=7))  # identical keys: only the id decides

    whole = [g.profile_id for g in await repository.list_open_groups(limit=50, offset=0)]
    paged = []
    for offset in (0, 2, 4):
        paged += [g.profile_id for g in await repository.list_open_groups(limit=2, offset=offset)]

    assert whole == sorted(profile_ids)
    assert paged == whole
    assert await repository.list_open_groups(limit=2, offset=100) == []


async def test_an_empty_queue_is_an_empty_list_and_zero(session, taxonomy_data):
    repository = SqlAlchemyReportRepository(session)

    assert await repository.list_open_groups(limit=50, offset=0) == []
    assert await repository.count_open_groups() == 0
