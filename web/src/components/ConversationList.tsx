import { useState } from "react";
import type { Conversation } from "../types";
import {
  Box, Button, List, ListItemButton, ListItemText, IconButton, TextField,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import CloseIcon from "@mui/icons-material/Close";
import EditIcon from "@mui/icons-material/Edit";

export function ConversationList({ items, activeId, onSelect, onNew, onDelete, onRename }: {
  items: Conversation[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const startEdit = (c: Conversation) => { setEditingId(c.id); setDraft(c.title); };
  const commit = () => {
    if (editingId && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  };

  return (
    <Box sx={{
      width: 240, borderRight: 1, borderColor: "divider",
      display: "flex", flexDirection: "column", height: "100%",
    }}>
      <Button startIcon={<AddIcon />} variant="contained" onClick={onNew} sx={{ m: 1 }}>
        新对话
      </Button>
      <List sx={{ flex: 1, overflowY: "auto", py: 0 }}>
        {items.map((c) => (
          <ListItemButton
            key={c.id}
            selected={c.id === activeId}
            onClick={() => editingId === c.id ? undefined : onSelect(c.id)}
            sx={{ "&:hover .conv-actions": { opacity: 1 } }}
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
                <Box className="conv-actions" sx={{ opacity: 0, display: "flex" }}>
                  <IconButton
                    size="small"
                    onClick={(e) => { e.stopPropagation(); startEdit(c); }}
                    aria-label="重命名对话"
                  >
                    <EditIcon fontSize="small" />
                  </IconButton>
                  <IconButton
                    size="small"
                    onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}
                    aria-label="删除对话"
                  >
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </Box>
              </>
            )}
          </ListItemButton>
        ))}
      </List>
    </Box>
  );
}
