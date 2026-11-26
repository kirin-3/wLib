<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref } from "vue";
import { IconFolder, IconFolderOpen, IconFolderX, IconStack2, IconTrash } from "@tabler/icons-vue";
import type { StorageGameRow } from "../../services/api";
import MenuButton, { type MenuButtonItem } from "../MenuButton.vue";
import PathText from "../PathText.vue";
import {
  formatStorageBytes,
  gameRowFlags,
  gameRowHint,
  gameRowParts,
  sortedStorageGames,
  splitLargestGames,
} from "../../utils/storage";

const props = defineProps<{
  games: Record<string, StorageGameRow>;
  busy?: boolean;
}>();

const emit = defineEmits<{
  (event: "open-folder", game: StorageGameRow): void;
  (event: "uninstall", game: StorageGameRow): void;
  (event: "pick-folder", game: StorageGameRow): void;
  (event: "clear-folder", game: StorageGameRow): void;
}>();

const TOP_GAMES = 10;
const BUSY_REASON = "Wait for the scan or cleanup to finish.";
const NO_EXE_REASON = "This game has no executable set.";

const orderedGames = computed(() => sortedStorageGames(props.games));
const split = computed(() => splitLargestGames(orderedGames.value, TOP_GAMES));
const showAll = ref(false);
// When no game has a measured size there is no "largest" list: show them all.
const canFold = computed(() => split.value.top.length > 0 && split.value.rest.length > 0);
const visibleGames = computed(() =>
  showAll.value || !canFold.value ? orderedGames.value : split.value.top,
);

type GameAction = "open-folder" | "pick-folder" | "clear-folder" | "uninstall";

/**
 * The one action a row shows as its button. A row that was not counted right
 * needs a folder picked; every other row opens its folder.
 */
function mainAction(row: StorageGameRow): "open-folder" | "pick-folder" {
  return row.unsafe || row.uncertain ? "pick-folder" : "open-folder";
}

function mainDisabledReason(row: StorageGameRow): string {
  if (mainAction(row) === "pick-folder") {
    if (props.busy) return BUSY_REASON;
    if (!row.exe_path) return NO_EXE_REASON;
    return "";
  }
  return !row.root && !row.exe_path ? "wLib does not know a folder for this game." : "";
}

/** The other actions, in the row's menu. Uninstall stays last. */
function menuItems(row: StorageGameRow): MenuButtonItem[] {
  const items: MenuButtonItem[] = [];
  if (mainAction(row) !== "open-folder") {
    const noFolder = !row.root && !row.exe_path;
    items.push({
      id: "open-folder",
      label: "Open folder",
      icon: IconFolderOpen,
      disabled: noFolder,
      disabledReason: noFolder ? "wLib does not know a folder for this game." : "",
    });
  }
  if (mainAction(row) !== "pick-folder") {
    items.push({
      id: "pick-folder",
      label: "Pick folder",
      icon: IconFolder,
      disabled: Boolean(props.busy) || !row.exe_path,
      disabledReason: props.busy ? BUSY_REASON : !row.exe_path ? NO_EXE_REASON : "",
    });
  }
  if (row.picked) {
    items.push({
      id: "clear-folder",
      label: "Reset folder",
      icon: IconFolderX,
      disabled: Boolean(props.busy),
      disabledReason: props.busy ? BUSY_REASON : "",
    });
  }
  items.push({
    id: "uninstall",
    label: "Uninstall...",
    icon: IconTrash,
    disabled: !row.exe_path,
    disabledReason: !row.exe_path ? NO_EXE_REASON : "",
  });
  return items;
}

function run(action: GameAction, row: StorageGameRow) {
  switch (action) {
    case "open-folder":
      emit("open-folder", row);
      break;
    case "pick-folder":
      emit("pick-folder", row);
      break;
    case "clear-folder":
      emit("clear-folder", row);
      break;
    case "uninstall":
      emit("uninstall", row);
      break;
  }
}

function isGameAction(id: string): id is GameAction {
  return id === "open-folder" || id === "pick-folder" || id === "clear-folder" || id === "uninstall";
}

function onMenuSelect(row: StorageGameRow, id: string) {
  if (isGameAction(id)) run(id, row);
}
</script>

<template>
  <section class="storage-card rounded-xl" aria-labelledby="storage-games-heading">
    <div class="flex flex-wrap items-baseline justify-between gap-2 mb-4">
      <h3 id="storage-games-heading" class="ui-section-heading text-base font-semibold" tabindex="-1">
        <IconStack2 class="ui-section-icon" />
        <span>Largest games</span>
      </h3>
      <span class="text-xs" style="color: var(--text-muted)">
        {{ orderedGames.length }} {{ orderedGames.length === 1 ? "game" : "games" }}
      </span>
    </div>

    <p v-if="!orderedGames.length" class="text-sm" style="color: var(--text-muted)">
      No games with measured data were found in this report.
    </p>

    <ul v-else class="space-y-2.5" aria-label="Games by disk usage">
      <li
        v-for="row in visibleGames"
        :key="row.game_id"
        class="game-row rounded-xl"
        :data-game-id="row.game_id"
      >
        <div class="game-row-body">
          <div class="game-row-main min-w-0 space-y-1.5">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-semibold text-sm truncate max-w-full" style="color: var(--text-primary)" :title="row.title">
                {{ row.title }}
              </span>
              <span
                v-for="flag in gameRowFlags(row)"
                :key="flag.kind"
                class="storage-flag text-xs font-medium px-2 py-0.5 rounded-full"
                :class="`storage-flag--${flag.tone}`"
                :data-flag="flag.kind"
              >
                {{ flag.label }}
              </span>
              <span
                v-if="row.picked"
                class="storage-flag storage-flag--info text-xs font-medium px-2 py-0.5 rounded-full"
              >
                folder you picked
              </span>
            </div>

            <div v-if="row.root" class="text-xs font-mono" style="color: var(--text-muted)">
              <PathText :path="row.root" />
            </div>
            <p
              v-if="gameRowHint(row)"
              class="text-xs"
              :style="{ color: row.unsafe ? 'var(--danger-text)' : 'var(--text-secondary)' }"
              data-game-hint
            >
              {{ gameRowHint(row) }}
            </p>

            <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" style="color: var(--text-secondary)">
              <span
                v-for="part in gameRowParts(row)"
                :key="part.key"
                class="tabular-nums"
              >
                {{ part.label }}: <strong style="color: var(--text-primary)">{{ formatStorageBytes(part.bytes) }}</strong>
              </span>
              <span v-if="row.torrent_bytes > 0" class="tabular-nums torrent-note">
                Torrent data: <strong>{{ formatStorageBytes(row.torrent_bytes) }}</strong>
                (not in the game total; wLib deletes it once the game is installed and seeding has stopped)
              </span>
            </div>
          </div>

          <div class="game-row-side">
            <span class="text-base font-bold tabular-nums" style="color: var(--text-primary)">
              {{ formatStorageBytes(row.total_bytes) }}
            </span>

            <MenuButton
              split
              align="end"
              :items="menuItems(row)"
              :menu-label="`More actions for ${row.title}`"
              :button-label="`More actions for ${row.title}`"
              :main-label="`${mainAction(row) === 'pick-folder' ? 'Pick folder' : 'Open folder'} for ${row.title}`"
              :main-disabled="Boolean(mainDisabledReason(row))"
              :main-disabled-reason="mainDisabledReason(row) || undefined"
              :main-title="mainDisabledReason(row) || undefined"
              :data-game-actions="row.game_id"
              @main="run(mainAction(row), row)"
              @select="(id: string) => onMenuSelect(row, id)"
            >
              <template #main>
                <IconFolder v-if="mainAction(row) === 'pick-folder'" class="ui-action-icon" aria-hidden="true" />
                <IconFolderOpen v-else class="ui-action-icon" aria-hidden="true" />
                {{ mainAction(row) === "pick-folder" ? "Pick folder" : "Open folder" }}
              </template>
            </MenuButton>
          </div>
        </div>
      </li>
    </ul>

    <button
      v-if="canFold"
      type="button"
      class="storage-show-all mt-2 text-sm font-semibold"
      :aria-expanded="showAll"
      data-games-show-all
      @click="showAll = !showAll"
    >
      <template v-if="showAll">Show the largest {{ split.top.length }}</template>
      <template v-else>
        Show all ({{ orderedGames.length }})<span
          v-if="split.restNotCounted"
          style="color: var(--text-muted); font-weight: 500"
        > · {{ split.restNotCounted }} not counted</span>
      </template>
    </button>
  </section>
</template>

<style scoped>
.storage-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  padding: 1.5rem;
}
.game-row {
  background: var(--bg-inset);
  border: 1px solid var(--border);
  padding: 1rem;
}
.game-row-body {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}
.game-row-main {
  flex: 1 1 auto;
}
/* Size above its actions, at the row's right edge. */
.game-row-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.5rem;
  flex-shrink: 0;
}
/* Narrow room (container set by StorageView): the details take the full width
   and the size and actions become one line under them, size left, actions
   right, instead of a column that squeezes the text. */
@container storage-page (max-width: 36rem) {
  .game-row-body {
    flex-direction: column;
    align-items: stretch;
  }
  .game-row-side {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
  }
  /* A wide size next to "Open folder" can push the actions to a second line
     at phone width: they keep to the right edge there too. */
  .game-row-side > :last-child {
    margin-left: auto;
  }
}
@container storage-page (max-width: 30rem) {
  .storage-card {
    padding: 1rem;
  }
  .game-row {
    padding: 0.75rem;
  }
}
.storage-flag--warn {
  background: var(--warning-bg);
  color: var(--warning-text);
  border: 1px solid var(--warning-border);
}
.storage-flag--danger {
  background: var(--danger-bg);
  color: var(--danger-text);
  border: 1px solid var(--danger-border);
}
.storage-flag--info {
  background: var(--bg-overlay);
  color: var(--text-secondary);
  border: 1px solid var(--border);
}
/* A sentence on the inset row: secondary text (muted is only 4.56:1 on the light inset). */
.torrent-note {
  color: var(--text-secondary);
}
/* A text link that is still a 2rem touch target (Steam Deck). */
.storage-show-all {
  display: inline-block;
  padding-block: 0.375rem;
  color: var(--brand);
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-thickness: 1px;
  border-radius: 0.25rem;
}
.storage-show-all:hover {
  text-decoration-thickness: 2px;
}
#storage-games-heading:focus-visible {
  outline: 2px solid var(--brand-glow);
  outline-offset: 4px;
  border-radius: 0.25rem;
}
.storage-show-all:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--brand-glow);
}
</style>
