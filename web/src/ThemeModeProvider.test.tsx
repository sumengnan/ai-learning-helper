import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeModeProvider, useColorMode } from "./ThemeModeProvider";

function Probe() {
  const { mode, toggleMode } = useColorMode();
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <button onClick={toggleMode}>toggle</button>
    </div>
  );
}

describe("ThemeModeProvider", () => {
  beforeEach(() => localStorage.clear());

  it("点击切换在 light/dark 间翻转并写入 localStorage", () => {
    render(<ThemeModeProvider><Probe /></ThemeModeProvider>);
    const before = screen.getByTestId("mode").textContent;
    fireEvent.click(screen.getByText("toggle"));
    const after = screen.getByTestId("mode").textContent;
    expect(after).not.toBe(before);
    expect(localStorage.getItem("color-mode")).toBe(after);
  });
});
