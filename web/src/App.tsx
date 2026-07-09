// web/src/App.tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { ChatPage } from "./pages/ChatPage";
import { KnowledgeView } from "./pages/KnowledgeView";
import QuestionBankView from "./pages/QuestionBankView";
import WrongAnswersView from "./pages/WrongAnswersView";
import DownloadsView from "./pages/DownloadsView";

function ShellRoutes() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<ChatPage />} />
        <Route path="/knowledge" element={<KnowledgeView />} />
        <Route path="/questions" element={<QuestionBankView />} />
        <Route path="/wrong" element={<WrongAnswersView />} />
        <Route path="/downloads" element={<DownloadsView />} />
      </Routes>
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
