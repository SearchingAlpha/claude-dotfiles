// Text as the terminal draws it: the control characters a drawing may not
// hold, how many cells a string takes (a CJK character or an emoji two, a
// combining mark none) and cutting to a width. No `$`, no I/O: the hooks module
// and the graph client both import it.

/**
 * C0 and C1 controls and DEL, tab and newline included, and the Unicode line
 * and paragraph separators: the engine refuses a tree that holds most of
 * them, a newline breaks a row, and YAML 1.1 (PyYAML) reads U+2028 and U+2029
 * as line breaks too.
 */
const CONTROL = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g

/** The same, but for tab and newline: what a block of source lines may keep. */
const CONTROL_IN_LINES = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u2028\u2029]+/g

/** Whether `text` holds a character `clean` would change. */
export const hasControl = (text: string): boolean => /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(text)

/** `text` with each run of control characters as one space: what a one-line label may hold. */
export function clean(text: string): string {
  return text.replace(CONTROL, ' ')
}

/** `text` with its line breaks made `\n` and every other control character but tab a space. */
export function cleanLines(text: string): string {
  return text.replace(/\r\n?/g, '\n').replace(CONTROL_IN_LINES, ' ')
}

// East Asian Wide and Fullwidth, and the emoji drawn two cells wide: the common
// ranges, not the whole Unicode table.
const WIDE: readonly (readonly [number, number])[] = [
  [0x1100, 0x115f],
  [0x231a, 0x231b],
  [0x2329, 0x232a],
  [0x23e9, 0x23ec],
  [0x23f0, 0x23f3],
  [0x25fd, 0x25fe],
  [0x2614, 0x2615],
  [0x2648, 0x2653],
  [0x267f, 0x267f],
  [0x2693, 0x2693],
  [0x26a1, 0x26a1],
  [0x26aa, 0x26ab],
  [0x26bd, 0x26be],
  [0x26c4, 0x26c5],
  [0x26ce, 0x26ce],
  [0x26d4, 0x26d4],
  [0x26ea, 0x26ea],
  [0x26f2, 0x26f5],
  [0x26fa, 0x26fd],
  [0x2705, 0x2705],
  [0x270a, 0x270b],
  [0x2728, 0x2728],
  [0x274c, 0x274e],
  [0x2753, 0x2757],
  [0x2795, 0x2797],
  [0x27b0, 0x27bf],
  [0x2b1b, 0x2b1c],
  [0x2b50, 0x2b55],
  [0x2e80, 0x303e],
  [0x3041, 0x33ff],
  [0x3400, 0x4dbf],
  [0x4e00, 0x9fff],
  [0xa000, 0xa4cf],
  [0xa960, 0xa97f],
  [0xac00, 0xd7a3],
  [0xf900, 0xfaff],
  [0xfe10, 0xfe19],
  [0xfe30, 0xfe6f],
  [0xff00, 0xff60],
  [0xffe0, 0xffe6],
  [0x16fe0, 0x16fe4],
  [0x17000, 0x18cff],
  [0x1b000, 0x1b2ff],
  [0x1f004, 0x1f004],
  [0x1f0cf, 0x1f0cf],
  [0x1f18e, 0x1f18e],
  [0x1f191, 0x1f19a],
  [0x1f200, 0x1f251],
  [0x1f300, 0x1f64f],
  [0x1f680, 0x1f6ff],
  [0x1f7e0, 0x1f7eb],
  [0x1f90c, 0x1f9ff],
  [0x1fa70, 0x1faff],
  [0x20000, 0x3fffd],
]

const isZeroWidth = (cp: number): boolean =>
  (cp >= 0x300 && cp <= 0x36f) ||
  (cp >= 0x1ab0 && cp <= 0x1aff) ||
  (cp >= 0x1dc0 && cp <= 0x1dff) ||
  (cp >= 0x200b && cp <= 0x200f) ||
  (cp >= 0x20d0 && cp <= 0x20ff) ||
  (cp >= 0xfe00 && cp <= 0xfe0f) ||
  (cp >= 0xe0100 && cp <= 0xe01ef)

/** The cells one code point takes: 0, 1 or 2. */
export function charWidth(cp: number): number {
  if (cp < 0x300) {
    return 1
  }
  if (isZeroWidth(cp)) {
    return 0
  }

  return WIDE.some(([lo, hi]) => cp >= lo && cp <= hi) ? 2 : 1
}

const widthOfChar = (ch: string): number => charWidth(ch.codePointAt(0) ?? 0)

/** The cells `text` takes on one row. */
export function widthOf(text: string): number {
  // Printable ASCII, the usual case, is one cell a character.
  if (/^[\x20-\x7e]*$/.test(text)) {
    return text.length
  }

  let width = 0
  for (const ch of text) {
    width += widthOfChar(ch)
  }

  return width
}

/** `text` cut to `width` cells, an ellipsis last when it was cut. */
export function cut(text: string, width: number): string {
  if (width <= 0) {
    return ''
  }
  if (widthOf(text) <= width) {
    return text
  }

  let kept = ''
  let used = 0
  for (const ch of text) {
    const w = widthOfChar(ch)
    if (used + w > width - 1) {
      break
    }
    kept += ch
    used += w
  }

  return `${kept}…`
}

/** `text` cut from the start to `width` cells, keeping its end: `…c › Offline editing`. */
export function cutStart(text: string, width: number): string {
  if (width <= 0) {
    return ''
  }
  if (widthOf(text) <= width) {
    return text
  }

  const chars = [...text]
  let kept = ''
  let used = 0
  for (let i = chars.length - 1; i >= 0; i--) {
    const ch = chars[i] ?? ''
    const w = widthOfChar(ch)
    if (used + w > width - 1) {
      break
    }
    kept = ch + kept
    used += w
  }

  return `…${kept}`
}

/** `text` padded with spaces to `width` cells. */
export function padCells(text: string, width: number): string {
  const length = widthOf(text)

  return length >= width ? text : text + ' '.repeat(width - length)
}
