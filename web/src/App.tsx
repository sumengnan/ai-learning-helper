// web/src/App.tsx
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { ChatPage } from "./pages/ChatPage";
import { KnowledgeView } from "./pages/KnowledgeView";
import QuestionBankView from "./pages/QuestionBankView";
import ExamView from "./pages/ExamView";
import WrongAnswersView from "./pages/WrongAnswersView";
import DownloadsView from "./pages/DownloadsView";

const NAV: [string, string][] = [
  ["/", "聊天"], ["/knowledge", "知识库"], ["/questions", "题库"],
  ["/exam", "考试"], ["/wrong", "错题集"], ["/downloads", "下载"],
];

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-full">
        <nav className="w-20 bg-gray-800 text-white flex flex-col shrink-0">
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}
              className={({ isActive }) =>
                `px-2 py-3 text-center text-sm ${isActive ? "bg-gray-600" : "hover:bg-gray-700"}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="flex-1 min-w-0">
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/knowledge" element={<KnowledgeView />} />
            <Route path="/questions" element={<QuestionBankView />} />
            <Route path="/exam" element={<ExamView />} />
            <Route path="/wrong" element={<WrongAnswersView />} />
            <Route path="/downloads" element={<DownloadsView />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  );
}
