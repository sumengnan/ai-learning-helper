"""打错 `/aifix` 时要有人吭一声。

起因是 issue #95：正文第一行写的是 `/affix`（少一个 i），workflow 的 job 级
`if:` 判假 —— job 根本没起，1 秒 skipped，**零回帖**。开 issue 的人只看到
Actions 页面上一条灰色的 skipped，无从知道是自己打错了字。

这个判据只回答一件事：这一行**像不像**在打 `/aifix` 却打错了。像就回帖提示，
不像就闭嘴。
"""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "aifix_typo",
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "aifix_typo.py")
_M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_M)

looks_like_typo = _M.looks_like_typo


# ------------------------------------------------------------ 要抓的

def test_the_real_one_from_issue_95():
    """`/affix` —— 少一个 i，这道闸存在的理由。"""
    assert looks_like_typo("/affix 增加AI聊天的默认对话") == "/affix"


def test_missing_a_letter():
    assert looks_like_typo("/aifx 修一下") == "/aifx"


def test_transposed_letters():
    assert looks_like_typo("/aifxi 修一下") == "/aifxi"


def test_wrong_case():
    """`/AIFIX` 不触发 aifix.yml（`startsWith` 区分大小写），一样是打错。"""
    assert looks_like_typo("/AIFIX 修一下") == "/AIFIX"


def test_leading_whitespace():
    """` /aifix` 拼写全对，但 `startsWith` 匹配不上 —— 同样静默失败。"""
    assert looks_like_typo("  /aifix 修一下") == "/aifix"


def test_only_the_first_line_matters():
    """命令必须在第一行。第二行写对了不算，那和 aifix 自己的判据一致。"""
    assert looks_like_typo("随便说点什么\n/affix 修一下") is None


def test_the_command_alone_on_the_line():
    assert looks_like_typo("/affix") == "/affix"


# ------------------------------------------------------------ 不该抓的

def test_the_correct_command_is_not_a_typo():
    """**最要紧的一条**：拼对了就闭嘴。

    aifix.yml 的判据是 `startsWith(body, '/aifix')`，两个 workflow 必须互斥 ——
    都回帖的话，一次正常触发会收到一条「你是不是打错了」，那比不提示更糟。
    """
    assert looks_like_typo("/aifix 修一下") is None
    assert looks_like_typo("/aifix") is None


def test_a_longer_command_starting_with_aifix_is_left_alone():
    """`/aifixx` 会被 `startsWith` 判真，归 aifix.yml 管，这里不能插手。"""
    assert looks_like_typo("/aifixx 修一下") is None


def test_other_slash_commands_are_not_typos():
    """别的斜杠命令离得远，不是在打 aifix。"""
    for line in ("/close", "/help", "/deploy 上线", "/label bug"):
        assert looks_like_typo(line) is None, line


def test_plain_text_is_not_a_typo():
    assert looks_like_typo("这个页面加载很慢") is None
    assert looks_like_typo("") is None


def test_a_path_is_not_a_command():
    """`/usr/bin/...` 开头的行是路径，不是命令。"""
    assert looks_like_typo("/usr/local/bin/python 报错了") is None


def test_prose_mentioning_aifix_is_not_a_command():
    """正文里提到 aifix 不等于在调用它 —— 命令必须是行首那个斜杠词。"""
    assert looks_like_typo("aifix 这个工具怎么用？") is None
    assert looks_like_typo("我想让 /aifix 跑一下") is None


def test_distance_three_is_too_far():
    """判据要收得住。差三个字母以上就不是手滑，是在说别的事。"""
    assert looks_like_typo("/prefix 什么什么") is None
