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
