# aifix：开一个 issue，换一个修复 PR

[aifix](https://github.com/sumengnan/aifix-code) 是一个测试失败驱动的自我修复循环。本仓库
把它接成了 issue 驱动：**开一个正文第一行是 `/aifix` 的 issue，描述一个缺陷，它会写一条
复现测试、去改代码、用全量测试判定改得对不对，然后推一条分支并开 PR。**

```
你开的 issue（/aifix + 缺陷描述）
        │
        ▼
  写复现测试 → 确认它真的红了 → 让模型去修 → 跑全量测试判定
        │
        ▼
分支 aifix/<run_id> + 一个 PR + 一份报告
        │
        ▼
  你看一眼 diff，决定合不合
```

两条不肯让步的性质，决定了它值不值得信：

- **判定「修好了」的是零 LLM 的确定性代码** —— 比较改前改后两次全量测试的失败集合。
  只要冒出**任何**新的红，哪怕目标用例真的修好了，一律判「引入回归」并回滚。
- **模型碰不到 `tests/` 下的任何文件**。测试是判卷标准，允许模型改测试等于允许它改判卷
  标准 —— 而它是真的会去试的。

它**不 merge、不推默认分支、不碰你的主工作区**。交付物是一条新分支，合不合完全是你的事。

完整的原理、安全边界与诊断手段见上游文档：
[architecture](https://github.com/sumengnan/aifix-code/blob/main/docs/architecture.md) ·
[safety](https://github.com/sumengnan/aifix-code/blob/main/docs/safety.md) ·
[configuration](https://github.com/sumengnan/aifix-code/blob/main/docs/configuration.md) ·
[diagnostics](https://github.com/sumengnan/aifix-code/blob/main/docs/diagnostics.md)

---

## 一、一次性配置

本仓库已经带好两个 workflow（`.github/workflows/aifix.yml` 与
`aifix-connectivity.yml`），但**它们要配上凭据、并且合进默认分支 `dev` 才会生效**。

### 1. secrets 与 variables

```bash
# ── 机密（日志里会被遮成 ***）
gh secret set AIFIX_BASE_URL --body "https://api.deepseek.com/v1"   # 必须以 /v1 结尾
gh secret set AIFIX_API_KEY                                          # 交互输入，别写进命令行

# ── 非机密，用 variable（这样日志里看得见）
gh variable set AIFIX_FIXER__MODEL    --body deepseek-v4-flash
gh variable set AIFIX_DETECTOR__MODEL --body deepseek-v4-flash
gh variable set AIFIX_PRICE_MAP       --body '{"deepseek-v4-flash": [输入价, 输出价]}'
gh variable set AIFIX_BUDGET_USD      --body 2.0
```

**`AIFIX_BASE_URL` 必须带 `/v1`。** aifix 是往 `$BASE_URL/chat/completions` 打的，少了 `/v1`
拿到的是 404 —— 而 DeepSeek 的其他 SDK 路径能容忍这个缺失，容易误判成「我配对了」。

**价格表用 variable 不用 secret，而且必须配。** 它不是机密，放进 secret 会在日志里被遮成
`***`，你反而看不出它配没配对。而没配的后果比看不见更重：成本恒算成 0，**美元预算闸永远
不会触发**。格式是扁平价表 `{模型名: [输入价/千token, 输出价/千token]}`，不是分档表，传错
格式会在启动阶段就被拒绝。

（顺带：显式设了 `AIFIX_BUDGET_USD` 却没配价格表时，aifix 当场拒绝启动并告诉你为什么 ——
与其给一个假的保证，不如现在就停。）

价格数字自己去端点的计费页面抄，这份文档不替你填 —— **填错不会报错，只会让闸失效。**

### 2. 仓库设置里的两格

`permissions:` 给够了也不行，还有两处仓库级设置：

1. **Settings → Actions → General → Workflow permissions → 勾上「Allow GitHub Actions to
   create and approve pull requests」。** 不勾的话 PR 开不出来，报的是 *not permitted to
   create and approve pull requests*。这是接入时撞上概率第一名的坑。
2. **确认没有 ruleset / 分支保护规则匹配 `aifix/*`。** aifix 推的是一条新分支，不推 `dev`，
   所以 `dev` 上的保护不影响它；但仓库级的 push restriction 会让它「修好了却推不上去」。

### 3. 合进默认分支

⚠️ **`issues` / `issue_comment` 的 workflow 只从默认分支加载。** 本仓库的默认分支是 `dev`，
所以 `aifix.yml` 必须合进 `dev` 才会开始工作 —— 放在特性分支上是**静默不触发**，不报错，
没有任何提示。改这个文件同理，每改一次都要合一次。

### 4. 先验一次连通性

这是接 aifix 之前唯一一件「做不成就得推倒重来」的事：GitHub runner 的出口 IP 是 Azure 的
动态大段，端点若有 IP 白名单，整条 Actions 路线不成立。

```bash
gh workflow run aifix-connectivity.yml
gh run watch
```

它只发一次最小的 `POST /chat/completions`，不碰仓库、不花几分钱。绿了才往下走。

---

## 二、怎么触发

**主入口：开一个 issue，正文第一行写 `/aifix`，其余部分写缺陷描述。**

```
/aifix
出题接口在 count 传 0 时抛 ZeroDivisionError，期望返回空题目列表。

复现：
    POST /api/quiz/generate  {"count": 0}
```

那行 `/aifix` 是给机器看的标记，**会被去掉再交给模型** —— 模型读到的就是你写的描述。
描述写得越具体（怎么复现、期望什么、实际什么），写出复现测试的概率越高。

**另一条入口：在已有的 issue 下评论。** 用来再跑一次，或者回答 aifix 提的问题：

```
/aifix        # 对这个 issue 再跑一次
/aifix 2      # 回答它刚才问的问题，选第 2 项
```

两条入口都**只看第一行** —— 正文里引用别人的话、或贴一段命令示例，不会误触发。

### 谁能触发

一条原则：**触发权 = 已经能改这个仓库的人。** 因为 issue 正文会驱动模型改代码、开 PR ——
一个本来就能直接推代码的人驱动它不增加新风险，反过来则等于给了一条间接的写路径。

默认放行仓库所有者、协作者、以及组织仓库的成员。**明确不放行 `CONTRIBUTOR`**（它的含义只是
「有 commit 进过这个仓库」—— 一年前合过一个改错别字的 PR 就永久是 CONTRIBUTOR，而他今天对
这个仓库没有任何权限）。

要点名放行某个人：`gh variable set AIFIX_ALLOWED_USERS --body "alice,bob"`。它是**加法**，
上面几条照旧生效。长期授权应该走 Settings → Collaborators，那样人员变动时权限跟着一起变。

外人开的 `/aifix` issue 会被拒并收到一条说明。要修的话，**由有权限的人自己新开一个 issue、
用自己的话复述一遍** —— 不能用「在他的 issue 下评论 `/aifix`」绕过，那条路会同时检查评论者
和 issue 作者，正是为了堵住「外人提一个藏了指令的 issue，等有权限的人顺手打上 `/aifix`」。

---

## 三、三种结局，都是正常的

| 你会看到 | 意思 |
|---|---|
| 一条回帖说**缺什么信息** | issue 写得不够具体，模型如实说了写不出复现。补充再来一次 |
| 一个标题带 **`[复现已就位，未修复]`** 的 PR | 没修好，但**那条红着的复现测试本身就是产出** —— 你可以直接接手 |
| 一个 **`fix: ...`** 的 PR | 修好了，报告在 PR 正文里 |

**Actions 页面绿着是这三种的共同结果。** 写不出复现、没修好都是正常结论，不是错误。只有真
崩了（环境不对、端点不通、推不上去）才会红。

### 拿到 PR 之后照着验一遍

```bash
gh pr checkout <PR 号>

git diff dev...HEAD -- tests/    # 1. 测试文件除了新增的复现测试，一个字节都不该变
git log --oneline dev..HEAD      # 2. 分支上真的有提交，不是空 PR
git diff dev...HEAD              # 3. 亲眼看一遍 diff —— 这是唯一那道人闸
uv run pytest -q                 # 4. 在自己机器上跑一遍全量，别只信报告
```

**第 4 步别省。** aifix 的判定是可信的，但它是在 runner 那个环境上做出的。

不管成没成，`.aifix/runs/` 都会作为 artifact 上传（30 天）。下载解压后
`aifix replay <run_id> --repo <解压出来的目录>` 能看到模型每一步读了什么、改了什么、为什么
被守卫拦下。

---

## 四、本地用法

不接 Actions 也能用，主命令 `aifix run` 本来就是独立的。

```bash
# 装在隔离环境里 —— **千万别装进本项目的 .venv**：aifix 自己依赖 langgraph / pydantic /
# openai，混进去轻则版本冲突，重则污染跑 baseline 的那套环境，凭空多出一批红。
uv tool install aifix-code==0.1.2

export AIFIX_FIXER__BASE_URL="https://api.deepseek.com/v1"
export AIFIX_FIXER__API_KEY="sk-..."
export AIFIX_FIXER__MODEL="deepseek-v4-flash"
export AIFIX_DETECTOR__BASE_URL="$AIFIX_FIXER__BASE_URL"
export AIFIX_DETECTOR__API_KEY="$AIFIX_FIXER__API_KEY"
export AIFIX_DETECTOR__MODEL="$AIFIX_FIXER__MODEL"
export AIFIX_TEST_PYTHON="$PWD/.venv/bin/python"
export HARNESS_API_KEY=""      # 与 CI 一致：需要真实端点的测试据此整文件 skip

# 空跑：不调用任何模型、不花一分钱，只看它认不认得这个项目
aifix run . --dry-run

# 真跑：修当前红着的用例
aifix run . --budget 1.0
aifix run . --test 'tests/app/test_api.py::test_xxx'    # 只修其中一个
git diff dev aifix/<run_id>                              # 跑完看一眼再合
```

`--dry-run` 应当输出「适配器：pytest / 修复 **0 / 0**」（0/0 表示仓库现在全绿，正常）。
**这一步能挡掉绝大部分接入失败**：适配器认不认得、解释器对不对、工作区干不干净、测试跑不
跑得起来，全在这里暴露。

---

## 五、成本

一次 `/aifix` 触发包含两段模型调用：写复现测试（一轮）+ 修复（最多 3 轮，每轮一次诊断 +
一次修复）。上游给的参考读数是每任务约 **$0.12**（39 个真实任务，只算修复那一段），复现那
一步另算，取决于仓库规模。

三个闸都配在 workflow 里：`AIFIX_BUDGET_USD`（默认 2.0）、`AIFIX_BUDGET_WALL_SECONDS`
（3600）、以及 job 的 `timeout-minutes: 90`。准确的语义是**「越线之后不再发起新的模型调用」**，
不是「绝不超过这个数」—— 成本只有在调用返回后才知道，所以超支上界是**一次模型调用**。

**别把预算设太紧。** 实测把每任务上限从 $0.60 调到 $0.20 时，某个任务 1 轮就被掐断判成
「没修好」，放回去之后同一个任务修好了。预算设太紧会把「模型不行」和「额度不够」混成同一
个数字。

---

## 六、这个仓库特有的几件事

**`HARNESS_API_KEY` 在 aifix 的 job 里被显式置空**，与 `ci.yml` 同一条理由：需要真实端点的
测试据此整文件 skip（`tests/test_integration_real.py`）。这两处必须一致 —— aifix 的 baseline
就是判卷标准，它和 CI 判的必须是同一个东西，否则「本来就红的」那个集合对不上，「这个补丁
没弄坏别的」这个结论跟着失效。

**`uv sync --frozen` 里的 `--frozen` 不是可选的。** 不带它 `uv sync` 会重写 `uv.lock` 把工作
区弄脏，而 aifix 的 preflight 见到脏工作区直接拒绝启动。

**aifix 只管 pytest，管不到 `web/`。** 前端的 `tsc -b` 与 `npm run test` 不在它的能力范围内，
那部分照旧由 `ci.yml` 的 `web` job 把关。

**没加 `pytest-xdist`。** 全量 1253 个用例约 51 秒（2026-08-03 实测），并行省不出有意义的
时间，而引入 xdist 要先证明这套测试是 xdist-安全的。哪天套件慢到 5 分钟以上再考虑。

---

## 七、跑不起来先看这里

| 症状 | 多半是 |
|---|---|
| 开了 `/aifix` issue，**什么都没发生** | workflow 不在默认分支 `dev` 上。这条永远静默，不报错 |
| PR 开不出来，日志说 *not permitted to create and approve pull requests* | Settings 里那格没勾，见[一次性配置](#2-仓库设置里的两格) |
| 一整批 collection error，报告说「整个测试文件没能跑起来」 | `AIFIX_TEST_PYTHON` 没指对。这道闸是有意的 —— 否则模型会被派去修「这台机器上缺了点什么」，真花钱，而报告写的是「模型没修好」 |
| preflight 拒绝启动，说工作区不干净 | 装依赖改动了被跟踪的文件，多半是 `uv.lock` |
| 成本一直显示 $0.00 或「未知」 | `AIFIX_PRICE_MAP` 没配或格式错 —— 此时美元闸是失效的 |
| 连不上（超时或 DNS） | 端点有 IP 白名单。跑 `aifix-connectivity.yml` 看那行出口 IP |
| 修复跑完了，但分支推不上去 | 有 ruleset 匹配到了 `aifix/*` |
