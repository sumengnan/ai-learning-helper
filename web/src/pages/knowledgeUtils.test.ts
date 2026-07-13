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
});
