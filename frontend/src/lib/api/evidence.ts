/** Offsets refer to normalized Unicode code points, not JavaScript UTF-16 units. */
export function quoteAt(text: string, start: number, end: number): string {
  const points = Array.from(text);
  if (
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 0 ||
    end <= start ||
    end > points.length
  ) {
    throw new RangeError("Invalid code point span");
  }
  return points.slice(start, end).join("");
}
