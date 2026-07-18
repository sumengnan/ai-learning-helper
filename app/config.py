# app/config.py
from __future__ import annotations

import logging
import os

from pydantic_settings import SettingsConfigDict

from harness.config import HarnessConfig

_log = logging.getLogger("app.config")


def load_env_file(path: str = ".env") -> list[str]:
    """把 .env 里尚未存在于 os.environ 的键补进进程环境，返回补入的键名。

    pydantic-settings 读 .env 只用来填 AppConfig 字段（且只认 HARNESS_ 前缀），**不会**
    写进 os.environ。而 mcp/mcp_servers.json 里的 ${VAR} 走 os.path.expandvars，只认
    os.environ —— 两者接不上：写在 .env 里的 DASHSCOPE_API_KEY 永远不生效，Authorization
    头原样发出 "Bearer ${DASHSCOPE_API_KEY}"，换来一个 401，且只有一条 warning 日志。

    真环境变量优先：已存在的键一律不覆盖（export / docker -e / k8s env 说了算）。
    """
    injected: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return injected                      # 无 .env 是正常情形（全靠真环境变量）
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:      # 已有真环境变量 → 不覆盖
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        os.environ[key] = val
        injected.append(key)
    return injected


class AppConfig(HarnessConfig):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_", env_file=".env", extra="ignore", protected_namespaces=())

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    auth_secret: str = "dev-insecure-secret-change-me"
    # 登录/注册是否强制图形验证码（后端校验）。默认关，便于测试直连；
    # 生产用 HARNESS_REQUIRE_CAPTCHA=true 打开。前端始终展示并回传验证码。
    require_captcha: bool = False
    # 应用领域各表统一存于此单一数据库文件（可用 HARNESS_APP_DB_PATH 覆盖）
    app_db_path: str = "app.db"
    app_system_prompt: str = "你是一个 AI 学习助手，可用工具检索知识、联网、计算来帮助用户学习。"
    enable_browser: bool = False
    # 抓取失败的网址登记：失败即记，下次抓前短路让模型换来源（分级 TTL，非永久拉黑）
    enable_url_blocklist: bool = True
    enable_sandbox: bool = False
    enable_dispatch: bool = False
    # === Plan-Execute-Reflect 编排器 ===
    enable_orchestrator: bool = False          # 开则主流程走编排器，否则维持 ReAct AgentLoop
    orchestrator_max_step_retry: int = 2       # 单步反复失败上限（含首次）
    orchestrator_max_replan: int = 2           # 终局重规划轮数上限
    orchestrator_planner_max_retries: int = 2  # Planner 出无效 DAG 的重试上限
    orchestrator_step_max_steps: int = 10      # 每个 Executor 步内部 AgentLoop 的步数上限
    enable_skills: bool = False
    enable_mcp: bool = False          # MCP 客户端总开关；开则按 mcp_config_path 连接 server
    cors_origins: list = ["http://localhost:5173"]
    app_max_upload_mb: int = 20
    # 题库导入：单块字符数（越小则块越多、并行度越高但请求数越多）与 LLM 抽取并发上限
    # （无上限会打爆单端点、触发超时重试反而更慢）。
    import_chunk_chars: int = 1800
    import_max_concurrency: int = 4
    quiz_max_count: int = 20
    quiz_retrieve_k: int = 6
    short_pass_score: int = 60
    downloads_dir: str = "downloads"
    download_max_mb: int = 25
    # 回答交付前校验门（默认关，保持现状直通流式）
    enable_answer_gate: bool = False
    answer_gate_max_retries: int = 1        # 校验不过时的自动重答次数（N）；总尝试 = N+1
    answer_pass_score: int = 60             # LLM 自评打分阈值（低于则不过）
    gate_check_format: bool = True          # 分项开关：格式/完整性
    gate_check_grounding: bool = True       # 分项开关：知识库 grounding
    gate_check_code: bool = True            # 分项开关：代码可运行
    gate_check_judge: bool = True           # 分项开关：LLM 自评打分
    gate_check_facts: bool = False          # 分项开关：引用链接可达性核对
    # 每步校验（实时层，规则/阈值为主，内核零改动）
    enable_step_check: bool = True          # 高风险步实时校验（检索相关性/代码执行）
    step_relevance_min: float = 0.0         # 检索低分阈值；0=只判空命中（起步）
    # 轨迹 judge（评估层，交付前一次性回看整轨迹分层打分）
    enable_trajectory_judge: bool = False   # 与 answer gate 独立，可单独开
    trajectory_pass_score: int = 60         # 最终层分数阈值（低于则软门不过）
    judge_model: str = ""                   # 独立 judge 模型；空则回退主 model
    judge_base_url: str = ""                # judge 独立端点；空则回退主 base_url
    judge_api_key: str = ""                 # judge 独立 key；空则回退主 api_key
    judge_samples: int = 1                  # 预留：多次取多数（起步 1）
    # 聊天附件：裸字节落盘目录、单文件上限、单会话待发数量上限、可直接喂视觉模型的图片上限
    attachments_dir: str = "attachments"
    attachment_max_mb: int = 100
    attachment_max_count: int = 10
    attachment_vision_max_mb: int = 5
    # 智能写入：让模型把对话提炼成分型事实（semantic/episodic/procedural）再入库，
    # 而非原文入库。开着才会产出 episodic —— add_texts 写死 SEMANTIC，故这也是记忆整合
    # （MemoryMaintainer）唯一的料源，关掉整合就永远空转。代价是每轮多 2 次 LLM
    # （提炼 + 与既有记忆调和），但它跑在答案交付之后的后台，不拖慢首字。
    memory_write_extract: bool = True
    memory_write_sample_rate: float = 1.0
    memory_write_candidate_k: int = 5
    # 记忆整合触发：会话内 episodic 记录数达到此值，就在后台把同主题的零散 episodic
    # 蒸馏成一条 semantic。0=关。整合后 episodic 被标 superseded、计数回落，故不会每轮重触发。
    memory_consolidate_after: int = 20
    ttl_episodic_days: int = 0
    ttl_semantic_days: int = 0
    ttl_procedural_days: int = 0
    consolidation_sim_threshold: float = 0.85
    consolidation_min_cluster: int = 2
    consolidation_max_source: int = 200
    # 分层上下文管理：full=全量拼接（默认，与历史行为字节级一致，安全回退）；
    # window=仅 L1 token 预算滑动窗口；layered=L1+L2 滚动摘要+L3 语义检索。
    context_strategy: str = "layered"              # full | window | layered
    context_window_tokens: int = 1000000           # 模型上下文窗口（按实际模型调整）
    context_response_reserve_tokens: int = 56000   # 给回复预留的 token（按模型最大输出留；
                                                   # 思考模型的思维链也算输出）
    # 输入总量的策略上限（0=不设）。与 window 的区别：window 是「塞不下会报错」的物理约束，
    # 这个是「塞得下但不划算」——典型用法是填分档计价的档位阈值（超档单价可能翻数倍）。
    # 不设它就只能靠谎报 window 来控成本，那会让 window 字段的含义失真。
    context_max_prompt_tokens: int = 240000
    context_working_ratio: float = 0.9             # 最近原文（L1）占可用预算的比例。注意剩余
                                                   # 部分不会被强制留给 L2/L3，见 ContextBudget
    context_summary_max_tokens: int = 2000         # L2 摘要块 token 上限
    context_retrieval_top_k: int = 5               # L3 召回条数
    context_enable_summary: bool = True            # layered 下是否启用 L2 摘要
    context_enable_retrieval: bool = True          # layered 下是否启用 L3 检索
    # 「快速模型」档：压缩/命名/提炼这类机械活的专用模型（同 judge_*：空则回退主模型/
    # 端点/key）。当前三处在用：L2 滚动摘要、对话自动命名、记忆写入的事实提炼（_extract）。
    # 这三件事压差了都无害——摘要糙了下轮重压、标题丑了用户改、事实提炼漏了下次再提。
    # 刻意不含记忆调和（_reconcile）：那是判断题且后果不可逆（判 REPLACE 会 set_superseded
    # 永久作废旧记忆），判错不是省钱是毁数据，故留在主模型。
    # 思考链恒关，不给配置：机械活开思考纯烧 token 与延迟，「快速档但要思考」是自相矛盾的
    # 组合（同 judge 档的处理）。注意它必须自己显式关——这些旁路调用够不着聊天页那个思考
    # 开关（那个只作用于本轮任务的模型调用），不表态就由服务端默认决定，Qwen3 系默认是开的。
    fast_model: str = ""                           # 独立快速模型；空则回退主 model
    fast_base_url: str = ""                        # 快速模型独立端点；空则回退主 base_url
    fast_api_key: str = ""                         # 快速模型独立 key；空则回退主 api_key
