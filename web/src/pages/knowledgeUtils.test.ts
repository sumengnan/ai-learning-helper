import { describe, it, expect } from "vitest";
import { relevanceColor } from "./knowledgeUtils";

// 从 hsl(H, S%, L%) 取色相，用于断言「越相关越绿」而不锁死每一档的具体数值
const hueOf = (c: string) => Number(/hsl\(\s*([\d.]+)/.exec(c)?.[1]);

const GREY_LIGHT = "#9e9e9e";
const GREY_DARK = "#bdbdbd";

describe("relevanceColor —— 按展示百分数：<50% 灰，≥50% 越相关越绿", () => {
  it("<50% 一律灰（不管量纲、不管明暗）", () => {
    expect(relevanceColor(49)).toBe(GREY_LIGHT);
    expect(relevanceColor(35)).toBe(GREY_LIGHT);
    expect(relevanceColor(0)).toBe(GREY_LIGHT);
    expect(relevanceColor(49, "dark")).toBe(GREY_DARK);
    expect(relevanceColor(0, "dark")).toBe(GREY_DARK);
  });

  it("分界含 50%：恰好 50% 就脱离灰、上色", () => {
    expect(relevanceColor(50)).not.toBe(GREY_LIGHT);
    expect(relevanceColor(49)).toBe(GREY_LIGHT);
    expect(relevanceColor(50, "dark")).not.toBe(GREY_DARK);
  });

  it("越相关越绿：色相从暖（50%）单调过渡到绿（100%）", () => {
    const h50 = hueOf(relevanceColor(50));
    const h75 = hueOf(relevanceColor(75));
    const h100 = hueOf(relevanceColor(100));
    expect(h50).toBeLessThan(h75);
    expect(h75).toBeLessThan(h100);
    expect(h50).toBeLessThan(90);              // 50% 落在暖色端
    expect(h100).toBeGreaterThanOrEqual(120);  // 100% 落在绿色端
  });

  it("超过 100% 按 100% 封顶，不产生越界色", () => {
    expect(relevanceColor(150)).toBe(relevanceColor(100));
    expect(relevanceColor(101, "dark")).toBe(relevanceColor(100, "dark"));
  });

  it("明暗各自为背景优化，同一分数取色不同", () => {
    expect(relevanceColor(75, "dark")).not.toBe(relevanceColor(75, "light"));
    expect(relevanceColor(100, "dark")).not.toBe(relevanceColor(100, "light"));
  });

  it("只看展示的百分数，不再区分 rerank/rank 量纲", () => {
    // 旧实现里 rerank 的 43% 是绿色、rank 的 43% 是灰色；新规则下 43% 一律灰（<50）
    expect(relevanceColor(43)).toBe(GREY_LIGHT);
    // 60% 一律上色，与它来自哪种量纲无关
    expect(relevanceColor(60)).not.toBe(GREY_LIGHT);
  });
});
