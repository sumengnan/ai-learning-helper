import { describe, it, expect } from "vitest";
import { relevanceColor } from "./knowledgeUtils";

describe("relevanceColor", () => {
  it("100% 深绿", () => expect(relevanceColor(100)).toBe("#1b5e20"));
  it("[95,100) 浅绿", () => {
    expect(relevanceColor(99)).toBe("#66bb6a");
    expect(relevanceColor(95)).toBe("#66bb6a");
  });
  it("[90,95) 紫色", () => {
    expect(relevanceColor(94)).toBe("#9c27b0");
    expect(relevanceColor(90)).toBe("#9c27b0");
  });
  it("[85,90) 蓝色", () => {
    expect(relevanceColor(89)).toBe("#1976d2");
    expect(relevanceColor(85)).toBe("#1976d2");
  });
  it("[80,85) 黑色", () => {
    expect(relevanceColor(84)).toBe("#212121");
    expect(relevanceColor(80)).toBe("#212121");
  });
  it("<80 灰色", () => {
    expect(relevanceColor(79)).toBe("#9e9e9e");
    expect(relevanceColor(0)).toBe("#9e9e9e");
  });

  it("暗色模式：黑档翻成近白、各档提亮", () => {
    expect(relevanceColor(82, "dark")).toBe("#f5f5f5");   // 黑 → 近白
    expect(relevanceColor(100, "dark")).toBe("#a5d6a7");  // 深绿提亮
    expect(relevanceColor(92, "dark")).toBe("#ce93d8");   // 紫提亮
    expect(relevanceColor(87, "dark")).toBe("#64b5f6");   // 蓝提亮
    expect(relevanceColor(50, "dark")).toBe("#bdbdbd");   // 灰提亮
  });

  // 精排量纲：相关性集中在 ~25-55，min_score=0.35（35%）是相关/无关分界
  describe("rerank 量纲", () => {
    it(">=50 很相关：深绿", () => {
      expect(relevanceColor(55, "light", "rerank")).toBe("#1b5e20");
      expect(relevanceColor(50, "light", "rerank")).toBe("#1b5e20");
    });
    it("[40,50) 相关：绿", () => {
      // 实测相关文档 ≈0.43 → 43%，必须是「相关」色而非灰
      expect(relevanceColor(43, "light", "rerank")).toBe("#2e7d32");
      expect(relevanceColor(40, "light", "rerank")).toBe("#2e7d32");
    });
    it("[35,40) 勉强过线：蓝", () => {
      expect(relevanceColor(35, "light", "rerank")).toBe("#1976d2");
    });
    it("[25,35) 弱相关：灰黄", () => {
      // 实测无关文档 ≈0.26 → 26%，应落在弱/无关档而非明显相关色
      expect(relevanceColor(26, "light", "rerank")).toBe("#c77700");
      expect(relevanceColor(34, "light", "rerank")).toBe("#c77700");
    });
    it("<25 基本无关：灰", () => {
      expect(relevanceColor(24, "light", "rerank")).toBe("#9e9e9e");
      expect(relevanceColor(0, "light", "rerank")).toBe("#9e9e9e");
    });
    it("暗色模式各档提亮", () => {
      expect(relevanceColor(55, "dark", "rerank")).toBe("#a5d6a7");
      expect(relevanceColor(43, "dark", "rerank")).toBe("#66bb6a");
      expect(relevanceColor(35, "dark", "rerank")).toBe("#64b5f6");
      expect(relevanceColor(26, "dark", "rerank")).toBe("#ffb74d");
      expect(relevanceColor(24, "dark", "rerank")).toBe("#bdbdbd");
    });

    // 变异证伪：若 rerank 也走旧 rank 阈值，43% 会落进 <80 灰色档。
    // 断言 rerank 下 43% 明确不是灰、且是相关绿，锁死量纲分流。
    it("证伪：rerank 43% 不能是旧阈值的灰色", () => {
      expect(relevanceColor(43, "light", "rerank")).not.toBe("#9e9e9e");
      expect(relevanceColor(43, "light", "rank")).toBe("#9e9e9e"); // 旧量纲对照：确实是灰
    });
  });
});
