import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建期把版本信息编译进 bundle：CI 通过 VITE_APP_VERSION / VITE_APP_GIT_SHA /
// VITE_APP_BUILD_TIME 传入（见 Dockerfile web 阶段）。本地未注入则回退 dev，
// 页脚即可区分前端是否走了 CI 构建、跑的是哪个版本。
export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(process.env.VITE_APP_VERSION || "dev"),
    __GIT_SHA__: JSON.stringify(process.env.VITE_APP_GIT_SHA || "dev"),
    __BUILD_TIME__: JSON.stringify(process.env.VITE_APP_BUILD_TIME || ""),
  },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom", setupFiles: "./src/test-setup.ts" },
});
