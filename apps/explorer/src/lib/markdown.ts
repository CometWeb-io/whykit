export type MdInline =
  | { t: "text"; v: string }
  | { t: "strong"; v: MdInline[] }
  | { t: "em"; v: MdInline[] }
  | { t: "code"; v: string }
  | { t: "wiki"; target: string; label: string }
  | { t: "link"; href: string; label: string };

export type MdBlock =
  | { t: "h"; level: 1 | 2 | 3 | 4; children: MdInline[] }
  | { t: "p"; children: MdInline[] }
  | { t: "ul"; items: MdInline[][] }
  | { t: "ol"; items: MdInline[][] }
  | { t: "table"; headers: MdInline[][]; rows: MdInline[][][] }
  | { t: "callout"; kind: string; title: string; children: MdInline[][] }
  | { t: "code"; lang: string; code: string }
  | { t: "hr" }
  | { t: "quote"; children: MdInline[][] };

const WIKI = /\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]*))?\]\]/;
const LINK = /\[([^\]]+)\]\(([^)]+)\)/;

export function parseInline(input: string): MdInline[] {
  const out: MdInline[] = [];
  let rest = input;
  while (rest.length) {
    const wiki = rest.match(WIKI);
    const link = rest.match(LINK);
    const code = rest.match(/`([^`]+)`/);
    const strong = rest.match(/\*\*([^*]+)\*\*/);
    const em = rest.match(/(?<!\*)\*([^*]+)\*(?!\*)/);

    type Hit = { i: number; n: number; node: MdInline };
    const hits: Hit[] = [];
    if (wiki && wiki.index != null) {
      hits.push({
        i: wiki.index,
        n: wiki[0].length,
        node: { t: "wiki", target: wiki[1]!.trim(), label: (wiki[2] || wiki[1]!).trim() },
      });
    }
    if (link && link.index != null) {
      hits.push({
        i: link.index,
        n: link[0].length,
        node: { t: "link", href: link[2]!, label: link[1]! },
      });
    }
    if (code && code.index != null) {
      hits.push({ i: code.index, n: code[0].length, node: { t: "code", v: code[1]! } });
    }
    if (strong && strong.index != null) {
      hits.push({
        i: strong.index,
        n: strong[0].length,
        node: { t: "strong", v: parseInline(strong[1]!) },
      });
    }
    if (em && em.index != null) {
      hits.push({ i: em.index, n: em[0].length, node: { t: "em", v: parseInline(em[1]!) } });
    }
    if (!hits.length) {
      out.push({ t: "text", v: rest });
      break;
    }
    hits.sort((a, b) => a.i - b.i);
    const hit = hits[0]!;
    if (hit.i > 0) out.push({ t: "text", v: rest.slice(0, hit.i) });
    out.push(hit.node);
    rest = rest.slice(hit.i + hit.n);
  }
  return out;
}

// Consecutive quoted lines form one paragraph; a bare `>` line separates them.
function quoteParagraphs(lines: string[]): MdInline[][] {
  const out: MdInline[][] = [];
  let buf: string[] = [];
  for (const line of [...lines, ""]) {
    if (line.trim()) buf.push(line.trim());
    else if (buf.length) { out.push(parseInline(buf.join(" "))); buf = []; }
  }
  return out;
}

function isTableSep(line: string) {
  return /^\|?\s*:?-{3,}/.test(line.trim());
}

function splitRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells: string[] = [];
  let buf = "";
  let depth = 0;
  const lastClose = trimmed.lastIndexOf("]]");
  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i]!;
    const next = trimmed[i + 1];
    if (ch === "\\" && next === "|") {
      buf += "|";
      i += 1;
      continue;
    }
    if (ch === "[" && next === "[" && lastClose >= i + 2) {
      depth += 1;
      buf += "[[";
      i += 1;
      continue;
    }
    if (ch === "]" && next === "]" && depth) {
      depth -= 1;
      buf += "]]";
      i += 1;
      continue;
    }
    if (ch === "|" && depth === 0) {
      cells.push(buf.trim());
      buf = "";
    } else {
      buf += ch;
    }
  }
  cells.push(buf.trim());
  return cells;
}


export function parseMarkdown(src: string): MdBlock[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: MdBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i] ?? "";
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed === "---") {
      blocks.push({ t: "hr" });
      i += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const lang = trimmed.slice(3).trim();
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").trim().startsWith("```")) {
        buf.push(lines[i] ?? "");
        i += 1;
      }
      i += 1;
      blocks.push({ t: "code", lang, code: buf.join("\n") });
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      blocks.push({
        t: "h",
        level: heading[1]!.length as 1 | 2 | 3 | 4,
        children: parseInline(heading[2]!),
      });
      i += 1;
      continue;
    }

    if (trimmed.startsWith("> [!")) {
      const m = trimmed.match(/^> \[!([a-zA-Z]+)\]\s*(.*)$/);
      const kind = (m?.[1] ?? "note").toLowerCase();
      const title = m?.[2] ?? "";
      const body: string[] = [];
      i += 1;
      while (i < lines.length && (lines[i] ?? "").startsWith(">")) {
        body.push((lines[i] ?? "").replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push({
        t: "callout",
        kind,
        title,
        children: quoteParagraphs(body),
      });
      continue;
    }

    if (trimmed.startsWith("> ")) {
      const body: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith(">")) {
        body.push((lines[i] ?? "").replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push({ t: "quote", children: quoteParagraphs(body) });
      continue;
    }

    if (trimmed.startsWith("|") && i + 1 < lines.length && isTableSep(lines[i + 1] ?? "")) {
      const headers = splitRow(trimmed).map(parseInline);
      i += 2;
      const rows: MdInline[][][] = [];
      while (i < lines.length && (lines[i] ?? "").trim().startsWith("|")) {
        rows.push(splitRow((lines[i] ?? "").trim()).map(parseInline));
        i += 1;
      }
      blocks.push({ t: "table", headers, rows });
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: MdInline[][] = [];
      while (i < lines.length && /^[-*]\s+/.test((lines[i] ?? "").trim())) {
        items.push(parseInline((lines[i] ?? "").trim().replace(/^[-*]\s+/, "")));
        i += 1;
      }
      blocks.push({ t: "ul", items });
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: MdInline[][] = [];
      while (i < lines.length && /^\d+\.\s+/.test((lines[i] ?? "").trim())) {
        items.push(parseInline((lines[i] ?? "").trim().replace(/^\d+\.\s+/, "")));
        i += 1;
      }
      blocks.push({ t: "ol", items });
      continue;
    }

    const para: string[] = [];
    while (i < lines.length) {
      const l = lines[i] ?? "";
      const t = l.trim();
      if (
        !t ||
        t === "---" ||
        t.startsWith("#") ||
        t.startsWith(">") ||
        t.startsWith("|") ||
        t.startsWith("```") ||
        /^[-*]\s+/.test(t) ||
        /^\d+\.\s+/.test(t)
      ) {
        break;
      }
      para.push(t);
      i += 1;
    }
    if (para.length) {
      blocks.push({ t: "p", children: parseInline(para.join(" ")) });
      continue;
    }

    // Unsupported or malformed block syntax must still make progress. Without
    // this fallback a line such as `##### heading`, `| not-a-table`, or `>x`
    // could leave `i` unchanged and lock the Explorer in an infinite loop.
    blocks.push({ t: "p", children: parseInline(trimmed) });
    i += 1;
  }

  return blocks;
}

export function extractWikilinks(src: string): string[] {
  const masked = src.replace(/```[\s\S]*?```/g, "").replace(/`[^`]*`/g, "");
  const out: string[] = [];
  const re = /\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]*)?\]\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(masked))) {
    const t = m[1]!.trim();
    if (t && !t.startsWith("http")) out.push(t);
  }
  return out;
}

export function extractHeadings(src: string): { id: string; text: string; level: number }[] {
  return src
    .split("\n")
    .map((line) => line.match(/^(#{2,3})\s+(.*)$/))
    .filter((m): m is RegExpMatchArray => Boolean(m))
    .map((m) => ({
      level: m[1]!.length,
      text: m[2]!.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2").replace(/\[\[([^\]]+)\]\]/g, "$1"),
      id: slugify(m[2]!),
    }));
}

export function slugify(text: string) {
  return text
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .normalize("NFKD")
    .toLowerCase()
    .replace(/\p{M}+/gu, "")
    .replace(/[^\p{Letter}\p{Number}]+/gu, "-")
    .replace(/(^-|-$)/g, "");
}

/**
 * Heading ids for one document. Repeated headings get `-1`, `-2` suffixes so
 * every in-page anchor stays unique and valid.
 */
export function createSlugger(): (text: string) => string {
  const seen = new Map<string, number>();
  return (text: string) => {
    const base = slugify(text) || "section";
    let n = seen.get(base) ?? 0;
    let id = n ? `${base}-${n}` : base;
    while (n && seen.has(id)) id = `${base}-${++n}`;
    seen.set(base, n + 1);
    seen.set(id, seen.get(id) ?? 1);
    return id;
  };
}

export function inlineText(nodes: MdInline[]): string {
  return nodes
    .map((n) => {
      if (n.t === "text" || n.t === "code") return n.v;
      if (n.t === "wiki") return n.label;
      if (n.t === "link") return n.label;
      if (n.t === "strong" || n.t === "em") return inlineText(n.v);
      return "";
    })
    .join("");
}
