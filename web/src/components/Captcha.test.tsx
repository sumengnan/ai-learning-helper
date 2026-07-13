import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { Captcha } from "./Captcha";
import * as client from "../api/client";

describe("Captcha 组件（后端验证码）", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => cleanup());

  it("挂载时取码、展示图片并上报 token", async () => {
    vi.spyOn(client.auth, "captcha").mockResolvedValue({
      token: "tok-1", image: "data:image/svg+xml;base64,AAAA",
    });
    const onToken = vi.fn();
    render(<Captcha onToken={onToken} />);
    await waitFor(() => expect(onToken).toHaveBeenCalledWith("tok-1"));
    const img = await screen.findByAltText("验证码");
    expect(img.getAttribute("src")).toContain("data:image/svg+xml");
  });

  it("点击刷新会再次取码并上报新 token", async () => {
    const spy = vi.spyOn(client.auth, "captcha")
      .mockResolvedValueOnce({ token: "tok-1", image: "data:image/svg+xml;base64,AAAA" })
      .mockResolvedValueOnce({ token: "tok-2", image: "data:image/svg+xml;base64,BBBB" });
    const onToken = vi.fn();
    render(<Captcha onToken={onToken} />);
    await waitFor(() => expect(onToken).toHaveBeenCalledWith("tok-1"));
    fireEvent.click(screen.getByRole("button", { name: "点击刷新验证码" }));
    await waitFor(() => expect(onToken).toHaveBeenCalledWith("tok-2"));
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("取码失败时上报空 token 不抛错", async () => {
    vi.spyOn(client.auth, "captcha").mockRejectedValue(new Error("boom"));
    const onToken = vi.fn();
    render(<Captcha onToken={onToken} />);
    await waitFor(() => expect(onToken).toHaveBeenCalledWith(""));
  });
});
