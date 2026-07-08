import { describe, it, expect } from "vitest";
import { drainSSE } from "./client";

describe("drainSSE", () => {
  it("解析完整事件、保留残缺尾巴", () => {
    const input =
      'data: {"type":"TextDelta","data":{"text":"hi"}}\n\n' +
      'data: {"type":"RunFinished","data":{}}\n\n' +
      "data: {\"type\":\"Par";
    const { events, rest } = drainSSE(input);
    expect(events.map((e) => e.type)).toEqual(["TextDelta", "RunFinished"]);
    expect(rest.startsWith("data: ")).toBe(true);   // 残缺片段留待下次
  });
});
