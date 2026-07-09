import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ConversationList } from "./ConversationList";

const items = [
  { id: "a", title: "对话甲", created_at: "" },
  { id: "b", title: "对话乙", created_at: "" },
];

function setup() {
  const onRename = vi.fn();
  const onSelect = vi.fn();
  render(
    <ConversationList items={items} activeId="a" onSelect={onSelect}
      onNew={() => {}} onDelete={() => {}} onRename={onRename} />,
  );
  return { onRename, onSelect };
}

describe("ConversationList 重命名", () => {
  afterEach(() => cleanup());

  it("双击标题进入编辑，回车提交新名字", () => {
    const { onRename } = setup();
    fireEvent.doubleClick(screen.getByText("对话甲"));
    const input = screen.getByDisplayValue("对话甲");
    fireEvent.change(input, { target: { value: "改后的名字" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onRename).toHaveBeenCalledWith("a", "改后的名字");
  });

  it("Esc 取消编辑不触发重命名", () => {
    const { onRename } = setup();
    fireEvent.doubleClick(screen.getByText("对话乙"));
    const input = screen.getByDisplayValue("对话乙");
    fireEvent.change(input, { target: { value: "xxx" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onRename).not.toHaveBeenCalled();
  });
});
