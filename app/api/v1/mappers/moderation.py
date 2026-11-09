from app.api.v1.mappers.taxonomy import map_tag_entity_to_tag_schema
from app.api.v1.schemas.moderation import (
    AdminActionPageSchema,
    AdminActionSchema,
    AdminProfileSchema,
    AdminReportSchema,
    AdminTagSchema,
    AdminUserSchema,
    PendingTagPageSchema,
    PendingTagSchema,
    ProfileReportsSchema,
    ReportGroupPageSchema,
    ReportGroupSchema,
    ReportOwnerSchema,
    TagCreatorSchema,
    TagModerationResultSchema,
    TagScopeSchema,
)
from app.domain.entities import (
    AdminActionEntity,
    PendingTagEntity,
    ProfileEntity,
    ReportEntity,
    ReportGroupEntity,
    TagModerationResult,
    UserEntity,
)


def map_profile_entity_to_admin_profile_schema(profile: ProfileEntity) -> AdminProfileSchema:
    return AdminProfileSchema(
        id=profile.id,
        user_id=profile.user_id,
        category_id=profile.category_id,
        role_id=profile.role_id,
        status=profile.status,
        country_code=profile.country_code,
        timezone=profile.timezone,
        bio=profile.bio,
        goals_description=profile.goals_description,
        extra_attributes=profile.extra_attributes,
        tags=[map_tag_entity_to_tag_schema(tag) for tag in profile.tags],
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def map_report_group_entity_to_report_group_schema(group: ReportGroupEntity) -> ReportGroupSchema:
    return ReportGroupSchema(
        profile_id=group.profile_id,
        profile_status=group.profile_status,
        category_id=group.category_id,
        role_id=group.role_id,
        owner=ReportOwnerSchema(
            user_id=group.owner_user_id,
            first_name=group.owner_first_name,
            username=group.owner_username,
            is_banned=group.owner_is_banned,
        ),
        open_count=group.open_count,
        oldest_at=group.oldest_at,
        newest_at=group.newest_at,
        reasons=group.reasons,
    )


def map_report_groups_to_report_group_page_schema(
    groups: list[ReportGroupEntity], total: int, limit: int, offset: int
) -> ReportGroupPageSchema:
    return ReportGroupPageSchema(
        items=[map_report_group_entity_to_report_group_schema(group) for group in groups],
        total=total,
        limit=limit,
        offset=offset,
    )


def map_report_entity_to_admin_report_schema(report: ReportEntity) -> AdminReportSchema:
    return AdminReportSchema(
        id=report.id,
        reporter_user_id=report.reporter_user_id,
        reason=report.reason,
        comment=report.comment,
        created_at=report.created_at,
    )


def map_profile_reports_to_profile_reports_schema(
    profile: ProfileEntity, owner: UserEntity, reports: list[ReportEntity]
) -> ProfileReportsSchema:
    return ProfileReportsSchema(
        profile=map_profile_entity_to_admin_profile_schema(profile),
        owner=map_user_entity_to_admin_user_schema(owner),
        reports=[map_report_entity_to_admin_report_schema(report) for report in reports],
    )


def map_user_entity_to_admin_user_schema(user: UserEntity) -> AdminUserSchema:
    return AdminUserSchema(
        id=user.id,
        telegram_id=user.telegram_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        is_admin=user.is_admin,
        is_banned=user.is_banned,
        ban_reason=user.ban_reason,
        created_at=user.created_at,
        last_active_at=user.last_active_at,
    )


def map_pending_tags_to_pending_tag_page_schema(
    tags: list[PendingTagEntity], total: int, limit: int, offset: int
) -> PendingTagPageSchema:
    return PendingTagPageSchema(
        items=[
            PendingTagSchema(
                id=item.tag.id,
                title=item.tag.title,
                normalized_title=item.tag.normalized_title,
                created_at=item.created_at,
                usage_count=item.tag.usage_count,
                profiles_count=item.profiles_count,
                creator=(
                    TagCreatorSchema(
                        user_id=item.creator.user_id,
                        first_name=item.creator.first_name,
                        username=item.creator.username,
                    )
                    if item.creator
                    else None
                ),
                scopes=[TagScopeSchema(category_id=s.category_id, role_id=s.role_id) for s in item.scopes],
            )
            for item in tags
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def map_tag_moderation_result_to_tag_moderation_result_schema(
    result: TagModerationResult,
) -> TagModerationResultSchema:
    return TagModerationResultSchema(
        tag=AdminTagSchema(
            id=result.tag.id,
            title=result.tag.title,
            normalized_title=result.tag.normalized_title,
            status=result.tag.status,
            merged_into_tag_id=result.tag.merged_into_tag_id,
            usage_count=result.tag.usage_count,
        ),
        affected_profiles=result.affected_profiles,
    )


def map_admin_action_entity_to_admin_action_schema(action: AdminActionEntity) -> AdminActionSchema:
    return AdminActionSchema(
        id=action.id,
        actor_user_id=action.actor_user_id,
        action=action.action,
        target_type=action.target_type,
        target_id=action.target_id,
        reason=action.reason,
        details=action.details,
        created_at=action.created_at,
    )


def map_admin_actions_to_admin_action_page_schema(
    actions: list[AdminActionEntity], total: int, limit: int, offset: int
) -> AdminActionPageSchema:
    return AdminActionPageSchema(
        items=[map_admin_action_entity_to_admin_action_schema(action) for action in actions],
        total=total,
        limit=limit,
        offset=offset,
    )
