// web/src/components/BeianFooter.tsx
import { useEffect, useState } from "react";
import { Box, Link, Stack, Typography } from "@mui/material";
import { fetchSiteInfo, type SiteInfo } from "../api/client";

// 公安备案查询页要的是备案号里那串数字（「京公网安备 11010102000001号」→ 11010102000001）
const policeCode = (s: string) => s.match(/\d{6,}/)?.[0] ?? "";

/** 登录/注册页页脚的备案信息。三项均未配置（或接口取不到）时整块不渲染。 */
export function BeianFooter() {
  const [info, setInfo] = useState<SiteInfo | null>(null);

  useEffect(() => {
    let alive = true;
    // 取不到就当没配：备案信息缺失不该让登录页报错或空占位
    fetchSiteInfo()
      .then((s) => alive && setInfo(s))
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  if (!info) return null;
  const { icp, police_icp: police, copyright } = info;
  if (!icp && !police && !copyright) return null;

  const code = policeCode(police);

  return (
    <Box component="footer" sx={{ mt: 2, textAlign: "center" }}>
      <Stack
        direction="row" spacing={1.5} useFlexGap
        sx={{ flexWrap: "wrap", justifyContent: "center", alignItems: "center" }}
      >
        {copyright && (
          <Typography variant="caption" color="text.secondary">
            © {new Date().getFullYear()} {copyright}
          </Typography>
        )}
        {icp && (
          <Link
            variant="caption" color="text.secondary" underline="hover"
            href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer"
          >
            {icp}
          </Link>
        )}
        {police && (
          <Link
            variant="caption" color="text.secondary" underline="hover"
            href={`https://beian.mps.gov.cn/#/query/webSearch?code=${code}`}
            target="_blank" rel="noreferrer"
          >
            {police}
          </Link>
        )}
      </Stack>
    </Box>
  );
}
