import { Fragment, useState } from "react";
import type { Conversation } from "../types";
import {
  Box, Button, List, ListItemButton, ListItemText, ListSubheader, IconButton,
  TextField, Typography, Dialog, DialogTitle, DialogContent, DialogContentText,
  DialogActions,
} from "@mui/material";
import { AnimatePresence, motion } from "framer-motion";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutlined";
import EditIcon from "@mui/icons-material/Edit";
import { chromeBg } from "./AppShell";
import { listItemVariants } from "./motion";

// 依据创建时间与今天的自然日差，归入「今天 / 昨天 / 3天前 / …」分组
function dayDiff(iso: string): number {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 0;
  const startOfDay = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  return Math.round((startOfDay(new Date()) - startOfDay(d)) / 86_400_000);
}
function bucketLabel(iso: string): string {
  const diff = dayDiff(iso);
  if (diff <= 0) return "今天";
  if (diff === 1) return "昨天";
  if (diff <= 3) return "3天前";
  if (diff <= 7) return "7天前";
  if (diff <= 30) return "30天前";
  return "更久";
}
// items 已按 created_at 倒序，相同分组必连续，据此切段
function groupByTime(items: Conversation[]): { label: string; items: Conversation[] }[] {
  const groups: { label: string; items: Conversation[] }[] = [];
  for (const c of items) {
    const label = bucketLabel(c.created_at);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(c);
    else groups.push({ label, items: [c] });
  }
  return groups;
}

export function ConversationList({ items, activeId, onSelect, onNew, onDelete, onRename }: {
  items: Conversation[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);

  const startEdit = (c: Conversation) => { setEditingId(c.id); setDraft(c.title); };
  const commit = () => {
    if (editingId && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  };
  const confirmDelete = () => {
    if (pendingDelete) onDelete(pendingDelete.id);
    setPendingDelete(null);
  };

  const groups = groupByTime(items);

  return (
    <Box sx={{
      width: 260, borderRight: 1, borderColor: "divider",
      display: "flex", flexDirection: "column", height: "100%",
      bgcolor: chromeBg,
    }}>
      <Box sx={{ p: 1.5 }}>
        <Button fullWidth startIcon={<AddIcon />} variant="contained" disableElevation onClick={onNew}>
          新对话
        </Button>
      </Box>
      <List sx={{ flex: 1, overflowY: "auto", py: 0 }}>
        {items.length === 0 && (
          <Typography
            variant="body2" color="text.secondary"
            sx={{ px: 2, py: 3, textAlign: "center" }}
          >
            暂无历史对话
          </Typography>
        )}
        {groups.map((g) => (
          <Fragment key={g.label}>
            <ListSubheader
              disableSticky
              sx={{
                bgcolor: "transparent", color: "text.secondary",
                fontWeight: 600, lineHeight: "32px",
              }}
            >
              {g.label}
            </ListSubheader>
            <AnimatePresence initial={false}>
            {g.items.map((c) => (
              <motion.div
                key={c.id}
                layout
                variants={listItemVariants}
                initial="initial"
                animate="animate"
                exit="exit"
              >
              <ListItemButton
                selected={c.id === activeId}
                onClick={() => (editingId === c.id ? undefined : onSelect(c.id))}
                sx={{
                  mx: 1, borderRadius: 1.5,
                  "&:hover .conv-actions": { opacity: 1 },
                }}
              >
                {editingId === c.id ? (
                  <TextField
                    autoFocus fullWidth size="small" variant="standard" value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    onBlur={commit}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commit();
                      else if (e.key === "Escape") setEditingId(null);
                    }}
                  />
                ) : (
                  <>
                    <ListItemText
                      primary={c.title}
                      slotProps={{ primary: { noWrap: true } }}
                      onDoubleClick={() => startEdit(c)}
                    />
                    <Box className="conv-actions" sx={{ opacity: 0, display: "flex", ml: 0.5 }}>
                      <IconButton
                        size="small"
                        onClick={(e) => { e.stopPropagation(); startEdit(c); }}
                        aria-label="重命名对话"
                      >
                        <EditIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        onClick={(e) => { e.stopPropagation(); setPendingDelete(c); }}
                        aria-label="删除对话"
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </Box>
                  </>
                )}
              </ListItemButton>
              </motion.div>
            ))}
            </AnimatePresence>
          </Fragment>
        ))}
      </List>

      <Dialog open={Boolean(pendingDelete)} onClose={() => setPendingDelete(null)}>
        <DialogTitle>删除对话</DialogTitle>
        <DialogContent>
          <DialogContentText component="div">
            确定删除对话「{pendingDelete?.title}」吗？删除后将<strong>一并清除</strong>：
            <Box component="ul" sx={{ mt: 1, mb: 1.5, pl: 2.5 }}>
              <li>本对话的全部聊天记录与消息</li>
              <li>工具调用与执行进度轨迹</li>
              <li>本对话的 Agent 运行数据（检查点与轨迹）</li>
              <li>本对话的沙箱容器及其中生成的临时文件</li>
            </Box>
            <Typography variant="body2" color="text.secondary" component="div">
              以下内容<strong>不会</strong>被删除：你的知识库文档、题库与错题集、
              已保存到「下载」的文件、账号记忆。
            </Typography>
            <Typography variant="body2" color="error" component="div" sx={{ mt: 1 }}>
              此操作不可撤销。
            </Typography>
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPendingDelete(null)}>取消</Button>
          <Button color="error" variant="contained" disableElevation onClick={confirmDelete}>
            删除
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
