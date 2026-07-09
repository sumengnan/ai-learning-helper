import { useEffect, useState } from "react";
import { Box, Typography, Card, CardContent, IconButton, Tooltip } from "@mui/material";
import DownloadIcon from "@mui/icons-material/Download";
import DeleteIcon from "@mui/icons-material/Delete";
import { api } from "../api/client";

interface Download {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  created_at: string;
}

export default function DownloadsView() {
  const [items, setItems] = useState<Download[]>([]);

  const refresh = () => api.downloads.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const remove = async (id: string) => { await api.downloads.remove(id); await refresh(); };

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography variant="h5" sx={{ fontWeight: 700 }}>下载管理</Typography>
      {items.length === 0 ? (
        <Typography color="text.secondary">
          暂无文件。聊天中让助手用 save_download 保存内容后会出现在这里。
        </Typography>
      ) : (
        items.map((d) => (
          <Card key={d.id} variant="outlined">
            <CardContent sx={{ display: "flex", alignItems: "center", gap: 2 }}>
              {d.content_type.startsWith("image/") && (
                <Box
                  component="img" src={`/api/downloads/${d.id}`} alt={d.filename}
                  sx={{ width: 48, height: 48, objectFit: "cover", borderRadius: 1, border: 1, borderColor: "divider" }}
                />
              )}
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography noWrap>{d.filename}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {d.content_type} · {d.size} 字节 · {d.created_at.slice(0, 10)}
                </Typography>
              </Box>
              <Tooltip title="下载">
                <IconButton component="a" href={`/api/downloads/${d.id}`} download={d.filename} aria-label="下载文件">
                  <DownloadIcon />
                </IconButton>
              </Tooltip>
              <IconButton color="error" onClick={() => remove(d.id)} aria-label="删除文件">
                <DeleteIcon />
              </IconButton>
            </CardContent>
          </Card>
        ))
      )}
    </Box>
  );
}
