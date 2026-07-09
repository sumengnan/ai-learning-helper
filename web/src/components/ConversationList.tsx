import type { Conversation } from "../types";
import { Box, Button, List, ListItemButton, ListItemText, IconButton } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import CloseIcon from "@mui/icons-material/Close";

export function ConversationList({ items, activeId, onSelect, onNew, onDelete }: {
  items: Conversation[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
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
            onClick={() => onSelect(c.id)}
            sx={{ "&:hover .conv-del": { opacity: 1 } }}
          >
            <ListItemText primary={c.title} slotProps={{ primary: { noWrap: true } }} />
            <IconButton
              size="small" className="conv-del" sx={{ opacity: 0 }}
              onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}
              aria-label="删除对话"
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          </ListItemButton>
        ))}
      </List>
    </Box>
  );
}
