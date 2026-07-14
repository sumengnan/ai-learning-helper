// web/src/App.tsx
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ChatPage } from "./pages/ChatPage";
import HomeView from "./pages/HomeView";
import { KnowledgeView } from "./pages/KnowledgeView";
import QuestionBankView from "./pages/QuestionBankView";
import WrongAnswersView from "./pages/WrongAnswersView";
import DownloadsView from "./pages/DownloadsView";
import { pageVariants } from "./components/motion";

function ShellRoutes() {
  const location = useLocation();
  return (
    <AppShell>
      <AnimatePresence mode="wait">
        <motion.div
          key={location.pathname}
          variants={pageVariants}
          initial="initial"
          animate="animate"
          exit="exit"
          style={{ height: "100%" }}
        >
          <Routes location={location}>
            <Route path="/" element={<HomeView />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/knowledge" element={<KnowledgeView />} />
            <Route path="/questions" element={<QuestionBankView />} />
            <Route path="/wrong" element={<WrongAnswersView />} />
            <Route path="/downloads" element={<DownloadsView />} />
            {/* AI 运行统计：与首页「概览」同一组件，靠路径切页签 */}
            <Route path="/monitor" element={<HomeView />} />
          </Routes>
        </motion.div>
      </AnimatePresence>
    </AppShell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/*" element={<RequireAuth><ShellRoutes /></RequireAuth>} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
