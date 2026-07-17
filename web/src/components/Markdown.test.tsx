// web/src/components/Markdown.test.tsx
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { Markdown } from "./Markdown";

afterEach(() => cleanup());

describe("Markdown 来源角标", () => {
  it("#cite- 链接渲染成可点上标并回调编号", () => {
    const onCite = vi.fn();
    render(
      <Markdown onCitationClick={onCite}>
        {"发生在叶绿体[1](#cite-0-1)。"}
      </Markdown>,
    );
    const sup = screen.getByText("[1]");
    expect(sup.tagName).toBe("SUP");
    fireEvent.click(sup);
    expect(onCite).toHaveBeenCalledWith(1);
  });

  it("普通链接仍渲染为 a（不受角标逻辑影响）", () => {
    render(<Markdown>{"[维基](https://zh.wikipedia.org/x)"}</Markdown>);
    const a = screen.getByText("维基").closest("a")!;
    expect(a.getAttribute("href")).toBe("https://zh.wikipedia.org/x");
  });
});

describe("Markdown 段内单换行", () => {
  it("单换行分隔的选项渲染为多行（<br>），不再挤成一行", () => {
    const { container } = render(
      <Markdown>{"A. 甲\nB. 乙\nC. 丙\nD. 丁"}</Markdown>,
    );
    // 3 个单换行 → 3 个 <br>；四个选项文本都在
    expect(container.querySelectorAll("br").length).toBe(3);
    for (const t of ["A. 甲", "B. 乙", "C. 丙", "D. 丁"]) {
      expect(container.textContent).toContain(t);
    }
  });

  it("双换行（段落）不受影响，仍是独立段落而非 <br>", () => {
    const { container } = render(<Markdown>{"第一段\n\n第二段"}</Markdown>);
    expect(container.querySelectorAll("p").length).toBe(2);
    expect(container.querySelectorAll("br").length).toBe(0);
  });
});
