// SPDX-License-Identifier: GPL-3.0-or-later
// Browser-preview mocks for the F95 account import (start_f95_account_read,
// get_f95_account_read_status, cancel_f95_account_read, import_f95_account_lists).
//
// Demo: the read walks 2 Watched pages and 1 Bookmarks page. Set localStorage
// "wlib-mock-f95-account" to an error code (no_extension, extension_outdated,
// login_required, blocked, not_a_list) to see that error. Use "empty" for
// empty lists, "missing_tags" for partial coverage or "catalog_off" for none.
import type {
  F95AccountImportResponse,
  F95AccountPreview,
  F95AccountReadStartResponse,
  F95AccountReadStatus,
  F95AccountResponse,
} from "../api";

// Slow enough for the status poll to show each page.
const MOCK_PAGE_MS = 700;
const PAGES: { list: "watched" | "bookmarks"; label: string; page: number; found: number }[] = [
  { list: "watched", label: "Watched games", page: 1, found: 20 },
  { list: "watched", label: "Watched games", page: 2, found: 25 },
  { list: "bookmarks", label: "Bookmarks", page: 1, found: 28 },
];
const MESSAGES: Record<string, string> = {
  no_extension: "No paired wLib browser extension is connected.",
  extension_outdated: "The wLib browser extension is too old to read F95 lists.",
  login_required: "The browser is not signed in to F95Zone.",
  blocked: "F95Zone blocked the request with an anti-bot check.",
  not_a_list: "F95Zone did not return a list page.",
};

const mockMode = (): string => {
  try {
    return localStorage.getItem("wlib-mock-f95-account") || "";
  } catch {
    return "";
  }
};

const demoPreview = (mode: string): F95AccountPreview => {
  if (mode === "empty") {
    return {
      lists: { watched: { found: 0, new: 0, existing: 0 }, bookmarks: { found: 0, new: 0, existing: 0 } },
      total: 0, new: 0, existing: 0, without_tags: 0, catalog_off: false, entries: [],
    };
  }
  const entries = Array.from({ length: 27 }, (_, index) => {
    const id = index + 1;
    const lists: ("watched" | "bookmarks")[] = id <= 25 ? ["watched"] : ["bookmarks"];
    if (id === 5) lists.push("bookmarks");
    return {
      thread_id: String(100000 + id), title: `Mock Game ${id}`, version: "1.0", developer: "Mock Dev",
      engine: "Ren'Py", prefixes: ["Ren'Py"], lists, in_library: id <= 4,
    };
  });
  const newCount = 23;
  const catalogOff = mode === "catalog_off";
  return {
    lists: { watched: { found: 25, new: 21, existing: 4 }, bookmarks: { found: 3, new: 3, existing: 0 } },
    total: 27, new: newCount, existing: 4, entries,
    without_tags: catalogOff ? newCount : mode === "missing_tags" ? 6 : 0,
    catalog_off: catalogOff,
  };
};

export class MockF95Account {
  status: F95AccountReadStatus = { success: true, read_id: "", state: "idle", progress: {} };
  timer: ReturnType<typeof setTimeout> | null = null;
  step = 0;
  counter = 0;

  pageMs: number;

  constructor(pageMs = MOCK_PAGE_MS) {
    this.pageMs = pageMs;
  }

  respond(method: string, args: unknown[]): unknown {
    switch (method) {
      case "start_f95_account_read":
        return this.start();
      case "get_f95_account_read_status":
        return { ...this.status, mock: true };
      case "cancel_f95_account_read":
        return this.cancel();
      case "import_f95_account_lists":
        return this.importLists(String(args[0] ?? ""));
      default:
        return undefined;
    }
  }

  start(): F95AccountReadStartResponse {
    if (this.status.state === "running") {
      return { success: false, mock: true, error: "wLib is already reading your F95 lists.", error_code: "busy" };
    }
    const mode = mockMode();
    if (mode === "no_extension" || mode === "extension_outdated") {
      return { success: false, mock: true, error: MESSAGES[mode] ?? mode, error_code: mode };
    }
    this.counter += 1;
    const readId = `mock-read-${this.counter}`;
    this.step = 0;
    this.status = { success: true, read_id: readId, state: "running", progress: {} };
    this.advance(readId, mode);
    return { success: true, mock: true, read_id: readId };
  }

  advance(readId: string, mode: string): void {
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.status.read_id !== readId || this.status.state !== "running") return;
      if (mode in MESSAGES) {
        this.status = { success: false, read_id: readId, state: "error", progress: this.status.progress ?? {},
          error: MESSAGES[mode] ?? mode, error_code: mode };
        return;
      }
      const page = mode === "empty" ? undefined : PAGES[this.step];
      if (!page) {
        this.status = { success: true, read_id: readId, state: "done", progress: this.status.progress ?? {},
          preview: demoPreview(mode) };
        return;
      }
      this.status = { ...this.status, progress: { list: page.list, list_label: page.label, page: page.page,
        found: page.found, total: 28 } };
      this.step += 1;
      this.advance(readId, mode);
    }, this.pageMs);
  }

  cancel(): F95AccountResponse {
    if (this.status.state !== "running") {
      return { success: false, mock: true, error: "wLib is not reading your F95 lists.", error_code: "not_running" };
    }
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.status = { success: false, read_id: this.status.read_id ?? "", state: "cancelled", progress: this.status.progress ?? {},
      error: "Reading the F95 lists was cancelled.", error_code: "cancelled" };
    return { success: true, mock: true };
  }

  importLists(readId: string): F95AccountImportResponse {
    const preview = this.status.preview;
    if (this.status.state !== "done" || !preview || readId !== this.status.read_id) {
      return { success: false, mock: true, error: "This preview is no longer available. Read your F95 lists again.",
        error_code: "not_found" };
    }
    if (!preview.total) {
      return { success: false, mock: true, error: "No watched or bookmarked games were found.", error_code: "empty" };
    }
    this.status = { success: true, read_id: readId, state: "imported", progress: {} };
    return { success: true, mock: true, added: preview.new, tagged: preview.existing,
      without_tags: preview.without_tags ?? 0, added_ids: [],
      metadata_check: "none" };
  }
}
