/** Markdown code recognition shared by the Explorer renderer and link scanner.
 * No HTML interpretation; intentionally keeps text offsets stable.
 */
const BT = String.fromCharCode(96);
const OPEN = new RegExp("^ {0,3}(" + BT + "{3,}|~{3,})([\\s\\S]*)$");
const CLOSE = new RegExp("^ {0,3}(" + BT + "+|~+)[ \\t]*$");
const RUNS = new RegExp(BT + "+", "g");
// Construct this marker at runtime: a literal would invalidate the
// Explorer's safe single-file HTML build.
const COMMENT_START = [60, 33, 45, 45].map((point) => String.fromCharCode(point)).join("");

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

function blank(chars: string[], start: number, stop: number): void {
  for (let i = start; i < stop; i++) {
    if (chars[i] !== "\r" && chars[i] !== "\n") chars[i] = " ";
  }
}

/** Mask fenced code and variable-length backtick code spans in linear time. */
export function maskMarkdownCode(text: string): string {
  const chars = text.split("");
  // Markdown line endings include CRLF, LF and bare CR (CommonMark §2.3).
  const lines = text.match(/[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+$/g) ?? [];
  let active: Fence | null = null;
  let at = 0;
  for (const line of lines) {
    const body = line.replace(/\r\n$|\n$|\r$/, "");
    if (!active) {
      const opening = fenceStart(body);
      if (opening) {
        active = opening;
        blank(chars, at, at + line.length);
      }
    } else {
      blank(chars, at, at + line.length);
      if (fenceEnd(body, active)) active = null;
    }
    at += line.length;
  }
  const masked = chars.join("");
  const runs: { start: number; end: number }[] = [];
  for (const hit of masked.matchAll(RUNS)) {
    runs.push({ start: hit.index, end: hit.index + hit[0].length });
  }
  const nextBySize = new Map<number, number>();
  const closing: (number | undefined)[] = runs.map(() => undefined);
  for (let i = runs.length - 1; i >= 0; i--) {
    const size = runs[i]!.end - runs[i]!.start;
    closing[i] = nextBySize.get(size);
    nextBySize.set(size, i);
  }

  // A comment that opens before a backtick span closes at the first literal
  // terminator, even if its body contains a matched backtick pair. Inline
  // code opened first owns any apparent comment marker within its span.
  // Keep next-marker positions while skipping tokens: each search is over a
  // fresh suffix, giving linear total scanning work.
  let cursor = 0;
  let runIndex = 0;
  let commentStart = masked.indexOf(COMMENT_START);
  while (cursor < masked.length) {
    while (runIndex < runs.length && runs[runIndex]!.end <= cursor) runIndex++;
    if (commentStart >= 0 && commentStart < cursor) {
      commentStart = masked.indexOf(COMMENT_START, cursor);
    }
    const nextRun = runIndex < runs.length ? runs[runIndex]!.start : Infinity;
    if (commentStart >= 0 && commentStart < nextRun) {
      let escapes = 0;
      while (commentStart - escapes - 1 >= 0 &&
             masked[commentStart - escapes - 1] === "\\") escapes++;
      if (escapes % 2 === 1) {
        cursor = commentStart + COMMENT_START.length;
        commentStart = masked.indexOf(COMMENT_START, cursor);
        continue;
      }
      // Short empty-comment forms also terminate immediately (CommonMark).
      const suffix = masked.slice(commentStart + COMMENT_START.length, commentStart + 6);
      if (suffix.startsWith(">")) cursor = commentStart + 5;
      else if (suffix.startsWith("->")) cursor = commentStart + 6;
      else {
        const stop = masked.indexOf("-->", commentStart + COMMENT_START.length);
        cursor = stop < 0 ? masked.length : stop + 3;
      }
      blank(chars, commentStart, cursor);
      commentStart = masked.indexOf(COMMENT_START, cursor);
      continue;
    }
    if (runIndex >= runs.length) break;
    const endIndex = closing[runIndex];
    if (endIndex === undefined) {
      cursor = runs[runIndex]!.end;
      runIndex++;
    } else {
      cursor = runs[endIndex]!.end;
      blank(chars, runs[runIndex]!.start, cursor);
      runIndex = endIndex + 1;
    }
  }
  return chars.join("");
}

/** First complete code span in a fragment; unmatched backticks are ordinary text. */
export function findCodeSpan(text: string): { index: number; length: number; value: string } | null {
  const runs: { start: number; end: number }[] = [];
  for (const hit of text.matchAll(RUNS)) {
    runs.push({ start: hit.index, end: hit.index + hit[0].length });
  }
  const nextBySize = new Map<number, number>();
  const closing: (number | undefined)[] = runs.map(() => undefined);
  for (let i = runs.length - 1; i >= 0; i--) {
    const size = runs[i]!.end - runs[i]!.start;
    closing[i] = nextBySize.get(size);
    nextBySize.set(size, i);
  }
  for (let i = 0; i < runs.length; i++) {
    const close = closing[i];
    if (close === undefined) continue;
    const open = runs[i]!, finish = runs[close]!;
    let value = text.slice(open.end, finish.start).replace(/\r\n|\n|\r/g, " ");
    if (value.length > 1 && value.startsWith(" ") && value.endsWith(" ") && /[^ ]/.test(value)) {
      value = value.slice(1, -1);
    }
    return { index: open.start, length: finish.end - open.start, value };
  }
  return null;
}
