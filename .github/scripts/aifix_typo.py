"""打错 `/aifix` 时回一句，别让它静默失败。

issue #95 的形状：正文第一行写的是 `/affix`，aifix.yml 的 job 级 `if:` 判假，
job 根本没起 —— 1 秒 skipped、零回帖。开 issue 的人只看到 Actions 页面上一条
灰色的 skipped，无从知道是自己少打了一个字母。

aifix.yml 的注释里已经讲过同一条道理（它刻意不在 `if:` 里判权限，理由是
「静默丢弃会让他以为已经在跑了」）。拼写打错属于同一类，只是当时漏掉了。

**与 aifix.yml 严格互斥。** 那边的判据是 `startsWith(body, '/aifix')`，这里
只处理它的补集 —— 两个 workflow 都回帖的话，一次正常触发会收到一条「你是不是
打错了」，比不提示更糟。
"""
from __future__ import annotations

import json
import os
import sys

COMMAND = "/aifix"

# 手滑的容忍度。2 够覆盖漏一个字母（/aifx）、错一个字母（/affix）、换序
# （/aifxi）这三种真实形态。
#
# 放到 3 就会把 `/prefix` 也算进来 —— 那是在说别的事，而一条误报的「你是不是
# 想打 /aifix」比不提示更烦人：它出现在一个跟 aifix 毫无关系的 issue 里。
_MAX_DISTANCE = 2


def _distance(a: str, b: str) -> int:
    """Levenshtein 距离。

    自己写而不是引第三方包：这个脚本跑在 Actions 的裸 python3 上，为了十几行
    逻辑去装一个包，是拿一次 pip 安装（和它的网络失败面）换一段谁都会写的代码。
    """
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1,          # 删
                           cur[j - 1] + 1,        # 增
                           prev[j - 1] + (ca != cb)))   # 改
        prev = cur
    return prev[-1]


def looks_like_typo(body: str) -> str | None:
    """这段正文的第一行是不是在打 `/aifix` 却打错了。返回打错的那个词。

    只看**第一行**，与 aifix 自己的判据一致（命令不在第一行就不是命令）。

    返回打错的原词而不是布尔：回帖里要把它引出来给人看。「你写的是 `/affix`」
    比「命令拼写有误」有用得多 —— 少一个 i 这种事，不指出来人盯着看三遍也发现
    不了。
    """
    first = body.split("\n", 1)[0].strip() if body else ""
    if not first.startswith("/"):
        return None

    word = first.split()[0] if first.split() else ""

    # 拼对了（含 `/aifixx` 这类前缀相同的）→ 归 aifix.yml 管，这里必须闭嘴。
    # 判据与那边逐字一致：`startsWith(body, '/aifix')`。注意比的是**原始正文**
    # 而不是 strip 过的 first —— ` /aifix` 那边匹配不上，正是这里要接的。
    if body.startswith(COMMAND):
        return None

    # `/usr/local/bin/python` 这类路径不是命令：命令是一个词，路径带更多斜杠。
    if word.count("/") > 1:
        return None

    if _distance(word.lower(), COMMAND) <= _MAX_DISTANCE:
        return word
    return None


def _comment_body(typo: str) -> str:
    return (
        f"👋 你写的是 `{typo}`，正确的命令是 `{COMMAND}` —— "
        "所以这条没有触发 aifix。\n\n"
        f"重新评论一条 `{COMMAND}`（可以跟上补充说明）就能跑起来。\n\n"
        "> 顺带一提：**改 issue 正文没用**。workflow 只认 `issues: [opened]`，"
        "不接 `edited` —— 那是有意的（改正文完全静默，一条老 issue 被悄悄改成 "
        "`/aifix` 开头就能触发，没人会知道）。所以这条路只能走评论。\n\n"
        f"aifix 是**测试失败驱动**的：它读 issue 写一条复现测试、确认它真的红了，"
        "再让模型去修。描述一个具体的缺陷（什么输入、期望什么、实际什么）它最擅长；"
        "纯功能需求它多半会如实说写不出复现。"
    )


def main() -> int:
    """从 GITHUB_EVENT_PATH 读事件，判断要不要回帖。

    只把该说的话打到 stdout，**不自己调 API** —— 发评论交给 workflow 里的
    `gh` 一步。这样这个函数是纯的、可测的，而鉴权、重试那些事归 CLI。
    """
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return 0
    with open(path, encoding="utf-8") as f:
        event = json.load(f)

    issue = event.get("issue") or {}
    # PR 上的评论不管：aifix 是 issue 驱动的，而 PR 的评论区里 `/xxx` 命令
    # （别的 bot 的）比 issue 里常见得多，在那儿提示纯属噪音。
    if issue.get("pull_request"):
        return 0

    comment = event.get("comment") or {}
    author = (comment.get("user") or issue.get("user") or {})
    # 机器人打错字不需要有人安慰它。更实际的理由：bot 之间互相回帖会绕圈。
    if author.get("type") == "Bot":
        return 0

    body = comment.get("body") if comment else issue.get("body")
    typo = looks_like_typo(body or "")
    if not typo:
        return 0

    print(_comment_body(typo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
