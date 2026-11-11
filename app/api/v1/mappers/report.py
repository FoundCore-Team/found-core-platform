from app.api.v1.schemas.report import ReportSchema
from app.domain.entities import ReportEntity


def map_report_entity_to_report_schema(report: ReportEntity) -> ReportSchema:
    return ReportSchema(
        id=report.id,
        profile_id=report.profile_id,
        reason=report.reason,
        comment=report.comment,
        created_at=report.created_at,
    )
