"""文件业务层：按账号查询既有私人附件和行程版本，不复制原件或新增行程文件表。"""

import re
from uuid import UUID

from sqlalchemy import Engine, Integer, case, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.models.attachment import ConversationAttachment
from app.models.requirement_turn import RequirementTurn
from app.models.trip import Itinerary, TravelRequest, TravelSession
from app.schemas.itinerary import PlanSnapshot, TravelPlan
from app.schemas.personal_files import (
    FileKind,
    PersonalFileDetail,
    PersonalFileItem,
    PersonalFilePage,
)
from app.schemas.requirement.base import TravelRequestExtraction
from app.services.attachment.storage import attachment_view
from app.services.trip_service import SessionNotFoundError

"""文件名函数：下载名称只来自展示标题，剔除控制字符和常见文件系统保留字符。"""


def plan_filename(title: str, version: int) -> str:
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title).strip() or "旅行行程"
    return f"{name}-v{version}.md"


"""行程条目函数：将同一会话的历史版本转换为文件列表元数据。"""


def itinerary_item(row: Itinerary, trip: TravelSession, count: int) -> PersonalFileItem:
    return PersonalFileItem(
        id=row.id,
        kind="itinerary",
        file_name=plan_filename(str(row.itinerary_json["title"]), row.version),
        mime_type="text/markdown",
        size_bytes=None,
        session_id=trip.id,
        session_title=trip.title,
        created_at=row.created_at,
        version=row.version,
        version_count=count,
    )


class PersonalFileService:
    """个人文件服务类：所有入口都按传入的已认证账号过滤，不授予管理员额外读取权。"""

    """初始化函数：借用应用连接池，不持有文件副本。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    """分页函数：数据库合并两类文件并排序分页，每个会话仅列最新逐日行程。"""

    def list(
        self,
        user_id: UUID,
        *,
        session_id: UUID | None = None,
        kind: FileKind | None = None,
        q: str = "",
        offset: int = 0,
        limit: int = 30,
    ) -> PersonalFilePage:
        conditions = [TravelSession.user_id == user_id]
        if session_id is not None:
            conditions.append(TravelSession.id == session_id)
        attachment = (
            select(
                ConversationAttachment.id,
                literal("attachment").label("kind"),
                ConversationAttachment.file_name,
                ConversationAttachment.mime_type,
                ConversationAttachment.size_bytes,
                TravelSession.id.label("session_id"),
                TravelSession.title.label("session_title"),
                ConversationAttachment.created_at,
                cast(literal(None), Integer).label("version"),
                cast(literal(None), Integer).label("version_count"),
            )
            .join(TravelSession, ConversationAttachment.session_id == TravelSession.id)
            .where(*conditions)
        )
        plans = (
            select(
                Itinerary.id,
                literal("itinerary").label("kind"),
                Itinerary.itinerary_json["title"].as_string().label("file_name"),
                literal("text/markdown").label("mime_type"),
                cast(literal(None), Integer).label("size_bytes"),
                TravelSession.id.label("session_id"),
                TravelSession.title.label("session_title"),
                Itinerary.created_at,
                Itinerary.version,
                func.count().over(partition_by=Itinerary.session_id).label("version_count"),
                func.row_number()
                .over(partition_by=Itinerary.session_id, order_by=Itinerary.version.desc())
                .label("position"),
            )
            .join(TravelSession, Itinerary.session_id == TravelSession.id)
            .where(
                *conditions,
                Itinerary.itinerary_json["format"].as_string() == "daily-plan-v1",
            )
            .subquery()
        )
        latest = select(*(plans.c[name] for name in attachment.selected_columns.keys())).where(
            plans.c.position == 1,
        )
        files = union_all(attachment, latest).subquery()
        query = select(files)
        if kind:
            query = query.where(files.c.kind == kind)
        if q.strip():
            # 用户输入按普通文本匹配，百分号和下划线不变成SQL通配符。
            filename = case(
                (files.c.kind == "itinerary", func.concat(
                    files.c.file_name, "-v", files.c.version, ".md",
                )),
                else_=files.c.file_name,
            )
            query = query.where(
                or_(
                    filename.icontains(q.strip(), autoescape=True),
                    files.c.session_title.icontains(q.strip(), autoescape=True),
                )
            )
        with Session(self.engine) as unit:
            if (
                session_id is not None
                and unit.scalar(select(TravelSession.id).where(*conditions)) is None
            ):
                raise SessionNotFoundError("旅行会话不存在")
            total = unit.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = (
                unit.execute(
                    query.order_by(files.c.created_at.desc(), files.c.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            items = [PersonalFileItem.model_validate(dict(row)) for row in rows]
        for item in items:
            if item.kind == "itinerary":
                item.file_name = plan_filename(item.file_name, item.version or 1)
        return PersonalFilePage(items=items, total=total, offset=offset, limit=limit)

    """详情函数：重新校验文件所属账号，再返回其原件信息或版本及对应的需求快照。"""

    def detail(self, user_id: UUID, kind: FileKind, identifier: UUID) -> PersonalFileDetail:
        with Session(self.engine) as unit:
            if kind == "attachment":
                result = unit.execute(
                    select(ConversationAttachment, TravelSession)
                    .join(
                        TravelSession,
                        ConversationAttachment.session_id == TravelSession.id,
                    )
                    .where(
                        ConversationAttachment.id == identifier, TravelSession.user_id == user_id
                    )
                ).first()
                if result is None:
                    raise SessionNotFoundError("文件不存在")
                attachment, trip = result
                view = attachment_view(attachment)
                return PersonalFileDetail(
                    item=PersonalFileItem(
                        id=view.id,
                        kind="attachment",
                        file_name=view.file_name,
                        mime_type=view.mime_type,
                        size_bytes=view.size_bytes,
                        session_id=trip.id,
                        session_title=trip.title,
                        created_at=view.created_at,
                    ),
                    attachment=view,
                )
            plan_result = unit.execute(
                select(Itinerary, TravelSession, TravelRequest)
                .join(
                    TravelSession,
                    Itinerary.session_id == TravelSession.id,
                )
                .join(TravelRequest, Itinerary.request_id == TravelRequest.id)
                .where(
                    Itinerary.id == identifier,
                    TravelSession.user_id == user_id,
                    Itinerary.itinerary_json["format"].as_string() == "daily-plan-v1",
                )
            ).first()
            if plan_result is None:
                raise SessionNotFoundError("文件不存在")
            itinerary, trip, requirement = plan_result
            versions = unit.scalars(
                select(Itinerary)
                .where(
                    Itinerary.session_id == trip.id,
                    Itinerary.itinerary_json["format"].as_string() == "daily-plan-v1",
                )
                .order_by(Itinerary.version.desc())
            ).all()
            snapshot = unit.scalar(
                select(RequirementTurn.response_json["itinerary"])
                .where(
                    RequirementTurn.session_id == trip.id,
                    RequirementTurn.response_json["itinerary"]["itinerary_id"].as_string()
                    == str(identifier),
                )
                .order_by(RequirementTurn.revision.desc())
                .limit(1)
            )
            # 早期直接保存的逐日草稿可能没有聊天快照，仍可查看，但不提供撤销操作。
            plan = (
                PlanSnapshot.model_validate(snapshot)
                if snapshot
                else PlanSnapshot(
                    itinerary_id=itinerary.id,
                    version=itinerary.version,
                    operation="create" if itinerary.version == 1 else "modify",
                    plan=TravelPlan.model_validate(itinerary.itinerary_json),
                    changes=[],
                )
            )
            return PersonalFileDetail(
                item=itinerary_item(itinerary, trip, len(versions)),
                itinerary=plan,
                extraction=TravelRequestExtraction.model_validate(requirement.request_json),
                versions=[itinerary_item(row, trip, len(versions)) for row in versions],
            )


"""行程导出函数：把所选已保存版本转换为Markdown，未知日期和估算费用明确标注。"""


def itinerary_markdown(detail: PersonalFileDetail) -> bytes:
    assert detail.itinerary is not None and detail.extraction is not None
    snapshot, request = detail.itinerary, detail.extraction
    plan = snapshot.plan
    lines = [
        f"# {plan.title}",
        "",
        f"来源会话：{detail.item.session_title}",
        f"会话编号：{detail.item.session_id}",
        f"行程版本：{snapshot.version}",
        f"保存时间：{detail.item.created_at.isoformat()}",
        f"出发地：{request.origin or '待定'}",
        f"目的地：{plan.destination}",
        "",
    ]
    for day in plan.days:
        lines.extend([f"## 第{day.day}天 · {day.date or '日期待定'}", ""])
        for activity in day.activities:
            lines.append(
                f"- {activity.start_time} {activity.place.map.name}，"
                f"游览{activity.duration_minutes}分钟，交通预留{activity.transfer_minutes}分钟"
            )
            for source in activity.place.sources:
                lines.append(
                    f"  - 来源：{source.title}" + (f" — {source.url}" if source.url else "")
                )
        lines.append("")
    lines.extend(
        [
            "## 预算估算",
            "",
            f"总预算：{plan.budget.total_budget}元",
            f"预计支出：{plan.budget.total}元（估算，非实时成交价格）",
            "",
        ]
    )
    for label, values in (
        ("饮食偏好", request.dietary),
        ("住宿偏好", request.lodging_preferences),
        ("必须遵守", request.hard_constraints),
        ("排除项目", request.excluded_items),
    ):
        if values:
            lines.append(f"{label}：{'、'.join(values)}")
    lines.extend(
        ["", "## 提醒", "", *[f"- {value}" for value in plan.warnings + plan.budget.assumptions]]
    )
    return ("\n".join(lines) + "\n").encode("utf-8")
