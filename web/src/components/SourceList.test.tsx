// web/src/components/SourceList.test.tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SourceList } from "./SourceList";
import { linkifyCitations, citeId } from "./citations";
import type { SourceItem } from "../types";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useNavigate: () => mockNavigate,
}));

afterEach(() => { cleanup(); mockNavigate.mockClear(); });

function renderList(sources: SourceItem[]) {
  return render(<MemoryRouter><SourceList sources={sources} msgKey="0" /></MemoryRouter>);
}

describe("SourceList", () => {
  it("空来源不渲染", () => {
    const { container } = renderList([]);
    expect(container.firstChild).toBeNull();
  });

  it("各类型显示编号、类型标签与文案，条目挂锚点 id", () => {
    renderList([
      { index: 1, type: "knowledge", label: "bio.pdf" },
      { index: 2, type: "web", label: "维基百科", url: "https://zh.wikipedia.org/x" },
      { index: 3, type: "question", label: "题库抽题 5 道" },
    ]);
    expect(screen.getByText(/参考来源（3）/)).toBeTruthy();
    expect(screen.getByText("知识库")).toBeTruthy();
    expect(screen.getByText("网络")).toBeTruthy();
    expect(screen.getByText("题库")).toBeTruthy();
    expect(screen.getByText("bio.pdf")).toBeTruthy();
    // 锚点 id 就位，供正文角标滚动定位
    expect(document.getElementById(citeId("0", 1))).toBeTruthy();
  });

  it("web 来源渲染为新标签外链", () => {
    renderList([{ index: 1, type: "web", label: "维基百科", url: "https://zh.wikipedia.org/x" }]);
    const link = screen.getByText("维基百科").closest("a")!;
    expect(link.getAttribute("href")).toBe("https://zh.wikipedia.org/x");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("知识库来源点击跳 /knowledge", () => {
    renderList([{ index: 1, type: "knowledge", label: "bio.pdf" }]);
    fireEvent.click(screen.getByText("bio.pdf"));
    expect(mockNavigate).toHaveBeenCalledWith("/knowledge");
  });

  it("题库来源点击跳 /questions", () => {
    renderList([{ index: 1, type: "question", label: "题库抽题 5 道" }]);
    fireEvent.click(screen.getByText("题库抽题 5 道"));
    expect(mockNavigate).toHaveBeenCalledWith("/questions");
  });

  it("沙箱/记忆类来源可就地展开原始片段", () => {
    renderList([{ index: 1, type: "code", label: "Python 代码执行", detail: "42\n" }]);
    expect(screen.queryByText("42")).toBeNull();     // 折叠态不显示
    fireEvent.click(screen.getByLabelText("展开来源"));
    expect(screen.getByText("42")).toBeTruthy();      // 展开后显示
  });
});

describe("linkifyCitations", () => {
  it("把范围内的 [n] 替换成锚点链接", () => {
    expect(linkifyCitations("发生在叶绿体[1]，膜上[2]。", 2, "0"))
      .toBe("发生在叶绿体[1](#cite-0-1)，膜上[2](#cite-0-2)。");
  });

  it("越界的 [n] 不替换", () => {
    expect(linkifyCitations("无关[5]标注", 2, "0")).toBe("无关[5]标注");
  });

  it("已是 markdown 链接的 [n]( 不重复替换", () => {
    expect(linkifyCitations("见[1](http://a)", 2, "0")).toBe("见[1](http://a)");
  });

  it("无来源时原样返回", () => {
    expect(linkifyCitations("正文[1]", 0, "0")).toBe("正文[1]");
  });
});
