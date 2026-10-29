<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { gameCoverSrc, gameCoverError } from "../utils/gameCover";
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from "vue";
import { toCssPx } from "../utils/interfaceScalePreference";
import TagColorPicker from "../components/TagColorPicker.vue";
import { colorForTag, loadTagColors } from "../utils/tagColors";
import { useRoute, useRouter } from "vue-router";
import {
  IconArchive,
  IconCheck,
  IconChevronDown,
  IconArrowLeft,
  IconArrowUp,
  IconClock,
  IconColumns2Filled,
  IconDeviceGamepad2,
  IconDeviceGamepad2Filled,
  IconFilterFilled,
  IconLibraryPlus,
  IconLayoutGridFilled,
  IconPanoramaHorizontalFilled,
  IconLayoutListFilled,
  IconFolderPlus,
  IconLoader2,
  IconPhotoFilled,
  IconPlayerStopFilled,
  IconPlayerPlayFilled,
  IconPlus,
  IconRefresh,
  IconSparkles,
  IconZoom,
  IconStarFilled,
  IconX,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import type { CompanionTool, GameRecord, LaunchMode, LaunchTarget, StoreLink } from "../services/api";
import AddGameModal from "../components/modals/AddGameModal.vue";
import ImportFolderModal from "../components/modals/ImportFolderModal.vue";
import GameDetailModal from "../components/modals/GameDetailModal.vue";
import {
  getPlayStatusOptions,
  getPlayStatusMeta,
  normalizePlayStatus,
} from "../utils/playStatus";
import {
  DEFAULT_FILTER_SECTIONS,
  DEFAULT_LIBRARY_VIEW_STATE,
  hasStatusFilter,
  normalizeActiveCollection,
  normalizeFilterAndSort,
  normalizeFilterPreset,
  normalizeLibraryViewState,
  readPresetsForSave,
  restoreSavedLibraryViewState,
  saveLibraryViewState,
  toggleStatusFilter,
  type FilterCollection,
  type FilterInstallState,
  type FilterSections,
  type LayoutMode,
  type CardSize,
  type LibraryFilterPreset,
  type LibraryViewState,
  type SortDir,
  type SortField,
} from "../utils/libraryViewState";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { notify, notifyError } from "../utils/toast";
import { sandboxLaunchError } from "../utils/sandbox";
import { openInstallArchive } from "../utils/installArchive";
import {
  buildTagFilterOptions,
  compareLibraryGames,
  gameFolder,
  hasAvailableUpdate,
  hasTagFilter,
  hasUnplayedUpdate,
  isUpdateSkipped,
  hasNewSinceFinished,
  isWishlisted,
  matchesGameSearch,
  matchesTagFilter,
  toggleTagFilter,
} from "../utils/libraryGames";

interface UpdateNotice {
  type: "" | "success" | "error";
  message: string;
}

interface ModalUpdateState extends UpdateNotice {
  running: boolean;
}

import { extensionGameId, readStoreLinks, gamePageUrl } from "../utils/storeLinks";

interface AddGamePayload {
  links?: StoreLink[];
  title: string;
  exe_path: string;
  f95_url?: string;
  version?: string;
  cover_image?: string;
  tags?: string;
  rating?: string;
  developer?: string;
  engine?: string;
  launch_mode?: LaunchMode;
  command_line_args?: string;
}

interface PlaytimeTickDetail {
  gameId?: number;
  delta?: number;
  isFinal?: boolean;
  lastPlayedVersion?: string;
  sessionId?: number;
}

interface EffectiveLaunchTarget {
  id: string;
  label: string;
  exe_path: string;
  isDefault: boolean;
}

const readQueryValue = (value: unknown): string => {
  if (Array.isArray(value)) {
    return typeof value[0] === "string" ? value[0] : "";
  }
  return typeof value === "string" ? value : "";
};

const route = useRoute();
const router = useRouter();
const games = ref<GameRecord[]>([]);
const runningGameIds = ref(new Set<number>());
const customStatuses = ref<string[]>([]);
// True once Settings has answered, so the custom status list is authoritative.
const customStatusesLoaded = ref(false);
// User-defined collections shown as tabs; activeCollection "" means "All".
const userCollections = ref<string[]>([]);
const activeCollection = ref(DEFAULT_LIBRARY_VIEW_STATE.activeCollection);
const newCollectionName = ref("");
const showNewCollectionInline = ref(false);
const collectionError = ref("");
const renamingCollection = ref<string | null>(null);
const renameCollectionDraft = ref("");
const newCollectionInput = ref<HTMLInputElement | null>(null);
const collectionTabsElement = ref<HTMLElement | null>(null);
const collectionTabMenu = ref<{ name: string; x: number; y: number } | null>(null);
const collectionTabMenuElement = ref<HTMLElement | null>(null);
// Saved filter presets listed at the top of the filters pane.
const savedPresets = ref<LibraryFilterPreset[]>([]);
// True once Settings has answered, so a save can't treat "not loaded" as empty.
const presetsLoaded = ref(false);
const newPresetName = ref("");
const presetError = ref("");
const showAddModal = ref(false);
const showImportModal = ref(false);
const showDetailModal = ref(false);
const selectedGame = ref<GameRecord | null>(null);
const openLaunchTargetMenuId = ref<number | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const tagSearchQuery = ref("");
const markingUpdatedIds = ref(new Set<number>());
const contextMenu = ref<{ game: GameRecord; x: number; y: number } | null>(
  null,
);
const contextMenuElement = ref<HTMLElement | null>(null);
const contextTools = ref<CompanionTool[]>([]);
let contextToolsRequest = 0;
const loadingCompanionId = ref<number | null>(null);
const launchContextTool = async (tool: CompanionTool) => {
  const game = contextMenu.value?.game;
  if (!game || loadingCompanionId.value !== null) return;
  loadingCompanionId.value = tool.id;
  try {
    const result = await api.launchCompanionTool(game.id, tool.id);
    if (!result.success) notifyError(`Failed to launch ${tool.name}:\n\n${result.error || "Unknown error"}`);
    else closeContextMenu();
  } catch (cause) { notifyError(`Failed to launch ${tool.name}: ${String(cause)}`); }
  finally { loadingCompanionId.value = null; }
};
const loadContextTools = async (gameId: number) => {
  const request = ++contextToolsRequest;
  contextTools.value = [];
  if (!runningGameIds.value.has(gameId)) return;
  try {
    const capabilities = await loadPlatformCapabilities();
    if (!capabilities.companion_tools || request !== contextToolsRequest) return;
    const result = await api.listLaunchableCompanionTools(gameId);
    if (request !== contextToolsRequest || contextMenu.value?.game.id !== gameId || !runningGameIds.value.has(gameId)) return;
    contextTools.value = result.success ? result.tools || [] : [];
    await nextTick();
    if (request === contextToolsRequest && contextMenu.value && contextMenuElement.value) {
      contextMenu.value.y = Math.max(8, Math.min(contextMenu.value.y, window.innerHeight - contextMenuElement.value.getBoundingClientRect().height - 8));
    }
  } catch (cause) { console.error("Could not load custom tools", cause); }
};
// "actions" is the button list; "collections" switches the body to the
// membership checklist so no second popup needs positioning.
const contextMenuView = ref<"actions" | "collections">("actions");
const contextNewCollectionName = ref("");
const contextNewCollectionError = ref("");
let contextMenuTrigger: HTMLElement | null = null;

const tableColumns: Array<{ key: SortField; label: string; class?: string }> = [
  { key: "title", label: "Title" },
  { key: "version", label: "Version" },
  { key: "play_status", label: "Status" },
  { key: "engine", label: "Engine", class: "col-engine" },
  { key: "playtime_seconds", label: "Playtime" },
  { key: "last_played", label: "Last played", class: "col-last-played" },
];

const updateCount = computed(
  () => games.value.filter((game) => hasAvailableUpdate({ ...game, skipped_version: game.skipped_version })).length,
);
const collections: Array<{
  value: FilterCollection;
  label: string;
  icon: typeof IconClock;
}> = [
  { value: "All", label: "All Games", icon: IconLayoutGridFilled },
  { value: "Favorites", label: "Favorites", icon: IconStarFilled },
  { value: "Updates available", label: "Updates available", icon: IconArrowUp },
  { value: "New since finished", label: "New since finished", icon: IconSparkles },
  { value: "Unplayed update", label: "Unplayed update", icon: IconClock },
  { value: "Recently played", label: "Recently played", icon: IconClock },
];

// Search & Filter state
const searchQuery = ref("");
const filterStatuses = ref<string[]>([
  ...DEFAULT_LIBRARY_VIEW_STATE.filterStatuses,
]);
const filterCollection = ref<FilterCollection>(
  DEFAULT_LIBRARY_VIEW_STATE.filterCollection,
);
const filterEngines = ref<string[]>([
  ...DEFAULT_LIBRARY_VIEW_STATE.filterEngines,
]);
const filterTags = ref<string[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterTags]);
const tagColorPicker = ref<{ tag: string; anchor: HTMLElement } | null>(null);
function openTagColorPicker(event: MouseEvent | KeyboardEvent, tag: string) {
  event.preventDefault();
  tagColorPicker.value = { tag, anchor: event.currentTarget as HTMLElement };
}
const filterInstallState = ref<FilterInstallState>(DEFAULT_LIBRARY_VIEW_STATE.filterInstallState);
const cardSize = ref<CardSize>(DEFAULT_LIBRARY_VIEW_STATE.cardSize);
const cardScales = [0.75, 0.875, 1, 1.25, 1.5] as const;
const layoutMode = ref<LayoutMode>(DEFAULT_LIBRARY_VIEW_STATE.layoutMode);
// Compact and banner both draw image-only tiles (cover, overlay, bottom row).
const isImageTileLayout = computed(
  () => layoutMode.value === "compact" || layoutMode.value === "banner",
);
const sortBy = ref<SortField>(DEFAULT_LIBRARY_VIEW_STATE.sortBy);
const sortDir = ref<SortDir>(DEFAULT_LIBRARY_VIEW_STATE.sortDir);

const isFiltersCollapsed = ref(DEFAULT_LIBRARY_VIEW_STATE.isFiltersCollapsed);
const filterSections = ref<FilterSections>({ ...DEFAULT_FILTER_SECTIONS });

const normalizeF95Url = (rawUrl: unknown): string => {
  if (typeof rawUrl !== "string") return "";

  const trimmed = rawUrl.trim();
  if (!trimmed) return "";

  try {
    const parsed = new URL(trimmed);
    const match = parsed.pathname.match(
      /^\/threads\/(?:(.+)\.)?(\d+)(?:\/.*)?$/,
    );
    if (match) {
      const slug = (match[1] || "").replace(/^\.+|\.+$/g, "");
      parsed.pathname = slug
        ? `/threads/${slug}.${match[2]}/`
        : `/threads/${match[2]}/`;
    }
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  } catch (_error) {
    return trimmed;
  }
};

const extractThreadIdFromUrl = (rawUrl: unknown): string => {
  const normalized = normalizeF95Url(rawUrl);
  const match = normalized.match(/\/threads\/(?:.+\.)?(\d+)\/?$/);
  return match ? (match[1] ?? "") : "";
};

const urlsMatchByThreadIdentity = (left: unknown, right: unknown): boolean => {
  const leftThreadId = extractThreadIdFromUrl(left);
  const rightThreadId = extractThreadIdFromUrl(right);

  if (leftThreadId && rightThreadId) {
    return leftThreadId === rightThreadId;
  }

  return normalizeF95Url(left) === normalizeF95Url(right);
};

const toggleSort = (field: SortField) => {
  if (sortBy.value === field) {
    sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
  } else {
    sortBy.value = field;
    sortDir.value = ["title", "version", "play_status", "engine"].includes(
      field,
    )
      ? "asc"
      : "desc";
  }
};

const selectCollection = (collection: FilterCollection) => {
  filterCollection.value = collection;
  if (collection === "Recently played") {
    sortBy.value = "last_played";
    sortDir.value = "desc";
  }
};

const toggleFiltersPane = () => {
  isFiltersCollapsed.value = !isFiltersCollapsed.value;
};

const toggleFilterSection = (section: keyof FilterSections) => {
  filterSections.value[section] = !filterSections.value[section];
};

const allStatuses = computed(() =>
  getPlayStatusOptions([
    ...customStatuses.value,
    ...games.value.map((game) =>
      normalizePlayStatus(game.play_status, game.status),
    ),
  ]),
);

const sortOptions: Array<{ key: SortField; label: string }> = [
  { key: "title", label: "A-Z" },
  { key: "date_added", label: "Newest" },
  { key: "last_played", label: "Recent" },
  { key: "playtime_seconds", label: "Playtime" },
  { key: "rating", label: "F95 ★" },
  { key: "own_rating", label: "My ★" },
];

const toggleFilter = (arr: string[], val: string) => {
  const idx = arr.indexOf(val);
  if (idx === -1) arr.push(val);
  else arr.splice(idx, 1);
};

// Tags are case-insensitive, so the pill's spelling may differ from a saved
// filter. Toggle every spelling and keep the visible one when selecting.
const toggleTag = (tag: string) => {
  filterTags.value = toggleTagFilter(
    filterTags.value,
    tag,
    !hasTagFilter(filterTags.value, tag),
  );
};

// Status filters keep the spelling they were restored with, so the checkbox
// matches case-insensitively and toggling it clears every spelling of the name.
const statusFilterActive = (status: string): boolean =>
  hasStatusFilter(filterStatuses.value, status);

const applyStatusFilter = (status: string, event: Event): void => {
  filterStatuses.value = toggleStatusFilter(
    filterStatuses.value,
    status,
    (event.target as HTMLInputElement).checked,
  );
};

const normalizeEngineFilterValue = (value: unknown): string =>
  typeof value === "string" ? value.trim() : "";

// Derived unique values for filter pills
const uniqueEngines = computed(() => {
  const set = new Set<string>();
  games.value.forEach((g) => {
    const engine = normalizeEngineFilterValue(g.engine);
    if (engine) set.add(engine);
  });
  return [...set].sort();
});

const tagOptions = computed(() => buildTagFilterOptions(games.value));
const tagCounts = computed(
  () => new Map(tagOptions.value.map((option) => [option.label, option.count])),
);
const uniqueTags = computed(() => tagOptions.value.map((option) => option.label));
const visibleTags = computed(() =>
  uniqueTags.value.filter((tag) =>
    tag.toLowerCase().includes(tagSearchQuery.value.trim().toLowerCase()),
  ),
);

const activeFilterCount = computed(
  () =>
    (filterCollection.value !== "All" ? 1 : 0) +
    (filterStatuses.value.length > 0 ? 1 : 0) +
    (filterInstallState.value ? 1 : 0) +
    (filterEngines.value.length > 0 ? 1 : 0) +
    (filterTags.value.length > 0 ? 1 : 0),
);
const clearFilters = () => {
  searchQuery.value = "";
  filterStatuses.value = [];
  filterInstallState.value = "";
  filterEngines.value = [];
  filterTags.value = [];
  filterCollection.value = "All";
};

const applyLibraryViewState = (state: LibraryViewState) => {
  layoutMode.value = state.layoutMode;
  cardSize.value = state.cardSize;
  sortBy.value = state.sortBy;
  sortDir.value = state.sortDir;
  filterCollection.value = state.filterCollection;
  filterStatuses.value = [...state.filterStatuses];
  filterInstallState.value = state.filterInstallState;
  filterEngines.value = [...state.filterEngines];
  filterTags.value = [...state.filterTags];
  isFiltersCollapsed.value = state.isFiltersCollapsed;
  filterSections.value = { ...state.filterSections };
  activeCollection.value = state.activeCollection;
};

const buildLibraryViewState = (): LibraryViewState =>
  normalizeLibraryViewState({
    layoutMode: layoutMode.value,
    cardSize: cardSize.value,
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    filterCollection: filterCollection.value,
    filterStatuses: filterStatuses.value,
    filterInstallState: filterInstallState.value,
    filterEngines: filterEngines.value,
    filterTags: filterTags.value,
    isFiltersCollapsed: isFiltersCollapsed.value,
    filterSections: filterSections.value,
    activeCollection: activeCollection.value,
  });

const sameStringArray = (
  left: readonly string[],
  right: readonly string[],
): boolean =>
  left.length === right.length &&
  left.every((value, index) => value === right[index]);

const persistLibraryViewState = () => {
  saveLibraryViewState(localStorage, buildLibraryViewState());
};

const restoreLibraryViewState = () => {
  applyLibraryViewState(restoreSavedLibraryViewState(localStorage));
};

const filteredGames = computed(() => {
  let result = [...games.value];
  // The selected collection tab limits everything below it.
  if (activeCollection.value) {
    const activeKey = activeCollection.value.toLowerCase();
    result = result.filter((game) =>
      (game.collections || []).some(
        (name) => name.toLowerCase() === activeKey,
      ),
    );
  }
  if (searchQuery.value.trim())
    result = result.filter((game) =>
      matchesGameSearch(game, searchQuery.value),
    );
  if (filterCollection.value === "Favorites") {
    result = result.filter((g) => g.is_favorite);
  }
  if (filterCollection.value === "Updates available")
    result = result.filter((game) => hasAvailableUpdate({ ...game, skipped_version: game.skipped_version }));
  if (filterCollection.value === "Unplayed update")
    result = result.filter(hasUnplayedUpdate);
  if (filterCollection.value === "New since finished")
    result = result.filter(hasNewSinceFinished);
  if (filterCollection.value === "Recently played") {
    result = result.filter((game) => !!game.last_played);
  }
  if (filterStatuses.value.length) {
    result = result.filter((g) => {
      return filterStatuses.value.some(
        (status) =>
          status.toLowerCase() ===
          normalizePlayStatus(g.play_status, g.status).toLowerCase(),
      );
    });
  }
  if (filterInstallState.value === "wishlist")
    result = result.filter(isWishlisted);
  if (filterInstallState.value === "installed")
    result = result.filter((game) => !isWishlisted(game));
  if (filterEngines.value.length) {
    result = result.filter((g) =>
      filterEngines.value.includes(normalizeEngineFilterValue(g.engine)),
    );
  }
  if (filterTags.value.length) {
    result = result.filter((game) => matchesTagFilter(game, filterTags.value));
  }
  result.sort((a, b) => compareLibraryGames(a, b, sortBy.value, sortDir.value));
  return result;
});

// Tab counts show each collection's size and ignore the active filters.
const collectionCounts = computed(() => {
  const valid = new Set(userCollections.value.map((name) => name.toLowerCase()));
  const counts = new Map<string, number>();
  games.value.forEach((game) =>
    (game.collections || []).forEach((name) => {
      const key = name.toLowerCase();
      if (!valid.has(key)) return;
      counts.set(key, (counts.get(key) || 0) + 1);
    }),
  );
  return counts;
});

const collectionCount = (name: string): number =>
  collectionCounts.value.get(name.toLowerCase()) || 0;

const applyCollectionsFromPayload = (collections: string[] | undefined) => {
  if (Array.isArray(collections)) userCollections.value = [...collections];
};

const startInlineCollectionCreate = async () => {
  collectionError.value = "";
  showNewCollectionInline.value = true;
  await nextTick();
  newCollectionInput.value?.focus();
};

const cancelInlineCollectionCreate = () => {
  showNewCollectionInline.value = false;
  newCollectionName.value = "";
  collectionError.value = "";
};

const createCollectionFromTab = async () => {
  const name = newCollectionName.value.trim();
  if (!name) {
    collectionError.value = "Enter a collection name.";
    return;
  }
  const result = await api.createCollection(name);
  if (!result.success) {
    collectionError.value = result.error || "Could not create the collection.";
    return;
  }
  applyCollectionsFromPayload(result.collections);
  cancelInlineCollectionCreate();
};

let collectionTabMenuTrigger: HTMLElement | null = null;
const openCollectionTabMenu = async (name: string, event: MouseEvent | KeyboardEvent) => {
  event.preventDefault();
  collectionTabMenuTrigger =
    (event.target as HTMLElement)?.closest?.("button") ||
    (event.currentTarget as HTMLElement) ||
    null;
  const rect = collectionTabMenuTrigger?.getBoundingClientRect?.();
  const rawX = event instanceof MouseEvent && event.clientX ? event.clientX : rect ? rect.left : 8;
  const rawY = event instanceof MouseEvent && event.clientY ? event.clientY : rect ? rect.bottom : 8;
  collectionTabMenu.value = { name, x: Math.max(8, rawX), y: Math.max(8, rawY) };
  await nextTick();
  // The menu is 15rem wide: clamp against its measured size so Rename and
  // Delete stay inside the window next to a tab near the right edge.
  const menu = collectionTabMenuElement.value;
  if (collectionTabMenu.value && menu) {
    collectionTabMenu.value = {
      ...collectionTabMenu.value,
      x: Math.max(
        8,
        Math.min(collectionTabMenu.value.x, window.innerWidth - menu.getBoundingClientRect().width - 8),
      ),
      y: Math.max(
        8,
        Math.min(collectionTabMenu.value.y, window.innerHeight - menu.getBoundingClientRect().height - 8),
      ),
    };
  }
  menu?.querySelector<HTMLButtonElement>("button")?.focus();
};

const closeCollectionTabMenu = (restoreFocus = true) => {
  if (!collectionTabMenu.value) return;
  collectionTabMenu.value = null;
  if (restoreFocus) collectionTabMenuTrigger?.focus();
};

const startCollectionRename = async (name: string) => {
  closeCollectionTabMenu(false);
  renamingCollection.value = name;
  renameCollectionDraft.value = name;
  await nextTick();
  const input = collectionTabsElement.value?.querySelector<HTMLInputElement>(
    "[data-rename-input]",
  );
  input?.focus();
  input?.select();
};

const cancelCollectionRename = () => {
  renamingCollection.value = null;
  renameCollectionDraft.value = "";
  collectionError.value = "";
};

const commitCollectionRename = async () => {
  const oldName = renamingCollection.value;
  const newName = renameCollectionDraft.value.trim();
  if (!oldName) return;
  if (!newName || newName === oldName) {
    cancelCollectionRename();
    return;
  }
  const result = await api.renameCollection(oldName, newName);
  if (!result.success) {
    collectionError.value = result.error || "Could not rename the collection.";
    return;
  }
  applyCollectionsFromPayload(result.collections);
  if (activeCollection.value.toLowerCase() === oldName.toLowerCase()) {
    activeCollection.value = newName;
  }
  cancelCollectionRename();
};

const deleteCollectionFromTab = async (name: string) => {
  closeCollectionTabMenu(false);
  const count = collectionCount(name);
  const membershipNote = count
    ? ` Its ${count} game${count === 1 ? "" : "s"} stay in your library.`
    : " Games stay in your library.";
  if (!confirm(`Delete the collection "${name}"?${membershipNote}`)) return;
  const result = await api.deleteCollection(name);
  if (!result.success) {
    notifyError(result.error || "Could not delete the collection.");
    return;
  }
  if (activeCollection.value.toLowerCase() === name.toLowerCase()) {
    activeCollection.value = "";
  }
  applyCollectionsFromPayload(result.collections);
};

// Arrow keys move focus and selection across the tab strip.
const handleCollectionTabsKeyboard = (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null;
  // Leave caret keys alone in the inline rename/create controls. No tab button
  // lives inside that form, so this also stops the keys from stealing focus
  // while a rename is being typed.
  if (target?.tagName === "INPUT" || target?.closest("form")) return;
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [
    ...(collectionTabsElement.value?.querySelectorAll<HTMLButtonElement>(
      "[data-collection-tab]",
    ) ?? []),
  ];
  if (!tabs.length) return;
  event.preventDefault();
  const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
  let next: number;
  if (event.key === "ArrowLeft") next = index === -1 ? 0 : (index - 1 + tabs.length) % tabs.length;
  else if (event.key === "ArrowRight") next = index === -1 ? 0 : (index + 1) % tabs.length;
  else if (event.key === "Home") next = 0;
  else next = tabs.length - 1;
  const tab = tabs[next];
  tab?.focus();
  activeCollection.value = tab?.dataset.collection ?? "";
};

const scrollSelectedCollectionTabIntoView = async () => {
  await nextTick();
  collectionTabsElement.value
    ?.querySelector('[aria-selected="true"]')
    ?.scrollIntoView({ block: "nearest", inline: "nearest" });
};

const gameInCollection = (game: GameRecord, name: string): boolean => {
  const key = name.toLowerCase();
  return (game.collections || []).some((entry) => entry.toLowerCase() === key);
};

// The context menu holds its own copy of the game, so membership is written to
// both it and the library list. Updating before the save lets a second toggle
// build on the first even while the first is still in flight.
const setLocalMembership = (gameId: number, names: string[]) => {
  const listed = games.value.find((entry) => entry.id === gameId);
  if (listed) listed.collections = names;
  if (contextMenu.value?.game.id === gameId) contextMenu.value.game.collections = names;
};

const toggleGameCollection = async (game: GameRecord, name: string) => {
  const key = name.toLowerCase();
  const current =
    games.value.find((entry) => entry.id === game.id)?.collections || game.collections || [];
  const next = current.some((entry) => entry.toLowerCase() === key)
    ? current.filter((entry) => entry.toLowerCase() !== key)
    : [...current, name];
  setLocalMembership(game.id, next);
  const result = await api.setGameCollections(game.id, next);
  if (!result.success) {
    notifyError(result.error || "Could not update the collection.");
    // Undo the optimistic change first so the open menu is right even if the
    // reload fails, then take whatever the database actually stored.
    setLocalMembership(game.id, current);
    await loadGames();
    const refreshed = games.value.find((entry) => entry.id === game.id);
    if (refreshed) setLocalMembership(game.id, refreshed.collections || []);
  }
};

const createCollectionForGame = async (game: GameRecord, name: string) => {
  const result = await api.createCollection(name, game.id);
  if (!result.success) return result.error || "Could not create the collection.";
  applyCollectionsFromPayload(result.collections);
  await loadGames();
  const refreshed = games.value.find((entry) => entry.id === game.id);
  if (refreshed) setLocalMembership(game.id, refreshed.collections || []);
  return "";
};

const contextCreateForGame = async (game: GameRecord) => {
  const name = contextNewCollectionName.value.trim();
  if (!name) {
    contextNewCollectionError.value = "Enter a collection name.";
    return;
  }
  const error = await createCollectionForGame(game, name);
  if (error) {
    contextNewCollectionError.value = error;
    return;
  }
  contextNewCollectionName.value = "";
  contextNewCollectionError.value = "";
};

// Saved filter presets.
const applyFilterPreset = (preset: LibraryFilterPreset) => {
  const normalized = normalizeFilterPreset(preset, {
    validEngines: uniqueEngines.value,
    validTags: uniqueTags.value,
    validStatuses: allStatuses.value.map((status) => status.value),
  });
  if (!normalized) return;
  filterCollection.value = normalized.filterCollection;
  filterStatuses.value = [...normalized.filterStatuses];
  filterInstallState.value = normalized.filterInstallState;
  filterEngines.value = [...normalized.filterEngines];
  filterTags.value = [...normalized.filterTags];
  sortBy.value = normalized.sortBy;
  sortDir.value = normalized.sortDir;
};

const saveFilterPreset = async () => {
  const name = newPresetName.value.trim();
  if (!name || name.length > 40) {
    presetError.value = "Preset names must contain 1–40 characters.";
    return;
  }
  if (!presetsLoaded.value) {
    const loaded = await readPresetsForSave(
      () => api.getSettings(),
      savedPresets.value,
      false,
    );
    if (!loaded.loaded) {
      presetError.value =
        "Could not load your saved filters, so the preset was not saved. Try again.";
      return;
    }
    savedPresets.value = loaded.presets;
    presetsLoaded.value = true;
  }
  const existing = savedPresets.value.find(
    (preset) => preset.name.toLowerCase() === name.toLowerCase(),
  );
  if (existing && !confirm(`Replace the saved filter "${name}"?`)) return;
  const preset: LibraryFilterPreset = {
    name,
    filterCollection: filterCollection.value,
    filterStatuses: [...filterStatuses.value],
    filterInstallState: filterInstallState.value,
    filterEngines: [...filterEngines.value],
    filterTags: [...filterTags.value],
    sortBy: sortBy.value,
    sortDir: sortDir.value,
  };
  const next = existing
    ? savedPresets.value.map((entry) => (entry === existing ? preset : entry))
    : [...savedPresets.value, preset];
  const result = await api.saveLibraryFilterPresets(next);
  if (!result.success) {
    presetError.value = result.error || "Could not save the filter.";
    return;
  }
  savedPresets.value = next;
  newPresetName.value = "";
  presetError.value = "";
};

const deleteFilterPreset = async (name: string) => {
  if (!confirm(`Delete the saved filter "${name}"?`)) return;
  const next = savedPresets.value.filter((preset) => preset.name !== name);
  const result = await api.saveLibraryFilterPresets(next);
  if (!result.success) {
    presetError.value = result.error || "Could not delete the filter.";
    return;
  }
  savedPresets.value = next;
  presetError.value = "";
};

const updatingId = ref<number | null>(null);
const modalUpdateState = ref<ModalUpdateState>({
  running: false,
  type: "",
  message: "",
});
let modalUpdateTimeout: ReturnType<typeof setTimeout> | null = null;

const clearModalUpdateState = () => {
  if (modalUpdateTimeout) {
    clearTimeout(modalUpdateTimeout);
    modalUpdateTimeout = null;
  }
  modalUpdateState.value = { running: false, type: "", message: "" };
};

const showModalUpdateNotice = (type: UpdateNotice["type"], message: string) => {
  if (modalUpdateTimeout) {
    clearTimeout(modalUpdateTimeout);
  }
  modalUpdateState.value = { running: false, type, message };
  modalUpdateTimeout = setTimeout(() => {
    modalUpdateState.value = { running: false, type: "", message: "" };
    modalUpdateTimeout = null;
  }, 5000);
};

const formatPlaytime = (seconds: number | null | undefined): string => {
  if (!seconds) return "0.0 hrs";
  return (seconds / 3600).toFixed(1) + " hrs";
};

const getGamePlayStatusMeta = (game: GameRecord) => {
  return getPlayStatusMeta(game.play_status, game.status);
};

const sortLaunchTargets = (targets: LaunchTarget[]): LaunchTarget[] => {
  return [...targets].sort(
    (a, b) => a.sort_order - b.sort_order || a.id - b.id,
  );
};

const getEffectiveLaunchTargets = (
  game: GameRecord,
): EffectiveLaunchTarget[] => [
  {
    id: `default-${game.id}`,
    label: "Default",
    exe_path: game.exe_path,
    isDefault: true,
  },
  ...sortLaunchTargets(game.launch_targets || []).map((target) => ({
    id: `target-${target.id}`,
    label: target.label,
    exe_path: target.exe_path,
    isDefault: false,
  })),
];

const hasAdditionalLaunchTargets = (game: GameRecord): boolean => {
  return (
    !isWishlisted(game) && !runningGameIds.value.has(game.id) && (game.launch_targets || []).length > 0
  );
};

const closeLaunchTargetMenus = () => {
  openLaunchTargetMenuId.value = null;
};

const toggleLaunchTargetMenu = (game: GameRecord) => {
  openLaunchTargetMenuId.value =
    openLaunchTargetMenuId.value === game.id ? null : game.id;
};

let gamesRequest = 0;
const loadGames = async () => {
  const request = ++gamesRequest;
  try {
    const data = await api.getGames();
    if (request !== gamesRequest) return;
    if (data) {
      games.value = data;
      if (selectedGame.value) {
        const selectedId = selectedGame.value.id;
        const updated = data.find((g) => g.id === selectedId);
        if (updated) selectedGame.value = updated;
      }
    }
  } catch (e) {
    console.error("Failed to load games", e);
  }
};

let runningGamesRequest = 0;
const loadRunningGames = async () => {
  const request = ++runningGamesRequest;
  try {
    const ids = await api.getRunningGames();
    if (request !== runningGamesRequest) return;
    runningGameIds.value = new Set(ids);
  } catch (error) {
    console.error("Failed to load running games", error);
  }
};

// Sync and imports can replace the collection list and presets, not just games.
let settingsRequest = 0;
const loadLibrarySettings = async () => {
  const request = ++settingsRequest;
  try {
    const settings = await api.getSettings();
    // A slow older read must not overwrite collections or presets that a
    // newer sync/import refresh already applied.
    if (request !== settingsRequest) return;
    customStatuses.value = settings.custom_play_statuses || [];
    customStatusesLoaded.value = true;
    applyCollectionsFromPayload(settings.collections);
    savedPresets.value = settings.library_filter_presets || [];
    presetsLoaded.value = true;
  } catch (error) {
    console.error("Failed to load library settings", error);
  }
};

const handleLibraryRefresh = () => {
  void loadGames();
  void loadRunningGames();
  void loadLibrarySettings();
};

const openDetail = (game: GameRecord) => {
  closeContextMenu(false);
  clearModalUpdateState();
  selectedGame.value = game;
  showDetailModal.value = true;
};

const handleGameUpdated = async () => {
  clearModalUpdateState();
  showDetailModal.value = false;
  selectedGame.value = null;
  await loadGames();
};

const handleGameDeleted = async () => {
  clearModalUpdateState();
  showDetailModal.value = false;
  selectedGame.value = null;
  await loadGames();
};

const handleLaunchTargetsChanged = async () => {
  await loadGames();
};

const runSingleGameUpdateCheck = async (game: GameRecord) => {
  if (!game.f95_url) {
    return {
      type: "error" as const,
      message: `${game.title}: missing F95 URL.`,
    };
  }

  updatingId.value = game.id;
  try {
    const result = await api.checkForUpdates(game.f95_url);
    if (result && result.success) {
      await loadGames();
      if (result.newly_finished) {
        notify(`${game.title} is finished`, "success");
        return { type: "" as const, message: "" };
      }
      if (result.has_update) {
        return {
          type: "success" as const,
          message: `${game.title}: new version ${result.version} available.`,
        };
      }

      return {
        type: "success" as const,
        message: `${game.title}: no new update found (${result.version}).`,
      };
    }

    const reason = result?.error || "Update check failed";
    return {
      type: "error" as const,
      message: `${game.title}: ${reason}`,
    };
  } catch (e) {
    console.error("Update check failed", e);
    return {
      type: "error" as const,
      message: `${game.title}: ${String(e) || "Update check failed"}`,
    };
  } finally {
    updatingId.value = null;
  }
};

const launchGameFast = async (game: GameRecord, exePath = game.exe_path) => {
  if (isWishlisted(game)) {
    openDetail(game);
    return;
  }
  closeLaunchTargetMenus();
  try {
    const result = await api.launchGame(
      game.id,
      exePath,
      game.command_line_args || "",
      game.run_japanese_locale || false,
      game.run_wayland || false,
      game.auto_inject_ce || false,
      game.custom_prefix || "",
      game.proton_version || "",
      game.launch_mode || "auto",
    );
    if (result && !result.success) {
      const sandboxError = sandboxLaunchError(result.code || result.error_code, result.error);
      if (sandboxError) notify(sandboxError.message, "error", "Could not launch game", sandboxError.openSettings ? { label: "Open Sandbox settings", run: () => { void router.push('/settings#sandbox'); } } : undefined);
      else notifyError(`Failed to launch game:\n\n${result.error}`);
    } else if (result?.success) {
      // Reconcile with the registry: HTML is untracked and short commands may already have exited.
      await loadRunningGames();
    }
  } catch (e) {
    console.error("Launch failed", e);
  }
};

const stopGame = async (gameId: number) => {
  try {
    const result = await api.stopGame(gameId);
    if (result.success === false) notifyError(result.error || "Could not stop game");
  } catch (error) {
    notifyError(String(error));
  }
};

const toggleGame = (game: GameRecord) => isWishlisted(game)
  ? openDetail(game)
  : runningGameIds.value.has(game.id) ? stopGame(game.id) : launchGameFast(game);

const launchSelectedTarget = async (
  game: GameRecord,
  target: EffectiveLaunchTarget,
) => {
  closeLaunchTargetMenus();
  await launchGameFast(game, target.exe_path);
};

const launchTargetFromSelect = (game: GameRecord, event: Event) => {
  const select = event.target as HTMLSelectElement;
  const target = getEffectiveLaunchTargets(game).find(
    (entry) => entry.id === select.value,
  );
  select.value = "";
  if (target) void launchSelectedTarget(game, target);
};

// Handle incoming extension import
watch(
  () => route.query,
  async (q) => {
    if (!q) return;
    const action = readQueryValue(q.action);
    const queryUrl = readQueryValue(q.f95url);
    if (action === "import" && (queryUrl || readStoreLinks(readQueryValue(q.links)).length)) {
      await loadGames();
      // Keep the import query until the dialog closes so its archive hand-off
      // can identify the extension entry point and retain all seed metadata.
      showAddModal.value = true;
    }
    // Ctrl+N from any view (App.vue).
    if (action === "add") {
      showAddModal.value = true;
      void router.replace({ path: "/", query: {} });
    }
    if (action === "open" && (queryUrl || extensionGameId(q.gameId))) {
      // Fetch directly: on mount, the view's own loadGames() supersedes one awaited here.
      try {
        const match = ((await api.getGames()) || []).find((g) =>
          extensionGameId(q.gameId) ? g.id === extensionGameId(q.gameId) : urlsMatchByThreadIdentity(g.f95_url, queryUrl),
        );
        if (match) openDetail(match);
      } catch (e) {
        console.error("Failed to open game from URL", e);
      }
      // Consume the request: an identical route push is ignored as a duplicate,
      // so a leftover query would swallow the next "Open in wLib" for this game.
      void router.replace({ path: "/", query: {} });
    }
  },
  { immediate: true },
);

const addingGame = ref(false);

const handleAddGame = async (gameData: AddGamePayload) => {
  if (addingGame.value) return;
  addingGame.value = true;
  try {
    const result = await api.addGame(
      gameData.title,
      gameData.exe_path,
      gameData.f95_url || "",
      gameData.version || "",
      gameData.cover_image || "",
      gameData.tags || "",
      gameData.rating || "",
      gameData.developer || "",
      gameData.engine || "",
      false, // run_japanese_locale
      false, // run_wayland
      false, // auto_inject_ce
      "", // custom_prefix
      "", // proton_version
      gameData.launch_mode || "auto",
      gameData.command_line_args || "",
      gameData.links || [],
    );
    if (result && result.success === false) {
      notifyError(`Failed to add game:\n\n${result.error || "Unknown error"}`);
      return;
    }

    if (result && result.id) {
      showAddModal.value = false;
      router.replace({ path: "/", query: {} });
      await loadGames();
    }
  } catch (e) {
    console.error("Failed to add game", e);
  } finally {
    addingGame.value = false;
  }
};

const checkUpdate = async (game: GameRecord) => {
  if (updatingId.value !== null) return;
  const feedback = await runSingleGameUpdateCheck(game);
  if (feedback.type === "error") notifyError(feedback.message);
  else if (feedback.type === "success") notify(feedback.message, "success");
};

const markAsUpdated = async (game: GameRecord) => {
  if (markingUpdatedIds.value.has(game.id)) return;
  markingUpdatedIds.value.add(game.id);
  try {
    const result = await api.markGameUpdated(game);
    if (!result.success)
      throw new Error(result.error || "Could not mark game as updated");
    notify(
      `${game.title}: installed version set to ${game.latest_version?.trim()}.`,
      "success",
    );
  } catch (error) {
    notifyError(String(error));
  } finally {
    markingUpdatedIds.value.delete(game.id);
  }
};

const toggleSkippedVersion = async (game: GameRecord) => {
  const skipped = isUpdateSkipped(game);
  const result = await api.updateGame(game.id, {
    skipped_version: skipped ? "" : game.latest_version || "",
  });
  if (!result.success) throw new Error(result.error || "Could not change skipped version");
  notify(skipped ? `${game.title}: stopped skipping ${game.skipped_version}.`
    : `${game.title}: skipping version ${game.latest_version}.`, "success");
};

const closeContextMenu = (restoreFocus = true) => {
  if (!contextMenu.value) return;
  contextMenu.value = null;
  contextToolsRequest++;
  contextTools.value = [];
  contextMenuView.value = "actions";
  contextNewCollectionName.value = "";
  contextNewCollectionError.value = "";
  if (restoreFocus) contextMenuTrigger?.focus();
};

const openContextMenu = async (
  game: GameRecord,
  event: MouseEvent | KeyboardEvent,
) => {
  if (
    event instanceof KeyboardEvent &&
    event.key !== "ContextMenu" &&
    !(event.shiftKey && event.key === "F10")
  )
    return;
  event.preventDefault();
  closeLaunchTargetMenus();
  contextMenuTrigger =
    (event.target as HTMLElement).closest<HTMLButtonElement>("button") ||
    (event.currentTarget as HTMLElement);
  const rect = contextMenuTrigger.getBoundingClientRect();
  contextMenu.value = {
    game,
    x: event instanceof MouseEvent ? event.clientX : rect.left,
    y: event instanceof MouseEvent ? event.clientY : rect.bottom,
  };
  void loadContextTools(game.id);
  await nextTick();
  if (!contextMenu.value || !contextMenuElement.value) return;
  const menu = contextMenuElement.value;
  contextMenu.value.x = Math.max(
    8,
    Math.min(contextMenu.value.x, window.innerWidth - menu.getBoundingClientRect().width - 8),
  );
  contextMenu.value.y = Math.max(
    8,
    Math.min(contextMenu.value.y, window.innerHeight - menu.getBoundingClientRect().height - 8),
  );
  menu.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
};

const handleContextMenuKeyboard = (event: KeyboardEvent) => {
  const targetTag = (event.target as HTMLElement).tagName;
  if ((targetTag === "SELECT" || targetTag === "INPUT") && event.key !== "Tab")
    return;
  // The collections view also holds a checkbox and a text input; without them
  // Tab would only ever cycle between Back and Add.
  const controls = [
    ...contextMenuElement.value!.querySelectorAll<HTMLElement>(
      "button:not(:disabled), select, input:not(:disabled)",
    ),
  ];
  const index = controls.indexOf(document.activeElement as HTMLElement);
  let next: number;
  if (event.key === "ArrowDown" || (event.key === "Tab" && !event.shiftKey))
    next = (index + 1) % controls.length;
  else if (event.key === "ArrowUp" || (event.key === "Tab" && event.shiftKey))
    next = (index - 1 + controls.length) % controls.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = controls.length - 1;
  else return;
  event.preventDefault();
  controls[next]?.focus();
};

const runContextAction = async (
  action:
    | "play"
    | "folder"
    | "thread"
    | "check"
    | "mark-updated"
    | "skip-version"
    | "favorite"
    | "remove"
    | "status",
  status = "",
) => {
  const game = contextMenu.value?.game;
  if (!game) return;
  closeContextMenu();
  try {
    if (action === "play") return await toggleGame(game);
    if (action === "check") return await checkUpdate(game);
    if (action === "mark-updated") return await markAsUpdated(game);
    if (action === "skip-version") return await toggleSkippedVersion(game);
    if (
      action === "remove" &&
      !confirm(`Remove "${game.title}" from your library?`)
    )
      return;
    const result =
      action === "folder"
        ? await api.openFolder(gameFolder(game.exe_path))
        : action === "thread"
          ? await api.openInBrowser(gamePageUrl(game))
          : action === "remove"
            ? await api.deleteGame(game.id)
            : await api.updateGame(
                game.id,
                action === "favorite"
                  ? { is_favorite: !game.is_favorite }
                  : { play_status: status },
              );
    if (!result.success) notifyError(result.error || "Game action failed");
  } catch (error) {
    notifyError(String(error));
  }
};

const handleDocumentClick = () => {
  closeLaunchTargetMenus();
  closeCollectionTabMenu(false);
  closeContextMenu(false);
};
const handleLibraryScroll = (event: Event) => {
  if (!contextMenuElement.value?.contains(event.target as Node))
    closeContextMenu(false);
  if (!collectionTabMenuElement.value?.contains(event.target as Node))
    closeCollectionTabMenu(false);
};
const handleLibraryKeyboard = (event: KeyboardEvent) => {
  if (contextMenu.value && event.key === "Escape") {
    event.preventDefault();
    closeContextMenu();
    return;
  }
  if (collectionTabMenu.value && event.key === "Escape") {
    event.preventDefault();
    closeCollectionTabMenu();
    return;
  }
  if (showAddModal.value || showImportModal.value || showDetailModal.value || contextMenu.value || collectionTabMenu.value) return;
  if (event.key === "Escape" && openLaunchTargetMenuId.value !== null) {
    closeLaunchTargetMenus();
    event.preventDefault();
    return;
  }
  const typing =
    event.target instanceof Element &&
    event.target.closest("input, textarea, select, [contenteditable='true']");
  if (
    ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") ||
    (event.key === "/" &&
      !typing &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey)
  ) {
    if (!searchInput.value) return;
    event.preventDefault();
    searchInput.value.focus();
    searchInput.value.select();
  }
};

const formatLastPlayed = (value?: string | null): string => {
  const date = new Date(value || "");
  return Number.isNaN(date.getTime()) ? "Never" : date.toLocaleDateString();
};

const handleModalUpdateCheck = async (gameId: number) => {
  const game = games.value.find((entry) => entry.id === gameId) || selectedGame.value;
  if (!game) return;

  modalUpdateState.value = { running: true, type: "", message: "" };
  const feedback = await runSingleGameUpdateCheck(game);
  showModalUpdateNotice(feedback.type, feedback.message);
};

const handlePlaytimeTick = (event: Event) => {
  const detail = (event as CustomEvent<PlaytimeTickDetail>).detail || {};
  const gameId = Number(detail.gameId);
  const delta = Number(detail.delta);
  if (detail.isFinal && Number.isInteger(gameId)) runningGameIds.value.delete(gameId);
  if (!Number.isInteger(gameId) || !Number.isFinite(delta) || delta < 0) return;

  const game = games.value.find((g) => g.id === gameId);
  if (!game) return;

  game.playtime_seconds = (Number(game.playtime_seconds) || 0) + delta;
  game.last_played = new Date().toISOString();
  if (typeof detail.lastPlayedVersion === "string") game.last_played_version = detail.lastPlayedVersion;

  if (selectedGame.value && selectedGame.value.id === gameId) {
    selectedGame.value = {
      ...selectedGame.value,
      playtime_seconds: game.playtime_seconds,
      last_played: game.last_played,
      last_played_version: game.last_played_version || "",
    };
  }
};

watch(
  [
    layoutMode,
    cardSize,
    sortBy,
    sortDir,
    filterCollection,
    filterStatuses,
    filterInstallState,
    filterEngines,
    filterTags,
    isFiltersCollapsed,
    filterSections,
    activeCollection,
  ],
  () => {
    persistLibraryViewState();
  },
  { deep: true },
);

// A saved tab naming a deleted or renamed collection falls back to "All".
watch(userCollections, (collections) => {
  const normalized = normalizeActiveCollection(activeCollection.value, collections);
  if (normalized !== activeCollection.value) activeCollection.value = normalized;
});

watch(activeCollection, () => {
  void scrollSelectedCollectionTabIntoView();
});

watch(uniqueEngines, (engines) => {
  const normalizedEngines = normalizeLibraryViewState(
    { filterEngines: filterEngines.value },
    { validEngines: engines },
  ).filterEngines;

  if (!sameStringArray(normalizedEngines, filterEngines.value)) {
    filterEngines.value = normalizedEngines;
  }
});

watch(uniqueTags, (tags) => {
  const normalizedTags = normalizeLibraryViewState(
    { filterTags: filterTags.value },
    { validTags: tags },
  ).filterTags;

  if (!sameStringArray(normalizedTags, filterTags.value)) {
    filterTags.value = normalizedTags;
  }
});

// A custom status deleted in Settings (or a preset from another device) leaves
// the saved name in the view state. Unlike engines and tags it has no pill to
// uncheck, so drop it here or the library shows "No games found" with every
// filter looking clear.
watch(allStatuses, (statuses) => {
  // Wait for both sources of status names: games may still be loading, and a
  // custom status with no games yet only comes from Settings.
  if (!games.value.length || !customStatusesLoaded.value) return;
  const normalizedStatuses = normalizeFilterAndSort(
    { filterStatuses: filterStatuses.value },
    { validStatuses: statuses.map((status) => status.value) },
  ).filterStatuses;

  if (!sameStringArray(normalizedStatuses, filterStatuses.value)) {
    filterStatuses.value = normalizedStatuses;
  }
});

watch(showDetailModal, (open) => {
  if (!open) {
    clearModalUpdateState();
    selectedGame.value = null;
    void loadGames();
  }
});

watch(showImportModal, (open) => {
  if (!open) {
    void loadGames();
  }
});

onMounted(() => {
  restoreLibraryViewState();

  window.addEventListener("wlib-refresh-library", handleLibraryRefresh);
  window.addEventListener("wlib-playtime-tick", handlePlaytimeTick);
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("keydown", handleLibraryKeyboard);
  document.addEventListener("scroll", handleLibraryScroll, true);
  window.addEventListener("resize", handleDocumentClick);
  onWebviewReady(() => {
    void loadTagColors().catch(console.error);
    void loadGames();
    void loadRunningGames();
    void loadLibrarySettings();
  });
});

onUnmounted(() => {
  gamesRequest++;
  runningGamesRequest++;
  settingsRequest++;
  window.removeEventListener("wlib-refresh-library", handleLibraryRefresh);
  window.removeEventListener("wlib-playtime-tick", handlePlaytimeTick);
  document.removeEventListener("click", handleDocumentClick);
  document.removeEventListener("keydown", handleLibraryKeyboard);
  document.removeEventListener("scroll", handleLibraryScroll, true);
  window.removeEventListener("resize", handleDocumentClick);
  if (modalUpdateTimeout) {
    clearTimeout(modalUpdateTimeout);
  }
});
</script>

<template>
  <div class="h-full flex overflow-hidden">
    <!-- Filters Sidebar -->
    <aside
      :inert="isFiltersCollapsed"
      :aria-hidden="isFiltersCollapsed"
      :class="[
        'filters-pane shrink-0 h-full collapse-width-transition',
        isFiltersCollapsed ? 'w-0 filters-pane-collapsed' : 'w-64',
      ]"
    >
      <div class="w-64 h-full flex flex-col overflow-y-auto">
        <div class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('presets')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Saved filters</span>
            <span class="text-sm">{{
              filterSections.presets ? "▾" : "▸"
            }}</span>
          </button>
          <div v-show="filterSections.presets" class="space-y-1 mt-3">
            <p
              v-if="!savedPresets.length"
              class="px-3 text-xs"
              style="color: var(--text-muted)"
            >
              Save the current filters and sort under a name to reuse them.
            </p>
            <div
              v-for="preset in savedPresets"
              :key="preset.name"
              class="flex items-center gap-1"
            >
              <button
                @click="applyFilterPreset(preset)"
                class="flex-1 min-w-0 flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ui-hover-surface text-left"
                style="color: var(--text-secondary)"
                :title="`Apply the saved filter '${preset.name}'`"
              >
                <IconSparkles class="w-4 h-4 shrink-0" />
                <span class="truncate">{{ preset.name }}</span>
              </button>
              <button
                @click="deleteFilterPreset(preset.name)"
                class="ui-icon-btn p-1.5 rounded-lg shrink-0"
                style="color: var(--text-muted)"
                :aria-label="`Delete the saved filter ${preset.name}`"
                title="Delete saved filter"
              >
                <IconX class="w-4 h-4" />
              </button>
            </div>
            <form class="flex gap-1 pt-1" @submit.prevent="saveFilterPreset">
              <input
                v-model="newPresetName"
                type="text"
                aria-label="Saved filter name"
                placeholder="Name for current filters"
                class="library-search-input flex-1 min-w-0 rounded-lg px-2 py-1.5 text-xs"
              />
              <button
                type="submit"
                class="ui-action-btn px-2 py-1.5 rounded-lg text-xs font-semibold border active:scale-95 active:bg-[var(--bg-overlay)]"
                style="color: var(--text-secondary); border-color: var(--border)"
                title="Save the current filters and sort under this name"
              >
                Save
              </button>
            </form>
            <p
              v-if="presetError"
              role="alert"
              class="px-3 text-xs"
              style="color: var(--danger-text)"
            >
              {{ presetError }}
            </p>
          </div>
        </div>

        <div class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('collections')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Quick filters</span>
            <span class="text-sm">{{
              filterSections.collections ? "▾" : "▸"
            }}</span>
          </button>
          <div v-show="filterSections.collections" class="space-y-1 mt-3">
            <button
              v-for="collection in collections"
              :key="collection.value"
              @click="selectCollection(collection.value)"
              :aria-pressed="filterCollection === collection.value"
              class="w-full min-h-11 flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ui-hover-surface"
              :style="
                filterCollection === collection.value
                  ? 'background: var(--bg-raised); color: var(--text-primary)'
                  : 'color: var(--text-secondary)'
              "
            >
              <component :is="collection.icon" class="w-4 h-4 shrink-0" />
              <span>{{ collection.label }}</span>
              <span
                v-if="collection.value === 'Updates available'"
                class="collection-count ml-auto"
                >{{ updateCount }}</span
              >
            </button>
          </div>
        </div>

        <div class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('status')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Play Status</span>
            <span class="text-sm">{{ filterSections.status ? "▾" : "▸" }}</span>
          </button>
          <div v-show="filterSections.status" class="space-y-1 mt-3">
            <label
              v-for="s in allStatuses"
              :key="s.value"
              class="flex items-center gap-3 px-3 py-1.5 rounded-lg text-sm cursor-pointer transition-colors ui-hover-surface"
            >
              <input
                type="checkbox"
                :checked="statusFilterActive(s.value)"
                @change="applyStatusFilter(s.value, $event)"
                class="filters-checkbox rounded"
              />
              <span class="ui-status-option" :class="s.toneClass">
                <component :is="s.icon" class="ui-status-icon" />
                <span>{{ s.label }}</span>
              </span>
            </label>
          </div>
        </div>

        <div class="p-6 pb-2 pt-4">
          <label class="block px-2 text-xs uppercase tracking-widest font-bold" style="color: var(--text-muted)">
            Install state
            <select v-model="filterInstallState" class="library-search-input mt-3 w-full rounded-lg px-3 py-2 text-sm normal-case tracking-normal font-medium">
              <option value="">All</option>
              <option value="installed">Installed</option>
              <option value="wishlist">Wishlist</option>
            </select>
          </label>
        </div>

        <div class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('engines')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Engines</span>
            <span class="text-sm">{{ filterSections.engines ? "▾" : "▸" }}</span>
          </button>
          <div v-show="filterSections.engines" class="mt-3">
            <div class="mb-2 flex justify-end">
              <button
                v-if="filterEngines.length"
                @click="filterEngines = []"
                class="cursor-pointer hover:text-red-400 text-[0.625rem] normal-case tracking-normal transition-colors"
                style="color: var(--text-muted)"
              >
                Clear
              </button>
            </div>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="engine in uniqueEngines"
                :key="engine"
                @click="toggleFilter(filterEngines, engine)"
                class="filter-tag-btn px-2.5 py-1 rounded-full text-[0.6875rem] font-medium"
                :style="
                  filterEngines.includes(engine)
                    ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                    : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'
                "
              >
                {{ engine }}
              </button>
              <div
                v-if="!uniqueEngines.length"
                class="text-xs italic"
                style="color: var(--text-muted)"
              >
                No engines found
              </div>
            </div>
          </div>
        </div>

        <div class="p-6 pt-4 flex-1">
          <button
            @click="toggleFilterSection('tags')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Tags</span>
            <span class="text-sm">{{ filterSections.tags ? "▾" : "▸" }}</span>
          </button>
          <div v-show="filterSections.tags" class="mt-3">
            <input
              v-model="tagSearchQuery"
              type="search"
              aria-label="Search tags"
              placeholder="Search tags..."
              class="library-search-input w-full rounded-lg px-3 py-2 text-xs mb-2"
            />
            <p class="text-xs mb-2" style="color: var(--text-muted)">
              Matches all selected tags.
            </p>
            <div class="mb-2 flex justify-end">
              <button
                v-if="filterTags.length"
                @click="filterTags = []"
                class="cursor-pointer hover:text-red-400 text-[0.625rem] normal-case tracking-normal transition-colors"
                style="color: var(--text-muted)"
              >
                Clear
              </button>
            </div>
            <div class="flex flex-wrap gap-1.5 max-h-64 overflow-y-auto">
              <button
                v-for="tag in visibleTags"
                :key="tag"
                @click="toggleTag(tag)"
                @contextmenu="openTagColorPicker($event, tag)"
                @keydown.shift.f10.prevent="openTagColorPicker($event, tag)"
                :data-tag-color="colorForTag(tag)"
                :aria-pressed="hasTagFilter(filterTags, tag)"
                class="tag-pill filter-tag-btn px-2.5 py-1 rounded-full text-[0.6875rem] font-medium"
                :style="
                  colorForTag(tag) ? undefined : hasTagFilter(filterTags, tag)
                    ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                    : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'
                "
              >
                <IconCheck v-if="colorForTag(tag) && hasTagFilter(filterTags, tag)" class="inline w-3 h-3 mr-1" aria-hidden="true" />
                {{ tag }}
                <span class="ml-1 opacity-70">{{ tagCounts.get(tag) }}</span>
              </button>
              <div
                v-if="!visibleTags.length"
                class="text-xs italic"
                style="color: var(--text-muted)"
              >
                {{ uniqueTags.length ? "No matching tags" : "No tags found" }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </aside>

    <!-- Main Content Area -->
    <div class="flex-1 min-w-0 p-5 overflow-y-auto flex flex-col relative">
      <header
        class="flex flex-wrap gap-3 justify-between items-center mb-5 shrink-0"
      >
        <div>
          <h2
            class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
            style="color: var(--text-primary)"
          >
            <IconDeviceGamepad2 class="ui-page-heading-icon" />
            <span>Your Library</span>
          </h2>
          <p
            class="text-sm pl-3"
            style="
              color: var(--text-secondary);
              border-left: 2px solid var(--brand);
            "
          >
            Manage and play your imported games.
          </p>
        </div>

        <div class="flex items-center gap-3">
          <button
            @click="showAddModal = true"
            class="ui-action-btn library-primary-btn px-3 py-1.5 rounded-lg text-sm font-semibold active:scale-95"
            style="
              background: var(--brand);
              color: var(--on-brand);
              box-shadow: var(--shadow-brand);
            "
          >
            <IconLibraryPlus class="ui-action-icon" />
            Add Game
          </button>
          <button
            @click="showImportModal = true"
            class="ui-action-btn px-3 py-1.5 rounded-lg text-sm font-semibold active:scale-95"
            style="
              background: var(--bg-raised);
              color: var(--text-primary);
              border: 1px solid var(--border);
            "
            title="Add every game in a folder"
          >
            <IconFolderPlus class="ui-action-icon" />
            Import Folder
          </button>
          <button
            @click="openInstallArchive()"
            class="ui-action-btn px-3 py-1.5 rounded-lg text-sm font-semibold active:scale-95"
            style="
              background: var(--bg-raised);
              color: var(--text-primary);
              border: 1px solid var(--border);
            "
            title="Install a game from a downloaded archive (.zip, .7z, .rar)"
          >
            <IconArchive class="ui-action-icon" />
            Install from archive
          </button>
        </div>
      </header>

      <!-- Collection tabs -->
      <div class="mb-3 shrink-0">
        <div
          ref="collectionTabsElement"
          class="flex items-center gap-1.5 overflow-x-auto collections-tab-strip"
          role="tablist"
          aria-label="Collections"
          @keydown="handleCollectionTabsKeyboard"
        >
          <button
            data-collection-tab=""
            role="tab"
            :aria-selected="activeCollection === ''"
            class="collection-tab shrink-0"
            title="Every game in your library"
            @click="activeCollection = ''"
          >
            All
          </button>
          <template v-for="name in userCollections" :key="name">
            <form
              v-if="renamingCollection === name"
              class="shrink-0 flex items-center gap-1"
              @submit.prevent="commitCollectionRename"
            >
              <input
                data-rename-input
                v-model="renameCollectionDraft"
                type="text"
                aria-label="Collection name"
                class="library-search-input rounded-lg px-2 py-1 text-sm w-36"
                @keydown.escape.prevent="cancelCollectionRename"
              />
              <button type="submit" class="ui-icon-btn p-1 rounded" title="Save name">
                <IconCheck class="w-4 h-4" />
              </button>
              <button
                type="button"
                class="ui-icon-btn p-1 rounded"
                title="Cancel rename"
                @click="cancelCollectionRename"
              >
                <IconX class="w-4 h-4" />
              </button>
            </form>
            <button
              v-else
              data-collection-tab=""
              :data-collection="name"
              role="tab"
              :aria-selected="activeCollection === name"
              class="collection-tab shrink-0"
              :title="name"
              @click="activeCollection = name"
              @contextmenu.prevent.stop="openCollectionTabMenu(name, $event)"
            >
              <span class="truncate max-w-40">{{ name }}</span>
              <span class="collection-count">{{ collectionCount(name) }}</span>
            </button>
          </template>
          <form
            v-if="showNewCollectionInline"
            class="shrink-0 flex items-center gap-1"
            @submit.prevent="createCollectionFromTab"
          >
            <input
              ref="newCollectionInput"
              v-model="newCollectionName"
              type="text"
              aria-label="New collection name"
              placeholder="Collection name"
              class="library-search-input rounded-lg px-2 py-1 text-sm w-36"
              @keydown.escape.prevent="cancelInlineCollectionCreate"
            />
            <button type="submit" class="ui-icon-btn p-1 rounded" title="Create collection">
              <IconCheck class="w-4 h-4" />
            </button>
            <button
              type="button"
              class="ui-icon-btn p-1 rounded"
              title="Cancel"
              @click="cancelInlineCollectionCreate"
            >
              <IconX class="w-4 h-4" />
            </button>
          </form>
          <button
            v-else
            class="collection-tab collection-tab-add shrink-0"
            title="New collection"
            aria-label="New collection"
            @click="startInlineCollectionCreate"
          >
            <IconPlus class="w-4 h-4" />
          </button>
        </div>
        <p
          v-if="collectionError"
          role="alert"
          class="text-xs mt-1"
          style="color: var(--danger-text)"
        >
          {{ collectionError }}
        </p>
      </div>

      <!-- Search & Filter Bar -->
      <div class="mb-4 space-y-3 shrink-0">
        <div class="flex items-center gap-3">
          <div class="relative flex-1 min-w-0">
            <IconZoom
              class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4"
              style="color: var(--text-muted)"
            />
            <input
              ref="searchInput"
              v-model="searchQuery"
              type="text"
              aria-label="Search games"
              placeholder="Search title, developer, tags or engine..."
              class="library-search-input w-full rounded-lg pl-10 pr-10 py-2.5 text-sm focus:outline-none"
            />
            <button
              v-if="searchQuery"
              @click="
                searchQuery = '';
                searchInput?.focus();
              "
              aria-label="Clear search"
              class="ui-icon-btn absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded"
            >
              <IconX class="w-4 h-4" />
            </button>
          </div>
          <button
            @click="toggleFiltersPane"
            class="ui-action-btn relative px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface active:scale-95 active:bg-[var(--bg-overlay)]"
            style="color: var(--text-primary); border: 1px solid var(--border)"
            :aria-expanded="!isFiltersCollapsed"
            :title="
              isFiltersCollapsed ? 'Show Filters Pane' : 'Hide Filters Pane'
            "
          >
            <IconFilterFilled class="ui-action-icon" />
            <span>Filters</span>
            <span
              v-if="activeFilterCount > 0"
              class="absolute -top-1.5 -right-1.5 min-w-5 h-5 rounded-full px-1 text-[0.625rem] font-bold flex items-center justify-center"
              style="background: var(--brand); color: var(--on-brand)"
            >
              {{ activeFilterCount }}
            </span>
          </button>
        </div>

        <!-- Filter Toggle, Sort & Counter -->
        <div class="flex flex-wrap items-center gap-3">
          <!-- Sort Buttons -->
          <div
            v-if="layoutMode !== 'list'"
            class="flex flex-wrap items-center rounded-lg overflow-hidden"
            style="border: 1px solid var(--border)"
          >
            <button
              v-for="s in sortOptions"
              :key="s.key"
              @click="toggleSort(s.key)"
              class="library-sort-btn px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1 active:scale-95 active:bg-[var(--bg-overlay)]"
              :style="
                sortBy === s.key
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'background: var(--bg-surface); color: var(--text-muted)'
              "
            >
              {{ s.label }}
              <svg
                v-if="sortBy === s.key"
                class="w-3 h-3"
                :class="sortDir === 'desc' ? 'rotate-180' : ''"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.5"
              >
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>

          <button
            v-if="activeFilterCount > 0"
            @click="clearFilters"
            class="ui-action-btn px-3 py-1.5 rounded-lg text-sm hover:text-red-400 border active:scale-95 active:bg-[var(--bg-overlay)]"
            style="color: var(--text-muted); border-color: var(--border)"
          >
            <IconX class="ui-action-icon" />
            Clear all
          </button>

          <div class="ml-auto flex items-center gap-4">
            <span class="text-xs" style="color: var(--text-muted)"
              >{{ filteredGames.length }} game{{
                filteredGames.length !== 1 ? "s" : ""
              }}</span
            >

            <div v-if="layoutMode !== 'list'" class="flex items-center gap-2" style="color: var(--text-muted)">
              <IconPhotoFilled class="size-3" aria-hidden="true" />
              <input v-model.number="cardSize" type="range" min="0" max="4" step="1" aria-label="Card size"
                class="card-size-control w-20 cursor-pointer" />
              <IconPhotoFilled class="size-5" aria-hidden="true" />
            </div>

            <!-- Layout Toggle -->
            <div
              class="flex items-center rounded-lg p-1"
              style="
                background: var(--bg-surface);
                border: 1px solid var(--border);
              "
            >
              <button
                @click="layoutMode = 'grid'"
                class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm active:scale-95 active:bg-[var(--bg-overlay)]"
                :style="
                  layoutMode === 'grid'
                    ? 'background: var(--bg-overlay); color: var(--text-primary)'
                    : 'color: var(--text-muted)'
                "
                title="Grid View"
              >
                <IconColumns2Filled class="ui-action-icon" />
              </button>
              <button
                @click="layoutMode = 'list'"
                class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm active:scale-95 active:bg-[var(--bg-overlay)]"
                :style="
                  layoutMode === 'list'
                    ? 'background: var(--bg-overlay); color: var(--text-primary)'
                    : 'color: var(--text-muted)'
                "
                title="List View"
              >
                <IconLayoutListFilled class="ui-action-icon" />
              </button>
              <button
                @click="layoutMode = 'compact'"
                class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm active:scale-95 active:bg-[var(--bg-overlay)]"
                :style="
                  layoutMode === 'compact'
                    ? 'background: var(--bg-overlay); color: var(--text-primary)'
                    : 'color: var(--text-muted)'
                "
                title="Compact View"
              >
                <IconLayoutGridFilled class="ui-action-icon opacity-90" />
              </button>
              <button
                @click="layoutMode = 'banner'"
                class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm active:scale-95 active:bg-[var(--bg-overlay)]"
                :style="
                  layoutMode === 'banner'
                    ? 'background: var(--bg-overlay); color: var(--text-primary)'
                    : 'color: var(--text-muted)'
                "
                title="Banner View"
              >
                <IconPanoramaHorizontalFilled class="ui-action-icon" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="games.length === 0" class="empty-state flex-1">
        <IconDeviceGamepad2Filled class="empty-state-icon" />
        <h3 class="empty-state-title">Your library is empty</h3>
        <p class="empty-state-subtext">Add your first game to get started</p>
        <p class="empty-state-hint">
          Use the Add Game button above to import your first title.
        </p>
      </div>

      <div v-else-if="filteredGames.length === 0" class="empty-state flex-1">
        <IconZoom class="empty-state-icon" />
        <h3 class="empty-state-title">No games found</h3>
        <p class="empty-state-subtext">Try adjusting your search or filters</p>
        <button
          @click="clearFilters"
          class="ui-action-btn px-3 py-1.5 rounded-lg text-sm font-medium border active:scale-95 active:bg-[var(--bg-overlay)]"
          style="color: var(--text-secondary); border-color: var(--border)"
        >
          Clear filters
        </button>
      </div>

      <div v-else-if="layoutMode === 'list'" class="library-table-wrap pb-12">
        <table class="library-table">
          <caption class="sr-only">
            Games in your library. Select a column header to sort.
          </caption>
          <thead>
            <tr>
              <th
                v-for="column in tableColumns"
                :key="column.key"
                :class="column.class"
                scope="col"
                :aria-sort="
                  sortBy === column.key
                    ? sortDir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                "
              >
                <button
                  @click="toggleSort(column.key)"
                  class="w-full text-left py-3 whitespace-nowrap"
                >
                  {{ column.label }}
                  <span v-if="sortBy === column.key" aria-hidden="true">{{
                    sortDir === "asc" ? "↑" : "↓"
                  }}</span>
                </button>
              </th>
              <th scope="col"><span class="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="game in filteredGames"
              :key="game.id"
              class="game-row"
              :class="{ 'is-running': runningGameIds.has(game.id) }"
              @click="openDetail(game)"
              @contextmenu="openContextMenu(game, $event)"
              @keydown="openContextMenu(game, $event)"
            >
              <td class="library-table-title">
                <div class="flex items-center gap-2 min-w-0">
                  <img
                    v-if="gameCoverSrc(game)"
                    :src="gameCoverSrc(game)"
                    @error="gameCoverError($event, game)"
                    alt=""
                    class="w-8 h-8 rounded object-cover shrink-0"
                  />
                  <span
                    v-else
                    class="w-8 h-8 rounded shrink-0 flex items-center justify-center"
                    style="background: var(--bg-raised)"
                    aria-hidden="true"
                    ><IconPhotoFilled
                      class="w-4 h-4"
                      style="color: var(--text-muted)"
                  /></span>
                  <button
                    @click.stop="openDetail(game)"
                    class="truncate text-left font-semibold"
                    :title="game.title"
                  >
                    {{ game.title }}
                  </button>
                  <span v-if="isWishlisted(game)" class="wishlist-pill shrink-0">Wishlist</span>
                  <span v-if="isWishlisted(game) && game.thread_completed" class="finished-pill shrink-0">Finished</span>
                  <IconStarFilled
                    v-if="game.is_favorite"
                    class="w-3.5 h-3.5 shrink-0 text-yellow-500"
                    aria-label="Favorite"
                  />
                  <span
                    v-if="runningGameIds.has(game.id)"
                    class="running-label shrink-0"
                    >Running</span
                  >
                  <IconLoader2
                    v-if="game.metadata_pending"
                    class="w-3.5 h-3.5 shrink-0 animate-spin"
                    style="color: var(--text-muted)"
                    aria-label="Fetching F95 info"
                  />
                </div>
              </td>
              <td>
                <div class="flex items-center gap-1">
                  <span class="font-mono text-xs">{{
                    game.version || "Unknown"
                  }}</span>
                  <span
                    v-if="hasAvailableUpdate({ ...game, skipped_version: game.skipped_version })"
                    class="update-pill"
                    :title="`Update available: ${game.latest_version}`"
                  >
                    <IconArrowUp class="w-3 h-3" aria-hidden="true" />
                    <span class="sr-only">Update available: </span
                    >{{ game.latest_version }}
                  </span>
                  <span v-if="hasUnplayedUpdate(game)" class="unplayed-label">Unplayed</span>
                </div>
              </td>
              <td class="library-table-clip" :title="getGamePlayStatusMeta(game).label">
                <span
                  class="ui-status-inline whitespace-nowrap"
                  :class="getGamePlayStatusMeta(game).toneClass"
                  ><component
                    :is="getGamePlayStatusMeta(game).icon"
                    class="ui-status-icon"
                  />{{ getGamePlayStatusMeta(game).label }}</span
                >
              </td>
              <td
                class="col-engine library-table-clip text-xs"
                :title="game.engine || 'Unknown'"
              >
                {{ game.engine || "Unknown" }}
              </td>
              <td class="text-xs">
                {{ formatPlaytime(game.playtime_seconds) }}
              </td>
              <td
                class="col-last-played text-xs"
                :title="game.last_played || 'Never'"
              >
                {{ formatLastPlayed(game.last_played) }}
              </td>
              <td>
                <div class="flex items-center gap-1">
                  <button
                    @click.stop="toggleGame(game)"
                    :title="`${isWishlisted(game) ? 'Set up' : runningGameIds.has(game.id) ? 'Stop' : 'Play'} ${game.title}`"
                    class="play-btn ui-play-btn rounded-md p-1.5"
                  >
                    <IconPlayerStopFilled
                      v-if="!isWishlisted(game) && runningGameIds.has(game.id)"
                      class="w-4 h-4"
                    /><span v-else-if="isWishlisted(game)" class="text-xs font-semibold">Set up</span><IconPlayerPlayFilled v-else class="w-4 h-4" />
                  </button>
                  <select
                    v-if="hasAdditionalLaunchTargets(game)"
                    aria-label="Choose launch target"
                    class="table-target-select"
                    @click.stop
                    @change="launchTargetFromSelect(game, $event)"
                  >
                    <option value="">▾</option>
                    <option
                      v-for="target in getEffectiveLaunchTargets(game)"
                      :key="target.id"
                      :value="target.id"
                    >
                      {{ target.label }}
                    </option>
                  </select>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div
        v-else
        class="library-cards grid gap-4 pb-12"
        :style="{ '--card-scale': cardScales[cardSize] }"
        :class="
          layoutMode === 'compact'
            ? 'library-cards--compact'
            : layoutMode === 'banner'
              ? 'library-cards--banner'
              : 'library-cards--grid'
        "
      >
        <div
          v-for="game in filteredGames"
          :key="game.id"
          @click="openDetail(game)"
          @keydown.enter.self="openDetail(game)"
          @keydown.space.self.prevent="openDetail(game)"
          @keydown="openContextMenu(game, $event)"
          @contextmenu="openContextMenu(game, $event)"
          tabindex="0"
          role="group"
          :aria-label="`${game.title}. Press Enter for details.`"
          class="game-card group rounded-xl cursor-pointer relative"
          :class="[
            layoutMode === 'banner'
              ? 'aspect-[2/1]'
              : isImageTileLayout
                ? 'aspect-[16/9]'
                : 'flex flex-col',
            {
              'is-running': runningGameIds.has(game.id),
              'is-menu-open': openLaunchTargetMenuId === game.id,
            },
          ]"
        >
          <div
            class="flex items-center justify-center relative shadow-inner shrink-0 rounded-t-xl"
            :class="
              isImageTileLayout
                ? 'w-full h-full rounded-b-xl'
                : 'w-full aspect-[16/9]'
            "
            style="
              background: linear-gradient(
                135deg,
                var(--bg-raised),
                var(--bg-inset)
              );
            "
          >
            <img
              v-if="gameCoverSrc(game)"
              :src="gameCoverSrc(game)"
              @error="gameCoverError($event, game)"
              :alt="game.title"
              class="absolute inset-0 w-full h-full object-cover rounded-t-xl"
              :class="[
                layoutMode === 'banner' ? 'object-center' : 'object-top',
                { 'rounded-b-xl': isImageTileLayout },
              ]"
            />
            <IconPhotoFilled
              v-else
              class="w-12 h-12"
              style="color: var(--border)"
            />
            <div
              v-if="game.metadata_pending"
              class="metadata-pending absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 text-xs font-medium"
              :class="isImageTileLayout ? 'rounded-xl' : 'rounded-t-xl'"
              role="status"
            >
              <IconLoader2 class="w-6 h-6 animate-spin" />
              Fetching F95 info…
            </div>
            <div class="absolute top-2 left-2 flex items-center gap-1 z-10">
              <span
                v-if="runningGameIds.has(game.id)"
                class="running-label"
                title="Game is running"
                >● Running</span
              >
              <span
                v-if="game.is_favorite"
                class="card-indicator"
                title="Favorite"
                aria-label="Favorite"
                ><IconStarFilled class="w-3 h-3 text-yellow-400"
              /></span>
              <span
                v-if="isImageTileLayout && hasAvailableUpdate({ ...game, skipped_version: game.skipped_version })"
                class="update-pill update-pill-on-art"
                :title="`Update available: ${game.latest_version}`"
              >
                <IconArrowUp class="w-3 h-3" aria-hidden="true" />
                <span class="sr-only">Update available: </span
                >{{ game.latest_version }}
              </span>
              <span v-if="isImageTileLayout && hasUnplayedUpdate(game)" class="unplayed-label unplayed-on-art">Unplayed</span>
            </div>
            <button
              v-if="!isImageTileLayout && game.f95_url"
              @click.stop="checkUpdate(game)"
              :disabled="updatingId !== null"
              :title="`Check updates for ${game.title}`"
              class="update-overlay-btn ui-icon-btn absolute top-2 right-2 p-1.5 rounded-lg"
              :class="{ 'update-check-running': updatingId === game.id }"
            >
              <IconRefresh
                v-if="updatingId !== game.id"
                class="ui-action-icon"
              /><IconLoader2 v-else class="ui-action-icon animate-spin" />
            </button>
            <span
              v-if="!isImageTileLayout && game.rating"
              class="rating-badge absolute bottom-2 left-2 text-xs font-bold px-2 py-0.5 rounded flex items-center gap-1"
              ><IconStarFilled class="w-3 h-3" />{{ game.rating }}</span
            >
            <div
              v-if="isImageTileLayout"
              class="compact-image-overlay banner-reveal absolute inset-0 rounded-xl"
            ></div>
            <div
              v-if="isImageTileLayout"
              class="banner-reveal absolute inset-x-0 bottom-0 z-10 p-2 flex items-end gap-2"
              :class="layoutMode === 'banner' ? 'justify-start' : 'justify-between'"
            >
              <h3
                class="compact-title text-sm font-bold leading-tight"
                :class="
                  layoutMode === 'banner'
                    ? 'banner-title min-w-0 flex-1'
                    : ''
                "
                :title="game.title"
              >
                {{ game.title }}
              </h3>
              <div
                class="relative flex shrink-0"
                :class="{ 'order-first': layoutMode === 'banner' }"
              >
                <button
                  @click.stop="toggleGame(game)"
                  class="compact-play-btn ui-play-btn rounded-md p-2"
                  :title="`${isWishlisted(game) ? 'Set up' : runningGameIds.has(game.id) ? 'Stop' : 'Play'} ${game.title}`"
                >
                  <IconPlayerStopFilled
                    v-if="!isWishlisted(game) && runningGameIds.has(game.id)"
                    class="w-3.5 h-3.5"
                  /><span v-else-if="isWishlisted(game)" class="text-xs font-semibold">Set up</span><IconPlayerPlayFilled v-else class="w-3.5 h-3.5" />
                </button>
                <button
                  v-if="hasAdditionalLaunchTargets(game)"
                  @click.stop="toggleLaunchTargetMenu(game)"
                  class="compact-target-toggle rounded-md px-1.5"
                  :title="`Choose launch target for ${game.title}`"
                  :aria-expanded="openLaunchTargetMenuId === game.id"
                >
                  <IconChevronDown class="w-3.5 h-3.5" />
                </button>
                <div
                  v-if="openLaunchTargetMenuId === game.id && !runningGameIds.has(game.id)"
                  class="launch-target-menu"
                  @click.stop
                >
                  <button
                    v-for="target in getEffectiveLaunchTargets(game)"
                    :key="target.id"
                    class="launch-target-menu-item"
                    @click.stop="launchSelectedTarget(game, target)"
                  >
                    {{ target.label }}
                  </button>
                </div>
              </div>
            </div>
          </div>
          <div
            v-if="!isImageTileLayout"
            class="p-3 flex flex-col flex-1 min-w-0"
          >
            <h3
              class="font-bold truncate text-base mb-1"
              :title="game.title"
              style="color: var(--text-primary)"
            >
              {{ game.title }}
            </h3>
            <p
              v-if="game.developer"
              class="truncate text-xs mb-2"
              style="color: var(--text-muted)"
            >
              by {{ game.developer }}
            </p>
            <div class="flex flex-wrap items-center gap-1.5 text-xs mb-3">
              <span class="font-mono" style="color: var(--brand)">{{
                game.version || "Unknown"
              }}</span>
              <span v-if="isWishlisted(game)" class="wishlist-pill">Wishlist</span>
              <span v-if="isWishlisted(game) && game.thread_completed" class="finished-pill">Finished</span>
              <span
                v-if="hasAvailableUpdate({ ...game, skipped_version: game.skipped_version })"
                class="update-pill"
                :title="`Update available: ${game.latest_version}`"
              >
                <IconArrowUp class="w-3 h-3" aria-hidden="true" />
                <span class="sr-only">Update available: </span
                >{{ game.latest_version }}
              </span>
              <span v-if="hasUnplayedUpdate(game)" class="unplayed-label">Unplayed</span>
              <span
                v-if="game.playtime_seconds"
                style="color: var(--text-secondary)"
                >⏱ {{ formatPlaytime(game.playtime_seconds) }}</span
              >
            </div>
            <div class="flex items-center justify-between gap-2 mt-auto">
              <span
                class="ui-status-chip px-2 py-1 text-xs min-w-0"
                :class="getGamePlayStatusMeta(game).toneClass"
                :title="getGamePlayStatusMeta(game).label"
                ><component
                  :is="getGamePlayStatusMeta(game).icon"
                  class="ui-status-icon"
                /><span class="truncate">{{
                  getGamePlayStatusMeta(game).label
                }}</span></span
              >
              <div class="relative flex shrink-0">
                <button
                  @click.stop="toggleGame(game)"
                  :title="`${isWishlisted(game) ? 'Set up' : runningGameIds.has(game.id) ? 'Stop' : 'Play'} ${game.title}`"
                  class="play-btn ui-play-btn ui-action-btn px-2.5 py-1.5 rounded-lg text-xs font-bold"
                >
                  <IconPlayerStopFilled
                    v-if="!isWishlisted(game) && runningGameIds.has(game.id)"
                    class="ui-action-icon"
                  /><IconPlayerPlayFilled v-else-if="!isWishlisted(game)" class="ui-action-icon" />{{
                    isWishlisted(game) ? "Set up" : runningGameIds.has(game.id) ? "Stop" : "Play"
                  }}
                </button>
                <button
                  v-if="hasAdditionalLaunchTargets(game)"
                  @click.stop="toggleLaunchTargetMenu(game)"
                  class="target-menu-toggle ui-action-btn px-1 py-1.5 rounded-lg"
                  :title="`Choose launch target for ${game.title}`"
                  :aria-expanded="openLaunchTargetMenuId === game.id"
                >
                  <IconChevronDown class="ui-action-icon" />
                </button>
                <div
                  v-if="openLaunchTargetMenuId === game.id && !runningGameIds.has(game.id)"
                  class="launch-target-menu"
                  @click.stop
                >
                  <button
                    v-for="target in getEffectiveLaunchTargets(game)"
                    :key="target.id"
                    class="launch-target-menu-item"
                    @click.stop="launchSelectedTarget(game, target)"
                  >
                    {{ target.label }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <Teleport to="body">
        <div
          v-if="contextMenu"
          ref="contextMenuElement"
          role="dialog"
          :aria-label="`Actions for ${contextMenu.game.title}`"
          class="game-context-menu"
          :style="{ left: `${toCssPx(contextMenu.x)}px`, top: `${toCssPx(contextMenu.y)}px` }"
          @click.stop
          @contextmenu.prevent
          @keydown="handleContextMenuKeyboard"
        >
          <p
            class="truncate px-2 py-1 text-xs font-bold"
            :title="contextMenu.game.title"
          >
            {{ contextMenu.game.title }}
          </p>
          <template v-if="contextMenuView === 'actions'">
            <button @click="runContextAction('play')">
              {{ isWishlisted(contextMenu.game) ? "Set up" : runningGameIds.has(contextMenu.game.id) ? "Stop" : "Play" }}
            </button>
            <template v-if="runningGameIds.has(contextMenu.game.id) && contextTools.length">
              <div role="separator" class="my-1 border-t" style="border-color: var(--border)" />
              <button v-for="tool in contextTools" :key="tool.id" type="button" class="context-item" :disabled="loadingCompanionId !== null" @click="launchContextTool(tool)">{{ tool.name }}</button>
              <div role="separator" class="my-1 border-t" style="border-color: var(--border)" />
            </template>
            <button
              @click="runContextAction('folder')"
              :disabled="!gameFolder(contextMenu.game.exe_path)"
            >
              Open folder
            </button>
            <button
              v-if="gamePageUrl(contextMenu.game)"
              @click="runContextAction('thread')"
            >
              {{ contextMenu.game.f95_url ? "Open F95 thread" : "Open store page" }}
            </button>
            <button
              @click="runContextAction('check')"
              :disabled="!contextMenu.game.f95_url || updatingId !== null"
            >
              Check update
            </button>
            <button
              v-if="hasAvailableUpdate({ ...contextMenu.game, skipped_version: contextMenu.game.skipped_version })"
              @click="runContextAction('mark-updated')"
              :disabled="markingUpdatedIds.has(contextMenu.game.id)"
            >
              Mark as updated to {{ contextMenu.game.latest_version }}
            </button>
            <button
              v-if="isUpdateSkipped(contextMenu.game) || hasAvailableUpdate({ ...contextMenu.game, skipped_version: contextMenu.game.skipped_version })"
              class="min-h-11"
              @click="runContextAction('skip-version')"
            >{{ isUpdateSkipped(contextMenu.game) ? `Stop skipping ${contextMenu.game.skipped_version}` : `Skip version ${contextMenu.game.latest_version}` }}</button>
            <label
              class="flex items-center justify-between gap-2 px-2 py-1.5 text-xs"
              >Set status
              <select
                :value="
                  normalizePlayStatus(
                    contextMenu.game.play_status,
                    contextMenu.game.status,
                  )
                "
                @change="
                  runContextAction(
                    'status',
                    ($event.target as HTMLSelectElement).value,
                  )
                "
                class="min-w-0 max-w-36 rounded px-1 py-1"
                style="background: var(--bg-raised); color: var(--text-primary)"
              >
                <option
                  v-for="status in allStatuses"
                  :key="status.value"
                  :value="status.value"
                >
                  {{ status.label }}
                </option>
              </select>
            </label>
            <button @click="contextMenuView = 'collections'">
              Collections…
            </button>
            <button @click="runContextAction('favorite')">
              {{ contextMenu.game.is_favorite ? "Unfavorite" : "Favorite" }}
            </button>
            <button
              @click="runContextAction('remove')"
              style="color: var(--danger-text)"
            >
              Remove
            </button>
          </template>
          <template v-else>
            <button
              class="flex items-center gap-2"
              @click="contextMenuView = 'actions'"
            >
              <IconArrowLeft class="w-3.5 h-3.5" />
              Back
            </button>
            <div class="max-h-64 overflow-y-auto">
              <p
                v-if="!userCollections.length"
                class="px-2 py-1.5 text-xs"
                style="color: var(--text-muted)"
              >
                No collections yet.
              </p>
              <label
                v-for="name in userCollections"
                :key="name"
                class="flex items-center gap-2 px-2 py-1.5 text-xs cursor-pointer rounded ui-hover-surface"
                :style="
                  gameInCollection(contextMenu.game, name)
                    ? 'color: var(--text-primary)'
                    : 'color: var(--text-secondary)'
                "
              >
                <input
                  type="checkbox"
                  :checked="gameInCollection(contextMenu.game, name)"
                  @change="toggleGameCollection(contextMenu.game, name)"
                  class="rounded"
                />
                <span class="truncate">{{ name }}</span>
              </label>
            </div>
            <form
              class="flex gap-1 px-1 pt-1"
              @submit.prevent="
                contextCreateForGame(contextMenu.game)
              "
            >
              <input
                v-model="contextNewCollectionName"
                type="text"
                aria-label="New collection name"
                placeholder="New collection"
                class="library-search-input min-w-0 flex-1 rounded px-1.5 py-1 text-xs"
              />
              <button
                type="submit"
                class="rounded px-2 py-1 text-xs font-semibold border"
                style="color: var(--text-secondary); border-color: var(--border)"
              >
                Add
              </button>
            </form>
            <p
              v-if="contextNewCollectionError"
              role="alert"
              class="px-2 py-1 text-xs"
              style="color: var(--danger-text)"
            >
              {{ contextNewCollectionError }}
            </p>
          </template>
        </div>

        <!-- Collection tab menu -->
        <div
          v-if="collectionTabMenu"
          ref="collectionTabMenuElement"
          role="menu"
          :aria-label="`Actions for collection ${collectionTabMenu.name}`"
          class="game-context-menu"
          :style="{
            left: `${toCssPx(collectionTabMenu.x)}px`,
            top: `${toCssPx(collectionTabMenu.y)}px`,
          }"
          @click.stop
          @contextmenu.prevent
        >
          <p class="truncate px-2 py-1 text-xs font-bold" :title="collectionTabMenu.name">
            {{ collectionTabMenu.name }}
          </p>
          <button role="menuitem" @click="startCollectionRename(collectionTabMenu.name)">
            Rename
          </button>
          <button
            role="menuitem"
            style="color: var(--danger-text)"
            @click="deleteCollectionFromTab(collectionTabMenu.name)"
          >
            Delete
          </button>
        </div>
      </Teleport>

      <ImportFolderModal v-model="showImportModal" />
      <AddGameModal
        v-model="showAddModal"
        :saving="addingGame"
        @save="handleAddGame"
      />
      <GameDetailModal
        v-model="showDetailModal"
        :game="selectedGame"
        :is-running="!!selectedGame && runningGameIds.has(selectedGame.id)"
        :update-check-state="modalUpdateState"
        @updated="handleGameUpdated"
        @deleted="handleGameDeleted"
        @launch="launchGameFast"
        @stop="stopGame"
        @check-updates="handleModalUpdateCheck"
        @targets-changed="handleLaunchTargetsChanged"
      />
    </div>
    <TagColorPicker v-if="tagColorPicker" :key="tagColorPicker.tag" :tag="tagColorPicker.tag" :anchor="tagColorPicker.anchor" @close="tagColorPicker = null" />
  </div>
</template>

<style scoped>
.card-size-control { accent-color: var(--brand); }
.card-size-control:focus-visible { outline: 2px solid var(--brand); outline-offset: 4px; }

.library-cards--grid {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, calc(220px * var(--card-scale, 1))), 1fr));
}
.library-cards--compact {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, calc(180px * var(--card-scale, 1))), 1fr));
}
.library-cards--banner {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, calc(420px * var(--card-scale, 1))), 1fr));
}
.banner-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  text-overflow: ellipsis;
  overflow: hidden;
}
/* Hidden only visually: opacity keeps Play clickable and focusable, so a click
   that arrives before :hover applies still hits the button, not the card. */
.library-cards--banner .banner-reveal {
  opacity: 0;
  transition: opacity 0.15s ease;
}
.library-cards--banner .game-card:hover .banner-reveal,
.library-cards--banner .game-card:focus-within .banner-reveal,
.library-cards--banner .game-card.is-running .banner-reveal,
.library-cards--banner .game-card.is-menu-open .banner-reveal {
  opacity: 1;
}
@media (hover: none) {
  .library-cards--banner .banner-reveal {
    opacity: 1;
  }
}
/* Play sits bottom-left here, so the chooser opens rightward from it. */
.library-cards--banner .launch-target-menu {
  right: auto;
  left: 0;
}
.game-card:focus-visible,
.library-table button:focus-visible,
.game-context-menu :is(button, select):focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 3px;
}
.metadata-pending {
  background: color-mix(in srgb, var(--bg-surface) 82%, transparent);
  color: var(--text-secondary);
}

.game-card.is-running {
  border-color: var(--brand);
  box-shadow:
    0 0 0 2px var(--brand),
    var(--shadow-card);
}
.running-label,
.collection-count {
  background: var(--brand);
  color: var(--on-brand);
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  font-size: 0.65rem;
  font-weight: 700;
}
.collections-tab-strip {
  scrollbar-width: thin;
}
.collection-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.75rem;
  border: 1px solid transparent;
  border-radius: 0.5rem;
  font-size: 0.875rem;
  font-weight: 500;
  white-space: nowrap;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.collection-tab:not(.collection-tab-add) {
  border-color: var(--border);
  background: var(--bg-surface);
  color: var(--text-secondary);
}
.collection-tab[aria-selected="true"] {
  border-color: var(--brand-deep);
  background: var(--brand-glow-strong);
  color: var(--text-primary);
}
.collection-tab:hover {
  background: var(--bg-overlay);
  color: var(--text-primary);
}
.collection-tab:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.collection-tab-add {
  color: var(--text-muted);
}
.collection-tab .collection-count {
  background: var(--bg-overlay);
  color: var(--text-secondary);
}
.collection-tab[aria-selected="true"] .collection-count {
  background: var(--brand);
  color: var(--on-brand);
}
.wishlist-pill,
.finished-pill {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 0.65rem;
  font-weight: 700;
  line-height: 1.2;
}
.finished-pill {
  color: var(--success-text);
}
.card-indicator {
  background: var(--overlay-scrim-strong);
  border-radius: 0.3rem;
  padding: 0.25rem;
}
.library-table-wrap {
  overflow-x: auto;
  container-type: inline-size;
}
/* Thresholds tuned to the list width at 1200x800 with filters open. */
@container (max-width: 780px) {
  .col-engine {
    display: none;
  }
}
@container (max-width: 690px) {
  .col-last-played {
    display: none;
  }
}
.library-table {
  width: 100%;
  border-collapse: collapse;
  color: var(--text-secondary);
  background: var(--bg-surface);
  font-size: 0.8rem;
}
.library-table :is(th, td) {
  padding: 0.4rem 0.5rem;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
/* Fits the longest built-in status ("Waiting For Update"); longer custom names truncate. */
.library-table-clip {
  max-width: 10.5rem;
  overflow: hidden;
  text-overflow: ellipsis;
}
.library-table th {
  color: var(--text-muted);
  font-size: 0.7rem;
}
.library-table-title {
  max-width: 16rem;
  min-width: 10rem;
  color: var(--text-primary);
}
.game-row {
  height: 48px;
  cursor: pointer;
}
.game-row:hover {
  background: var(--bg-raised);
}
.game-row.is-running {
  background: var(--brand-glow);
  box-shadow: inset 3px 0 var(--brand);
}
.table-target-select {
  width: 1.5rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  border-radius: 0.25rem;
}
.game-context-menu {
  position: fixed;
  z-index: 100;
  width: 15rem;
  max-width: calc(var(--app-vw) - 16px);
  max-height: calc(var(--app-vh) - 16px);
  overflow-y: auto;
  padding: 0.4rem;
  border: 1px solid var(--border-hover);
  border-radius: 0.5rem;
  background: var(--bg-surface);
  color: var(--text-primary);
  box-shadow: var(--shadow-card);
}
.game-context-menu > button {
  display: block;
  width: 100%;
  padding: 0.45rem 0.5rem;
  border-radius: 0.3rem;
  text-align: left;
  font-size: 0.8rem;
}
.game-context-menu > button:hover {
  background: var(--bg-raised);
}
.game-context-menu button:disabled {
  opacity: 0.5;
  cursor: wait;
}
.game-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.game-card:hover {
  border-color: var(--brand);
  box-shadow: 0 4px 20px var(--brand-glow);
  transform: translateY(-2px);
}

:global(.motion-off) .game-card:not(.is-running):hover {
  border-color: var(--border);
  box-shadow: var(--shadow-card);
  transform: none;
}

.target-menu-toggle {
  margin-left: 0.35rem;
  background: var(--bg-raised);
  border: 1px solid var(--border-hover);
  color: var(--text-secondary);
}

.target-menu-toggle:hover {
  background: var(--bg-overlay);
  color: var(--text-primary);
}

.launch-target-menu {
  position: absolute;
  z-index: 30;
  right: 0;
  bottom: calc(100% + 0.5rem);
  min-width: 11rem;
  max-width: 16rem;
  padding: 0.35rem;
  border: 1px solid var(--border-hover);
  border-radius: 0.5rem;
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
}

.launch-target-menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  width: 100%;
  padding: 0.45rem 0.55rem;
  border-radius: 0.4rem;
  color: var(--text-primary);
  font-size: 0.75rem;
  text-align: left;
}

.launch-target-menu-item:hover {
  background: var(--bg-raised);
}

.library-primary-btn {
  transition: transform 0.15s ease, filter 0.15s ease, box-shadow 0.15s ease;
}

.filters-pane {
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
}

.filters-pane-collapsed {
  border-right: 0 solid transparent;
}

.library-search-input {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
.library-search-input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.filters-checkbox {
  border-color: var(--border);
  background: transparent;
}

.filters-checkbox:focus {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}

.compact-image-overlay {
  background: linear-gradient(
    to top,
    rgba(0, 0, 0, 0.85) 0%,
    rgba(0, 0, 0, 0.58) 42%,
    rgba(0, 0, 0, 0.24) 70%,
    rgba(0, 0, 0, 0.08) 100%
  );
}

.compact-title {
  color: #f8fafc;
  text-shadow:
    0 1px 2px rgba(0, 0, 0, 0.95),
    0 0 8px rgba(0, 0, 0, 0.85);
  -webkit-text-stroke: 0.4px rgba(0, 0, 0, 0.65);
  max-height: 2.6em;
  overflow: hidden;
}

.compact-target-toggle {
  margin-left: 0.25rem;
  background: rgba(17, 24, 39, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.28);
  color: #f8fafc;
}

.compact-target-toggle:hover {
  background: rgba(17, 24, 39, 0.78);
  border-color: rgba(255, 255, 255, 0.42);
}

/* Sits on cover art in both themes, so it keeps dark-on-image colors. */
.rating-badge {
  background: var(--overlay-scrim-strong);
  border: 1px solid var(--overlay-border);
  color: #facc15;
}

.rating-badge svg {
  fill: currentColor;
}

.update-overlay-btn {
  background: var(--overlay-scrim-strong);
  border: 1px solid var(--overlay-border);
  color: var(--overlay-control-text);
  /* Hidden only visually: it stays clickable so a click that arrives with the
     pointer (before :hover applies) still hits the button, not the card. */
  opacity: 0;
  transition: opacity 0.18s ease, background-color 0.18s ease, color 0.18s ease, transform 0.18s ease;
}

.game-card:hover .update-overlay-btn,
.game-card:focus-within .update-overlay-btn,
/* Every card's control is disabled while any check runs; only the card being
   checked keeps its spinner visible at rest. */
.update-overlay-btn.update-check-running {
  opacity: 1;
}

@media (hover: none) {
  .update-overlay-btn {
    opacity: 1;
  }
}

.update-overlay-btn:hover {
  background: var(--overlay-scrim);
  color: var(--overlay-control-text);
}

.update-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  background: var(--success-bg);
  border: 1px solid var(--success-border);
  color: var(--success-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  white-space: nowrap;
}

/* Sits on cover art, so it keeps a solid fill for contrast. */
.update-pill-on-art {
  background: var(--success-text);
  border-color: transparent;
  color: var(--text-inverse);
}

.library-sort-btn {
  transition: background-color 0.15s ease, color 0.15s ease, transform 0.15s ease;
}

.unplayed-label {
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.unplayed-on-art {
  background: var(--bg-surface);
}

.filter-tag-btn {
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
</style>
