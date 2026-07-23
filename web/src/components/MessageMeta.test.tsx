import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { MessageMeta } from "./MessageMeta";

afterEach(() => cleanup());

describe("MessageMeta", () => {
  it("完成态：状态 + 耗时 + tokens 三枚药丸", () => {
    render(<MessageMeta status="done" live={false} elapsedMs={65_000}
      usage={{ tokens: 1234, cost: 0.02 }} showMeta />);
    expect(screen.getByText("已完成")).toBeTruthy();
    expect(screen.getByText("1 分 5 秒")).toBeTruthy();   // 耗时时长
    expect(screen.getByText("tokens")).toBeTruthy();
    expect(screen.getByText(/¥0\.0200/)).toBeTruthy();    // 成本
  });

  it("生成中：显示「生成中」状态药丸", () => {
    render(<MessageMeta status="streaming" live startedAt={Date.now()} showMeta />);
    expect(screen.getByText("生成中")).toBeTruthy();
  });

  it("生成中：即使关闭 Token 开关，也显示状态与增长中的耗时", () => {
    render(<MessageMeta status="streaming" live startedAt={Date.now() - 3000} showMeta={false} />);
    expect(screen.getByText("生成中")).toBeTruthy();
    expect(screen.getByText(/\d+\s*秒/)).toBeTruthy();   // 生成中耗时不受开关限制
  });

  it("关闭「展示 Token」时隐藏 tokens，但状态与耗时保留", () => {
    render(<MessageMeta status="done" live={false} elapsedMs={5000}
      usage={{ tokens: 10, cost: null }} showMeta={false} />);
    expect(screen.getByText("已完成")).toBeTruthy();
    expect(screen.queryByText("tokens")).toBeNull();      // token 仍受开关控制
    expect(screen.getByText(/5\s*秒/)).toBeTruthy();       // 耗时不再随开关消失
  });

  it("悬停 tokens 药丸弹出各模型用量明细（MUI Tooltip，非原生 title）", async () => {
    render(<MessageMeta status="done" live={false} elapsedMs={1000}
      usage={{ tokens: 30, cost: 0.02 }}
      usageByModel={{ main: { tokens: 20, cost: 0.015 }, fast: { tokens: 10, cost: 0.005 } }}
      showMeta />);
    fireEvent.mouseOver(screen.getByText("tokens"));
    expect(await screen.findByText(/本轮各模型用量/)).toBeTruthy();
  });

  it("各终态文案：失败 / 已停止 / 已中断", () => {
    for (const [status, text] of [["error", "回复失败"], ["stopped", "已停止"], ["interrupted", "已中断"]] as const) {
      render(<MessageMeta status={status} live={false} showMeta />);
      expect(screen.getByText(text)).toBeTruthy();
      cleanup();
    }
  });

  it("无任何内容时返回 null（不渲染空页脚）", () => {
    const { container } = render(<MessageMeta live={false} showMeta />);
    expect(container.firstChild).toBeNull();
  });
});
