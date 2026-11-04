// SPDX-License-Identifier: GPL-3.0-or-later
export interface SettingsSearchSection {
  id: string;
  title: string;
  rows: { id: string; label: string; keywords: string }[];
}

/** Return matching section and row IDs without mutating the supplied registry. */
export function matchSettings(sections: SettingsSearchSection[], query: string) {
  const term = query.trim().toLocaleLowerCase();
  const sectionIds: string[] = [];
  const rowIds: string[] = [];
  for (const section of sections) {
    const wholeSection = !term || section.title.toLocaleLowerCase().includes(term);
    const matches = section.rows.filter(row => wholeSection ||
      `${row.label} ${row.keywords}`.toLocaleLowerCase().includes(term));
    if (matches.length) sectionIds.push(section.id);
    rowIds.push(...matches.map(row => row.id));
  }
  return { sectionIds, rowIds };
}
