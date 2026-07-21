// web/src/components/BeianFooter.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { BeianFooter } from "./BeianFooter";

function mockSite(body: unknown, ok = true) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok, json: async () => body,
  }));
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("BeianFooter", () => {
  it("展示配置好的三项备案信息", async () => {
    mockSite({
      icp: "京ICP备12345678号-1",
      police_icp: "京公网安备 11010102000001号",
      copyright: "某某科技有限公司",
    });
    render(<BeianFooter />);
    expect(await screen.findByText("京ICP备12345678号-1")).toBeTruthy();
    expect(screen.getByText("京公网安备 11010102000001号")).toBeTruthy();
    expect(screen.getByText(/某某科技有限公司/)).toBeTruthy();
  });

  it("ICP 与公安备案分别链到工信部 / 公安部查询页", async () => {
    mockSite({
      icp: "京ICP备12345678号-1",
      police_icp: "京公网安备 11010102000001号",
      copyright: "",
    });
    render(<BeianFooter />);
    const icp = (await screen.findByText("京ICP备12345678号-1")).closest("a");
    expect(icp?.getAttribute("href")).toBe("https://beian.miit.gov.cn/");
    const police = screen.getByText("京公网安备 11010102000001号").closest("a");
    // 公安备案链接要带备案号里的数字串，否则跳过去查不到本站
    expect(police?.getAttribute("href")).toContain("11010102000001");
  });

  it("一项都没配则整块不渲染", async () => {
    mockSite({ icp: "", police_icp: "", copyright: "" });
    const { container } = render(<BeianFooter />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("只配了 ICP 时只渲染 ICP，不留空占位", async () => {
    mockSite({ icp: "沪ICP备1号", police_icp: "", copyright: "" });
    render(<BeianFooter />);
    expect(await screen.findByText("沪ICP备1号")).toBeTruthy();
    expect(screen.queryByText(/公网安备/)).toBeNull();
  });

  it("divider 变体在未配置时也不留下空的分隔线", async () => {
    // 分隔线画在组件内部而非外层包裹：否则未配置时会在内容区下方留一条无来由的横线
    mockSite({ icp: "", police_icp: "", copyright: "" });
    const { container } = render(<BeianFooter divider />);
    await waitFor(() => expect(container.textContent).toBe(""));
    expect(container.querySelector("hr")).toBeNull();
  });

  it("接口挂了也只是不展示，不把错误抛到登录页", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
    const { container } = render(<BeianFooter />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});
