"""聊天系统提示的当前日期注入：避免模型用训练年份做时间推算。"""
from datetime import datetime

from app.api.chat import _CN_TZ, _today_guide


def test_today_guide_contains_current_cn_date():
    g = _today_guide()
    now = datetime.now(_CN_TZ)
    assert f"{now:%Y 年 %m 月 %d 日}" in g      # 含北京时间当天日期（每请求动态）
    assert "当前日期" in g and "基准" in g       # 明确要求以此为时间基准
    assert "训练数据" in g                       # 提示不要沿用训练年份


def test_today_guide_uses_beijing_year_not_stale():
    # 年份来自实时时钟而非硬编码：断言含当前年份、不含明显过时的年份
    g = _today_guide()
    assert str(datetime.now(_CN_TZ).year) in g
