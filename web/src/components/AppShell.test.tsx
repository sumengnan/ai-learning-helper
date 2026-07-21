// web/src/components/AppShell.test.tsx
import { describe, it, expect } from "vitest";
import { displayName, avatarLetter } from "./AppShell";

describe("displayName —— 顶栏按钮显示什么", () => {
  it("有姓名就显示姓名", () => {
    expect(displayName({ id: "1", username: "alice", full_name: "张三" })).toBe("张三");
  });

  it("没姓名回退账号——老账号 full_name 为空，不能显示成空白按钮", () => {
    expect(displayName({ id: "1", username: "alice" })).toBe("alice");
    expect(displayName({ id: "1", username: "alice", full_name: "" })).toBe("alice");
  });

  it("姓名只有空白也回退账号", () => {
    expect(displayName({ id: "1", username: "alice", full_name: "   " })).toBe("alice");
  });

  it("未登录时给占位，不显示 undefined", () => {
    expect(displayName(null)).toBe("未登录");
  });
});

describe("avatarLetter —— 头像里的字", () => {
  it("取显示名首字，英文转大写", () => {
    expect(avatarLetter("alice")).toBe("A");
  });

  it("中文姓名取姓", () => {
    expect(avatarLetter("张三")).toBe("张");
  });

  it("空串给问号，不给空头像", () => {
    expect(avatarLetter("")).toBe("?");
  });
});
