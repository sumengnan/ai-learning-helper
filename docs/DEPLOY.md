# 自动部署（GitHub Actions → Docker Hub → 服务器 docker compose）

`main` 分支有 push 时，`.github/workflows/deploy.yml` 会自动：
1. 在 GitHub Actions 里**构建镜像并推送到 Docker Hub** `sumengnan/ai-learning-helper`
   （打两个 tag：`:latest` 与 `:<版本号>`）；
2. 把 `docker-compose.yml` 拷到服务器 `/opt/ai-learning-helper`；
3. SSH 进服务器 `docker compose up -d --pull always --no-build`，**拉取**该版本镜像滚动更新
   （服务器不构建、不需要源码）。

### 版本号（大.中 手动，小自增）

镜像 tag 用语义版本号 `大.中.小`：

- **大.中版本**由仓库根的 `VERSION` 文件声明（如 `0.0`）。**只有你改这个文件**才动大/中版本。
- **小版本（patch）**由 CI 自增：在当前 `大.中` 下取最高的 `.小` + 1；该 `大.中` 尚无 tag
  （你刚改了大/中）则从 `.1` 起。

行为示例：
- `VERSION=0.0` 一直不改 → `0.0.1` → `0.0.2` → `0.0.3` …（小版本一直涨）
- 把 `VERSION` 改成 `0.1` 并合并 → 下次部署 `0.1.1`，之后 `0.1.2` …
- 把 `VERSION` 改成 `1.0` → `1.0.1` …

实现：workflow 读 `VERSION` 得 `大.中`，扫描现有 `大.中.*` git tag 取最高小版本 +1，构建后把
新版本号作为镜像 tag 并回推同名 git tag（作下次递增依据；`concurrency` 已串行化，无竞争）。

前端在镜像构建阶段（Node 20）打包成 `web/dist`，由 FastAPI 生产环境托管，与后端同源，无需单独 web 服务。

## 需要的 GitHub Secrets

仓库 Settings → Secrets and variables → Actions：

| Secret | 说明 |
| --- | --- |
| `SERVER_HOST` | 服务器 IP / 域名 |
| `SERVER_USER` | SSH 用户名 |
| `SERVER_PASSWORD` | SSH 密码 |
| `SERVER_PORT` | SSH 端口（如 22） |
| `DOCKERHUB_USERNAME` | Docker Hub 用户名（`sumengnan`） |
| `DOCKERHUB_TOKEN` | Docker Hub 访问令牌（Account Settings → Security → New Access Token，Read/Write） |

## 服务器一次性准备

在服务器上做一次即可（之后每次 push 全自动）：

```bash
# 1. 安装 Docker + compose 插件（官方脚本）
curl -fsSL https://get.docker.com | sh

# 2. 让部署用户免 sudo 用 docker（改组后需重新登录）
sudo usermod -aG docker "$USER"

# 3. 建部署目录并归属给部署用户（否则 scp 到 /opt 会没权限）
sudo mkdir -p /opt/ai-learning-helper
sudo chown -R "$USER" /opt/ai-learning-helper

# 4. 放好 .env（含密钥，绝不入库）。可参考仓库 .env.example
#    生产务必设置随机 AUTH_SECRET 与真实 HARNESS_API_KEY
vim /opt/ai-learning-helper/.env
```

> 镜像仓库 `sumengnan/ai-learning-helper` 为**公开**，服务器免登录直接 `pull`。若日后改为私有，
> 需在服务器上先 `docker login`（或在 workflow 拉取步骤前加 `docker login`）。

`.env` 至少需要：

```dotenv
HARNESS_API_KEY=sk-真实key
HARNESS_BASE_URL=https://api.openai.com/v1
HARNESS_MODEL=gpt-4o-mini
AUTH_SECRET=改成一段足够长的随机串
```

## 数据持久化

所有运行时数据都落在挂载卷 `/opt/ai-learning-helper/data/`（`app.db` / `harness.db` /
`memory.db` / `downloads/` / `attachments/`），重建/换镜像都不丢。部署只覆盖
`docker-compose.yml`，不碰 `.env` 和 `data/`。

## 代码沙箱 / 浏览器工具

compose 挂载了宿主 `/var/run/docker.sock`，应用可调用宿主 Docker 起子沙箱。要启用，在
`.env` 里打开对应开关（`HARNESS_ENABLE_SANDBOX=true` / `HARNESS_ENABLE_BROWSER=true`），
并确保宿主已拉取所需镜像（见 `.env.example` 与 `docker/playwright-browser.Dockerfile`）。
> 挂载 docker.sock 等同于把宿主 root 级控制权交给容器，请确保服务器仅自己可信使用。

## 访问

默认映射宿主 `8000` → 容器 `8000`。需要 80 端口把 `docker-compose.yml` 的端口改成
`"80:8000"`，或在前面挂 Nginx 反代。

## 部署自检：前后端版本

镜像构建时会把这次部署的**版本号** + 短 git sha + UTC 构建时间同时烙进**前端 bundle**与
**后端环境变量**：

- 后端：`GET /api/version` → `{version, git_sha, built_at}`（公开端点，登录前也可访问）。
- 前端：左侧菜单底部的版本徽标显示 `前端 v0.0.x · 后端 v0.0.x`，悬停看 git sha 与构建时间。
  - ✓ 绿色 = 前后端版本一致（同一次部署都成功了）。
  - ⚠ 橙色 = 不一致（通常是浏览器缓存了旧前端，或某一端没更新成功）。

命令行快速核对：`curl -s http://<host>:8000/api/version`。本地非 CI 构建时版本显示 `dev`。

## 手动触发 / 排障

- Actions 页可用 **Run workflow** 手动部署（`workflow_dispatch`）。
- 服务器上查看：`cd /opt/ai-learning-helper && docker compose logs -f` / `docker compose ps`。
- 首次连接跳过了 host key 校验（`StrictHostKeyChecking=no`）。如需更强安全性，改用 SSH 密钥
  并在 workflow 里固定 known_hosts。
