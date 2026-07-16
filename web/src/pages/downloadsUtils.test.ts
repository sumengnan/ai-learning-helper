import { describe, it, expect } from "vitest";
import { previewKind } from "./downloadsUtils";

describe("previewKind", () => {
  it("按 content_type 判 markdown", () => {
    expect(previewKind("text/markdown")).toBe("markdown");
  });

  it("content_type 不含 markdown 时，靠 .md/.markdown 后缀兜底", () => {
    // 后端 mimetypes.guess_type 各平台对 .md 结果不一，可能猜成 text/plain 甚至丢类型
    expect(previewKind("text/plain", "笔记.md")).toBe("markdown");
    expect(previewKind("application/octet-stream", "README.markdown")).toBe("markdown");
    expect(previewKind("", "a.MD")).toBe("markdown");           // 后缀大小写不敏感
  });

  it("普通文本仍是 text，不被误判成 markdown", () => {
    expect(previewKind("text/plain", "log.txt")).toBe("text");
    expect(previewKind("application/json", "data.json")).toBe("text");
    expect(previewKind("text/csv", "t.csv")).toBe("text");
  });

  it("markdown 优先于 text：text/markdown 不该落到 text 分支", () => {
    // 顺序敏感——markdown 的判断必须在 text/ 之前
    expect(previewKind("text/markdown", "x.md")).toBe("markdown");
  });

  it("图片 / PDF / 不支持 各归其位", () => {
    expect(previewKind("image/png", "a.png")).toBe("image");
    expect(previewKind("application/pdf", "a.pdf")).toBe("pdf");
    expect(previewKind("application/zip", "a.zip")).toBe("none");
  });

  it("文件名里含 .md 但不是后缀，不误判", () => {
    expect(previewKind("application/zip", "v1.md.zip")).toBe("none");
  });
});
