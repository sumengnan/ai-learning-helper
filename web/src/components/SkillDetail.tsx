import { useEffect, useState } from "react";
import { Box, Typography, CircularProgress } from "@mui/material";
import { api, type SkillDetail as SkillDetailData } from "../api/client";
import { Markdown } from "./Markdown";

// 技能正文按需拉取，而不是随 skill 进度事件下发：正文是静态资源（随部署固定，2-3KB/个），
// 塞进事件就会连同 progress 列一起，在每条命中技能的助手消息里各存一份。
// 技能可能已被删除或改名（历史消息里的名字指向不存在的技能）→ 显示错误而不是干转圈。
export function SkillDetail({ name }: { name: string }) {
  const [data, setData] = useState<SkillDetailData | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr("");
    api.skills
      .get(name)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setErr(String(e?.message || e) || "技能加载失败"); });
    // 展开后立刻收起（或切到另一条技能）时不再 setState，避免更新已卸载的组件
    return () => { alive = false; };
  }, [name]);

  return (
    <Box sx={{ mt: 0.5, mb: 0.5, ml: 2.5, pl: 1.25, borderLeft: 2, borderColor: "divider" }}>
      {err ? (
        <Typography variant="caption" color="error">{err}</Typography>
      ) : !data ? (
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, py: 0.5 }}>
          <CircularProgress size={12} />
          <Typography variant="caption" color="text.secondary">加载技能内容…</Typography>
        </Box>
      ) : (
        // 正文按 markdown 渲染（技能就是一份 SKILL.md）。字号压到 caption 级：
        // 这是参考资料，不该在视觉上盖过对话正文。
        <Box sx={{ fontSize: 13, "& :first-of-type": { mt: 0 }, "& :last-child": { mb: 0 } }}>
          <Markdown>{data.body}</Markdown>
        </Box>
      )}
    </Box>
  );
}
