// web/src/components/Captcha.tsx
// 后端图形验证码：向 /api/auth/captcha 取「token + 图片」，展示图片、点击可刷新；
// token 通过 onToken 上报父组件，随登录/注册回传后端校验（服务端 HMAC 无状态验证）。
import { useCallback, useEffect, useState } from "react";
import { Box, CircularProgress } from "@mui/material";
import RefreshRoundedIcon from "@mui/icons-material/RefreshRounded";
import { auth as authApi } from "../api/client";

const CANVAS_W = 120;
const CANVAS_H = 44;

/**
 * 图形验证码。onToken 在每次（含首次/刷新）取到新验证码时回调，供父组件保存 token；
 * 取码失败时回调空串（父组件据此可提示"验证码加载失败"）。
 */
export function Captcha({
  onToken,
  disabled = false,
}: {
  onToken: (token: string) => void;
  disabled?: boolean;
}) {
  const [image, setImage] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    if (disabled) return;
    setLoading(true);
    setFailed(false);
    try {
      const { token, image: img } = await authApi.captcha();
      setImage(img);
      onToken(token);
    } catch {
      setImage("");
      setFailed(true);
      onToken("");
    } finally {
      setLoading(false);
    }
  }, [disabled, onToken]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Box
      role="button"
      aria-label="点击刷新验证码"
      title="看不清？点击换一张"
      onClick={load}
      sx={{
        cursor: disabled ? "default" : "pointer",
        width: CANVAS_W, height: CANVAS_H, flex: "0 0 auto",
        borderRadius: 1.5, overflow: "hidden", position: "relative",
        display: "grid", placeItems: "center",
        border: (t) => `1px solid ${t.palette.divider}`,
        bgcolor: (t) => (t.palette.mode === "dark" ? "#22242c" : "#f1f3f8"),
        userSelect: "none",
        transition: "transform .1s",
        "&:active": { transform: disabled ? "none" : "scale(0.97)" },
      }}
    >
      {loading ? (
        <CircularProgress size={18} />
      ) : failed ? (
        <RefreshRoundedIcon fontSize="small" color="action" />
      ) : (
        <Box component="img" src={image} alt="验证码"
          sx={{ display: "block", width: CANVAS_W, height: CANVAS_H }} />
      )}
    </Box>
  );
}
