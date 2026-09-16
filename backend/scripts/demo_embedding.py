"""命令入口层：取少量文字或已保存片段，预览输入或试跑一次向量服务。

--dry-run只读本地数据；不带该选项会把选中文字发给配置的服务，可能产生API费用。
"""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import load_settings
from app.database import create_database_engine
from app.llm.embeddings import EmbeddingClient, EmbeddingError
from app.services.document.service import DocumentService

"""命令执行函数：读取指定输入，输出片段摘要或向量调用结果，结束时关闭连接。"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="小批量验证Embedding，不保存向量或改变资料状态")
    parser.add_argument("--env-file", type=Path, help="显式指定.env，不自动寻找配置")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", action="append", help="试跑文字，可重复传入，最多8段")
    source.add_argument("--document-id", type=UUID, help="从这份资料读取已生成的片段")
    parser.add_argument("--limit", type=int, choices=range(1, 9), default=3, help="读取1至8段")
    parser.add_argument("--offset", type=int, default=0, help="跳过多少片段，默认0")
    parser.add_argument("--dry-run", action="store_true", help="只显示输入摘要，不调用向量服务")
    arguments = parser.parse_args(argv)
    if arguments.offset < 0:
        parser.error("--offset不能小于0")
    if arguments.env_file is not None and not arguments.env_file.is_file():
        print("配置文件不存在，请检查--env-file路径。", file=sys.stderr)
        return 1

    engine: Engine | None = None
    try:
        settings = load_settings(arguments.env_file)
        texts: list[str] = arguments.text or []
        chunk_ids: list[str] = []
        if arguments.document_id is not None:
            engine = create_database_engine(settings)
            service = DocumentService(engine, settings.document_upload_dir, "knowledge-base")
            page = service.list_chunks(arguments.document_id, arguments.limit, arguments.offset)
            if not page.items:
                raise EmbeddingError("没有可试跑的片段，请先在资料页生成片段或减小offset")
            texts = [chunk.text for chunk in page.items]
            chunk_ids = [str(chunk.id) for chunk in page.items]
        if not 1 <= len(texts) <= 8 or any(not text.strip() or len(text) > 800 for text in texts):
            raise EmbeddingError("每次需提供1至8段非空文字，每段最多800个字符")
        summary: dict[str, object] = {
            "mode": "dry_run",
            "document_id": str(arguments.document_id) if chunk_ids else None,
            "chunk_ids": chunk_ids,
            "characters": [len(text) for text in texts],
        }
        if not arguments.dry_run:
            # 仅此分支联网；只读预览既不创建HTTP连接，也不要求Embedding密钥。
            with httpx.Client() as http:
                result = EmbeddingClient(settings, http).embed(texts)
            summary.update(result.model_dump(exclude={"vectors"}))
            summary.update(mode="request_succeeded", vector_count=len(result.vectors))
        # 只打印编号、长度和模型信息，不把正文、完整向量或密钥刷满终端。
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except ValidationError:
        print("Embedding配置不合法，请检查地址、维度和超时设置。", file=sys.stderr)
    except (EmbeddingError, ValueError) as error:
        print(str(error), file=sys.stderr)
    except HTTPException as error:
        print(
            f"资料读取失败（HTTP {error.status_code}），请检查资料编号和读取状态。", file=sys.stderr
        )
    except SQLAlchemyError:
        print("数据库读取失败，请检查配置、容器和迁移。", file=sys.stderr)
    finally:
        if engine is not None:
            engine.dispose()
    return 1


# 导入只定义函数；只有明确运行命令时才读取配置或调用服务。
if __name__ == "__main__":
    raise SystemExit(main())
