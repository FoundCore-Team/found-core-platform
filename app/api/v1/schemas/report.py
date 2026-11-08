from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.api.v1.schemas.common import Int64Id, ReportComment
from app.domain.enums import ReportReason


class ReportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: Int64Id
    reason: ReportReason
    comment: ReportComment | None = None


class ReportSchema(BaseModel):
    id: int
    profile_id: int
    reason: ReportReason
    comment: str | None
    created_at: datetime
