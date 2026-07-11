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
