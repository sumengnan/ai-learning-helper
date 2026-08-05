import { describe, it, expect } from "vitest";
import { linkifyCitations } from "./citations";

describe("linkifyCitations", () => {
  it("不改写代码块和行内代码里的 [n]", () => {
    const input = [
      "根据资料[1]，可以这样写：",
      "",
      "```python",
      "items = [10, 20, 30]",
      "print(items[1])",
      "```",
      "",
      "行内代码 `arr[2]` 同理。"
    ].join("\n");

    const result = linkifyCitations(input, 3, "0");

    // 散文中的 [1] 照常改写
    expect(result).toContain("根据资料[1](#cite-0-1)");
    // 代码块里的 [1] 不动
    expect(result).toContain("print(items[1])");
    expect(result).not.toContain("print(items[1](#cite-0-1))");
    // 行内代码里的 [2] 不动
    expect(result).toContain("`arr[2]`");
    expect(result).not.toContain("`arr[2](#cite-0-2)`");
  });
});
