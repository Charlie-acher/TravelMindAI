# 部署层：从锁文件安装运行依赖，镜像不包含个人.env和开发测试工具。
FROM ghcr.io/astral-sh/uv:0.12.10 AS uv
FROM public.ecr.aws/docker/library/python:3.11-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/backend/.venv/bin:$PATH"
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/migrations ./migrations
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home travelmind \
    && mkdir -p /app/temp/uploads && chown -R travelmind:travelmind /app/temp
USER travelmind
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--env-file", "/run/secrets/travelmind.env", "--host", "0.0.0.0", "--port", "8000"]
