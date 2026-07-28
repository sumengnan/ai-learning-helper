// web/src/components/BeianFooter.tsx
import { useEffect, useState } from "react";
import { Box, Divider, Link, Stack, Typography } from "@mui/material";
import { fetchSiteInfo, type SiteInfo } from "../api/client";

// 公安备案查询页要的是备案号里那串数字（「京公网安备 11010102000001号」→ 11010102000001）
const policeCode = (s: string) => s.match(/\d{6,}/)?.[0] ?? "";

/** 页脚备案信息，登录/注册页与主外壳共用。三项均未配置（或接口取不到）时整块不渲染。
 *
 * `divider`：在上方画一条分隔线，供主外壳把它与内容区隔开。分隔线画在组件内部而非交给
 * 调用方包一层，正是因为"未配置就整块不渲染"——外层包的边框会在没有备案信息时空留一条横线。
 */
export function BeianFooter({ divider = false }: { divider?: boolean }) {
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
    <Box component="footer" sx={{ textAlign: "center", flexShrink: 0 }}>
      {divider ? <Divider /> : null}
      <Stack
        direction="row" spacing={1.5} useFlexGap
        sx={{ flexWrap: "wrap", justifyContent: "center", alignItems: "center", px: 2, py: 1 }}
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
            sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
          >
            {/* 公安备案惯例：备案号前带警徽图标。图放 web/public，构建后位于站点根 /beian-gongan.png。
                alt 留空作装饰性图标——紧邻的备案号文字已表意，避免读屏器重复播报。 */}
            <Box
              component="img" src="/beian-gongan.png" alt=""
              sx={{ height: 15, width: "auto", display: "block" }}
            />
            {police}
          </Link>
        )}
      </Stack>
    </Box>
  );
}
