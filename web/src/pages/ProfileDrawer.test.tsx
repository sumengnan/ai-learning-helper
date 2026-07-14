import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";

vi.mock("../api/profile", () => ({
  EMPTY_PROFILE: { identity: "", goal: "", explain_prefs: [], tone: "", notes: "" },
  isProfileSet: (p: any) => Boolean(p.identity || p.goal || p.tone || p.notes || p.explain_prefs.length),
  profileApi: { get: vi.fn(), save: vi.fn() },
}));

import { ProfileDrawerProvider, useProfileDrawer } from "./ProfileDrawer";
import { profileApi } from "../api/profile";

function Harness() {
  const { open } = useProfileDrawer();
  return <button onClick={open}>打开个性化</button>;
}

const renderWithProvider = () =>
  render(<ProfileDrawerProvider><Harness /></ProfileDrawerProvider>);

describe("ProfileDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(profileApi.get).mockResolvedValue(
      { identity: "职场人", goal: "", explain_prefs: ["多用类比"], tone: "", notes: "" });
    vi.mocked(profileApi.save).mockImplementation(async (p: any) => p);
  });
  afterEach(() => cleanup());

  it("打开后加载并回显 profile", async () => {
    renderWithProvider();
    fireEvent.click(screen.getByText("打开个性化"));
    expect(await screen.findByDisplayValue("职场人")).toBeTruthy();
    await waitFor(() => expect(profileApi.get).toHaveBeenCalled());
    // 预设 chips 渲染
    expect(screen.getByText("步骤拆细")).toBeTruthy();
    expect(screen.getByText("鼓励式")).toBeTruthy();
  });

  it("点选讲解偏好并保存：save 带上新增项", async () => {
    renderWithProvider();
    fireEvent.click(screen.getByText("打开个性化"));
    await screen.findByDisplayValue("职场人");
    fireEvent.click(screen.getByText("步骤拆细"));         // 新增一个偏好
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(profileApi.save).toHaveBeenCalled());
    const sent = vi.mocked(profileApi.save).mock.calls[0][0];
    expect(sent.explain_prefs).toEqual(expect.arrayContaining(["多用类比", "步骤拆细"]));
    // 成功提示
    expect(await screen.findByText(/已保存/)).toBeTruthy();
  });

  it("语气单选：点第二个会替换第一个", async () => {
    vi.mocked(profileApi.get).mockResolvedValue(
      { identity: "", goal: "", explain_prefs: [], tone: "鼓励式", notes: "" });
    renderWithProvider();
    fireEvent.click(screen.getByText("打开个性化"));
    await screen.findByText("严格教练");
    fireEvent.click(screen.getByText("严格教练"));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(profileApi.save).toHaveBeenCalled());
    expect(vi.mocked(profileApi.save).mock.calls[0][0].tone).toBe("严格教练");
  });
});
