"""接口验收层：验证个人文件分页、行程版本和真实登录账号之间的隔离。"""

from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import COOKIE_NAME
from app.config import Settings
from app.main import create_app
from app.models.trip import TravelSession
from app.schemas.itinerary import PlanSnapshot, RouteEstimate
from app.schemas.personal_files import PersonalFileDetail, PersonalFileItem
from app.services.attachment.storage import AttachmentService
from app.services.auth import AuthService
from app.services.itinerary.rules import build_plan
from app.services.personal_files import itinerary_markdown
from app.services.trip_service import TripService
from tests.test_itinerary_agent import places, proposal, requirements
from tests.test_itinerary_history import save_two

"""完整导出测试函数：文件保留细节和证据，不能只导出聊天卡片上的概览。"""

def test_markdown_keeps_full_saved_plan():
    request = requirements()
    plan = build_plan(request, [proposal(1, "p1"), proposal(2, "p2")], places(), None, None)
    activity = plan.days[0].activities[0]
    activity.place.map.address = "验收地址"
    activity.place.sources[0].text = "来源的完整正文"
    activity.route = RouteEstimate(status="estimated", duration_minutes=12, distance_m=800)
    identifier = uuid4()
    detail = PersonalFileDetail(item=PersonalFileItem(
        id=identifier, kind="itinerary", file_name="行程.md", mime_type="text/markdown",
        size_bytes=None, session_id=uuid4(), session_title="验收",
        created_at=datetime.now(timezone.utc),
    ), itinerary=PlanSnapshot(itinerary_id=identifier, version=1, operation="create",
                             plan=plan, changes=[]), extraction=request)
    content = itinerary_markdown(detail).decode()
    assert "验收地址" in content and "来源的完整正文" in content
    assert "12分钟" in content and "800米" in content
    assert "预算明细" in content and "预备金" in content

"""个人文件环境函数：创建真实账号、不同归属会话和未发送的同名附件。"""


@pytest.fixture
def files_context(store_engine, tmp_path):
    auth = AuthService(store_engine)
    owner = auth.create_user("files-owner", "secret12")
    admin = auth.create_user("files-admin", "secret12", role="admin")
    trip, history, first, second, undo = save_two(store_engine, owner.id)
    restored = history.undo(trip.id, undo, "files-undo")
    other = TripService(store_engine).create_session("另一场旅行", user_id=owner.id)
    foreign = TripService(store_engine).create_session("管理员私人会话", user_id=admin.id)
    storage = AttachmentService(store_engine, tmp_path / "conversations")
    attachment = storage.upload(trip.id, BytesIO(b"first"), "同名攻略.txt", "text/plain")
    same_name = storage.upload(other.id, BytesIO(b"second"), "同名攻略.txt", "text/plain")
    hidden = storage.upload(foreign.id, BytesIO(b"private"), "隐藏攻略.txt", "text/plain")
    # 老预算草稿应保留在原流程中，但不得作为逐日行程文件列出。
    TripService(store_engine).save_draft(other.id, request_json={}, itinerary_json={"budget": {}})
    app = create_app(Settings(document_upload_dir=tmp_path))
    app.state.database_engine = store_engine
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, auth.login("files-owner", "secret12")[1])
    return client, auth, trip, other, foreign, attachment, same_name, hidden, first, restored


"""分页测试函数：按数据库身份聚合文件，同名保留两条且撤销后的最新版本只列一次。"""


def test_personal_files_pagination_search_and_latest_undo(files_context):
    client, _, trip, _, _, attachment, same_name, _, _, restored = files_context
    response = client.get("/api/v1/personal-files", params={"limit": 2})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    page = response.json()
    assert page["total"] == 3 and len(page["items"]) == 2
    second_page = client.get("/api/v1/personal-files", params={"offset": 2, "limit": 2}).json()
    assert len(second_page["items"]) == 1
    assert len({item["id"] for item in page["items"] + second_page["items"]}) == 3
    matches = client.get(
        "/api/v1/personal-files", params={"q": "同名", "kind": "attachment"}
    ).json()
    assert {row["id"] for row in matches["items"]} == {str(attachment.id), str(same_name.id)}
    scoped = client.get("/api/v1/personal-files", params={"session_id": str(trip.id)}).json()
    plan = next(item for item in scoped["items"] if item["kind"] == "itinerary")
    assert scoped["total"] == 2
    assert plan["id"] == str(restored.response.itinerary.itinerary_id)
    assert plan["version"] == 3 and plan["version_count"] == 3
    assert plan["session_title"] == "杭州两日"
    by_filename = client.get("/api/v1/personal-files", params={"q": plan["file_name"]}).json()
    assert by_filename["total"] == 1
    assert by_filename["items"][0]["id"] == plan["id"]
    assert client.get("/api/v1/personal-files", params={"q": "%"}).json()["total"] == 0
    assert client.get("/api/v1/personal-files", params={"kind": "document"}).status_code == 422
    assert client.get("/api/v1/personal-files", params={"limit": 101}).status_code == 422


"""详情下载测试函数：读取既有需求和历史版本，Markdown反映所选版本而非当前会话。"""


def test_personal_file_details_versions_and_download(files_context, store_engine, tmp_path):
    client, _, trip, _, _, attachment, _, _, first, restored = files_context
    identifier = str(restored.response.itinerary.itinerary_id)
    detail = client.get(f"/api/v1/personal-files/itinerary/{identifier}").json()
    assert detail["itinerary"]["operation"] == "undo"
    assert detail["extraction"]["dietary"] == ["不吃辣"]
    assert [row["version"] for row in detail["versions"]] == [3, 2, 1]
    original = client.get(
        f"/api/v1/personal-files/itinerary/{first.response.itinerary.itinerary_id}"
    ).json()
    assert original["itinerary"]["version"] == 1
    download = client.get(f"/api/v1/personal-files/itinerary/{identifier}/download")
    assert download.status_code == 200
    assert "杭州两日" in download.text and "西湖" in download.text and "上海" in download.text
    assert "09:00" in download.text and "10:00" not in download.text
    assert "attachment;" in download.headers["Content-Disposition"]
    previous_id = detail["versions"][1]["id"]
    previous_download = client.get(f"/api/v1/personal-files/itinerary/{previous_id}/download")
    assert "10:00" in previous_download.text and "素食" in previous_download.text
    assert "行程版本：2" in previous_download.text
    attachment_detail = client.get(f"/api/v1/personal-files/attachment/{attachment.id}").json()
    assert attachment_detail["attachment"]["status"] == "uploaded"
    assert attachment_detail["versions"] == []
    assert (
        client.get(f"/api/v1/sessions/{trip.id}/attachments/{attachment.id}/content").content
        == b"first"
    )
    # 标题是来源会话的当前标题；删除会话后不能通过个人文件地址继续看到旧记录。
    with Session(store_engine) as unit:
        current = unit.get(TravelSession, trip.id)
        owner_id = current.user_id
        current.title = "修改后的来源标题"
        unit.commit()
    assert (
        client.get(f"/api/v1/personal-files/itinerary/{identifier}").json()["item"]["session_title"]
        == "修改后的来源标题"
    )
    TripService(store_engine, attachment_dir=tmp_path / "conversations").delete_session(
        trip.id, user_id=owner_id,
    )
    assert client.get("/api/v1/personal-files").json()["total"] == 1
    assert client.get(f"/api/v1/personal-files/itinerary/{identifier}").status_code == 404
    assert client.get(f"/api/v1/personal-files/attachment/{attachment.id}").status_code == 404


"""隔离测试函数：匿名与越权账号不能从列表、详情或下载看到其他用户的文件。"""


def test_personal_files_enforce_owner_even_admin(files_context):
    client, auth, trip, _, foreign, attachment, _, hidden, first, _ = files_context
    assert (
        client.get("/api/v1/personal-files", params={"session_id": str(foreign.id)}).status_code
        == 404
    )
    assert client.get(f"/api/v1/personal-files/attachment/{hidden.id}").status_code == 404
    client.cookies.set(COOKIE_NAME, auth.login("files-admin", "secret12")[1])
    assert client.get("/api/v1/personal-files").json()["total"] == 1
    for suffix in ("", "/download"):
        assert (
            client.get(
                f"/api/v1/personal-files/itinerary/{first.response.itinerary.itinerary_id}{suffix}"
            ).status_code
            == 404
        )
    assert client.get(f"/api/v1/personal-files/attachment/{attachment.id}").status_code == 404
    assert (
        client.get(f"/api/v1/sessions/{trip.id}/attachments/{attachment.id}/content").status_code
        == 404
    )
    client.cookies.clear()
    assert client.get("/api/v1/personal-files").status_code == 401
