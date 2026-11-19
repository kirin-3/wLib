// SPDX-License-Identifier: GPL-3.0-or-later
// State and messages for the "F95 account" tab on the Import / Export page.
import type {
  F95AccountImportResponse,
  F95AccountPreview,
  F95AccountReadProgress,
  F95AccountReadStartResponse,
  F95AccountReadStatus,
  F95AccountResponse,
} from "../services/api";

export interface F95AccountClient {
  startF95AccountRead(): Promise<F95AccountReadStartResponse>;
  getF95AccountReadStatus(): Promise<F95AccountReadStatus>;
  cancelF95AccountRead(): Promise<F95AccountResponse>;
  importF95AccountLists(readId: string): Promise<F95AccountImportResponse>;
}

export type F95AccountPhase = "idle" | "starting" | "reading" | "preview" | "importing" | "done" | "error" | "cancelled";

const ERROR_MESSAGES: Record<string, string> = {
  no_extension:
    "No wLib browser extension is connected. Install or pair the wLib extension, keep the browser open, then try again.",
  extension_outdated:
    "Your wLib browser extension is too old to read F95 lists. Update the extension, then try again.",
  login_required:
    "You are signed out of F95Zone in the browser. Sign in to F95Zone in the browser with the wLib extension, then try again.",
  blocked:
    "F95Zone blocked the request. Open f95zone.to in the browser with the wLib extension, keep that tab open, then try again.",
  not_a_list:
    "F95Zone did not return a watched-threads list page. Open f95zone.to/watched/threads in the browser to check it, then try again.",
  rate_limited: "F95Zone asked wLib to slow down. Wait a minute, then try again.",
  timeout: "The browser extension did not answer in time. Keep the browser open, then try again.",
  fetch_failed: "The browser extension could not read the page. Keep the browser open, then try again.",
  busy: "wLib is already reading your F95 lists.",
  not_found: "This preview is no longer available. Read your F95 lists again.",
  expired: "This preview expired. Read your F95 lists again.",
  empty: "No watched or bookmarked games were found on F95Zone. There is nothing to import.",
  import_failed: "The import could not be saved. Your library was not changed. Try again.",
};

/** The message for an error code; it names the fix. */
export function f95AccountErrorMessage(code: string | undefined, fallback = ""): string {
  return (code && ERROR_MESSAGES[code]) || fallback || "Reading your F95 lists failed. Try again.";
}

export function f95AccountProgressText(progress: F95AccountReadProgress | undefined): string {
  if (!progress?.list_label || !progress.page) return "Starting...";
  const found = progress.found ?? 0;
  const total = typeof progress.total === "number" ? ` of ${progress.total}` : "";
  return `${progress.list_label}, page ${progress.page}. Found ${found}${total} games.`;
}

export function f95AccountResultText(result: F95AccountImportResponse): string {
  return `${result.added ?? 0} added, ${result.tagged ?? 0} tagged`;
}

export class F95AccountImport {
  phase: F95AccountPhase = "idle";
  readId = "";
  progress: F95AccountReadProgress = {};
  preview: F95AccountPreview | null = null;
  result: F95AccountImportResponse | null = null;
  error = "";
  errorCode = "";
  cancelling = false;

  private readonly client: F95AccountClient;

  constructor(client: F95AccountClient) {
    this.client = client;
  }

  get busy(): boolean {
    return this.phase === "starting" || this.phase === "reading" || this.phase === "importing";
  }

  get emptyLists(): boolean {
    return this.phase === "preview" && (this.preview?.total ?? 0) === 0;
  }

  get canImport(): boolean {
    return this.phase === "preview" && (this.preview?.total ?? 0) > 0 && Boolean(this.readId);
  }

  private fail(response: F95AccountResponse | undefined, fallback: string): void {
    this.phase = response?.error_code === "cancelled" ? "cancelled" : "error";
    this.errorCode = response?.error_code ?? "";
    this.error = f95AccountErrorMessage(response?.error_code, response?.error || fallback);
  }

  /** Start a read. Returns true when it runs and the caller should poll. */
  async start(): Promise<boolean> {
    if (this.busy) return false;
    this.phase = "starting";
    this.readId = "";
    this.progress = {};
    this.preview = null;
    this.result = null;
    this.error = "";
    this.errorCode = "";
    this.cancelling = false;
    try {
      const response = await this.client.startF95AccountRead();
      if (!response || response.success === false || !response.read_id) {
        this.fail(response, "Could not start reading your F95 lists.");
        return false;
      }
      this.readId = response.read_id;
      this.phase = "reading";
      return true;
    } catch (error) {
      this.fail(undefined, `Could not start reading your F95 lists: ${String(error)}`);
      return false;
    }
  }

  /** Read the status once. Returns true while the read still runs. */
  async poll(): Promise<boolean> {
    if (this.phase !== "reading") return false;
    let status: F95AccountReadStatus;
    try {
      status = await this.client.getF95AccountReadStatus();
    } catch (error) {
      this.fail(undefined, `Could not read the progress: ${String(error)}`);
      return false;
    }
    if (status.read_id && status.read_id !== this.readId) {
      this.fail({ error_code: "not_found" }, "");
      return false;
    }
    this.progress = status.progress ?? this.progress;
    if (status.state === "running") return true;
    this.cancelling = false;
    if (status.state === "done" && status.preview) {
      this.preview = status.preview;
      this.phase = "preview";
      return false;
    }
    this.fail(status, "Reading your F95 lists failed.");
    return false;
  }

  async cancel(): Promise<void> {
    if (this.phase !== "reading" || this.cancelling) return;
    this.cancelling = true;
    try {
      const response = await this.client.cancelF95AccountRead();
      if (response?.success === false && response.error_code !== "not_running") {
        this.cancelling = false;
      }
    } catch {
      this.cancelling = false;
    }
  }

  async importLists(): Promise<boolean> {
    if (!this.canImport) return false;
    this.phase = "importing";
    this.error = "";
    this.errorCode = "";
    try {
      const response = await this.client.importF95AccountLists(this.readId);
      if (!response || response.success === false) {
        this.fail(response, "Import failed.");
        // The library was not changed and the backend kept the preview: allow a retry.
        if (response?.error_code === "import_failed") this.phase = "preview";
        return false;
      }
      this.result = response;
      this.preview = null;
      this.phase = "done";
      return true;
    } catch (error) {
      this.fail(undefined, `Import failed: ${String(error)}`);
      return false;
    }
  }
}
