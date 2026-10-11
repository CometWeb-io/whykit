/** Markdown code recognition shared by the Explorer renderer and link scanner.
 * No HTML interpretation; intentionally keeps text offsets stable.
 */
const BT = String.fromCharCode(96);
const OPEN = new RegExp("^ {0,3}(" + BT + "{3,}|~{3,})(.*)$");
const CLOSE = new RegExp("^ {0,3}(" + BT + "+|~+)[ \\t]*$");
const RUNS = new RegExp(BT + "+", "g");

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
  const lines = text.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  let active: Fence | null = null;
  let at = 0;
  for (const line of lines) {
    const body = line.replace(/\r?\n$/, "");
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
  for (let i = 0; i < runs.length;) {
    const close = closing[i];
    if (close === undefined) { i++; continue; }
    blank(chars, runs[i]!.start, runs[close]!.end);
    i = close + 1;
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
    let value = text.slice(open.end, finish.start).replace(/\n/g, " ");
    if (value.length > 1 && value.startsWith(" ") && value.endsWith(" ") && /[^ ]/.test(value)) {
      value = value.slice(1, -1);
    }
    return { index: open.start, length: finish.end - open.start, value };
  }
  return null;
}
