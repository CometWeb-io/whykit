/** Width of `text` in pixels, in whatever font the caller measures with. */
export type Measure = (text: string) => number;

export type FittedLabel = { lines: string[]; truncated: boolean };

const ELLIPSIS = "…";

/** Longest prefix of `text` that, with an ellipsis, fits `width`. */
function ellipsize(text: string, width: number, measure: Measure): string {
  const trimmed = text.trimEnd();
  if (measure(trimmed) <= width) return trimmed;
  let lo = 0, hi = trimmed.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (measure(trimmed.slice(0, mid).trimEnd() + ELLIPSIS) <= width) lo = mid;
    else hi = mid - 1;
  }
  return trimmed.slice(0, lo).trimEnd() + ELLIPSIS;
}

/** Longest prefix of `word` that fits `width` on its own (at least one character). */
function breakWord(word: string, width: number, measure: Measure): number {
  let lo = 1, hi = word.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (measure(word.slice(0, mid)) <= width) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/**
 * Wrap `text` into at most `maxLines` lines no wider than `width`, breaking at
 * spaces (or inside a word that is wider than a line on its own) and ending
 * the last line with an ellipsis when the text does not fit. A label is only
 * shortened when it really overflows the space it has, so short and narrow
 * titles are never cut at an arbitrary character count.
 */
export function fitLabel(text: string, width: number, measure: Measure, maxLines = 2): FittedLabel {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return { lines: [""], truncated: false };
  const lines: string[] = [];
  let line = "";
  let i = 0;
  while (i < words.length) {
    const word = words[i]!;
    const candidate = line ? `${line} ${word}` : word;
    if (measure(candidate) <= width) {
      line = candidate;
      i++;
      continue;
    }
    if (!line) {
      // A single word wider than the line: split it.
      const cut = breakWord(word, width, measure);
      line = word.slice(0, cut);
      words[i] = word.slice(cut);
    }
    lines.push(line);
    line = "";
    if (lines.length === maxLines) break;
  }
  if (lines.length < maxLines) {
    if (line) lines.push(line);
    return { lines, truncated: false };
  }
  const rest = words.slice(i).join(" ");
  if (!rest && !line) return { lines, truncated: false };
  const last = lines.pop()!;
  lines.push(ellipsize(`${last} ${rest}`, width, measure));
  return { lines, truncated: true };
}

/**
 * A cached text measurer for one CSS font, backed by a canvas. Falls back to
 * an average character width where canvas is unavailable (tests, old engines).
 */
export function canvasMeasure(font: string, fallbackCharWidth = 6): Measure {
  const cache = new Map<string, number>();
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
    if (ctx) ctx.font = font;
  } catch {
    ctx = null;
  }
  return (text: string) => {
    let width = cache.get(text);
    if (width === undefined) {
      width = ctx ? ctx.measureText(text).width : text.length * fallbackCharWidth;
      cache.set(text, width);
    }
    return width;
  };
}
