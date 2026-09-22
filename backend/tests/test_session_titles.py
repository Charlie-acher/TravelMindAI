"""测试层：验证自动标题只生成一次，失败回退和手动名称优先。"""

from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.trip import TravelSession
from app.services.session_titles import summarize_session_title
from app.services.trip_service import TripService
from tests.helpers import TEST_USER_ID

"""标题测试函数：手动改名发生在模型等待期间时，最终更新不能覆盖它。"""

def test_title_respects_manual_rename(store_engine):
    trips = TripService(store_engine)
    trip = trips.create_session("新建对话", user_id=TEST_USER_ID)
    turn = SimpleNamespace(revision=1, response=SimpleNamespace(
        result=SimpleNamespace(original_message="想去苏州玩三天"), reply="可以慢慢逛园林"))

    class Model:
        """模型替身类：在返回标题前模拟用户手动改名。"""

        """标题返回函数：制造模型等待与手动保存的竞争。"""

        def generate_json(self, messages):
            trips.rename_session(trip.id, "我的假期", user_id=TEST_USER_ID)
            return '{"title":"苏州三日慢游"}'

    summarize_session_title(store_engine, trip.id, turn, Model())
    with Session(store_engine) as unit:
        saved = unit.get(TravelSession, trip.id)
        assert saved.title == "我的假期"
        assert saved.title_source == "manual"


"""幂等测试函数：成功生成的会话不重复调用模型，失败后保留回退标题。"""

def test_title_success_and_fallback(store_engine):
    trips = TripService(store_engine)
    trip = trips.create_session("新建对话", user_id=TEST_USER_ID)
    turn = SimpleNamespace(revision=1, response=SimpleNamespace(
        result=SimpleNamespace(original_message="想去苏州玩三天"), reply="可以慢慢逛园林"))
    calls = []

    class Model:
        """模型替身类：记录调用次数并返回固定标题。"""

        """标题返回函数：供幂等检查计数。"""

        def generate_json(self, messages):
            calls.append(messages)
            return '{"title":"苏州三日慢游"}'

    summarize_session_title(store_engine, trip.id, turn, Model())
    summarize_session_title(store_engine, trip.id, turn, Model())
    assert len(calls) == 1
    assert trips.get_session(trip.id).title == "苏州三日慢游"
    failed = trips.create_session("新建对话", user_id=TEST_USER_ID)

    class Broken:
        """失败模型类：模拟提供方不可用。"""

        """失败函数：验证标题故障不影响已保存回答。"""

        def generate_json(self, messages):
            raise RuntimeError("provider unavailable")

    summarize_session_title(store_engine, failed.id, turn, Broken())
    assert trips.get_session(failed.id).title_source == "fallback"


"""数据库故障测试函数：独立标题存储失败不影响已经保存成功的对话返回。"""

def test_title_database_error_does_not_fail_saved_reply():
    from unittest.mock import Mock
    from uuid import uuid4

    from sqlalchemy.exc import SQLAlchemyError
    engine, model = Mock(), Mock()
    engine.begin.side_effect = SQLAlchemyError("connection unavailable")
    summarize_session_title(engine, uuid4(), SimpleNamespace(revision=1), model)
    model.generate_json.assert_not_called()
