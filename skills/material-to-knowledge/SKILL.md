---
name: material-to-knowledge
description: 资料消化入库——把上传的文档/笔记提炼要点、存入知识库、自动出题入库并生成复习提纲
---
# 资料消化·一键变学材

当用户上传了资料并说「帮我整理这份」「把这个 PDF 变成能学的」「据此出题」时使用，把一份原始资料变成可检索、可测验、可复习的学材。

## 步骤

1. **读料**：`list_attachments` 看有哪些附件，`read_attachment(attachment_id)` 读取用户要处理的那份。
2. **提炼结构**：通读后梳理出**知识点大纲**（章节/主题 → 要点），点出重点与难点；太长的资料分主题处理。
3. **入知识库**：`save_to_knowledge(title, text)` 把整理后的要点存入知识库（供以后检索与 grounding）。按主题分条存，标题清晰便于日后检索。
4. **出题入库**：对每个核心知识点 `generate_questions(topic=知识点, count=n)` 从知识库出题并自动入题库（自带去重）；知识库覆盖不到的补充点用 `add_questions(questions=[...])` 现编入库。记住返回末尾 `〔题目ID:...〕` 里的 id。
5. **复习提纲**：`save_download` 导出一份 Markdown 复习提纲（大纲 + 重点 + 「已入库 N 道题」提示），方便用户离线复习。
6. **交接**：告诉用户「资料已入库、出了 N 道题」，并提示可以「就考这几道」（用 `start_exam(source="ids", question_ids=[...])`）或之后「讲讲某个点」。
7. **收尾**：`unload_skill("material-to-knowledge")` 释放上下文。

## 注意
- 入库前先提炼结构，别把原文整段灌进去——知识库要的是「可检索的要点」。
- 出题走 `generate_questions`/`add_questions`（自带去重），不要手动重复添加同题。
- 资料很长时分主题多次 `save_to_knowledge`，每条标题独立清晰。
