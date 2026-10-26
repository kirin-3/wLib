// SPDX-License-Identifier: GPL-3.0-or-later
import type { CatalogStatusResponse } from "../services/api";

export const CATALOG_SOURCE_LABELS: Record<string, string> = {
  f95: "F95Zone",
  dlsite: "DLsite",
  ryuugames: "Ryuugames",
  vndb: "VNDB",
};

const SOURCE_ORDER = Object.keys(CATALOG_SOURCE_LABELS);

export const formatCatalogTime = (value: string | null | undefined): string => {
  if (!value) return "never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

export interface CatalogSourceLine {
  key: string;
  label: string;
  count: string;
  updated: string;
}

export const catalogSourceLines = (status: CatalogStatusResponse | null): CatalogSourceLine[] => {
  const sources = status?.sources ?? {};
  return Object.keys(sources)
    .sort((a, b) => {
      const order = (key: string) => (SOURCE_ORDER.includes(key) ? SOURCE_ORDER.indexOf(key) : SOURCE_ORDER.length);
      return order(a) - order(b) || a.localeCompare(b);
    })
    .map((key) => ({
      key,
      label: CATALOG_SOURCE_LABELS[key] ?? key,
      count: typeof sources[key]?.count === "number" ? sources[key].count.toLocaleString() : "—",
      updated: formatCatalogTime(sources[key]?.updated_at),
    }));
};

export const catalogSummary = (status: CatalogStatusResponse | null): string => {
  if (!status) return "Loading catalog status…";
  if (status.success === false) return status.error || "Could not read the catalog status.";
  if (!status.enabled) return "Turned off. wLib does not download the catalog.";
  if (status.checking && !status.installed) return "Downloading the catalog…";
  if (!status.installed) return "Not downloaded yet.";
  return `Built ${formatCatalogTime(status.built_at)}.`;
};
