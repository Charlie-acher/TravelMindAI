"""维护命令层：恢复会话删除后未完成的原件清理，只处理专属目录里的清理记录。"""

import argparse
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from app.config import load_settings
from app.database import create_database_engine
from app.services.trip_service import TripService

"""恢复清理函数：逐份记录核对会话已删除，仍存在的会话保留原件和记录。"""

def cleanup_attachment_files(engine: Engine, attachment_dir: Path) -> tuple[int, int]:
    service = TripService(engine, attachment_dir=attachment_dir)
    completed = skipped = 0
    for path in attachment_dir.glob(".delete-*.json"):
        session_id = UUID(path.name.removeprefix(".delete-").removesuffix(".json"))
        if path.name != f".delete-{session_id}.json":
            raise ValueError("附件清理记录文件名无效")
        if service.cleanup_attachment_files(session_id):
            completed += 1
        else:
            skipped += 1
    return completed, skipped


"""命令入口函数：从正式配置读取目录和数据库，清理失败时返回非零状态。"""

def main() -> int:
    parser = argparse.ArgumentParser(description="恢复私人附件会话的原件清理")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    settings = load_settings(args.env_file)
    engine = create_database_engine(settings)
    try:
        completed, skipped = cleanup_attachment_files(
            engine, settings.document_upload_dir / "conversations",
        )
        print(f"已完成清理：{completed}；会话仍存在或记录已被处理：{skipped}")
        return 0
    except Exception:
        # 不输出数据库地址、原件名或用户编号；失败记录留在原目录，修复占用后重试。
        print("原件清理失败；清理记录已保留，请检查目录权限、文件占用与数据库连接后重试。")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
