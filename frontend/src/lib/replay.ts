/** Display-only progression: no demand/availability forecast logic. */
export function advanceFrame(index: number, length: number) {
  return Math.min(Math.max(0, length - 1), index + 1);
}
