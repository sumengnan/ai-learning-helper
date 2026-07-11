# 浏览器子沙箱镜像（供 HARNESS_BROWSER_SANDBOX_IMAGE 使用）
#
# 坑：官方 mcr.microsoft.com/playwright/python 镜像已含 Chromium/Firefox/WebKit
# （在 /ms-playwright，PLAYWRIGHT_BROWSERS_PATH 已设）+ curl，但**不含 playwright 的
# Python 包**——直接用会报 `ModuleNotFoundError: No module named 'playwright'`。
# 这里补装 pip 包即可（浏览器已在镜像里，无需再 `playwright install`）。
#
# 构建（在能连到沙箱 Docker 守护进程的机器上）：
#   docker build -f docker/playwright-browser.Dockerfile \
#     -t ai-learning-helper/playwright-py:v1.47.0 .
# 然后设置：
#   HARNESS_BROWSER_SANDBOX_IMAGE=ai-learning-helper/playwright-py:v1.47.0
#
# 注：浏览器子沙箱内存默认给到 1g（HARNESS_BROWSER_SANDBOX_MEM_LIMIT），Chromium 远比
# 普通沙箱吃内存，沿用基础沙箱的小额度（如 100m）会被 OOM 杀掉。

FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
RUN pip install --no-cache-dir playwright==1.47.0
