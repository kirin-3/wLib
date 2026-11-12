<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { IconChevronDown, IconChevronLeft, IconChevronRight } from "@tabler/icons-vue";
import type { GameRecord } from "../services/api";
import { addTagCsv, launchModeApplicable, removeTagCsv } from "../utils/bulkActions";
import { ENGINE_OPTIONS, isEngine } from "../utils/engines";
import { gameTags } from "../utils/libraryGames";
import { normalizePlayStatus } from "../utils/playStatus";

type MenuId = "status" | "collections" | "updates" | "runtime" | "metadata" | "maintenance";
type SubView = "" | "launch-mode" | "add-tag" | "remove-tag" | "engine" | "collection";

const props = defineProps<{
  games: GameRecord[];
  statuses: { value: string; label: string }[];
  collections: string[];
  tagSuggestions: string[];
  runningIds: ReadonlySet<number>;
  showRuntime: boolean;
  busy: boolean;
  checking: boolean;
  savesBusy: boolean;
  eligibleChecks: number;
  eligibleUpdates: number;
}>();

const emit = defineEmits<{
  (event: "select-all"): void;
  (event: "done"): void;
  (event: "action", action: string, value?: string): void;
  (event: "wine"): void;
}>();

const root = ref<HTMLElement | null>(null);
const openMenu = ref<MenuId | null>(null);
const panelStyle = ref<Record<string, string>>({});
const subView = ref<SubView>("");
const collectionChoice = ref("");
const tagInput = ref("");
const removeTagChoice = ref("");
const engineInput = ref("");
const triggers = new Map<MenuId, HTMLElement>();

const menus = computed<{ id: MenuId; label: string }[]>(() => [
  { id: "status", label: "Status" },
  { id: "collections", label: "Collections" },
  { id: "updates", label: "Updates" },
  ...(props.showRuntime ? [{ id: "runtime" as const, label: "Runtime" }] : []),
  { id: "metadata", label: "Metadata" },
  { id: "maintenance", label: "Maintenance" },
]);

const setTrigger = (id: MenuId, el: unknown) => {
  if (el instanceof HTMLElement) triggers.set(id, el);
  else triggers.delete(id);
};

/** Closes an open menu; returns whether one was open (for controller Back). */
const closeMenu = (restoreFocus = true): boolean => {
  const id = openMenu.value;
  if (!id) return false;
  openMenu.value = null;
  subView.value = "";
  if (restoreFocus) triggers.get(id)?.focus();
  return true;
};

const focusFirstItem = async () => {
  await nextTick();
  root.value?.querySelector<HTMLElement>("[role='menu'] [role='menuitem']:not(:disabled), [role='menu'] input, [role='menu'] select")?.focus();
};

// Fixed placement above the trigger, clamped into the window, so the scrolling
// library area and a narrow window never clip the panel.
const placePanel = async (id: MenuId) => {
  const rect = triggers.get(id)?.getBoundingClientRect();
  if (!rect) return;
  panelStyle.value = { left: `${rect.left}px`, bottom: `${window.innerHeight - rect.top + 6}px`, visibility: "hidden" };
  await nextTick();
  const width = root.value?.querySelector<HTMLElement>("[role='menu']")?.getBoundingClientRect().width ?? 0;
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
  panelStyle.value = { left: `${left}px`, bottom: `${window.innerHeight - rect.top + 6}px` };
};

const toggleMenu = (id: MenuId) => {
  if (openMenu.value === id) { closeMenu(); return; }
  openMenu.value = id;
  subView.value = "";
  void placePanel(id).then(focusFirstItem);
};

const enterSubView = (view: SubView) => {
  subView.value = view;
  if (view === "remove-tag") removeTagChoice.value = removableTags.value[0] || "";
  void focusFirstItem();
};

const run = (action: string, value?: string) => {
  closeMenu(false);
  emit("action", action, value);
};

const onMenuKeydown = (event: KeyboardEvent) => {
  if (event.key === "Escape") { event.stopPropagation(); closeMenu(); return; }
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  const items = [...(root.value?.querySelectorAll<HTMLElement>("[role='menu'] [role='menuitem']:not(:disabled)") ?? [])];
  if (!items.length) return;
  event.preventDefault();
  const index = items.indexOf(document.activeElement as HTMLElement);
  const next = event.key === "ArrowDown" ? index + 1 : index - 1;
  items[(next + items.length) % items.length]?.focus();
};

const onOutside = (event: MouseEvent) => {
  if (openMenu.value && !root.value?.contains(event.target as Node)) closeMenu(false);
};
const onResize = () => { closeMenu(false); };
onMounted(() => { document.addEventListener("mousedown", onOutside); window.addEventListener("resize", onResize); });
onUnmounted(() => { document.removeEventListener("mousedown", onOutside); window.removeEventListener("resize", onResize); });

defineExpose({ closeMenu, focusFirst: () => root.value?.querySelector<HTMLElement>("button, select")?.focus() });

// Reasons double as disabled tooltips so each action says why it is unavailable.
const statusSetNothing = (value: string) => props.games.every((game) => normalizePlayStatus(game.play_status, game.status) === value);
const allFavorite = computed(() => props.games.every((game) => game.is_favorite));
const noneFavorite = computed(() => props.games.every((game) => !game.is_favorite));
const hasCollection = (game: GameRecord, name: string) => (game.collections || []).some((entry) => entry.toLowerCase() === name.toLowerCase());
const collectionAddDisabled = computed(() => !collectionChoice.value || props.games.every((game) => hasCollection(game, collectionChoice.value)));
const collectionRemoveDisabled = computed(() => !collectionChoice.value || props.games.every((game) => !hasCollection(game, collectionChoice.value)));

const removableTags = computed(() => {
  const seen = new Map<string, string>();
  props.games.forEach((game) => gameTags(game).forEach((tag) => { if (!seen.has(tag.toLowerCase())) seen.set(tag.toLowerCase(), tag); }));
  return [...seen.values()].sort((a, b) => a.localeCompare(b));
});
const addTagReason = computed(() => {
  if (!tagInput.value.trim()) return "Type a tag to add";
  return props.games.some((game) => addTagCsv(game, tagInput.value) !== null) ? "" : "Every selected game already has this tag";
});
const removeTagReason = computed(() => {
  if (!removableTags.value.length) return "No selected game has tags";
  if (!removeTagChoice.value) return "Choose a tag to remove";
  return props.games.some((game) => removeTagCsv(game, removeTagChoice.value) !== null) ? "" : "No selected game has that tag";
});
const engineReason = computed(() => {
  const engine = engineInput.value;
  if (!engine) return "Choose an engine";
  return props.games.every((game) => isEngine(game.engine, engine)) ? "Every selected game already uses this engine" : "";
});

const launchModes = [
  { value: "auto", label: "Auto Detect" },
  { value: "native", label: "Linux Native" },
  { value: "wine_proton", label: "Wine/Proton" },
];
const launchModeDisabled = (mode: string) => !props.games.some((game) => launchModeApplicable(game, mode, props.runningIds));

const sandboxOnCount = computed(() => props.games.filter((game) => !game.sandbox_enabled).length);
const sandboxOffCount = computed(() => props.games.filter((game) => game.sandbox_enabled).length);
const savesDisabledReason = computed(() => (props.savesBusy ? "A save backup is running" : ""));
const infoReason = computed(() => {
  if (!props.eligibleChecks) return "Selected games have no F95 thread";
  if (props.checking) return "An update check is running";
  return "";
});
</script>

<template>
  <div ref="root" class="bulk-bar flex flex-wrap items-center gap-2 p-3 rounded-xl" role="region" aria-label="Bulk actions" @keydown="onMenuKeydown">
    <strong class="bulk-count">{{ games.length }} selected</strong>
    <button class="ui-action-btn bulk-btn px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface" @click="emit('select-all')">Select all</button>
    <button class="ui-action-btn bulk-btn px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface" @click="emit('done')">Done</button>

    <div v-for="menu in menus" :key="menu.id" class="bulk-menu">
      <button
        :ref="(el) => setTrigger(menu.id, el)"
        class="ui-action-btn bulk-btn px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface"
        aria-haspopup="menu"
        :aria-expanded="openMenu === menu.id"
        :data-bulk-menu="menu.id"
        @click="toggleMenu(menu.id)"
      ><span>{{ menu.label }}</span><IconChevronDown class="ui-action-icon" aria-hidden="true" /></button>

      <div v-if="openMenu === menu.id" class="bulk-menu-panel" :style="panelStyle" role="menu" :aria-label="`${menu.label} actions`">
        <!-- Status -->
        <template v-if="menu.id === 'status'">
          <button v-for="item in statuses" :key="item.value" role="menuitem" :disabled="busy || statusSetNothing(item.value)" @click="run('status', item.value)">Set status: {{ item.label }}</button>
          <hr />
          <button role="menuitem" :disabled="busy || allFavorite" :title="allFavorite ? 'All selected games are already favorites' : ''" @click="run('favorite')">Favorite</button>
          <button role="menuitem" :disabled="busy || noneFavorite" :title="noneFavorite ? 'No selected games are favorites' : ''" @click="run('unfavorite')">Unfavorite</button>
        </template>

        <!-- Collections -->
        <template v-else-if="menu.id === 'collections'">
          <label class="bulk-field">Collection
            <select v-model="collectionChoice" aria-label="Collection">
              <option value="">Choose collection</option>
              <option v-for="name in collections" :key="name">{{ name }}</option>
            </select>
          </label>
          <button role="menuitem" :disabled="busy || collectionAddDisabled" title="Choose a collection that is missing from at least one selected game" @click="run('add', collectionChoice)">Add to collection</button>
          <button role="menuitem" :disabled="busy || collectionRemoveDisabled" title="Choose a collection present on at least one selected game" @click="run('remove-collection', collectionChoice)">Remove from collection</button>
        </template>

        <!-- Updates -->
        <template v-else-if="menu.id === 'updates'">
          <button role="menuitem" :disabled="busy || checking || !eligibleChecks" :title="!eligibleChecks ? 'Selected games have no F95 thread' : checking ? 'An update check is running' : 'Check selected games'" @click="run('check')">Check for updates</button>
          <button role="menuitem" :disabled="busy || !eligibleUpdates" title="Requires an available update" @click="run('updated')">Mark as updated</button>
          <button role="menuitem" :disabled="busy || !eligibleUpdates" title="Requires an available update" @click="run('skip')">Skip version</button>
        </template>

        <!-- Runtime (Linux only) -->
        <template v-else-if="menu.id === 'runtime'">
          <template v-if="subView === 'launch-mode'">
            <button role="menuitem" class="bulk-back" @click="subView = ''"><IconChevronLeft class="ui-action-icon" aria-hidden="true" />Back</button>
            <button v-for="mode in launchModes" :key="mode.value" role="menuitem" :disabled="busy || launchModeDisabled(mode.value)" :title="launchModeDisabled(mode.value) ? 'Selected games are already in this mode or running' : ''" @click="run('launch-mode', mode.value)">{{ mode.label }}</button>
          </template>
          <template v-else>
            <button role="menuitem" :disabled="busy" @click="closeMenu(false); emit('wine')">Set Wine/Proton…</button>
            <button role="menuitem" :disabled="busy" @click="enterSubView('launch-mode')"><span>Set launch mode…</span><IconChevronRight class="ui-action-icon" aria-hidden="true" /></button>
            <button role="menuitem" :disabled="busy || !sandboxOnCount" :title="!sandboxOnCount ? 'All selected games are already sandboxed' : ''" @click="run('sandbox-on')">Sandbox on</button>
            <button role="menuitem" :disabled="busy || !sandboxOffCount" :title="!sandboxOffCount ? 'No selected game is sandboxed' : ''" @click="run('sandbox-off')">Sandbox off</button>
          </template>
        </template>

        <!-- Metadata -->
        <template v-else-if="menu.id === 'metadata'">
          <template v-if="subView === 'add-tag'">
            <button role="menuitem" class="bulk-back" @click="subView = ''"><IconChevronLeft class="ui-action-icon" aria-hidden="true" />Back</button>
            <label class="bulk-field">Tag
              <input v-model="tagInput" list="bulk-tag-suggestions" type="text" aria-label="Tag to add" @keydown.enter.prevent="!addTagReason && run('add-tag', tagInput)" />
            </label>
            <datalist id="bulk-tag-suggestions"><option v-for="tag in tagSuggestions" :key="tag" :value="tag" /></datalist>
            <button role="menuitem" class="bulk-submit" :disabled="busy || !!addTagReason" :title="addTagReason" @click="run('add-tag', tagInput)">Add tag</button>
          </template>
          <template v-else-if="subView === 'remove-tag'">
            <button role="menuitem" class="bulk-back" @click="subView = ''"><IconChevronLeft class="ui-action-icon" aria-hidden="true" />Back</button>
            <label class="bulk-field">Tag
              <select v-model="removeTagChoice" aria-label="Tag to remove">
                <option v-for="tag in removableTags" :key="tag" :value="tag">{{ tag }}</option>
              </select>
            </label>
            <button role="menuitem" class="bulk-submit" :disabled="busy || !!removeTagReason" :title="removeTagReason" @click="run('remove-tag', removeTagChoice)">Remove tag</button>
          </template>
          <template v-else-if="subView === 'engine'">
            <button role="menuitem" class="bulk-back" @click="subView = ''"><IconChevronLeft class="ui-action-icon" aria-hidden="true" />Back</button>
            <label class="bulk-field">Engine
              <select v-model="engineInput" aria-label="Engine">
                <option value="">Choose an engine</option>
                <option v-for="engine in ENGINE_OPTIONS" :key="engine" :value="engine">{{ engine }}</option>
              </select>
            </label>
            <button role="menuitem" class="bulk-submit" :disabled="busy || !!engineReason" :title="engineReason" @click="run('engine', engineInput)">Set engine</button>
          </template>
          <template v-else>
            <button role="menuitem" :disabled="busy" @click="enterSubView('add-tag')"><span>Add tag…</span><IconChevronRight class="ui-action-icon" aria-hidden="true" /></button>
            <button role="menuitem" :disabled="busy || !removableTags.length" :title="!removableTags.length ? 'No selected game has tags' : ''" @click="enterSubView('remove-tag')"><span>Remove tag…</span><IconChevronRight class="ui-action-icon" aria-hidden="true" /></button>
            <button role="menuitem" :disabled="busy" @click="enterSubView('engine')"><span>Set engine…</span><IconChevronRight class="ui-action-icon" aria-hidden="true" /></button>
          </template>
        </template>

        <!-- Maintenance -->
        <template v-else-if="menu.id === 'maintenance'">
          <button role="menuitem" :disabled="busy || savesBusy" :title="savesDisabledReason || 'Back up the saves of each selected game that has a saves folder'" @click="run('backup-saves')">Back up saves</button>
          <button role="menuitem" :disabled="busy || !!infoReason" :title="infoReason || 'Reads each selected game’s F95 thread, one paced request per game, and refreshes its cover, rating and F95 tags. Your own tag changes stay.'" @click="run('refresh-info')">Refresh F95 info</button>
        </template>
      </div>
    </div>

    <button class="ui-action-btn bulk-btn px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface bulk-danger" :disabled="busy" @click="emit('action', 'remove')">Remove</button>
  </div>
</template>

<style scoped>
.bulk-bar { position: sticky; bottom: 0; z-index: 40; background: var(--bg-surface); border: 1px solid var(--border-hover); box-shadow: var(--shadow-card); }
.bulk-count { margin-right: 0.25rem; color: var(--text-primary); }
.bulk-btn { color: var(--text-primary); border: 1px solid var(--border); }
.bulk-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.bulk-btn[aria-expanded="true"] { background: var(--bg-raised); border-color: var(--border-hover); }
.bulk-danger { color: var(--danger-text); border-color: var(--danger-border); }
.bulk-menu { position: relative; }
.bulk-menu-panel {
  position: fixed; z-index: 100;
  width: 15rem; max-width: calc(100vw - 16px); max-height: min(60vh, calc(100vh - 16px)); overflow-y: auto;
  display: flex; flex-direction: column; gap: 2px; padding: 0.4rem;
  background: var(--bg-surface); color: var(--text-primary);
  border: 1px solid var(--border-hover); border-radius: 0.5rem; box-shadow: var(--shadow-card);
}
.bulk-menu-panel [role="menuitem"] {
  display: flex; align-items: center; justify-content: space-between; gap: 0.5rem;
  width: 100%; padding: 0.45rem 0.5rem; border-radius: 0.3rem; text-align: left; font-size: 0.8rem;
  background: transparent; color: inherit;
}
.bulk-menu-panel [role="menuitem"]:hover:not(:disabled) { background: var(--bg-raised); }
.bulk-menu-panel [role="menuitem"]:focus-visible, .bulk-btn:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.bulk-menu-panel [role="menuitem"]:disabled { opacity: 0.5; cursor: not-allowed; }
.bulk-menu-panel .bulk-back { justify-content: flex-start; color: var(--text-secondary); }
.bulk-menu-panel .bulk-submit { justify-content: center; margin-top: 0.25rem; background: var(--brand); color: var(--on-brand); font-weight: 700; }
.bulk-menu-panel .bulk-submit:hover:not(:disabled) { background: var(--brand); filter: brightness(1.08); }
.bulk-menu-panel hr { border: 0; border-top: 1px solid var(--border); margin: 4px 0; }
.bulk-field { display: flex; flex-direction: column; gap: 4px; font-size: 0.75rem; color: var(--text-muted); padding: 0.25rem 0.5rem; }
.bulk-field input, .bulk-field select {
  width: 100%; background: var(--bg-inset); border: 1px solid var(--border); border-radius: 0.3rem;
  padding: 0.4rem 0.5rem; color: var(--text-primary); font-size: 0.8rem;
}
.bulk-field input:focus-visible, .bulk-field select:focus-visible { outline: 2px solid var(--brand); outline-offset: 1px; }
</style>
