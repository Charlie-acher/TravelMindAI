"""评测入口层：用固定对话运行真实聊天链，记录程序检查、阶段耗时和待人工评阅正文。"""

import argparse
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import load_settings
from app.llm.contracts import ErrorCode, ProviderError
from app.llm.providers import ProviderAdapter
from app.main import create_app
from app.models.auth import AuthSession, User
from app.services.auth import AuthService

"""评分函数：仅比较明确标注的可观测结果，来源事实正确性留给人工评阅。"""

def check_turn(response: dict, expected: dict, previous: dict | None, events: list) -> list[dict]:
    checks = []

    """记录函数：失败检查不从分母剔除，保留预期与实际值供复查。"""

    def record(name, wanted, actual):
        checks.append({"name": name, "expected": wanted, "actual": actual,
                       "passed": wanted == actual})

    extraction = response["result"]["extraction"]
    for key, value in expected.get("fields", {}).items():
        record(key, value, extraction.get(key))
    plan = (response.get("itinerary") or {}).get("plan")
    if "plan" in expected:
        record("plan", expected["plan"], plan is not None)
    if words := expected.get("reply_has_any"):
        record("reply_has_any", True, any(word in response["reply"] for word in words))
    if "fallback" in expected:
        record("fallback", expected["fallback"], any(event == "fallback" for event, _ in events))
    if "max_activities" in expected:
        record("max_activities", True, bool(plan) and all(
            len(day["activities"]) <= expected["max_activities"] for day in plan["days"]))
    if name := expected.get("exclude_place"):
        record("exclude_place", True, bool(plan) and all(
            name not in activity["place"]["map"]["name"]
            for day in plan["days"] for activity in day["activities"]))
    for day in expected.get("unchanged_days", []):
        record(f"day_{day}_unchanged", True, bool(plan and previous) and
               plan["days"][day - 1] == previous["days"][day - 1])
    for day, start in expected.get("day_starts", {}).items():
        activities = plan["days"][int(day) - 1]["activities"] if plan else []
        record(f"day_{day}_start", start, activities[0]["start_time"] if activities else None)
    return checks


"""单组评测函数：每组独立会话，多轮使用实际历史，失败后不拿标注补齐状态。"""

def evaluate_case(client: TestClient, case: dict, provider: str) -> dict:
    created = client.post("/api/v1/sessions", json={"title": "固定评测 " + case["id"]})
    created.raise_for_status()
    sid = created.json()["session"]["id"]
    rows, revision, previous, workflow = [], 0, None, None
    try:
        attachment_ids = []
        if attachment := case.get("attachment"):
            uploaded = client.post(f"/api/v1/sessions/{sid}/attachments", files={"file": (
                attachment["name"], attachment["text"].encode(), "text/markdown")})
            uploaded.raise_for_status()
            attachment_ids = [uploaded.json()["id"]]
        for turn in case["turns"]:
            payload = {"message": turn["message"], "message_id": str(uuid4()),
                       "expected_revision": revision,
                       "selected_provider": case.get("provider", provider),
                       "attachment_ids": attachment_ids}
            if workflow and workflow["status"] == "waiting":
                payload["workflow_resume"] = {"run_id": workflow["run_id"], "action": "continue"}
            started, events, saved, failure = perf_counter(), [], None, None
            first_event = None
            event = ""
            with client.stream("POST", f"/api/v1/sessions/{sid}/requirement-messages/stream",
                               json=payload) as stream:
                stream.raise_for_status()
                for line in stream.iter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                        if first_event is None:
                            first_event = perf_counter() - started
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        events.append((event, data))
                        if event == "done":
                            saved = data
                        elif event == "error":
                            failure = data
            response = saved["response"] if saved else None
            checks = check_turn(response, turn["expected"], previous, events) if response else [
                {"name": "completed", "expected": True, "actual": False, "passed": False}]
            rows.append({"message": turn["message"], "checks": checks,
                "passed": all(item["passed"] for item in checks), "response": response,
                "error": failure, "elapsed_seconds": round(perf_counter() - started, 3),
                # TestClient会缓冲SSE，首事件值不能当作真实网络首包延迟。
                "buffered_first_event_seconds": first_event,
                "metrics": next((data for event, data in events if event == "metrics"), None)})
            if saved:
                revision, workflow = saved["revision"], response.get("workflow")
                previous = (response.get("itinerary") or {}).get("plan") or previous
                attachment_ids = []
            else:
                break
        return {"id": case["id"], "category": case["category"], "turns": rows,
                "passed": len(rows) == len(case["turns"]) and all(row["passed"] for row in rows),
                "manual_review": {"source_accuracy": None, "preference_quality": None},
                "fault_injected": bool(case.get("fault_provider"))}
    finally:
        deleted = client.delete(f"/api/v1/sessions/{sid}")
        deleted.raise_for_status()


"""主函数：默认只读题库，live运行独立测试账号并清理，不修改用户会话或共享资料。"""

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="真实聊天固定场景评测")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--provider", choices=["deepseek", "kimi", "qwen"], default="deepseek")
    parser.add_argument("--cases", nargs="*", help="只运行指定编号；报告会标明子集")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data_path = root / "evals/chat.v1.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    cases = [case for case in data["cases"] if not args.cases or case["id"] in args.cases]
    if args.cases and set(args.cases) - {case["id"] for case in cases}:
        parser.error("指定了不存在的场景编号")
    print(f"准备 {len(cases)} 组固定场景。", flush=True)
    if not args.live:
        print("未调用模型；--live 会使用.env中的真实服务并产生API费用。")
        return 0
    now = datetime.now(timezone.utc)
    output = args.output or root.parent / "temp/reports" / f"chat-{now:%Y%m%dT%H%M%S%fZ}.json"
    if output.exists():
        parser.error("报告已存在，请换新路径")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"version": data["version"],
              "dataset_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
              "started_at": now.isoformat(), "provider": args.provider, "completed": False,
              "selected_cases": [case["id"] for case in cases], "results": [],
              "source_accuracy": None, "cost_cny": None,
              "limits": "小样本程序检查；正文与来源事实待人工评阅。"
                        "每轮metrics含原币已知费用估算和未知项，不代表供应商实扣；"
                        "OCR按页、网页和地图按次数，不能按token比较。"}
    app = create_app(load_settings(args.env_file))
    with TestClient(app) as client:
        engine = app.state.database_engine
        password = secrets.token_hex(5)
        user = AuthService(engine).create_user("qa-chat-" + uuid4().hex[:12], password)
        try:
            client.headers["X-Requested-With"] = "TravelMindAI"
            client.post("/api/v1/auth/login", json={
                "username": user.username, "password": password}).raise_for_status()
            original = ProviderAdapter.native
            for case in cases:
                """故障探针函数：只在本评测进程模拟指定工具提供方超时，备用仍真实调用。"""

                def native(adapter, messages, options):
                    if adapter.provider == case.get("fault_provider"):
                        raise ProviderError(adapter.provider, ErrorCode.TIMEOUT)
                    return original(adapter, messages, options)

                with patch.object(ProviderAdapter, "native", native):
                    result = evaluate_case(client, case, args.provider)
                report["results"].append(result)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                print(case["id"], "PASS" if result["passed"] else "FAIL", flush=True)
        finally:
            with Session(engine) as db, db.begin():
                db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
    report["completed"] = True
    report["passed_cases"] = sum(case["passed"] for case in report["results"])
    turns = [turn for case in report["results"] for turn in case["turns"]]
    checks = [item for turn in turns for item in turn["checks"]]
    report["checks"] = {"passed": sum(item["passed"] for item in checks), "total": len(checks)}
    elapsed = sorted(turn["elapsed_seconds"] for turn in turns)
    report["latency"] = {"sample_count": len(elapsed), "median_seconds": median(elapsed),
                         "max_seconds": max(elapsed)}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"程序检查 {report['passed_cases']}/{len(cases)}；报告 {output}", flush=True)
    return 0 if report["passed_cases"] == len(cases) else 2


if __name__ == "__main__":
    raise SystemExit(main())
