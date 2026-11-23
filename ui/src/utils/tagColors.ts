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
  let loadGeneration = 0;
  let confirmed: Record<string, TagSwatch> = {};
  // Edits not yet settled, the newest per tag (null clears). They stay on screen over refreshes.
  const pending = new Map<string, TagSwatch | null>();
  const tagRevisions = new Map<string, number>();
  let revisions = 0;
  // Successful saves are numbered so a refresh that started before one cannot undo it.
  let saveCount = 0;
  const savedAt = new Map<string, number>();
  let queue = Promise.resolve();
  const normalize = (map: Record<string, string>) => Object.fromEntries(
    Object.entries(map).filter(([tag, swatch]) => normalizeTagName(tag) && TAG_COLOR_SWATCHES.includes(swatch as TagSwatch))
      .map(([tag, swatch]) => [normalizeTagName(tag), swatch as TagSwatch]),
  );
  const hasOwn = (map: object, key: string) => Object.prototype.hasOwnProperty.call(map, key);
  const render = () => {
    const next = { ...confirmed };
    for (const [key, swatch] of pending) {
      if (swatch) next[key] = swatch;
      else delete next[key];
    }
    colors.value = next;
  };
  const load = (force = false): Promise<void> => {
    if (loaded && !force) return loaded;
    const revision = ++loadGeneration;
    const savesAtStart = saveCount;
    loaded = getApi().then(api => api.getSettings()).then(settings => {
      if (revision !== loadGeneration) return;
      const next = normalize(settings.tag_colors || {});
      // A save that settled after this read began is newer than what it read.
      for (const [key, count] of savedAt) {
        if (count <= savesAtStart) continue;
        if (hasOwn(confirmed, key)) next[key] = confirmed[key]!;
        else delete next[key];
      }
      confirmed = next;
      render();
    }).catch(error => { loaded = null; throw error; });
    return loaded;
  };
  const set = (tag: string, swatch: TagSwatch | null): Promise<void> => {
    const key = normalizeTagName(tag);
    const revision = ++revisions;
    tagRevisions.set(key, revision);
    pending.set(key, swatch);
    render();
    const settle = () => {
      if (tagRevisions.get(key) === revision) pending.delete(key);
      render();
    };
    const save = queue.then(async () => {
      try {
        const result = await (await getApi()).setTagColor(key, swatch);
        if (!result.success) throw new Error(result.error || "Could not save tag color");
        if (result.tag_colors) confirmed = normalize(result.tag_colors);
        else if (swatch) confirmed = { ...confirmed, [key]: swatch };
        else {
          confirmed = { ...confirmed };
          delete confirmed[key];
        }
        savedAt.set(key, ++saveCount);
      } catch (error) {
        settle();
        throw error;
      }
      settle();
    });
    queue = save.catch(() => {});
    return save;
  };
  return { colors: readonly(colors), load, set, colorForTag: (tag: string) => hasOwn(colors.value, normalizeTagName(tag)) ? colors.value[normalizeTagName(tag)] : undefined };
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
