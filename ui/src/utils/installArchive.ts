// SPDX-License-Identifier: GPL-3.0-or-later
/** Opens the global install-from-archive dialog (hosted in App.vue). */
import type { StoreLink } from "../services/api";

export interface InstallArchiveSeed {
  links?: StoreLink[];
  title: string;
  version: string;
  creator: string;
  engine: string;
  f95Url: string;
  coverImage: string;
  tags: string | string[];
  rating: string;
}

export interface InstallArchiveRequest {
  seed?: InstallArchiveSeed | undefined;
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

/** Apply automatic fills only to fields the user has left untouched. */
export function mergeArchiveFields<T extends Record<string, string>>(
  current: T, incoming: Partial<T>, edited: ReadonlySet<keyof T>,
): T {
  const merged = { ...current };
  for (const key of Object.keys(incoming) as Array<keyof T>) {
    const value = incoming[key];
    if (!edited.has(key) && value) merged[key] = value;
  }
  return merged;
}

/** Invalidate in-flight responses immediately, including during the debounce. */
export function createInstallPreview<T, R>(
  request: (input: T) => Promise<R>,
  apply: (result: R) => void,
  onError: (error: unknown) => void,
  delay = 250,
) {
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const cancel = () => {
    generation++;
    clearTimeout(timer);
    timer = undefined;
  };
  return {
    cancel,
    schedule(input: T) {
      cancel();
      const id = generation;
      timer = setTimeout(async () => {
        timer = undefined;
        try {
          const result = await request(input);
          if (id === generation) apply(result);
        } catch (error) {
          if (id === generation) onError(error);
        }
      }, delay);
    },
  };
}
