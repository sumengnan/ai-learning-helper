import { describe, it, expect } from "vitest";
import { relevanceColor } from "./knowledgeUtils";

// 从 hsl(H, S%, L%) 取色相，用于断言「越相关越绿」而不锁死每一档的具体数值
const hueOf = (c: string) => Number(/hsl\(\s*([\d.]+)/.exec(c)?.[1]);

const GREY_LIGHT = "#9e9e9e";
const GREY_DARK = "#bdbdbd";

describe("relevanceColor —— 按展示百分数：<40% 灰，≥40% 越相关越绿", () => {
  it("<40% 一律灰（不管量纲、不管明暗）", () => {
    expect(relevanceColor(39)).toBe(GREY_LIGHT);
    expect(relevanceColor(25)).toBe(GREY_LIGHT);
    expect(relevanceColor(0)).toBe(GREY_LIGHT);
    expect(relevanceColor(39, "dark")).toBe(GREY_DARK);
    expect(relevanceColor(0, "dark")).toBe(GREY_DARK);
  });

  it("分界含 40%：恰好 40% 就脱离灰、上色", () => {
    expect(relevanceColor(40)).not.toBe(GREY_LIGHT);
    expect(relevanceColor(39)).toBe(GREY_LIGHT);
    expect(relevanceColor(40, "dark")).not.toBe(GREY_DARK);
  });

  it("越相关越绿：色相从暖（40%）单调过渡到绿（100%）", () => {
    const h40 = hueOf(relevanceColor(40));
    const h70 = hueOf(relevanceColor(70));
    const h100 = hueOf(relevanceColor(100));
    expect(h40).toBeLessThan(h70);
    expect(h70).toBeLessThan(h100);
    expect(h40).toBeLessThan(90);              // 40% 落在暖色端
    expect(h100).toBeGreaterThanOrEqual(120);  // 100% 落在绿色端
  });

  it("超过 100% 按 100% 封顶，不产生越界色", () => {
    expect(relevanceColor(150)).toBe(relevanceColor(100));
    expect(relevanceColor(101, "dark")).toBe(relevanceColor(100, "dark"));
  });

  it("明暗各自为背景优化，同一分数取色不同", () => {
    expect(relevanceColor(70, "dark")).not.toBe(relevanceColor(70, "light"));
    expect(relevanceColor(100, "dark")).not.toBe(relevanceColor(100, "light"));
  });

  it("只看展示的百分数，不再区分 rerank/rank 量纲", () => {
    // 阈值降到 40 后：实测常见的 40–50% 相关文档现在会上色，不再一屏全灰
    expect(relevanceColor(43)).not.toBe(GREY_LIGHT);
    expect(relevanceColor(50)).not.toBe(GREY_LIGHT);
    // 仍低于 40% 的判为不够相关，保持灰
    expect(relevanceColor(35)).toBe(GREY_LIGHT);
  });
});
