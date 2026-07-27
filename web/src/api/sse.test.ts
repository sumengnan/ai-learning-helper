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

  it("忽略心跳注释行，只解析夹在其间的真实事件", () => {
    // 后端在空闲间隙发 ": ping" 注释保活；这类行不带 data: 字段，必须被跳过
    const input =
      ": ping\n\n" +
      'data: {"type":"TextDelta","data":{"text":"hi"}}\n\n' +
      ": ping\n\n";
    const { events, rest } = drainSSE(input);
    expect(events.map((e) => e.type)).toEqual(["TextDelta"]);
    expect(rest).toBe("");
  });
});
