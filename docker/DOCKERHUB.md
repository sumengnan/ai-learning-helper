# AI 学习助手（ai-learning-helper）

面向学习场景的 AI 助手：聊天问答、上传资料建成个人知识库、基于资料出题开考、答错自动进错题本，
并在首页看到学习概览与 AI 用量成本。

**后端是一套自研的最小 Agent 运行时内核 `harness`（不套任何 Agent 框架）**，内核之上是一层
Plan-Execute-Reflect 编排器：复杂请求先拆成 DAG 计划、无依赖的步骤并行执行、逐步质检后汇总；
简单问答自动短路成单轮直答，不额外增加开销。

- 源码与完整文档：<https://github.com/sumengnan/ai-learning-helper>
- 在线演示：<http://192.144.213.12>（可自行注册账号）

---

## 快速开始

镜像不内置任何模型密钥，**必须自带一个 OpenAI 兼容端点**（OpenAI / 千问 Qwen / vLLM / 其它兼容服务均可）。

```bash
docker run -d --name ai-learning-helper \
  -p 8000:8000 \
  -v "$PWD/data:/app/data" \
  -e AUTH_SECRET="$(openssl rand -hex 32)" \
  -e HARNESS_API_KEY="sk-..." \
  -e HARNESS_BASE_URL="https://api.openai.com/v1" \
  -e HARNESS_MODEL="gpt-4o-mini" \
  -e HARNESS_EMBEDDING_API_KEY="sk-..." \
  -e HARNESS_EMBEDDING_BASE_URL="https://api.openai.com/v1" \
  -e HARNESS_EMBEDDING_MODEL="text-embedding-3-small" \
  sumengnan/ai-learning-helper:latest
```

打开 <http://localhost:8000> 注册账号即可使用。

> **`AUTH_SECRET` 务必改掉。** 默认值是公开的占位串（`dev-insecure-secret-change-me`），
> 任何人都能凭它伪造登录态。

### 用 docker compose

仓库的 `docker/docker-compose.yml` 已配好持久化与端口，配置从项目根目录的 `.env` 读取
（照着 `.env.example` 复制一份填好即可）：

```bash
git clone https://github.com/sumengnan/ai-learning-helper.git
cd ai-learning-helper && cp .env.example .env   # 填 HARNESS_API_KEY / AUTH_SECRET
cd docker && docker compose up -d
```

---

## 配置

约定：除 `AUTH_SECRET` 外，全部环境变量以 `HARNESS_` 为前缀；dict/list 值用 JSON 字符串。
完整清单见仓库 [`.env.example`](https://github.com/sumengnan/ai-learning-helper/blob/main/.env.example)，
下表只列最常用的。

### 必填

| 变量 | 说明 |
| --- | --- |
| `HARNESS_API_KEY` | 主模型密钥。**不填无法对话** |
| `AUTH_SECRET` | 登录态签名密钥。**生产必须改** |

### 模型

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HARNESS_BASE_URL` | `https://api.openai.com/v1` | 任意 OpenAI 兼容端点 |
| `HARNESS_MODEL` | `gpt-4o-mini` | 主模型 |
| `HARNESS_EMBEDDING_BASE_URL` | 回退主端点 | 向量化端点 |
| `HARNESS_EMBEDDING_API_KEY` | 回退主密钥 | 向量化密钥 |
| `HARNESS_EMBEDDING_MODEL` | `text-embedding-3-small` | 知识库检索依赖它 |

### 可选能力（默认全关）

| 变量 | 说明 |
| --- | --- |
| `HARNESS_ENABLE_RERANK` | 精排。**建议开**，见下方「知识库检索质量」 |
| `HARNESS_ENABLE_ANSWER_GATE` | 回答校验门：交付前校验格式 / grounding / 代码可运行，不过则带反馈重答 |
| `HARNESS_ENABLE_SANDBOX` | 代码沙箱（按语言起一次性子沙箱执行） |
| `HARNESS_ENABLE_BROWSER` | 无头浏览器抓取 |
| `HARNESS_ENABLE_SKILLS` | 技能渐进式披露 |
| `HARNESS_ENABLE_MCP` | MCP 客户端（stdio + streamable-http） |
| `HARNESS_REQUIRE_CAPTCHA` | 注册/登录图形验证码。**公网部署建议开** |
| `HARNESS_CORS_ORIGINS` | 跨域白名单，JSON 数组 |

### 知识库检索质量

启用精排时，`HARNESS_RERANK_MIN_SCORE`（默认 `0.35`）会丢弃相关性低于阈值的检索结果。

这个下限**不是可有可无的调优项**：整条检索链上没有别的地方能表达「都不够相关」——RRF 融合
只看排名，加权前又对候选集做了 min-max 归一化（最好的那条永远得 1.0）。关掉它，小知识库会被
任意查询整个倒出来，问「厨具」也能从一堆 AI 资料里返回满满一屏。

**换精排模型务必重新实测**：各家分数量纲不同（`[0,1]` 概率 vs 未归一化 logit），照抄会误伤。
`0.35` 实测自 `qwen3-rerank`（无关查询最高 0.26，相关查询最低 0.43）。标定失准不会静默——
某次查询被全部滤光会记 `WARNING`，日志里搜「相关性下限」即可看到。

---

## 数据持久化

容器内所有状态都落在 **`/app/data`**，务必挂载出来，否则重建容器即丢失：

| 路径 | 内容 |
| --- | --- |
| `/app/data/app.db` | 业务数据：用户、会话、题库、错题集 |
| `/app/data/memory.db` | 向量库：知识库与长期记忆 |
| `/app/data/harness.db` | 运行轨迹：用量、成本、统计 |
| `/app/data/downloads` | AI 生成的可下载文件 |
| `/app/data/attachments` | 用户上传的附件 |

镜像默认把这些路径指向 `/app/data`；若自定义 `HARNESS_*_DB_PATH`，请确保仍落在挂载卷内。

---

## 沙箱与浏览器（可选）

启用 `HARNESS_ENABLE_SANDBOX` 或 `HARNESS_ENABLE_BROWSER` 时，应用会**调用宿主 Docker**
另起子容器执行代码 / 抓网页，需要额外挂载：

```bash
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /etc/docker:/etc/docker:ro
```

> 挂载 Docker socket 等于把宿主 Docker 的完整控制权交给容器内进程。
> 只在你信任该部署、且确实需要沙箱能力时才这么做；不需要就别开这两个开关。

沙箱里的危险命令（任何 `rm` 等）执行前会弹窗要求人工审核，拒绝即终止该步。

---

## 镜像信息

| | |
| --- | --- |
| 架构 | `linux/amd64` |
| 基础镜像 | `python:3.12-slim`（前端由 `node:20-slim` 多阶段构建后内置） |
| 暴露端口 | `8000` |
| 启动命令 | `python -m app` |
| 健康检查端点 | `GET /api/version` |

标签：`latest` 跟随主分支；另有按 commit SHA 的不可变标签，生产建议钉具体 SHA 而非 `latest`。

前端由后端一并托管（FastAPI 提供 `web/dist`），**无需另起 Web 服务器**。
左侧菜单底部的版本徽标会比对前后端版本：一致为绿 ✓、不一致为橙 ⚠，可用来自检部署是否完整。

---

## 功能一览

- **AI 聊天** — 多轮对话、断点续传（刷新/重连可接回在途生成）、附件上传、耗时与 tokens 统计、
  回复标注参考来源、生成的文件在聊天内内联预览下载
- **任务编排** — Plan-Execute-Reflect：拆 DAG 计划、无依赖步骤并行、逐步质检后汇总
- **知识库（RAG）** — PDF / docx / txt / md 入库、语义检索、按用户隔离；回答基于知识库做事实核对
- **资料与记忆分离** — `search_knowledge` 查你上传的资料，`search_memory` 查 AI 记下的长期结论与偏好，两者互不串味
- **题库 / 模拟考试 / 错题集** — 基于知识库出题开考、逐题判分，答错自动进错题集；
  考试由服务端托管游标与判分，不靠模型自觉
- **人工确认** — 删题库/删错题这类破坏性操作，AI 只登记待确认卡片，用户点确认后才真正执行
- **抓取防护** — 拦掉指向保留域名（`example.com` 一族）的编造网址；抓失败的网址按原因分级登记
  （1 小时 ~ 30 天），下次短路让模型换来源，不永久拉黑
- **循环防护** — 步数/token/墙钟三道硬上限之外，连续 N 步发起完全相同的工具调用即判原地打转，
  先注入纠偏提示，仍重复才中止
- **上下文管理** — 长对话按 `full` / `window` / `layered` 三档策略裁剪
- **记忆管理** — 语义/情景/程序三类长期记忆，自动提炼、去重消矛盾、自我整合
- **首页概览** — 学习主场 + 系统监控双视角，图表、产物预览、模型分层计费与成本估算

---

## 许可

仓库当前未附许可证文件。如需在自有项目中使用，请先与作者确认。
