import { Box, Typography, CircularProgress } from "@mui/material";
import FactCheckIcon from "@mui/icons-material/FactCheck";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { CollapsibleBlock } from "./CollapsibleBlock";
import { EllipsisText } from "./EllipsisText";
import type { ChatMessage } from "../types";

// 轮次开头下发的「本轮开了结果校验」信号（app/api/chat.py 同名常量，改这里必须同时改那里）。
// 它借 scope=verify 通道，但不是一条校验进展——此刻模型连初稿都还没生成，没有任何东西可校验。
// 唯一用途是让 ChatView 在交付前盖住本轮生成的文件（终局校验不过会重答、产物届时被服务端清掉）。
// 故校验徽章必须把它排除在外：否则回答刚起头徽章就转圈说「生成中…」，谎称正在校验，
// 而真正的校验要等模型出完初稿（「校验中…」）才开始。
//
// 字面值保留旧名 gate-open：历史消息的 progress 列里存的就是这个串，改了会让老消息里
// 这条信号被当成一条真校验事件，徽章从此永远转圈。名字里的 gate 已无对应物（交付门已删）。
export const VERIFY_OPEN_KEY = "verify:gate-open";
export const isVerifyOpen = (p: { scope: string; key?: string | null }) =>
  p.scope === "verify" && p.key === VERIFY_OPEN_KEY;

// 轨迹 judge 只对「真发生过的环节」打分：模型没调 plan 工具就没有拆分可评，该项为 null
// （见 verify.py TRAJECTORY_SYSTEM「无拆分或无步骤时对应字段给 null」）。
// null 的项直接不显示——摆一个「拆分 —」只会让人追问横线是什么意思，而它并不代表 0 分。
function scoresOf(q: NonNullable<ChatMessage["quality"]>): [string, number][] {
  const pairs: [string, number | null | undefined][] =
    [["拆分", q.plan], ["关键步", q.steps], ["最终", q.final]];
  return pairs.filter((p): p is [string, number] =>
    typeof p[1] === "number") as [string, number][];
}

// 从 progress 里重建轨迹质量分（scope="quality"，text 为 JSON）。
// message.quality 只在实时 SSE 时由 ChatView 赋值，刷新后走 ui_messages 加载则没有；
// 但 _emit_quality 同时把这条写进了 progress 列，故可从那里还原。解析失败当作没有。
function qualityFromProgress(progress: ChatMessage["progress"]): ChatMessage["quality"] {
  const items = (progress || []).filter((p) => p.scope === "quality");
  const last = items[items.length - 1];
  if (!last?.text) return null;
  try {
    const q = JSON.parse(last.text);
    return q && typeof q === "object" ? q : null;
  } catch {
    return null;      // 脏数据不该让整个气泡崩掉
  }
}

// 从 progress 里重建每步校验（scope="check"）。
// message.checks 同样只在实时 SSE 时由 ChatView 赋值，刷新后走 ui_messages 加载则没有
// （checks 不是数据库列，是从 progress 派生的）；而 scope="check" 的条目一直在 progress 列里。
// 必须复刻 ChatView 的实时逻辑：工具名取自 key `check:<tool>`，同工具合并为一行、后来者覆盖，
// 顺序按首次出现——否则刷新前后同一轮的明细会长得不一样。
function checksFromProgress(progress: ChatMessage["progress"]): NonNullable<ChatMessage["checks"]> {
  const out: NonNullable<ChatMessage["checks"]> = [];
  for (const p of progress || []) {
    if (p.scope !== "check") continue;
    const key = p.key || "";
    const tool = key.startsWith("check:")
      ? key.slice("check:".length)
      : (p.text || "").split(/\s+/)[0] || "check";
    const row = { tool, status: (p.status === "error" ? "error" : "ok") as "ok" | "error",
                  text: p.text || "" };
    const at = out.findIndex((c) => c.tool === tool);
    if (at >= 0) out[at] = row; else out.push(row);
  }
  return out;
}

// 从 progress 里取交付提醒（scope="notice"）。它只存在于 progress 列，没有独立字段——
// 提醒不参与徽章的通过/未通过判定，只作为一节明细展示，故无需像 checks 那样另开状态。
function noticesFromProgress(progress: ChatMessage["progress"]) {
  return (progress || [])
    .filter((p) => p.scope === "notice")
    .map((p) => ({ label: p.detail?.label || "检查", text: p.text || "" }));
}

// 展开明细里的来源分组标题（步骤校验 / 结果校验 / 交付提醒）
const SectionLabel = ({ text }: { text: string }) => (
  <Typography variant="caption" color="text.disabled"
    sx={{ display: "block", fontWeight: 600, letterSpacing: 0.3, py: 0.15 }}>
    {text}
  </Typography>
);

// 校验常驻徽章：脱离「展示工具调用」开关，恒在 assistant 气泡底部展示本轮校验/质量状态。
//
// 两套彼此独立的校验机制共用本徽章，主行文案必须区分二者——否则用户关掉聊天页「结果校验」开关后，
// 仅由每步校验触发的徽章仍写「校验通过」，等于谎称结果被校验过：
// - 结果校验：交付门（scope=verify，受聊天页开关 + 服务端 enable_answer_gate 双重控制）与轨迹
//   质量分（quality，由 trajectory judge 产出，同属结果层）→「结果校验通过/未通过」
// - 步骤校验：工具执行的实时标记（scope=check，由服务端 enable_step_check 控制、默认开，
//   聊天页无开关）→「步骤校验通过/未通过」
//
// - 进行中（本轮 streaming 或收到 verify running）→ spinner + 当前过程文案
// - 通过 → 「<层>校验通过」+ 若有 quality.final 显示「质量 N」
// - 未通过 → 红色「<层>校验未通过」+ 末条 verify 文案（summary）
// 展开明细按来源分组标注（步骤校验 / 结果校验 / 三层质量分），单看一行也知道它属于哪层。
// 仅当本轮有 verify/check/quality 任一信号时渲染，否则返回 null。
export function VerifyBadge({ message, live = false }: { message: ChatMessage; live?: boolean }) {
  // 排除门已开信号：它虽走 verify 通道，却先于任何校验发生，算进来会让徽章一开场就转圈
  const verify = (message.progress || []).filter((p) => p.scope === "verify" && !isVerifyOpen(p));
  // 交付后的机械检查提醒：完整性/检索依据/代码可运行/引用链接。**不参与状态判定**——
  // 它们发生在答复交付之后，既没拦下什么也没触发重答，把徽章标红等于谎称本轮失败。
  const notices = noticesFromProgress(message.progress);
  // checks 实时由 ChatView 赋值、刷新后为空 → 回退到从 progress 重建（数据一直在那）。
  // 检索命中是正常情形，不作为校验状态展示（仅保留失败/未命中等有意义的每步校验）
  const checks = (message.checks || checksFromProgress(message.progress)).filter(
    (c) => !(c.tool === "search_knowledge" && c.status === "ok"));
  // quality 只在实时 SSE 时被 ChatView 赋值；刷新后从 progress 里的 scope="quality"
  // 条目重建（_emit_quality 把同一份 JSON 既推事件也写进 progress 列），否则质量分
  // 徽章刷新即消失，而数据其实一直在。
  const quality = message.quality || qualityFromProgress(message.progress);

  // 无任何校验信号 → 不渲染徽章
  if (verify.length === 0 && checks.length === 0 && !quality && notices.length === 0) return null;

  const vLast = verify[verify.length - 1];
  // 校验历史：每一轮的终态（通过/未通过），失败轮保留原因；重答后新增新记录、通过后亦不清除
  const rounds = verify.filter((p) => p.status === "ok" || p.status === "error");
  // 仅当有多轮或出现过失败时展示历史（单轮直接通过无「过程」可留，主行已足够）
  const showHistory = rounds.length > 1 || rounds.some((p) => p.status === "error");
  const checkErr = checks.some((c) => c.status === "error");

  // 本轮结果层是否真的跑过：交付门事件或轨迹质量分任一即算。二者皆无时只剩每步校验信号，
  // 主行须降级说「步骤校验」——这正是结果校验开关关闭时的情形。
  const kind = verify.length > 0 || quality ? "结果校验" : "步骤校验";

  // 状态以「最后一个 verify 事件」为准：running 显示当前过程（结果校验中…/重答中…，故未通过→
  // 重答中→通过是连续过渡），ok/error 为终态。
  // 只有交付门会发 running：每步校验（scope=check）在工具跑完时直接出 ok/error，没有进行中态
  // （见 app/tools/validating.py）。故 state==="running" ⇒ kind 必为「结果校验」。
  // 无 verify 事件时不谎称「验证中」，按每步校验有无失败定 ok/error。
  const state: "running" | "ok" | "error" =
    vLast?.status === "running" ? "running"
      : vLast?.status === "error" ? "error"
        : vLast?.status === "ok" ? "ok"
          : checkErr ? "error" : "ok";
  void live;

  const stateIcon =
    state === "running" ? <CircularProgress size={14} />
      : state === "error" ? <CancelIcon sx={{ fontSize: 16 }} color="error" />
        : <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;

  // 进行中：原地显示当前过程文案（结果校验中…/重答中…，均出自 verify 事件），出结果后替换为终态。
  // 文案由服务端给出并自报层级；兜底串也必须带上 kind——写死「验证中…」等于把用户唯一能判断
  // 「转的是哪层」的线索丢掉，而这正是终态行一直都标着的。
  const label = state === "running" ? (vLast?.text || `${kind}中…`)
    : state === "error" ? `${kind}未通过` : `${kind}通过`;
  const qFinal = quality ? quality.final : undefined;
  const scoreRows = quality ? scoresOf(quality) : [];

  const summary = (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0, flex: 1 }}>
      {stateIcon}
      <Typography variant="caption"
        color={state === "error" ? "error" : "text.primary"}
        sx={{ fontWeight: 600, flexShrink: 0 }}>
        {label}
      </Typography>
      {state === "ok" && qFinal !== undefined && qFinal !== null && (
        <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
          · 质量 {qFinal}
        </Typography>
      )}
      {state === "error" && vLast?.text && (
        <EllipsisText text={vLast.text} sx={{ ml: 0.5, color: "text.secondary" }} maxChars={28} />
      )}
      {/* 提醒计数：正常态下也要看得见，否则用户不会想到去展开。用中性色，不抢「未通过」的红 */}
      {notices.length > 0 && (
        <Typography variant="caption" color="warning.main" sx={{ flexShrink: 0 }}>
          · {notices.length} 项提醒
        </Typography>
      )}
    </Box>
  );

  return (
    <Box>
      {/* 块名保持通用的「校验」——本块可同时含两层信号，具体是哪层由主行文案与展开分组标题交代 */}
      <CollapsibleBlock icon={<FactCheckIcon sx={{ fontSize: 15 }} color="action" />}
        title="校验" status={state} summary={summary}>
        {/* 步骤校验（scope=check，工具执行时发生、时间靠前）——带来源小标题，
            免得在结果校验也在场时被误读成交付门的明细 */}
        {checks.length > 0 && (
          <Box sx={{ mb: (showHistory || quality) ? 1 : 0 }}>
            <SectionLabel text="步骤校验" />
            {checks.map((c, i) => (
              <Box key={c.tool + i} sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.15 }}>
                {c.status === "error"
                  ? <CancelIcon sx={{ fontSize: 14 }} color="error" />
                  : <CheckCircleIcon sx={{ fontSize: 14 }} color="success" />}
                <Typography variant="caption" color="text.secondary">{c.text}</Typography>
              </Box>
            ))}
          </Box>
        )}
        {/* 结果校验：交付门历史（较晚发生），失败轮保留「哪层没过 + 原因」，通过后亦不清除 */}
        {showHistory && (
          <Box sx={{ mb: quality ? 1 : 0 }}>
            <SectionLabel text="结果校验" />
            {rounds.map((p, idx) => (
              <Box key={idx} sx={{ display: "flex", alignItems: "flex-start", gap: 0.75, py: 0.15 }}>
                {p.status === "error"
                  ? <CancelIcon sx={{ fontSize: 14, mt: 0.15 }} color="error" />
                  : <CheckCircleIcon sx={{ fontSize: 14, mt: 0.15 }} color="success" />}
                <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "pre-wrap" }}>
                  {rounds.length > 1 ? `第 ${idx + 1} 次：` : ""}
                  {p.status === "error" ? `未通过 — ${p.text}` : "校验通过"}
                </Typography>
              </Box>
            ))}
          </Box>
        )}
        {/* 交付提醒：答复交付后才跑的机械检查，只提示不影响成败，故与校验分开成一节 */}
        {notices.length > 0 && (
          <Box sx={{ mb: quality ? 1 : 0 }}>
            <SectionLabel text="交付提醒（不影响本次结果）" />
            {notices.map((n, i) => (
              <Box key={i} sx={{ display: "flex", alignItems: "flex-start", gap: 0.75, py: 0.15 }}>
                <WarningAmberIcon sx={{ fontSize: 14, mt: 0.15 }} color="warning" />
                <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "pre-wrap" }}>
                  {n.label}：{n.text}
                </Typography>
              </Box>
            ))}
          </Box>
        )}
        {quality && (
          <Box>
            {scoreRows.length > 0 && (
              <Box sx={{ display: "flex", gap: 1.5, flexWrap: "wrap", py: 0.15 }}>
                {scoreRows.map(([label, n]) => (
                  <Typography key={label} variant="caption" color="text.secondary">
                    {label} {n}
                  </Typography>
                ))}
              </Box>
            )}
            {quality.feedback && (
              <Typography variant="caption" color="text.secondary"
                sx={{ display: "block", mt: 0.25, whiteSpace: "pre-wrap" }}>
                {quality.feedback}
              </Typography>
            )}
          </Box>
        )}
      </CollapsibleBlock>
    </Box>
  );
}
