"""检查导入正式包时没有隐藏行为，防止把实验脚本的顶层调用带入产品。

使用独立 Python 进程，是因为当前测试进程可能已经导入过模块。
Python 会缓存导入结果；在同一进程重复 import 可能掩盖首次导入的问题。
"""

import os
import subprocess
import sys
from pathlib import Path

"""导入只能定义类和函数，不能读配置创建实例、联网、提问或打印。

下面给新进程放入一个故意无效的配置值：如果模块在顶层创建 Settings，
导入就会失败。网络和 input 被替换为立即报错的函数，避免测试真的联网。
"""

def test_import_does_not_read_configuration_connect_or_prompt(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["TRAVELMIND_ENVIRONMENT"] = "invalid-on-purpose"
    # 排除个人 PYTHONPATH，确保测试使用安装好的正式包，而非碰巧找到根目录。
    environment.pop("PYTHONPATH", None)

    # 这是交给子进程执行的短程序，不是要创建的新文件。
    # 审计钩子在 socket 连接/DNS 查询发生时拒绝操作，导入应当完全不触发它。
    program = """
import builtins
import sys

def reject_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("Network access during import")

def reject_input(*args, **kwargs):
    raise RuntimeError("Interactive input during import")

sys.addaudithook(reject_network)
builtins.input = reject_input
import app
import app.config
import app.services.budget_service
import app.schemas.budget
import app.api.budget
import app.database
import app.models.trip
import app.services.trip_service
import scripts.demo_trip
import app.schemas.trip
import app.api.trips
import app.main
import app.models
import app.schemas.common
import scripts.check_db
import app.schemas.requirement.base
import app.services.requirement.prompt
import app.services.requirement.extract
import app.llm.client
import scripts.demo_requirement
import app.schemas.requirement.update
import app.schemas.requirement.chat
import app.services.requirement.merge
import app.api.requirement.routes
import app.models.requirement_turn
import app.schemas.requirement.history
import app.services.requirement.history
import app.api.requirement.history
import scripts.requirement_eval
import scripts.evaluate_requirements
import app.llm.embeddings
import scripts.demo_embedding
import app.services.document.vector_store
import app.services.document.search
import app.schemas.document.search
import app.api.document.search
import app.schemas.document.answer
import app.services.document.answer
import app.services.chat.rag
import app.services.amap
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,  # 保留结果，由下面的断言给出清晰失败信息。
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
