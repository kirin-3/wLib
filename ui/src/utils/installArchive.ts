// SPDX-License-Identifier: GPL-3.0-or-later
/** Opens the global install-from-archive dialog (hosted in App.vue). */

export interface InstallArchiveRequest {
  gameId?: number | undefined;
  archivePath?: string | undefined;
}

export const INSTALL_ARCHIVE_EVENT = "wlib-install-archive";

export const openInstallArchive = (request: InstallArchiveRequest = {}): void => {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<InstallArchiveRequest>(INSTALL_ARCHIVE_EVENT, { detail: request }),
  );
};

export interface InstallArchiveReseedState {
  modelValue: boolean;
  /** True while an install owns the dialog: its progress and result must survive. */
  installing: boolean;
}

/**
 * The dialog is hosted once, so a request can arrive while it is already open
 * (download-offer toasts sit above the dialog backdrop). The form must re-seed
 * from the new request — a superseded inspection is cheap to cancel — but never
 * while an install is running, because that job owns the fields, the progress
 * display and the result.
 */
export const canReseedInstallArchiveDialog = (state: InstallArchiveReseedState): boolean =>
  state.modelValue && !state.installing;
