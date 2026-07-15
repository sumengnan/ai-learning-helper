# evals/dataset.py
"""数据集加载。JSONL 逐行校验，错误带行号；重复 id 直接报错。

数据集是 git 里的确定性文件，不是运行时 store.sample() —— 门禁靠「同样输入必得同样分数」
成立，任何随机采样都会让基线比对失去意义。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .schema import EvalCase

_DIR = Path(__file__).parent / "datasets"
_ADAPTER: TypeAdapter[EvalCase] = TypeAdapter(EvalCase)


def load_jsonl(path: str | Path) -> list[EvalCase]:
    """逐行解析 JSONL 成 case。空行与 # 开头的注释行跳过。"""
    p = Path(path)
    cases: list[EvalCase] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            case = _ADAPTER.validate_json(line)
        except ValidationError as e:
            raise ValueError(f"{p}:{lineno} case 不合 schema：{e}") from e
        if case.id in seen:
            raise ValueError(
                f"{p}:{lineno} 重复的 case id {case.id!r}（首次出现在第 {seen[case.id]} 行）")
        seen[case.id] = lineno
        cases.append(case)
    return cases


def load_suite(name: str) -> list[EvalCase]:
    """按套件名加载 evals/datasets/<name>.jsonl。"""
    return load_jsonl(_DIR / f"{name}.jsonl")


def load_corpus(name: str) -> list[tuple[str, str]]:
    """加载检索语料 evals/datasets/corpus/<name>.jsonl（{"id","text"} 一行一条）→ [(id, text)]。"""
    p = _DIR / "corpus" / f"{name}.jsonl"
    out: list[tuple[str, str]] = []
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
            out.append((d["id"], d["text"]))
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f"{p}:{lineno} 语料行需为 {{'id','text'}} 的 JSON：{e}") from e
    return out


def dump_jsonl(cases: list[EvalCase], path: str | Path) -> None:
    """写出 JSONL（供 evals/export.py 从题库导出用）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(c.model_dump(mode="json"), ensure_ascii=False) for c in cases]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
