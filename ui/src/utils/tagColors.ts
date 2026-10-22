// SPDX-License-Identifier: GPL-3.0-or-later
import { readonly, ref } from "vue";
import { api } from "../services/api";

export const TAG_COLOR_SWATCHES = ["red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"] as const;
export type TagSwatch = typeof TAG_COLOR_SWATCHES[number];
export const normalizeTagName = (tag: string): string => tag.trim().toLowerCase();

interface TagColorApi {
  getSettings(): Promise<{ tag_colors: Record<string, string> }>;
  setTagColor(tag: string, swatch: string | null): Promise<{ success?: boolean; error?: string | undefined; tag_colors?: Record<string, string> }>;
}

// Injection keeps state and rollback behavior testable without a desktop bridge.
export function createTagColors(getApi: () => Promise<TagColorApi>) {
  const colors = ref<Record<string, TagSwatch>>({});
  let loaded: Promise<void> | null = null;
  let generation = 0;
  let confirmed: Record<string, TagSwatch> = {};
  const tagRevisions = new Map<string, number>();
  let queue = Promise.resolve();
  const normalize = (map: Record<string, string>) => Object.fromEntries(
    Object.entries(map).filter(([tag, swatch]) => normalizeTagName(tag) && TAG_COLOR_SWATCHES.includes(swatch as TagSwatch))
      .map(([tag, swatch]) => [normalizeTagName(tag), swatch as TagSwatch]),
  );
  const load = (force = false): Promise<void> => {
    if (loaded && !force) return loaded;
    const revision = ++generation;
    loaded = getApi().then(api => api.getSettings()).then(settings => {
      if (revision === generation) {
        confirmed = normalize(settings.tag_colors || {});
        colors.value = { ...confirmed };
      }
    }).catch(error => { loaded = null; throw error; });
    return loaded;
  };
  const set = (tag: string, swatch: TagSwatch | null): Promise<void> => {
    const key = normalizeTagName(tag);
    const revision = ++generation;
    tagRevisions.set(key, revision);
    if (swatch) colors.value = { ...colors.value, [key]: swatch };
    else delete colors.value[key];
    const save = queue.then(async () => {
      try {
        const result = await (await getApi()).setTagColor(key, swatch);
        if (!result.success) throw new Error(result.error || "Could not save tag color");
        if (result.tag_colors) confirmed = normalize(result.tag_colors);
        else if (swatch) confirmed[key] = swatch;
        else delete confirmed[key];
        if (revision === generation) colors.value = { ...confirmed };
      } catch (error) {
        if (tagRevisions.get(key) === revision) {
          if (Object.prototype.hasOwnProperty.call(confirmed, key)) colors.value = { ...colors.value, [key]: confirmed[key]! };
          else delete colors.value[key];
        }
        throw error;
      }
    });
    queue = save.catch(() => {});
    return save;
  };
  return { colors: readonly(colors), load, set, colorForTag: (tag: string) => Object.prototype.hasOwnProperty.call(colors.value, normalizeTagName(tag)) ? colors.value[normalizeTagName(tag)] : undefined };
}

const shared = createTagColors(async () => api);
export const tagColors = shared.colors;
export const loadTagColors = shared.load;
export const colorForTag = shared.colorForTag;
export const setTagColor = shared.set;
if (typeof window !== "undefined") {
  window.addEventListener("wlib-refresh-library", () => {
    void loadTagColors(true).catch(error => console.error("Could not reload tag colors", error));
  });
}
