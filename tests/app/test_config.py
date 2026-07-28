from app.config import AppConfig


def test_app_defaults():
    # _env_file=None：断言源码默认值，不受开发机本地 .env 影响
    cfg = AppConfig(api_key="k", _env_file=None)
    assert cfg.app_host == "127.0.0.1"
    assert cfg.app_port == 8000
    assert cfg.app_db_path == "app.db"
    assert cfg.enable_sandbox is False
    assert cfg.model == "gpt-4o-mini"      # 继承 HarnessConfig


def test_app2_config_defaults():
    cfg = AppConfig(api_key="k")
    assert cfg.app_max_upload_mb == 20
    assert cfg.downloads_dir == "downloads"


def test_system_prompt_has_reasoning_invisibility_guard():
    """元问题兜底：用户问「你上一步的思维链是什么/为什么是英文」时，模型并不掌握——
    思考内容只用于界面展示，不回灌进上下文，任何检索/记忆/文件工具里都没有它。
    实测过一次：模型为回答这类问题白调了 search_knowledge/recall_episodes/read_file
    全落空。系统提示须让它直接如实说明「无法查看自己的思考过程」，而非徒劳查找。

    放在 app_system_prompt（主聊天、执行子步、规划器三条路共用，见 assembly.py），
    一处覆盖三处。
    """
    sp = AppConfig(api_key="k", _env_file=None).app_system_prompt
    assert "思考过程" in sp and "思维链" in sp
    assert "界面" in sp                     # 说清它去了哪：只在界面展示
    assert "无法查看" in sp                 # 给出明确的答复口径
    assert "不要调用工具徒劳查找" in sp     # 拦住白调 search/recall/read_file


def test_system_prompt_allows_creative_writing():
    """回归：简单直答路径的学习边界只由 app_system_prompt 把关（复杂路径另有 off_topic 门，
    且那道门本就把「写一首诗」判为学习相关）。此前措辞把「娱乐」列为拒绝项、白名单又漏了创作，
    模型据此把「写一首诗」当娱乐婉拒——而写诗/写故事恰是正当的写作学习。边界须显式允许写作
    创作、判断从宽，只挡「直接索取与学习无关的具体结果」。"""
    sp = AppConfig(api_key="k", _env_file=None).app_system_prompt
    assert "写诗" in sp and "创作" in sp     # 写作/文学创作被显式列入允许范围
    assert "拿不准" in sp                    # 存疑默认按学习请求照做，不拒绝
    # 「娱乐」不再作为一刀切的拒绝触发词——否则写诗又会被误判为娱乐而婉拒
    assert "如闲聊、娱乐" not in sp
