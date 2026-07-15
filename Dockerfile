# syntax=docker/dockerfile:1

# 版本戳（CI 经 build-arg 注入语义版本号、git sha 与构建时间；本地默认 dev）
ARG APP_VERSION=dev
ARG APP_GIT_SHA=dev
ARG APP_BUILD_TIME=

# ---------- Stage 1: 构建前端静态产物 ----------
FROM node:20-slim AS web
ARG APP_VERSION
ARG APP_GIT_SHA
ARG APP_BUILD_TIME
WORKDIR /web
# 先装依赖，利用层缓存（package*.json 未变则不重装）
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# 把版本信息透传给 vite（define 编译进 bundle）
ENV VITE_APP_VERSION=$APP_VERSION \
    VITE_APP_GIT_SHA=$APP_GIT_SHA \
    VITE_APP_BUILD_TIME=$APP_BUILD_TIME
RUN npm run build            # 产出 /web/dist（FastAPI 生产环境托管此目录）

# ---------- Stage 2: Python 运行时 ----------
FROM python:3.12-slim AS runtime
ARG APP_VERSION
ARG APP_GIT_SHA
ARG APP_BUILD_TIME

# uv：按 uv.lock 复现锁定依赖
RUN pip install --no-cache-dir uv

WORKDIR /app

# 先装依赖（利用层缓存）：只装锁定依赖，不构建本项目包——源码走 PYTHONPATH，无需打包
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 源码与运行期需要的目录
COPY src ./src
COPY app ./app
COPY skills ./skills
COPY agents ./agents
COPY mcp ./mcp
# 前端静态产物（app/main.py 在 /app/web/dist 处 mount 静态站点）
COPY --from=web /web/dist ./web/dist

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:/app" \
    PYTHONUNBUFFERED=1 \
    HARNESS_APP_HOST=0.0.0.0 \
    HARNESS_APP_PORT=8000 \
    APP_VERSION=$APP_VERSION \
    APP_GIT_SHA=$APP_GIT_SHA \
    APP_BUILD_TIME=$APP_BUILD_TIME

EXPOSE 8000
CMD ["python", "-m", "app"]
