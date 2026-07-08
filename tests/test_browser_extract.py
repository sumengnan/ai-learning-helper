from harness.browser.extract import extract_main_text

ARTICLE_HTML = """
<html><head><title>光合作用</title></head><body>
<nav>首页 关于 联系我们 登录 注册</nav>
<article>
<h1>光合作用的原理</h1>
<p>光合作用是绿色植物利用光能，把二氧化碳和水转化为储存能量的有机物，并释放氧气的过程。
这一过程主要发生在叶绿体中，是地球上几乎所有生命能量的最终来源，对维持大气中氧气与二氧化碳的平衡至关重要。</p>
<p>光合作用分为光反应和暗反应两个阶段。光反应在类囊体膜上进行，把光能转化为化学能并释放氧气；
暗反应在基质中进行，利用这些化学能固定二氧化碳，最终合成葡萄糖等有机物。</p>
</article>
<footer>版权所有 © 2026 保留所有权利 隐私政策 网站地图</footer>
</body></html>
"""


def test_extract_returns_main_text():
    text = extract_main_text(ARTICLE_HTML)
    assert "光合作用是绿色植物" in text
    assert "光反应和暗反应" in text


def test_extract_strips_boilerplate():
    text = extract_main_text(ARTICLE_HTML)
    assert "登录 注册" not in text
    assert "版权所有" not in text


def test_extract_empty_html():
    assert extract_main_text("") == ""
