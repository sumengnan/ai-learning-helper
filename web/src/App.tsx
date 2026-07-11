// web/src/App.tsx
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ChatPage } from "./pages/ChatPage";
import { KnowledgeView } from "./pages/KnowledgeView";
import { KnowledgeDetailView } from "./pages/KnowledgeDetailView";
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
            <Route path="/" element={<ChatPage />} />
            <Route path="/knowledge" element={<KnowledgeView />} />
            <Route path="/knowledge/:id" element={<KnowledgeDetailView />} />
            <Route path="/questions" element={<QuestionBankView />} />
            <Route path="/wrong" element={<WrongAnswersView />} />
            <Route path="/downloads" element={<DownloadsView />} />
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
