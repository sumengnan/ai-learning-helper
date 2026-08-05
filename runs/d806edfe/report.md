# aifix run d806edfe

- 适配器：pytest、vitest
- 分支：`aifix/d806edfe`
- 修复：**1 / 1**
- 成本：¥0.64（30,278 tokens，按 1 USD = 7.2 CNY 折算）

| 测试用例 | 结果 | 尝试次数 | 中止原因 |
|---|---|---|---|
| `web/src/components/citations.test.ts::linkifyCitations > 不改写代码块和行内代码里的 [n]` | 已修复 | 1 | — |

## 这条复现测试钉的规则

> linkifyCitations 只能改写散文中的 [n] 角标为引用链接，围栏代码块（```...```）和行内代码（`...`）内的 [n] 必须原样保留

写复现的那一步给的说法，**仅供参考**，不参与判定 —— 判定只看测试结果。合并之前值得对着它看一眼 diff：补丁是按这条规则改的，还是只让那一条用例通过。

合并：`git merge aifix/d806edfe`
