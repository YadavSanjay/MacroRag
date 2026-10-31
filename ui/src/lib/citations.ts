// Citation numbering — assign stable, reader-facing numbers ([1], [2], …) to
// cited chunks so the primary UI never shows a raw chunk id. Numbers follow
// first appearance across the supplied groups (answer details in reading order,
// then any remaining cited ids), so the same source keeps the same number
// wherever it is referenced.
export function citationIndex(groups: Iterable<string>[]): Map<string, number> {
  const map = new Map<string, number>();
  let n = 0;
  for (const group of groups) {
    for (const id of group) {
      if (!map.has(id)) map.set(id, ++n);
    }
  }
  return map;
}
