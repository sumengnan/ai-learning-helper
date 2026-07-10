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

describe("ConversationList 删除确认", () => {
  afterEach(() => cleanup());

  function setupDelete() {
    const onDelete = vi.fn();
    render(
      <ConversationList items={items} activeId="a" onSelect={() => {}}
        onNew={() => {}} onDelete={onDelete} onRename={() => {}} />,
    );
    return { onDelete };
  }

  it("点删除图标弹出确认，列出将删除的内容与保留说明，确认后触发 onDelete", () => {
    const { onDelete } = setupDelete();
    fireEvent.click(screen.getAllByLabelText("删除对话")[0]);
    // 弹窗列出会被删除的内容与不会被删除的内容
    expect(screen.getByText(/本对话的全部聊天记录与消息/)).toBeTruthy();
    expect(screen.getByText(/本对话的沙箱容器及其中生成的临时文件/)).toBeTruthy();
    expect(screen.getByText(/以下内容/)).toBeTruthy();
    expect(screen.getByText(/此操作不可撤销/)).toBeTruthy();
    // 未确认前不应删除
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onDelete).toHaveBeenCalledWith("a");
  });

  it("取消不触发 onDelete", () => {
    const { onDelete } = setupDelete();
    fireEvent.click(screen.getAllByLabelText("删除对话")[0]);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onDelete).not.toHaveBeenCalled();
  });
});
