/** Position-preserving code recognition shared by rendering and link scanning.
 * shortcut: containers, indented code and other HTML blocks need a full parser.
 */
const BT = String.fromCharCode(96);
const OPEN = new RegExp("^ {0,3}(" + BT + "{3,}|~{3,})([\\s\\S]*)$");
const CLOSE = new RegExp("^ {0,3}(" + BT + "+|~+)[ \\t]*$");
const ATX_HEADING = /^ {0,3}#{1,6}(?:[ \t]|$)/;
const THEMATIC_BREAK = /^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$/;
// A literal comment opener would invalidate the safe single-file HTML build.
const COMMENT_START = [60, 33, 45, 45].map((point) => String.fromCharCode(point)).join("");
const TOKENS = new RegExp(BT + "+|" + COMMENT_START, "g");

export type Fence = { character: string; width: number; info: string };

export function fenceStart(line: string): Fence | null {
  const hit = OPEN.exec(line);
  if (!hit || (hit[1]![0] === BT && hit[2]!.includes(BT))) return null;
  return { character: hit[1]![0]!, width: hit[1]!.length, info: hit[2]!.trim() };
}

export function fenceEnd(line: string, fence: Fence): boolean {
  const hit = CLOSE.exec(line);
  return !!hit && hit[1]![0] === fence.character && hit[1]!.length >= fence.width;
}

export function thematicBreak(line: string): boolean {
  return THEMATIC_BREAK.test(line);
}

type Span = { start: number; end: number; kind: "code" | "fence" | "comment" };

function* markdownSpans(text: string): Generator<Span> {
  const lines = Array.from(text.matchAll(/[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+$/g), (hit) => {
    const body = hit[0].replace(/\r\n$|\n$|\r$/, "");
    return { start: hit.index, end: hit.index + hit[0].length, body, fence: fenceStart(body) };
  });
  const tokens = Array.from(text.matchAll(TOKENS));
  const closing: (number | undefined)[] = tokens.map(() => undefined);
  const nextBySize = new Map<number, number>();
  let tokenIndex = tokens.length - 1;
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!;
    const heading = ATX_HEADING.test(line.body);
    const boundary = line.fence || /^[ \t]*$/.test(line.body) || thematicBreak(line.body);
    if (boundary || heading) nextBySize.clear();
    while (tokenIndex >= 0 && tokens[tokenIndex]!.index >= line.start) {
      const token = tokens[tokenIndex]!;
      if (!boundary && token[0][0] === BT) {
        closing[tokenIndex] = nextBySize.get(token[0].length);
        nextBySize.set(token[0].length, tokenIndex);
      }
      tokenIndex--;
    }
    // Keep a heading's own inline spans, but never pair them with other lines.
    if (heading) nextBySize.clear();
  }

  let active: Fence | null = null;
  let cursor = 0;
  tokenIndex = 0;
  for (const line of lines) {
    if (line.end <= cursor) continue;
    if (active) {
      yield { start: line.start, end: line.end, kind: "fence" };
      if (fenceEnd(line.body, active)) active = null;
      cursor = line.end;
      continue;
    }
    if (cursor <= line.start && line.fence) {
      active = line.fence;
      yield { start: line.start, end: line.end, kind: "fence" };
      cursor = line.end;
      continue;
    }
    while (tokenIndex < tokens.length && tokens[tokenIndex]!.index < line.end) {
      const index = tokenIndex++;
      const token = tokens[index]!;
      const start = token.index;
      if (start < cursor) continue;
      if (token[0][0] === BT) {
        const endIndex = closing[index];
        cursor = endIndex === undefined ? start + token[0].length
          : tokens[endIndex]!.index + tokens[endIndex]![0].length;
        if (endIndex !== undefined) yield { start, end: cursor, kind: "code" };
        continue;
      }
      let escapes = 0;
      while (start - escapes - 1 >= 0 && text[start - escapes - 1] === "\\") escapes++;
      if (escapes % 2 === 1) { cursor = start + COMMENT_START.length; continue; }
      const suffix = text.slice(start + COMMENT_START.length, start + 6);
      if (suffix.startsWith(">")) cursor = start + 5;
      else if (suffix.startsWith("->")) cursor = start + 6;
      else {
        const stop = text.indexOf("-->", start + COMMENT_START.length);
        cursor = stop < 0 ? text.length : stop + 3;
      }
      yield { start, end: cursor, kind: "comment" };
    }
  }
}

function blank(chars: string[], start: number, end: number): void {
  for (let i = start; i < end; i++) {
    if (chars[i] !== "\r" && chars[i] !== "\n") chars[i] = " ";
  }
}

export function maskMarkdownCode(text: string): string {
  const chars = text.split("");
  for (const span of markdownSpans(text)) blank(chars, span.start, span.end);
  return chars.join("");
}

export function maskMarkdownComments(text: string): string {
  const chars = text.split("");
  for (const span of markdownSpans(text)) {
    if (span.kind === "comment") blank(chars, span.start, span.end);
  }
  return chars.join("");
}

/** First complete inline span; unmatched backticks are ordinary text. */
export function findCodeSpan(text: string): { index: number; length: number; value: string } | null {
  for (const span of markdownSpans(text)) {
    if (span.kind !== "code") continue;
    let width = 1;
    while (text[span.start + width] === BT) width++;
    let value = text.slice(span.start + width, span.end - width).replace(/\r\n|\n|\r/g, " ");
    if (value.length > 1 && value.startsWith(" ") && value.endsWith(" ") && /[^ ]/.test(value)) {
      value = value.slice(1, -1);
    }
    return { index: span.start, length: span.end - span.start, value };
  }
  return null;
}
