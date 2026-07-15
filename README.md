# AI 学习助手

一个面向学习场景的 AI 助手：以自研的最小 Agent 运行时内核（`harness`）为后端，配合
React 前端，提供 AI 聊天、知识库、题库、错题集、学习概览等能力。支持联网检索、代码沙箱执行、
无头浏览器抓取与 MCP 工具接入。

## 功能特性

- **AI 聊天**：多轮对话、断点续传（刷新/重连接回在途生成）、附件上传、消息耗时/tokens 统计、
  AI 回复标注参考来源、生成文件在聊天内内联预览/下载。
- **知识库**：文档入库、语义检索、片段抽屉预览；AI 回答基于知识库做 grounding。
- **题库 / 错题集**：基于知识库出题、作答判分，错题归集回顾。
- **首页概览**：学习主场 + 系统监控双视角，时间范围筛选、图表（带轴与悬停数值）、
  记忆查看、产物预览、模型分层计费与成本估算。
- **工具能力**：联网 `http_request`（失败/被防抓自动改用浏览器抓取）、代码沙箱
  （按语言起一次性子沙箱执行）、无头浏览器抓取、MCP 客户端（stdio + streamable-http）。
- **回答校验门**（可选）：交付前对格式 / 知识库 grounding / 代码可运行 / LLM 自评打分做校验，
  不过则自动带反馈重答。
- **部署自检**：左侧菜单底部版本徽标显示前后端版本，一致=绿 ✓、不一致=橙 ⚠。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python ≥ 3.11、FastAPI + uvicorn、自研 `harness` Agent 内核、SQLite（+ sqlite-vec 向量检索） |
| 前端 | React 18 + TypeScript、MUI、Vite、React Router、framer-motion |
| 依赖管理 | 后端 `uv`（`uv.lock`）、前端 `npm`（`package-lock.json`） |
| 部署 | Docker / docker compose，GitHub Actions CI/CD → Docker Hub |

## 目录结构

```
app/          FastAPI 应用层（api 路由、会话/知识/题库/下载等领域服务）
src/harness/  最小 Agent 运行时内核（客户端、工具注册、沙箱、记忆、持久化、MCP）
web/          React 前端（Vite）
skills/       技能目录
agents/       子 agent 花名册
tests/        pytest 测试
docker/       浏览器子沙箱镜像等
docs/         部署等文档
Dockerfile / docker/             容器化与编排（compose 在 docker/ 下）
VERSION       部署版本号的「大.中」声明（小版本由 CI 自增）
```

## 快速开始（本地开发）

前置：Python ≥ 3.11、[uv](https://docs.astral.sh/uv/)、Node.js ≥ 20。

### 1. 后端

```bash
# 安装依赖（按 uv.lock 复现）
uv sync

# 配置环境变量：复制示例并填入真实 key
cp .env.example .env
#   至少设置 HARNESS_API_KEY / HARNESS_BASE_URL / HARNESS_MODEL / AUTH_SECRET

# 启动 API（默认 http://127.0.0.1:8000）
uv run python -m app
```

### 2. 前端

```bash
cd web
npm install
npm run dev        # http://localhost:5173，/api 已代理到后端 8000
```

浏览器打开 http://localhost:5173，注册/登录后即可使用。

> 生产模式下前端 `npm run build` 产出 `web/dist`，由 FastAPI 同源托管，无需单独的 web 服务。

## 环境变量

全部配置见 [`.env.example`](.env.example)（`HARNESS_` 前缀，另有 `AUTH_SECRET`），涵盖模型、
沙箱、无头浏览器、MCP、回答校验门、上下文管理策略等。生产务必设置随机 `AUTH_SECRET` 与真实
`HARNESS_API_KEY`。

## 测试

```bash
# 后端
uv run pytest

# 前端（web/ 目录下）
npm run test
```

## 部署

`main` 有 push 时，GitHub Actions 自动**构建镜像并推送到 Docker Hub**
`sumengnan/ai-learning-helper`，服务器 `docker compose pull` 拉取运行。镜像 tag 采用语义
版本号：`大.中` 由仓库根 `VERSION` 文件声明（改它才动大/中），`小`（patch）由 CI 自增。

服务器一次性准备、所需 GitHub Secrets、版本自检等完整说明见 [`docs/DEPLOY.md`](docs/DEPLOY.md)。

本地也可直接容器化运行：

```bash
# 需先在项目根准备好 .env（compose 用 ../.env 读它）
cd docker && docker compose up -d --build
```

## 技能框架

本项目集成了 `superpowers-zh` 中文技能框架，配合 Claude Code 使用；约定见
[`CLAUDE.md`](CLAUDE.md)。
