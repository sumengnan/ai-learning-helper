// 把 AI 正文里的 [n] 角标（n 在来源范围内、且非 markdown 链接）替换成指向来源的锚点链接，
// 交给 Markdown 渲染成可点上标。msgKey 让不同消息的锚点 id 唯一，避免跨消息串号。
export function linkifyCitations(content: string, count: number, msgKey: string): string {
  if (!count || !content) return content;

  // 保护代码块和行内代码：先把它们提取出来，只对散文部分做替换，再拼回去。
  const codeSegments: string[] = [];
  const placeholder = (i: number) => `\x00CITE${i}\x00`;

  // 匹配围栏代码块（```...```）和行内代码（`...`）
  const protected_ = content.replace(
    /```[\s\S]*?```|`[^`\n]+`/g,
    (match) => {
      const idx = codeSegments.length;
      codeSegments.push(match);
      return placeholder(idx);
    },
  );

  const linked = protected_.replace(/\[(\d+)\](?!\()/g, (m, d) => {
    const n = Number(d);
    return n >= 1 && n <= count ? `[${n}](#cite-${msgKey}-${n})` : m;
  });

  return linked.replace(/\x00CITE(\d+)\x00/g, (_, i) => codeSegments[Number(i)]);
}

export function citeId(msgKey: string, index: number): string {
  return `cite-${msgKey}-${index}`;
}
