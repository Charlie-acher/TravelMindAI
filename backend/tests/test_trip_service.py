"""业务存储集成测试：真实验证提交、回滚、版本和并发。

每个测试使用独立的临时 schema；连接关闭后重新读取，不能依赖 ORM 内存缓存。
只有显式设置 TRAVELMIND_TEST_DATABASE_URL 才连接数据库，默认自动跳过。
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.trip import Itinerary, TravelRequest, TravelSession
from tests.helpers import TEST_USER_ID

pytestmark = pytest.mark.postgres


"""通过存储方法保存，再用另一实例读取，保留中文、金额字符串和关联 ID。"""

def test_save_and_read_using_new_store(store_engine: Engine) -> None:
    from app.services.trip_service import TripService

    store = TripService(store_engine)
    trip = store.create_session("  杭州三日游  ", user_id=TEST_USER_ID)
    saved = store.save_draft(
        trip.id,
        request_json={"days": 3, "total_budget": "5000.01"},
        itinerary_json={"title": "西湖漫步", "total": "2442.00"},
    )
    # 关闭池中的连接，新实例重新建立连接，不能依靠上一实例的连接或对象。
    store_engine.dispose()
    reader = TripService(store_engine)
    loaded_trip = reader.get_session(trip.id)
    loaded = reader.get_draft(trip.id)
    assert loaded_trip is not None and loaded is not None
    assert loaded_trip.title == "杭州三日游"
    assert loaded_trip.thread_id == trip.thread_id
    assert loaded.itinerary.id == saved.itinerary.id
    assert loaded.requirement.id == loaded.itinerary.request_id
    assert loaded.requirement.request_json == {"days": 3, "total_budget": "5000.01"}
    assert loaded.itinerary.itinerary_json == {"title": "西湖漫步", "total": "2442.00"}
    assert loaded.itinerary.status == "draft"
    assert loaded_trip.updated_at >= trip.created_at


"""保存第二版不会覆盖第一版；查询最新版本和历史版本得到不同快照。"""

def test_versions_and_sessions_are_isolated(store_engine: Engine) -> None:
    from app.services.trip_service import TripService

    store = TripService(store_engine)
    first = store.create_session("甲", user_id=TEST_USER_ID)
    second = store.create_session("乙", user_id=TEST_USER_ID)
    for destination in ["杭州", "苏州"]:
        store.save_draft(first.id, request_json={"destination": destination}, itinerary_json={})
    other = store.save_draft(second.id, request_json={"destination": "北京"}, itinerary_json={})
    original = store.get_draft(first.id, version=1)
    latest = store.get_draft(first.id)
    assert original is not None and latest is not None
    assert original.requirement.request_json["destination"] == "杭州"
    assert latest.requirement.request_json["destination"] == "苏州"
    assert latest.itinerary.version == 2
    assert latest.requirement.version == 2
    assert other.itinerary.version == 1
    assert store.get_draft(second.id, version=2) is None
    assert store.get_session(uuid4()) is None
    assert store.get_draft(uuid4()) is None


"""草稿 INSERT 被数据库拒绝时，已 flush 的需求也必须回滚，且不占用版本号。"""

def test_failed_draft_rolls_back_requirement_and_timestamp(store_engine: Engine) -> None:
    from app.services.trip_service import TripService

    store = TripService(store_engine)
    trip = store.create_session("回滚测试", user_id=TEST_USER_ID)
    # JSON 数组可以被驱动序列化，但会违反表的“必须为对象”约束。
    # 故意越过类型提示，模拟存储调用方的程序错误，不修改生产代码来制造失败。
    with pytest.raises(IntegrityError):
        store.save_draft(trip.id, request_json={"days": 3}, itinerary_json=[])
    with Session(store_engine) as reader:
        assert reader.scalar(select(func.count()).select_from(TravelRequest)) == 0
        assert reader.scalar(select(func.count()).select_from(Itinerary)) == 0
    unchanged = store.get_session(trip.id)
    assert unchanged is not None and unchanged.updated_at == trip.updated_at
    saved = store.save_draft(trip.id, request_json={}, itinerary_json={})
    assert saved.requirement.version == saved.itinerary.version == 1


"""保存到不存在的会话时，给出明确异常，不能自动创建不明来源的会话。"""

def test_missing_session_is_rejected(store_engine: Engine) -> None:
    from app.services.trip_service import SessionNotFoundError, TripService

    with pytest.raises(SessionNotFoundError):
        TripService(store_engine).save_draft(uuid4(), request_json={}, itinerary_json={})


"""会话标题先去首尾空白，再检查长度；错误标题不能写入数据库。"""

@pytest.mark.parametrize("title", ["", "   ", "旅" * 201])
def test_invalid_title_is_rejected(store_engine: Engine, title: str) -> None:
    from app.services.trip_service import TripService

    with pytest.raises(ValueError):
        TripService(store_engine).create_session(title, user_id=TEST_USER_ID)
    with Session(store_engine) as reader:
        assert reader.scalar(select(func.count()).select_from(TravelSession)) == 0


"""两个线程同时保存同一会话，各自得到唯一版本，两组快照都完整提交。"""

def test_concurrent_saves_allocate_distinct_versions(store_engine: Engine) -> None:
    from app.services.trip_service import TripService

    store = TripService(store_engine)
    trip = store.create_session("并发测试", user_id=TEST_USER_ID)
    ready = Barrier(2)

    """两个任务就绪后一起调用；TripService 内部必须为每次调用创建独立 Session。"""

    def save_one(number: int) -> int:
        ready.wait(timeout=10)
        return store.save_draft(
            trip.id, request_json={"number": number}, itinerary_json={"number": number}
        ).itinerary.version

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(executor.map(save_one, [1, 2]))
    assert sorted(versions) == [1, 2]
    for version in versions:
        saved = store.get_draft(trip.id, version=version)
        assert saved is not None
        assert saved.requirement.version == version
        assert saved.requirement.request_json["number"] == saved.itinerary.itinerary_json["number"]


"""调用方修改嵌套字典后，不影响已保存的快照，也不改变方法返回的快照内容。"""

def test_saved_snapshot_does_not_share_input_dictionaries(store_engine: Engine) -> None:
    from app.services.trip_service import TripService

    store = TripService(store_engine)
    trip = store.create_session("快照隔离", user_id=TEST_USER_ID)
    content = {"activities": ["西湖"]}
    saved = store.save_draft(trip.id, request_json={}, itinerary_json=content)
    content["activities"].append("修改后的值")
    assert saved.itinerary.itinerary_json == {"activities": ["西湖"]}
    reloaded = store.get_draft(trip.id)
    assert reloaded is not None
    assert reloaded.itinerary.itinerary_json == {"activities": ["西湖"]}


"""终端示例先保存，再用独立进程按 ID 读取；证明不是进程内对象缓存。"""

def test_demo_can_save_then_read_in_another_process(
    store_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    import subprocess
    import sys

    from scripts.demo_trip import main

    monkeypatch.delenv("TRAVELMIND_DATABASE_URL", raising=False)
    config_file = tmp_path / ".env"
    # 地址含临时 schema，两个进程都只访问当前测试空间；不在输出中显示此地址。
    config_file.write_text(
        f"TRAVELMIND_DATABASE_URL={store_engine.url.render_as_string(hide_password=False)}\n",
        encoding="utf-8",
    )
    assert main(["--env-file", str(config_file), "--user-id", str(TEST_USER_ID)]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["version"] == 1
    assert saved["budget"]["total"] == "2442.00"
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "scripts.demo_trip",
            "--env-file",
            str(config_file),
            "--session-id",
            saved["session_id"],
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == saved
    with Session(store_engine) as reader:
        # 带 --session-id 的第二次执行只能读取，不能额外写入一份演示数据。
        assert reader.scalar(select(func.count()).select_from(TravelSession)) == 1
        assert reader.scalar(select(func.count()).select_from(Itinerary)) == 1


"""读取不存在的演示会话返回失败，不创建任何新会话。"""

def test_demo_missing_session_does_not_write(
    store_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.demo_trip import main

    monkeypatch.setenv(
        "TRAVELMIND_DATABASE_URL", store_engine.url.render_as_string(hide_password=False)
    )
    assert main(["--session-id", str(uuid4())]) == 1
    assert "没有找到" in capsys.readouterr().err
    with Session(store_engine) as reader:
        assert reader.scalar(select(func.count()).select_from(TravelSession)) == 0
