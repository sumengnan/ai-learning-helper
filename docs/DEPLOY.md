# 部署

前后端打进**同一个镜像**：前端在构建阶段（Node 20）产出 `web/dist`，由 FastAPI 同源托管，
无需单独的 web 服务。运行时只有一个容器 + 一个数据卷。

本地开发模式（`uv run python -m app` + `npm run dev`）见 [README](../README.md#快速开始本地开发)。

## 一、本机用 Docker 跑一份

```bash
# 1. 准备配置（.env 必须在项目根，compose 用 ../ 指回来）
cp .env.example .env
# 至少填 HARNESS_API_KEY 与 AUTH_SECRET，见下方「必需配置」

# 2. 构建并启动（compose 文件在 docker/ 下，相对路径都相对该目录解析）
cd docker && docker compose up -d --build

# 3. 打开 http://localhost:8000
curl -s http://localhost:8000/api/version

# 日志 / 状态 / 停止
docker compose logs -f
docker compose ps
docker compose down
```

`docker compose up` 不带 `--build` 时会去 Docker Hub 拉 `sumengnan/ai-learning-helper:latest`
（镜像仓库公开，免登录）。想跑指定版本：`APP_IMAGE_TAG=0.1.3 docker compose up -d --no-build`。

## 二、必需配置

`.env` 放在**项目根 / 服务器部署目录**下（不是 `docker/` 里），绝不入库。
全部配置项及默认值见 `.env.example`（每项上方一行中文说明）。

最少这四项就能跑起来：

```dotenv
HARNESS_API_KEY=sk-真实key
HARNESS_BASE_URL=https://api.openai.com/v1
HARNESS_MODEL=gpt-4o-mini
AUTH_SECRET=改成一段足够长的随机串
```

> `AUTH_SECRET` **不带 `HARNESS_` 前缀**（`app/main.py` 直接读 `os.environ["AUTH_SECRET"]`，
> 回退到配置里的 `auth_secret`）。除它和 `DASHSCOPE_API_KEY` 外，其余配置一律 `HARNESS_` 前缀。
> 保持默认值 `dev-insecure-secret-change-me` 时启动会打 warning。

生产还建议改：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HARNESS_REQUIRE_CAPTCHA` | `false` | 登录/注册强制后端图形验证码，建议 `true` |
| `HARNESS_CORS_ORIGINS` | `["http://localhost:5173"]` | 跨域白名单（JSON 数组）。同源托管时用不到，独立部署前端才需要 |
| `HARNESS_EMBEDDING_API_KEY` | 空 | 知识库/记忆的向量检索；留空则回退用 `HARNESS_API_KEY`。两个都空则不注册记忆与知识库工具 |
| `HARNESS_CONTEXT_WINDOW_TOKENS` 等 | 见 `.env.example` | 按实际模型的上下文窗口调 |

`HARNESS_APP_HOST` / `HARNESS_APP_PORT` **不用在 `.env` 里改**：compose 的 `environment`
段已硬性置为 `0.0.0.0:8000`（容器内监听），会覆盖 `env_file` 里的值。要换对外端口改端口映射即可。

## 三、端口与数据卷

由 `docker/docker-compose.yml` 定义：

| 项 | 值 |
| --- | --- |
| 端口 | 宿主 `8000` → 容器 `8000`。要 80 端口把映射改成 `"80:8000"`，或在前面挂 Nginx 反代 |
| 数据卷 | `../data` → `/app/data`。即部署目录下的 `data/`，**不在 `docker/` 里** |
| `restart` | `unless-stopped` |

容器内所有持久化路径都被 `environment` 段指到 `/app/data`：

```
/app/data/app.db          # 用户 / 会话 / 题库 / 错题
/app/data/harness.db      # 轨迹与检查点
/app/data/memory.db       # 向量记忆与知识库
/app/data/downloads/      # AI 生成的可下载文件
/app/data/attachments/    # 聊天附件原始字节
```

重建容器、换镜像都不丢；升级只覆盖 `docker/docker-compose.yml`，不碰 `.env` 和 `data/`。

## 四、代码沙箱 / 浏览器工具（可选）

**默认关闭**。开启需要三个变量配齐，只设 `HARNESS_ENABLE_SANDBOX=true` 是**不够的**——
装配层要求 `enable_sandbox` 且 `sandbox_docker_host` 非空才建 SandboxManager，而
`sandbox_backend` 决定用真容器还是直接在应用进程里跑（`local` 无隔离）：

```dotenv
HARNESS_ENABLE_SANDBOX=true
HARNESS_SANDBOX_BACKEND=docker
# compose 已把宿主 socket 挂进容器，直接指它即可
HARNESS_SANDBOX_DOCKER_HOST=unix:///var/run/docker.sock
```

compose 挂了两处宿主路径供此用途：`/var/run/docker.sock`（起子沙箱容器）和
`/etc/docker:ro`（daemon 配置与 TLS 客户端证书）。走 `unix://` 时 docker-py 用 UnixHTTPAdapter，
`HARNESS_SANDBOX_DOCKER_TLS_*` 那几项不生效、可留空；只有连远程 `tcp://host:2376` 时才需要它们。

> 挂载 `docker.sock` 等同于把宿主 root 级控制权交给容器。请确保服务器仅自己可信使用。

浏览器工具另需：

```dotenv
HARNESS_ENABLE_BROWSER=true
HARNESS_BROWSER_SANDBOX_IMAGE=ai-learning-helper/playwright-py:v1.47.0
```

该镜像要**先在能连到沙箱 Docker 守护进程的机器上自行构建**（官方 playwright 镜像自带浏览器
但不含 playwright 的 Python 包）：

```bash
docker build -f docker/playwright-browser.Dockerfile \
  -t ai-learning-helper/playwright-py:v1.47.0 .
```

浏览器子沙箱内存默认 1g（`HARNESS_BROWSER_SANDBOX_MEM_LIMIT`），比普通沙箱的 100m 大得多——
Chromium 用小额度会被 OOM 杀掉。

其余可选能力开关（默认全 `false`）：`HARNESS_ENABLE_DISPATCH`（多智能体派发）、
`HARNESS_ENABLE_SKILLS`（技能框架）、`HARNESS_ENABLE_MCP`（MCP 客户端）。

## 五、自动部署（GitHub Actions → Docker Hub → 服务器）

`main` 有 push 时（也可在 Actions 页 **Run workflow** 手动触发），`.github/workflows/deploy.yml`：

1. **先跑全量测试**（`workflow_call` 复用 `ci.yml`：pytest 含 eval 门禁 + 前端 tsc/vitest）。
   红的代码不上生产。
2. 计算自增版本号 → 构建镜像并推 Docker Hub `sumengnan/ai-learning-helper`（`:latest` 与 `:<版本号>` 两个 tag）
   → 推送同名 git tag。
3. `scp` 把 `docker/docker-compose.yml` 传到服务器 `/opt/ai-learning-helper/docker/`。
4. 若设了 `APP_ENV_FILE` secret，经 stdin 管道写入服务器 `/opt/ai-learning-helper/.env`（`chmod 600`）。
5. SSH 进服务器执行：

```bash
cd /opt/ai-learning-helper/docker
export APP_IMAGE_TAG=<本次版本号>
docker compose up -d --pull always --no-build   # --no-build：服务器绝不构建，也没有源码
docker image prune -f
docker compose ps
```

若服务器上 `../.env` 不存在，这一步会直接失败退出并提示。

### 版本号（大.中 手动，小自增）

镜像 tag 是语义版本 `大.中.小`：

- **大.中** 由仓库根 `VERSION` 文件声明（如 `0.0`），**只有你改这个文件**才动它。格式必须是两段数字，否则 CI 报错退出。
- **小版本** 由 CI 自增：扫描现有 `大.中.*` git tag 取最高的 + 1；该 `大.中` 尚无 tag 则从 `.1` 起。
- git tag 在镜像推送**成功后**才打——失败的构建不消耗版本号。`concurrency: deploy-main` 已串行化，无竞争。

例：当前 `VERSION` 是 `0.1`，不动它就一路 `0.1.1` → `0.1.2` → `0.1.3` …；改成 `1.0` 并合并 →
下次部署是 `1.0.1`。

### 需要的 GitHub Secrets

仓库 Settings → Secrets and variables → Actions：

| Secret | 说明 |
| --- | --- |
| `SERVER_HOST` | 服务器 IP / 域名 |
| `SERVER_USER` | SSH 用户名 |
| `SERVER_PASSWORD` | SSH 密码（workflow 用 sshpass） |
| `SERVER_PORT` | SSH 端口（如 `22`） |
| `DOCKERHUB_USERNAME` | Docker Hub 用户名（`sumengnan`） |
| `DOCKERHUB_TOKEN` | Docker Hub 访问令牌（Account Settings → Security → New Access Token，Read/Write） |
| `APP_ENV_FILE` | **可选**。整份 `.env` 内容。设了就每次部署自动写到服务器；不设则沿用服务器上已放好的 `.env`。`gh secret set APP_ENV_FILE < .env` 一键设置 |

用 `APP_ENV_FILE` 的好处：密钥唯一真源在 GitHub Secrets（加密、不入库、不进日志），换 key 只改
Secret 重跑即可。

### 服务器一次性准备

```bash
# 1. 安装 Docker + compose 插件
curl -fsSL https://get.docker.com | sh

# 2. 让部署用户免 sudo 用 docker（改组后需重新登录）
sudo usermod -aG docker "$USER"

# 3. 建部署目录并归属给部署用户（否则 scp 到 /opt 会没权限）
sudo mkdir -p /opt/ai-learning-helper
sudo chown -R "$USER" /opt/ai-learning-helper

# 4. 放好 .env（除非你用 APP_ENV_FILE secret）。参考仓库 .env.example
vim /opt/ai-learning-helper/.env
chmod 600 /opt/ai-learning-helper/.env
```

之后每次 push 全自动。目录结构（与仓库一致）：

```
/opt/ai-learning-helper/
├── .env                       # 密钥与模型配置（部署不覆盖，除非设了 APP_ENV_FILE）
├── data/                      # 持久化数据（部署不碰）
└── docker/docker-compose.yml  # 每次部署覆盖
```

## 六、部署自检

镜像构建时把版本号 + 短 git sha + UTC 构建时间同时烙进**前端 bundle**与**后端环境变量**：

- 后端：`GET /api/version` → `{version, git_sha, built_at}`，公开端点，登录前也可访问。
- 前端：左侧菜单底部版本徽标显示 `前端 v0.1.x · 后端 v0.1.x`，悬停看 git sha 与构建时间。
  - 绿色 = 前后端一致（同一次部署都成功了）。
  - 橙色 = 不一致（通常是浏览器缓存了旧前端，或某一端没更新成功）。

```bash
curl -s http://<host>:8000/api/version
```

本地非 CI 构建时版本显示 `dev`（Dockerfile 的 `APP_VERSION` 默认值）。

## 七、排障

```bash
cd /opt/ai-learning-helper/docker
docker compose logs -f          # 实时日志
docker compose ps               # 容器状态
docker compose up -d --pull always --no-build   # 手动重新拉起
```

- 容器起不来：先看 `.env` 是否存在、`HARNESS_API_KEY` 是否填了。
- 日志时间：容器时区固定东八区（Dockerfile 装 tzdata + 链 `/etc/localtime`，compose 再显式设
  `TZ: Asia/Shanghai`）。要换时区改这两处的 `TZ` 即可。数据库里的时间戳一律存带时区的 UTC，
  由前端换算，不受此设置影响。
- 日志里认人：每条日志前缀带 `[user=<账号>]`（未登录为 `user=匿名`），另有每请求一行
  `方法 路径 -> 状态码 耗时`。嫌吵可在 `.env` 里设 `HARNESS_ACCESS_LOG=0` 只关访问行
  （账号前缀仍在）；整体级别用 `HARNESS_LOG_LEVEL` 控制。
- 镜像仓库 `sumengnan/ai-learning-helper` 目前**公开**，服务器免登录 pull。若改为私有，需在服务器上
  先 `docker login`（或在 workflow 拉取步骤前加 `docker login`）。
- workflow 用密码 SSH 且跳过 host key 校验（`StrictHostKeyChecking=no`）。要更强的安全性，改用 SSH
  密钥并在 workflow 里固定 `known_hosts`。
