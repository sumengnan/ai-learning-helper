# 自动部署（GitHub Actions → 服务器 docker compose）

`main` 分支有 push 时，`.github/workflows/deploy.yml` 会自动：
1. 用 SSH（密码）把源码 `rsync` 到服务器 `/opt/ai-learning-helper`；
2. SSH 进服务器执行 `docker compose up -d --build`，在服务器上构建镜像并滚动更新。

前端在镜像构建阶段（Node 20）打包成 `web/dist`，由 FastAPI 生产环境托管，与后端同源，无需单独 web 服务。

## 需要的 GitHub Secrets

仓库 Settings → Secrets and variables → Actions（你已配置）：

| Secret | 说明 |
| --- | --- |
| `SERVER_HOST` | 服务器 IP / 域名 |
| `SERVER_USER` | SSH 用户名 |
| `SERVER_PASSWORD` | SSH 密码 |
| `SERVER_PORT` | SSH 端口（如 22） |

## 服务器一次性准备

在服务器上做一次即可（之后每次 push 全自动）：

```bash
# 1. 安装 Docker + compose 插件（官方脚本）
curl -fsSL https://get.docker.com | sh

# 2. 让部署用户免 sudo 用 docker（改组后需重新登录）
sudo usermod -aG docker "$USER"

# 3. 建部署目录并归属给部署用户（否则 rsync 到 /opt 会没权限）
sudo mkdir -p /opt/ai-learning-helper
sudo chown -R "$USER" /opt/ai-learning-helper

# 4. 放好 .env（含密钥，绝不入库）。可参考仓库 .env.example
#    生产务必设置随机 AUTH_SECRET 与真实 HARNESS_API_KEY
vim /opt/ai-learning-helper/.env
```

`.env` 至少需要：

```dotenv
HARNESS_API_KEY=sk-真实key
HARNESS_BASE_URL=https://api.openai.com/v1
HARNESS_MODEL=gpt-4o-mini
AUTH_SECRET=改成一段足够长的随机串
```

## 数据持久化

所有运行时数据都落在挂载卷 `/opt/ai-learning-helper/data/`（`app.db` / `harness.db` /
`memory.db` / `downloads/` / `attachments/`），重建容器不丢。`rsync --delete` 与镜像构建
都不会碰 `.env` 和 `data/`。

## 代码沙箱 / 浏览器工具

compose 挂载了宿主 `/var/run/docker.sock`，应用可调用宿主 Docker 起子沙箱。要启用，在
`.env` 里打开对应开关（`HARNESS_ENABLE_SANDBOX=true` / `HARNESS_ENABLE_BROWSER=true`），
并确保宿主已拉取所需镜像（见 `.env.example` 与 `docker/playwright-browser.Dockerfile`）。
> 挂载 docker.sock 等同于把宿主 root 级控制权交给容器，请确保服务器仅自己可信使用。

## 访问

默认映射宿主 `8000` → 容器 `8000`。需要 80 端口把 `docker-compose.yml` 的端口改成
`"80:8000"`，或在前面挂 Nginx 反代。

## 手动触发 / 排障

- Actions 页可用 **Run workflow** 手动部署（`workflow_dispatch`）。
- 服务器上查看：`cd /opt/ai-learning-helper && docker compose logs -f` / `docker compose ps`。
- 首次连接跳过了 host key 校验（`StrictHostKeyChecking=no`）。如需更强安全性，改用 SSH 密钥
  并在 workflow 里固定 known_hosts。
