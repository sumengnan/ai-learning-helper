import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box, Card, CardContent, Typography, Stack, Button, CircularProgress, Alert,
  Select, MenuItem, IconButton, Tooltip, useTheme,
} from "@mui/material";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import AddCommentIcon from "@mui/icons-material/AddComment";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import EditNoteIcon from "@mui/icons-material/EditNote";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlined";
import DownloadIcon from "@mui/icons-material/Download";
import VisibilityIcon from "@mui/icons-material/Visibility";
import { statsApi, type StatsOverview } from "../api/stats";
import { api } from "../api/client";
import { DownloadPreviewDialog, type PreviewFile } from "./DownloadPreviewDialog";
import { previewKind } from "./downloadsUtils";
import { MemoryDrawer } from "./MemoryDrawer";
import {
  RANGES, DEFAULT_DAYS, rangeLabel, fmtTokens, fmtPct, cardSx, Eyebrow, Sparkline, fromNow,
} from "./statsShared";

// ---------- 主组件（学习主场）----------
export default function HomeView() {
  const nav = useNavigate();
  const theme = useTheme();
  const [days, setDays] = useState<number>(DEFAULT_DAYS);
  const [data, setData] = useState<StatsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const [memoryOpen, setMemoryOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    setError(null);
    statsApi.overview(days)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "加载失败"); });
    return () => { alive = false; };
  }, [days]);

  // 下载：需带 Bearer，取鉴权 blob 再触发保存
  async function download(id: string, filename: string) {
    const blob = await api.downloads.blob(id);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }

  const greeting = useMemo(() => {
    const h = new Date().getHours();
    return h < 6 ? "夜深了" : h < 12 ? "早上好" : h < 18 ? "下午好" : "晚上好";
  }, []);

  if (error) return <Box sx={{ p: 4 }}><Alert severity="error">概览加载失败：{error}</Alert></Box>;
  if (!data) return (
    <Box sx={{ display: "grid", placeItems: "center", height: "60vh" }}><CircularProgress /></Box>
  );

  const { learn } = data;
  const abilityMax = Math.max(1, ...learn.abilities.map((a) => a.count));

  return (
    <Box sx={{ width: "100%", maxWidth: 1600, mx: "auto", px: { xs: 2, sm: 2.5, md: 3 }, py: 3, pb: 8 }}>
      {/* 顶部：标题 + 时间范围 */}
      <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1, mb: 1 }}>
        <Typography sx={{ fontSize: 22, fontWeight: 700, letterSpacing: "-.02em", flex: 1 }}>概览台</Typography>
        <Select size="small" value={days} onChange={(e) => setDays(Number(e.target.value))}
          aria-label="时间范围" sx={{ minWidth: 108, "& .MuiSelect-select": { py: 0.7 } }}>
          {RANGES.map((r) => <MenuItem key={r.days} value={r.days}>{r.label}</MenuItem>)}
        </Select>
      </Stack>

      {/* 欢迎 + 继续 + 快捷 */}
      <Typography sx={{ fontSize: 26, fontWeight: 700, letterSpacing: "-.02em", mt: 2 }}>
        {greeting}，继续保持
      </Typography>
      <Typography sx={{ color: "text.secondary", fontSize: 14.5, mb: 2 }}>
        下面是你的学习进度，以及 AI 最近为你做了什么
      </Typography>
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
            ].map((q) => (
              <Box key={q.label} onClick={() => nav(q.to)} sx={{
                display: "flex", flexDirection: "column", gap: 0.75, p: 1.5, cursor: "pointer",
                border: 1, borderColor: "divider", borderRadius: 2, transition: ".15s",
                "&:hover": { borderColor: "primary.main", transform: "translateY(-1px)" },
              }}>
                <Box sx={{ color: "primary.main", display: "flex" }}>{q.icon}</Box>
                <Typography sx={{ fontSize: 13.5, fontWeight: 600 }}>{q.label}</Typography>
              </Box>
            ))}
          </CardContent>
        </Card>
      </Box>

      {/* 我的积累 */}
      <Eyebrow>我的积累</Eyebrow>
      <Box sx={{ display: "grid", gap: 1.75, gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(4,1fr)" } }}>
        {[
          { lbl: "📚 学习资料", v: learn.assets.documents, sub: "已建索引 · 查看 →", onClick: () => nav("/knowledge") },
          { lbl: "🧠 AI 记的偏好", v: learn.assets.memory, sub: "点击查看它记住了什么 →", onClick: () => setMemoryOpen(true) },
          { lbl: "✏️ 题库", v: learn.assets.questions, sub: learn.assets.questions ? "去练习 →" : "生成一套 →", act: true, onClick: () => nav("/questions") },
          { lbl: "❌ 错题本", v: learn.assets.wrong_answers, sub: learn.assets.wrong_answers ? "去复习 →" : "目前全对 👍", onClick: () => nav("/wrong") },
        ].map((a) => (
          <Card key={a.lbl} onClick={a.onClick} sx={(t) => ({ ...cardSx(t), cursor: "pointer",
            transition: ".15s", "&:hover": { borderColor: "primary.main", transform: "translateY(-1px)" } })}>
            <CardContent>
              <Typography sx={{ fontSize: 12.5, color: "text.secondary" }}>{a.lbl}</Typography>
              <Typography sx={{ fontSize: 29, fontWeight: 700, letterSpacing: "-.025em",
                fontVariantNumeric: "tabular-nums", mt: 0.3 }}>{a.v}</Typography>
              <Typography sx={{ fontSize: 12, color: a.act ? "primary.main" : "text.disabled",
                fontWeight: a.act ? 600 : 400 }}>{a.sub}</Typography>
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
    </Box>
  );
}
