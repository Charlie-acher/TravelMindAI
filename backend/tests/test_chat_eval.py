"""评测测试层：固定用例包含关键场景，评分不把参考文字或缺少旧草稿当作通过。"""

import json
from pathlib import Path

from scripts.evaluate_chat import check_turn

"""评分边界测试函数：没有结构化草稿时，局部修改检查必须失败。"""

def test_chat_dataset_and_missing_baseline_failures():
    dataset = json.loads((Path(__file__).parents[1] / "evals/chat.v1.json").read_text("utf-8"))
    assert len(dataset["cases"]) == len({case["id"] for case in dataset["cases"]}) == 6
    response = {"reply": "参考建议", "result": {"extraction": {"travelers": 3}}}
    checks = check_turn(response, {"fields": {"travelers": 3}, "plan": True,
        "unchanged_days": [1], "fallback": True}, None, [("fallback", {})])
    assert [item["passed"] for item in checks] == [True, False, True, False]


"""指定时间评分测试函数：保留其他天不能代替实际修改目标日，参考文字也不能算修改成功。"""

def test_target_day_start_must_match():
    response = {"reply": "已调整", "result": {"extraction": {}}, "itinerary": {"plan": {
        "days": [{"activities": []}, {"activities": [{"start_time": "09:00"}]}]}}}
    expected = {"day_starts": {"2": "11:00"}}
    assert check_turn(response, expected, None, [])[0]["passed"] is False
    response["itinerary"]["plan"]["days"][1]["activities"][0]["start_time"] = "11:00"
    assert check_turn(response, expected, None, [])[0]["passed"] is True
    response["itinerary"] = None
    assert check_turn(response, expected, None, [])[0]["passed"] is False
