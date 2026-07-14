// 「概览」页签（原首页「学习主场」）：我的积累、继续学习、AI 在为我做什么。
// 数据与时间范围由父组件 HomeView 统一拉取并下发，本组件不自取数。
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Card, CardContent, Typography, Stack, Button, IconButton, Tooltip, useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useAuth } from "../auth/AuthProvider";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import AddCommentIcon from "@mui/icons-material/AddComment";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import EditNoteIcon from "@mui/icons-material/EditNote";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlined";
import DownloadIcon from "@mui/icons-material/Download";
import VisibilityIcon from "@mui/icons-material/Visibility";
import type { StatsOverview } from "../api/stats";
import { api } from "../api/client";
import { DownloadPreviewDialog, type PreviewFile } from "./DownloadPreviewDialog";
import { previewKind } from "./downloadsUtils";
import { MemoryDrawer } from "./MemoryDrawer";
import { rangeLabel, fmtTokens, fmtPct, cardSx, Eyebrow, Sparkline, fromNow } from "./statsShared";

// 按当前时段给出问候语与配图
function greetingOf(hour: number): { text: string; emoji: string } {
  if (hour < 6) return { text: "夜深了", emoji: "🌙" };
  if (hour < 12) return { text: "早上好", emoji: "🌅" };
  if (hour < 14) return { text: "中午好", emoji: "🌤️" };
  if (hour < 18) return { text: "下午好", emoji: "☀️" };
  return { text: "晚上好", emoji: "🌆" };
}

// 我的积累四张卡的主题色（各一色，打破清一色白卡的单调）
type Hue = "info" | "primary" | "warning" | "secondary";

export function OverviewTab({ data, days }: { data: StatsOverview; days: number }) {
  const nav = useNavigate();
  const theme = useTheme();
  const { user } = useAuth();
  const greet = greetingOf(new Date().getHours());
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const [memoryOpen, setMemoryOpen] = useState(false);

  // 下载：需带 Bearer，取鉴权 blob 再触发保存
  async function download(id: string, filename: string) {
    const blob = await api.downloads.blob(id);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }

  const { learn } = data;
  const abilityMax = Math.max(1, ...learn.abilities.map((a) => a.count));

  const assetCards: { icon: string; lbl: string; v: number; sub: string; hue: Hue; act?: boolean; onClick: () => void }[] = [
    { icon: "📚", lbl: "学习资料", v: learn.assets.documents, sub: "已建索引 · 查看 →", hue: "info", onClick: () => nav("/knowledge") },
    { icon: "✏️", lbl: "题库", v: learn.assets.questions, sub: learn.assets.questions ? "去练习 →" : "生成一套 →", hue: "primary", act: true, onClick: () => nav("/questions") },
    { icon: "❌", lbl: "错题本", v: learn.assets.wrong_answers, sub: learn.assets.wrong_answers ? "去复习 →" : "目前全对 👍", hue: "warning", onClick: () => nav("/wrong") },
    { icon: "🧠", lbl: "AI 记的偏好", v: learn.assets.memory, sub: "点击查看它记住了什么 →", hue: "secondary", onClick: () => setMemoryOpen(true) },
  ];

  return (
    <>
      {/* 问候横幅：按时段问候 + 用户名，柔和渐变，给页面一个有温度的开场 */}
      <Box sx={(t) => ({
        position: "relative", overflow: "hidden", borderRadius: 3,
        px: { xs: 2.5, md: 3.5 }, py: { xs: 2.25, md: 2.75 },
        border: 1, borderColor: "divider",
        background: `linear-gradient(120deg, ${alpha(t.palette.primary.main, t.palette.mode === "light" ? 0.12 : 0.22)}, ${alpha(t.palette.secondary.main, t.palette.mode === "light" ? 0.08 : 0.16)})`,
      })}>
        <Typography sx={{ fontSize: { xs: 20, md: 24 }, fontWeight: 750, letterSpacing: "-.02em" }}>
          {greet.emoji} {greet.text}{user?.username ? `，${user.username}` : ""}
        </Typography>
        <Typography sx={{ fontSize: 13.5, color: "text.secondary", mt: 0.5 }}>
          已积累 {learn.assets.documents} 份资料 · {learn.assets.questions} 道题 · AI 记住你 {learn.assets.memory} 条偏好，继续加油 ✨
        </Typography>
        {/* 右侧装饰光斑 */}
        <Box aria-hidden sx={(t) => ({
          position: "absolute", right: -40, top: -40, width: 160, height: 160, borderRadius: "50%",
          background: `radial-gradient(circle, ${alpha(t.palette.primary.main, 0.18)}, transparent 70%)`,
          pointerEvents: "none",
        })} />
      </Box>

      {/* 我的积累 */}
      <Eyebrow>我的积累</Eyebrow>
      <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4,1fr)" } }}>
        {assetCards.map((a) => (
          <Card key={a.lbl} onClick={a.onClick} sx={(t) => ({ ...cardSx(t),
            position: "relative", overflow: "hidden", cursor: "pointer", transition: ".18s",
            "&:hover": { borderColor: t.palette[a.hue].main, transform: "translateY(-2px)",
              boxShadow: `0 12px 26px -16px ${alpha(t.palette[a.hue].main, 0.6)}` } })}>
            {/* 顶部主题色细条 */}
            <Box sx={(t) => ({ position: "absolute", left: 0, top: 0, right: 0, height: 3, bgcolor: t.palette[a.hue].main })} />
            <CardContent>
              <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                <Box sx={(t) => ({ width: 32, height: 32, flex: "none", borderRadius: 2,
                  display: "grid", placeItems: "center", fontSize: 17,
                  bgcolor: alpha(t.palette[a.hue].main, 0.12) })}>{a.icon}</Box>
                <Typography sx={{ fontSize: 12.5, color: "text.secondary", fontWeight: 600 }}>{a.lbl}</Typography>
              </Stack>
              <Typography sx={{ fontSize: 29, fontWeight: 700, letterSpacing: "-.025em",
                fontVariantNumeric: "tabular-nums", mt: 0.6 }}>{a.v}</Typography>
              <Typography sx={(t) => ({ fontSize: 12, mt: 0.1,
                color: a.act ? t.palette[a.hue].main : "text.disabled",
                fontWeight: a.act ? 600 : 400 })}>{a.sub}</Typography>
            </CardContent>
          </Card>
        ))}
      </Box>
      {learn.recent_downloads.length > 0 && (
        <Card sx={(t) => ({ ...cardSx(t), mt: 1.75 })}>
          <CardContent sx={{ display: "flex", alignItems: "center", gap: 1.25, flexWrap: "wrap",
            py: 1.5, "&:last-child": { pb: 1.5 } }}>
            <Typography sx={{ fontSize: 12.5, color: "text.secondary", fontWeight: 600, mr: 0.5 }}>最近生成的产物</Typography>
            {learn.recent_downloads.map((d) => {
              const canPreview = previewKind(d.content_type) !== "none";
              return (
                <Stack key={d.id} direction="row" spacing={0.25} sx={{ alignItems: "center",
                  border: 1, borderColor: "divider", borderRadius: 5, pl: 1.25, pr: 0.25, py: 0.25 }}>
                  <Typography noWrap sx={{ fontSize: 13, maxWidth: 220 }}>{d.filename}</Typography>
                  {canPreview && (
                    <Tooltip title="预览"><IconButton size="small" aria-label={`预览 ${d.filename}`}
                      onClick={() => setPreview({ id: d.id, filename: d.filename, content_type: d.content_type })}>
                      <VisibilityIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
                  )}
                  <Tooltip title="下载"><IconButton size="small" aria-label={`下载 ${d.filename}`}
                    onClick={() => download(d.id, d.filename)}>
                    <DownloadIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
                </Stack>
              );
            })}
            <Box sx={{ flex: 1 }} />
            <Typography onClick={() => nav("/downloads")}
              sx={{ fontSize: 13, color: "primary.main", fontWeight: 600, cursor: "pointer" }}>
              查看全部 →
            </Typography>
          </CardContent>
        </Card>
      )}

      {/* 继续学习：继续上次 + 快捷入口 */}
      <Eyebrow>继续学习</Eyebrow>
      <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr", md: "1.5fr 1fr" } }}>
        <Card sx={(t) => ({ ...cardSx(t), position: "relative", overflow: "hidden" })}>
          <Box sx={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 4, bgcolor: "primary.main" }} />
          <CardContent>
            <Typography sx={{ fontSize: 11.5, fontWeight: 700, letterSpacing: ".05em",
              textTransform: "uppercase", color: "text.disabled" }}>继续上次</Typography>
            {learn.last_conversation ? (
              <>
                <Typography sx={{ fontSize: 17, fontWeight: 650, mt: 0.5 }}>
                  {learn.last_conversation.title}
                </Typography>
                <Typography sx={{ color: "text.secondary", fontSize: 13, mt: 0.3 }}>
                  {fromNow(learn.last_conversation.updated_at)} · {learn.last_conversation.message_count} 条消息
                </Typography>
                <Button variant="contained" endIcon={<ArrowForwardIcon />} sx={{ mt: 1.5, textTransform: "none" }}
                  onClick={() => nav("/chat")}>继续对话</Button>
              </>
            ) : (
              <>
                <Typography sx={{ fontSize: 16, fontWeight: 600, mt: 0.5 }}>还没有对话</Typography>
                <Typography sx={{ color: "text.secondary", fontSize: 13, mt: 0.3 }}>
                  开始你的第一次提问，AI 会用工具帮你查资料、跑代码、出题
                </Typography>
                <Button variant="contained" endIcon={<ArrowForwardIcon />} sx={{ mt: 1.5, textTransform: "none" }}
                  onClick={() => nav("/chat")}>开始对话</Button>
              </>
            )}
          </CardContent>
        </Card>
        <Card sx={cardSx}>
          <CardContent sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1.25 }}>
            {[
              { icon: <AddCommentIcon />, label: "新对话", to: "/chat" },
              { icon: <UploadFileIcon />, label: "上传资料", to: "/knowledge" },
              { icon: <EditNoteIcon />, label: "做题练习", to: "/questions" },
              { icon: <ErrorOutlineIcon />, label: "看错题本", to: "/wrong" },
            ].map((qk) => (
              <Box key={qk.label} onClick={() => nav(qk.to)} sx={{
                display: "flex", flexDirection: "column", gap: 0.75, p: 1.5, cursor: "pointer",
                border: 1, borderColor: "divider", borderRadius: 2, transition: ".15s",
                "&:hover": { borderColor: "primary.main", transform: "translateY(-1px)" },
              }}>
                <Box sx={{ color: "primary.main", display: "flex" }}>{qk.icon}</Box>
                <Typography sx={{ fontSize: 13.5, fontWeight: 600 }}>{qk.label}</Typography>
              </Box>
            ))}
          </CardContent>
        </Card>
      </Box>

      {/* AI 在为我做什么 */}
      <Eyebrow>AI 在为我做什么</Eyebrow>
      <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr", md: "1.2fr 1fr" } }}>
        <Card sx={cardSx}>
          <CardContent>
            <Typography sx={{ fontSize: 15, fontWeight: 650 }}>它用过的能力</Typography>
            <Typography sx={{ fontSize: 12.5, color: "text.secondary", mb: 2 }}>
              {rangeLabel(days)}，AI 为回答你的问题实际动用的工具 —— 用得越多条越长 · 悬停看次数
            </Typography>
            {learn.abilities.length === 0 ? (
              <Typography sx={{ fontSize: 13, color: "text.disabled" }}>还没有调用记录</Typography>
            ) : (
              <Stack spacing={1.25}>
                {learn.abilities.map((a) => (
                  <Tooltip key={a.label} arrow followCursor placement="top"
                    title={`${a.label} · 调用 ${a.count} 次`}>
                    <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", cursor: "pointer" }}>
                      <Box sx={{ width: 30, height: 30, flex: "none", borderRadius: 2, display: "grid",
                        placeItems: "center", bgcolor: "action.hover", fontSize: 15 }}>{a.icon}</Box>
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography sx={{ fontSize: 13.5, fontWeight: 550 }}>{a.label}</Typography>
                        <Box sx={{ height: 5, borderRadius: 3, bgcolor: "action.selected", mt: 0.6, overflow: "hidden" }}>
                          <Box sx={{ height: "100%", width: `${(a.count / abilityMax) * 100}%`,
                            bgcolor: "primary.main", borderRadius: 3 }} />
                        </Box>
                      </Box>
                      <Typography sx={{ fontFamily: "monospace", fontSize: 12.5, color: "text.secondary",
                        fontVariantNumeric: "tabular-nums" }}>{a.count}</Typography>
                    </Stack>
                  </Tooltip>
                ))}
              </Stack>
            )}
          </CardContent>
        </Card>
        <Card sx={cardSx}>
          <CardContent>
            <Typography sx={{ fontSize: 15, fontWeight: 650 }}>它为你花的力气</Typography>
            <Typography sx={{ fontSize: 12.5, color: "text.secondary", mb: 2 }}>同一份运行数据，换成你能感知的说法</Typography>
            <Stack direction="row" spacing={2.5} sx={{ mb: 1.5 }}>
              <Box>
                <Typography sx={{ fontSize: 26, fontWeight: 700, letterSpacing: "-.025em",
                  fontVariantNumeric: "tabular-nums" }}>{learn.effort.runs}</Typography>
                <Typography sx={{ fontSize: 12, color: "text.secondary" }}>次独立思考</Typography>
              </Box>
              <Box>
                <Typography sx={{ fontSize: 26, fontWeight: 700, letterSpacing: "-.025em",
                  fontVariantNumeric: "tabular-nums" }}>{fmtTokens(learn.effort.total_tokens)}</Typography>
                <Typography sx={{ fontSize: 12, color: "text.secondary" }}>token 投入</Typography>
              </Box>
            </Stack>
            <Stack spacing={0.9} sx={{ fontSize: 13, color: "text.secondary", mb: 1 }}>
              <Stack direction="row" sx={{ justifyContent: "space-between" }}>
                <span>平均每个问题想</span><b style={{ color: theme.palette.text.primary }}>≈ {learn.effort.avg_steps} 步</b>
              </Stack>
              <Stack direction="row" sx={{ justifyContent: "space-between" }}>
                <span>最深的一次推理</span><b style={{ color: theme.palette.text.primary }}>{learn.effort.max_steps} 步</b>
              </Stack>
              <Stack direction="row" sx={{ justifyContent: "space-between" }}>
                <span>一次答对、没返工</span><b style={{ color: theme.palette.text.primary }}>{fmtPct(learn.effort.success_rate)}</b>
              </Stack>
            </Stack>
            <Sparkline data={learn.activity} />
            <Typography sx={{ fontSize: 11.5, color: "text.disabled", mt: 0.5 }}>
              每日活跃度 · 越高说明这天 AI 为你干得越多
            </Typography>
          </CardContent>
        </Card>
      </Box>

      <DownloadPreviewDialog file={preview} onClose={() => setPreview(null)} />
      <MemoryDrawer open={memoryOpen} onClose={() => setMemoryOpen(false)} />
    </>
  );
}
