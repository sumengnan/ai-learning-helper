// web/src/App.tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ChatPage } from "./pages/ChatPage";
import { KnowledgeView } from "./pages/KnowledgeView";
import QuestionBankView from "./pages/QuestionBankView";
import ExamView from "./pages/ExamView";
import WrongAnswersView from "./pages/WrongAnswersView";
import DownloadsView from "./pages/DownloadsView";

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/knowledge" element={<KnowledgeView />} />
          <Route path="/questions" element={<QuestionBankView />} />
          <Route path="/exam" element={<ExamView />} />
          <Route path="/wrong" element={<WrongAnswersView />} />
          <Route path="/downloads" element={<DownloadsView />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
