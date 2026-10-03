<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  IconChevronDown,
  IconArrowUp,
  IconClock,
  IconColumns2Filled,
  IconDeviceGamepad2,
  IconDeviceGamepad2Filled,
  IconFilterFilled,
  IconLibraryPlus,
  IconLayoutGridFilled,
  IconLayoutListFilled,
  IconFolderPlus,
  IconLoader2,
  IconPhotoFilled,
  IconPlayerStopFilled,
  IconPlayerPlayFilled,
  IconRefresh,
  IconZoom,
  IconStarFilled,
  IconX,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import type { GameRecord, LaunchMode, LaunchTarget } from "../services/api";
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
  clearLegacyLibraryViewState,
  normalizeLibraryViewState,
  readLibraryViewState,
  saveLibraryViewState,
  type FilterCollection,
  type FilterSections,
  type LayoutMode,
  type LibraryViewState,
  type SortDir,
  type SortField,
} from "../utils/libraryViewState";
import { notify, notifyError } from "../utils/toast";
import {
  compareLibraryGames,
  gameFolder,
  gameTags,
  hasAvailableUpdate,
  matchesGameSearch,
} from "../utils/libraryGames";

interface UpdateNotice {
  type: "" | "success" | "error";
  message: string;
}

interface ModalUpdateState extends UpdateNotice {
  running: boolean;
}

interface AddGamePayload {
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
let contextMenuTrigger: HTMLElement | null = null;

const tableColumns: Array<{ key: SortField; label: string }> = [
  { key: "title", label: "Title" },
  { key: "version", label: "Version" },
  { key: "play_status", label: "Status" },
  { key: "engine", label: "Engine" },
  { key: "playtime_seconds", label: "Playtime" },
  { key: "last_played", label: "Last played" },
];

const updateCount = computed(
  () => games.value.filter(hasAvailableUpdate).length,
);
const collections: Array<{
  value: FilterCollection;
  label: string;
  icon: typeof IconClock;
}> = [
  { value: "All", label: "All Games", icon: IconLayoutGridFilled },
  { value: "Favorites", label: "Favorites", icon: IconStarFilled },
  { value: "Updates available", label: "Updates available", icon: IconArrowUp },
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
const layoutMode = ref<LayoutMode>(DEFAULT_LIBRARY_VIEW_STATE.layoutMode);
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

const tagCounts = computed(() => {
  const counts = new Map<string, number>();
  games.value.forEach((game) =>
    gameTags(game).forEach((tag) =>
      counts.set(tag, (counts.get(tag) || 0) + 1),
    ),
  );
  return counts;
});
const uniqueTags = computed(() => [...tagCounts.value.keys()].sort());
const visibleTags = computed(() =>
  uniqueTags.value.filter((tag) =>
    tag.toLowerCase().includes(tagSearchQuery.value.trim().toLowerCase()),
  ),
);

const activeFilterCount = computed(
  () =>
    (filterCollection.value !== "All" ? 1 : 0) +
    (filterStatuses.value.length > 0 ? 1 : 0) +
    (filterEngines.value.length > 0 ? 1 : 0) +
    (filterTags.value.length > 0 ? 1 : 0),
);
const clearFilters = () => {
  searchQuery.value = "";
  filterStatuses.value = [];
  filterEngines.value = [];
  filterTags.value = [];
  filterCollection.value = "All";
};

const applyLibraryViewState = (state: LibraryViewState) => {
  layoutMode.value = state.layoutMode;
  sortBy.value = state.sortBy;
  sortDir.value = state.sortDir;
  filterCollection.value = state.filterCollection;
  filterStatuses.value = [...state.filterStatuses];
  filterEngines.value = [...state.filterEngines];
  filterTags.value = [...state.filterTags];
  isFiltersCollapsed.value = state.isFiltersCollapsed;
  filterSections.value = { ...state.filterSections };
};

const buildLibraryViewState = (): LibraryViewState =>
  normalizeLibraryViewState({
    layoutMode: layoutMode.value,
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    filterCollection: filterCollection.value,
    filterStatuses: filterStatuses.value,
    filterEngines: filterEngines.value,
    filterTags: filterTags.value,
    isFiltersCollapsed: isFiltersCollapsed.value,
    filterSections: filterSections.value,
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
  const restored = readLibraryViewState(localStorage);
  applyLibraryViewState(restored.state);

  if (restored.source === "legacy") {
    persistLibraryViewState();
    clearLegacyLibraryViewState(localStorage);
  }
};

const filteredGames = computed(() => {
  let result = [...games.value];
  if (searchQuery.value.trim())
    result = result.filter((game) =>
      matchesGameSearch(game, searchQuery.value),
    );
  if (filterCollection.value === "Favorites") {
    result = result.filter((g) => g.is_favorite);
  }
  if (filterCollection.value === "Updates available")
    result = result.filter(hasAvailableUpdate);
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
  if (filterEngines.value.length) {
    result = result.filter((g) =>
      filterEngines.value.includes(normalizeEngineFilterValue(g.engine)),
    );
  }
  if (filterTags.value.length) {
    result = result.filter((game) =>
      filterTags.value.every((tag) => gameTags(game).includes(tag)),
    );
  }
  result.sort((a, b) => compareLibraryGames(a, b, sortBy.value, sortDir.value));
  return result;
});

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
    !runningGameIds.value.has(game.id) && (game.launch_targets || []).length > 0
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
      notifyError(`Failed to launch game:\n\n${result.error}`);
    } else if (result?.success) {
      // Reconcile with the registry: HTML is untracked and short commands may already have exited.
      runningGameIds.value = new Set(await api.getRunningGames());
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

const toggleGame = (game: GameRecord) => runningGameIds.value.has(game.id) ? stopGame(game.id) : launchGameFast(game);

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
    if (action === "import" && queryUrl) {
      await loadGames();
      showAddModal.value = true;
    }
    // Ctrl+N from any view (App.vue).
    if (action === "add") {
      showAddModal.value = true;
      void router.replace({ path: "/", query: {} });
    }
    if (action === "open" && queryUrl) {
      // Fetch directly: on mount, the view's own loadGames() supersedes one awaited here.
      const match = ((await api.getGames()) || []).find((g) =>
        urlsMatchByThreadIdentity(g.f95_url, queryUrl),
      );
      if (match) openDetail(match);
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
  else notify(feedback.message, "success");
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

const closeContextMenu = (restoreFocus = true) => {
  if (!contextMenu.value) return;
  contextMenu.value = null;
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
  await nextTick();
  if (!contextMenu.value || !contextMenuElement.value) return;
  const menu = contextMenuElement.value;
  contextMenu.value.x = Math.max(
    8,
    Math.min(contextMenu.value.x, window.innerWidth - menu.offsetWidth - 8),
  );
  contextMenu.value.y = Math.max(
    8,
    Math.min(contextMenu.value.y, window.innerHeight - menu.offsetHeight - 8),
  );
  menu.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
};

const handleContextMenuKeyboard = (event: KeyboardEvent) => {
  if ((event.target as HTMLElement).tagName === "SELECT" && event.key !== "Tab")
    return;
  const controls = [
    ...contextMenuElement.value!.querySelectorAll<HTMLElement>(
      "button:not(:disabled), select",
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
    "play" | "folder" | "thread" | "check" | "favorite" | "remove" | "status",
  status = "",
) => {
  const game = contextMenu.value?.game;
  if (!game) return;
  closeContextMenu();
  try {
    if (action === "play") return await toggleGame(game);
    if (action === "check") return await checkUpdate(game);
    if (
      action === "remove" &&
      !confirm(`Remove "${game.title}" from your library?`)
    )
      return;
    const result =
      action === "folder"
        ? await api.openFolder(gameFolder(game.exe_path))
        : action === "thread"
          ? await api.openInBrowser(game.f95_url || "")
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
  closeContextMenu(false);
};
const handleLibraryScroll = (event: Event) => {
  if (!contextMenuElement.value?.contains(event.target as Node))
    closeContextMenu(false);
};
const handleLibraryKeyboard = (event: KeyboardEvent) => {
  if (contextMenu.value && event.key === "Escape") {
    event.preventDefault();
    closeContextMenu();
    return;
  }
  if (showAddModal.value || showImportModal.value || showDetailModal.value || contextMenu.value) return;
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

const formatLastPlayed = (value?: string): string => {
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
  if (!Number.isInteger(gameId) || !Number.isFinite(delta) || delta <= 0) return;

  const game = games.value.find((g) => g.id === gameId);
  if (!game) return;

  game.playtime_seconds = (Number(game.playtime_seconds) || 0) + delta;
  game.last_played = new Date().toISOString();

  if (selectedGame.value && selectedGame.value.id === gameId) {
    selectedGame.value = {
      ...selectedGame.value,
      playtime_seconds: game.playtime_seconds,
      last_played: game.last_played,
    };
  }
};

watch(
  [
    layoutMode,
    sortBy,
    sortDir,
    filterCollection,
    filterStatuses,
    filterEngines,
    filterTags,
    isFiltersCollapsed,
    filterSections,
  ],
  () => {
    persistLibraryViewState();
  },
  { deep: true },
);

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

watch(showDetailModal, (open) => {
  if (!open) {
    clearModalUpdateState();
  }
});

onMounted(() => {
  restoreLibraryViewState();

  window.addEventListener("wlib-refresh-library", loadGames);
  window.addEventListener("wlib-playtime-tick", handlePlaytimeTick);
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("keydown", handleLibraryKeyboard);
  document.addEventListener("scroll", handleLibraryScroll, true);
  window.addEventListener("resize", handleDocumentClick);
  onWebviewReady(() => {
    void loadGames();
    void api.getRunningGames().then((ids) => { runningGameIds.value = new Set(ids); }).catch(console.error);
    void api.getSettings().then((settings) => { customStatuses.value = settings.custom_play_statuses || []; }).catch(console.error);
  });
});

onUnmounted(() => {
  gamesRequest++;
  window.removeEventListener("wlib-refresh-library", loadGames);
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
    <!-- Smart Collections Sidebar -->
    <aside
      :inert="isFiltersCollapsed"
      :aria-hidden="isFiltersCollapsed"
      :class="[
        'filters-pane shrink-0 h-full collapse-width-transition',
        isFiltersCollapsed ? 'w-0 filters-pane-collapsed' : 'w-64',
      ]"
    >
      <div class="w-64 h-full flex flex-col overflow-y-auto">
        <div class="p-6 pb-2">
          <button
            @click="toggleFilterSection('collections')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Smart Collections</span>
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
              class="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ui-hover-surface"
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
                :value="s.value"
                v-model="filterStatuses"
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
                class="cursor-pointer hover:text-red-400 text-[10px] normal-case tracking-normal transition-colors"
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
                class="filter-tag-btn px-2.5 py-1 rounded-full text-[11px] font-medium"
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
                class="cursor-pointer hover:text-red-400 text-[10px] normal-case tracking-normal transition-colors"
                style="color: var(--text-muted)"
              >
                Clear
              </button>
            </div>
            <div class="flex flex-wrap gap-1.5 max-h-64 overflow-y-auto">
              <button
                v-for="tag in visibleTags"
                :key="tag"
                @click="toggleFilter(filterTags, tag)"
                :aria-pressed="filterTags.includes(tag)"
                class="filter-tag-btn px-2.5 py-1 rounded-full text-[11px] font-medium"
                :style="
                  filterTags.includes(tag)
                    ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                    : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'
                "
              >
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
              color: var(--text-inverse);
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
        </div>
      </header>

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
              class="absolute -top-1.5 -right-1.5 min-w-5 h-5 rounded-full px-1 text-[10px] font-bold flex items-center justify-center"
              style="background: var(--brand); color: var(--text-inverse)"
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
                    v-if="game.cover_image_path"
                    :src="game.cover_image_path"
                    alt=""
                    class="w-8 h-8 rounded object-cover shrink-0"
                  />
                  <button
                    @click.stop="openDetail(game)"
                    class="truncate text-left font-semibold"
                    :title="game.title"
                  >
                    {{ game.title }}
                  </button>
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
                <div class="flex flex-wrap items-center gap-1">
                  <span class="font-mono text-xs">{{
                    game.version || "Unknown"
                  }}</span>
                  <button
                    v-if="hasAvailableUpdate(game)"
                    @click.stop="markAsUpdated(game)"
                    :disabled="markingUpdatedIds.has(game.id)"
                    class="update-version-badge rounded px-1.5 py-0.5 text-xs"
                    :title="`Mark as updated to ${game.latest_version}`"
                    :aria-label="`Mark ${game.title} as updated to ${game.latest_version}`"
                  >
                    ⬆ {{ game.latest_version }}
                  </button>
                </div>
              </td>
              <td>
                <span
                  class="ui-status-inline whitespace-nowrap"
                  :class="getGamePlayStatusMeta(game).toneClass"
                  ><component
                    :is="getGamePlayStatusMeta(game).icon"
                    class="ui-status-icon"
                  />{{ getGamePlayStatusMeta(game).label }}</span
                >
              </td>
              <td class="text-xs">{{ game.engine || "Unknown" }}</td>
              <td class="whitespace-nowrap text-xs">
                {{ formatPlaytime(game.playtime_seconds) }}
              </td>
              <td
                class="whitespace-nowrap text-xs"
                :title="game.last_played || 'Never'"
              >
                {{ formatLastPlayed(game.last_played) }}
              </td>
              <td>
                <div class="flex items-center gap-1">
                  <button
                    @click.stop="toggleGame(game)"
                    :title="`${runningGameIds.has(game.id) ? 'Stop' : 'Play'} ${game.title}`"
                    class="play-btn rounded-md p-1.5"
                  >
                    <IconPlayerStopFilled
                      v-if="runningGameIds.has(game.id)"
                      class="w-4 h-4"
                    /><IconPlayerPlayFilled v-else class="w-4 h-4" />
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
        :class="
          layoutMode === 'compact'
            ? 'library-cards--compact'
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
            layoutMode === 'compact' ? 'aspect-[16/9]' : 'flex flex-col',
            { 'is-running': runningGameIds.has(game.id) },
          ]"
        >
          <div
            class="flex items-center justify-center relative shadow-inner shrink-0 rounded-t-xl"
            :class="
              layoutMode === 'compact'
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
              v-if="game.cover_image_path"
              :src="game.cover_image_path"
              :alt="game.title"
              class="absolute inset-0 w-full h-full object-cover object-top rounded-t-xl"
              :class="{ 'rounded-b-xl': layoutMode === 'compact' }"
            />
            <IconPhotoFilled
              v-else
              class="w-12 h-12"
              style="color: var(--border)"
            />
            <div
              v-if="game.metadata_pending"
              class="metadata-pending absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 text-xs font-medium"
              :class="layoutMode === 'compact' ? 'rounded-xl' : 'rounded-t-xl'"
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
              <button
                v-if="layoutMode === 'compact' && hasAvailableUpdate(game)"
                @click.stop="markAsUpdated(game)"
                :disabled="markingUpdatedIds.has(game.id)"
                class="update-version-badge compact-update-badge rounded px-1.5 py-0.5 text-xs"
                :title="`Mark as updated to ${game.latest_version}`"
                :aria-label="`Mark ${game.title} as updated to ${game.latest_version}`"
              >
                ⬆
              </button>
            </div>
            <button
              v-if="layoutMode !== 'compact' && game.f95_url"
              @click.stop="checkUpdate(game)"
              :disabled="updatingId !== null"
              :title="`Check updates for ${game.title}`"
              class="update-overlay-btn ui-icon-btn absolute top-2 right-2 p-1.5 rounded-lg"
            >
              <IconRefresh
                v-if="updatingId !== game.id"
                class="ui-action-icon"
              /><IconLoader2 v-else class="ui-action-icon animate-spin" />
            </button>
            <span
              v-if="layoutMode !== 'compact' && game.rating"
              class="rating-badge absolute bottom-2 left-2 text-xs font-bold px-2 py-0.5 rounded flex items-center gap-1"
              ><IconStarFilled class="w-3 h-3" />{{ game.rating }}</span
            >
            <div
              v-if="layoutMode === 'compact'"
              class="compact-image-overlay absolute inset-0 rounded-xl"
            ></div>
            <div
              v-if="layoutMode === 'compact'"
              class="absolute inset-x-0 bottom-0 z-10 p-2 flex items-end justify-between gap-2"
            >
              <h3
                class="compact-title text-sm font-bold leading-tight"
                :title="game.title"
              >
                {{ game.title }}
              </h3>
              <div class="relative flex shrink-0">
                <button
                  @click.stop="toggleGame(game)"
                  class="compact-play-btn rounded-md p-2"
                  :title="`${runningGameIds.has(game.id) ? 'Stop' : 'Play'} ${game.title}`"
                >
                  <IconPlayerStopFilled
                    v-if="runningGameIds.has(game.id)"
                    class="w-3.5 h-3.5"
                  /><IconPlayerPlayFilled v-else class="w-3.5 h-3.5" />
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
            v-if="layoutMode !== 'compact'"
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
              <button
                v-if="hasAvailableUpdate(game)"
                @click.stop="markAsUpdated(game)"
                :disabled="markingUpdatedIds.has(game.id)"
                class="update-version-badge rounded px-1.5 py-0.5 font-mono"
                :title="`Mark as updated to ${game.latest_version}`"
                :aria-label="`Mark ${game.title} as updated to ${game.latest_version}`"
              >
                ⬆ {{ game.latest_version }}
              </button>
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
                  :title="`${runningGameIds.has(game.id) ? 'Stop' : 'Play'} ${game.title}`"
                  class="play-btn ui-action-btn px-2.5 py-1.5 rounded-lg text-xs font-bold"
                >
                  <IconPlayerStopFilled
                    v-if="runningGameIds.has(game.id)"
                    class="ui-action-icon"
                  /><IconPlayerPlayFilled v-else class="ui-action-icon" />{{
                    runningGameIds.has(game.id) ? "Stop" : "Play"
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
          :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
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
          <button @click="runContextAction('play')">
            {{ runningGameIds.has(contextMenu.game.id) ? "Stop" : "Play" }}
          </button>
          <button
            @click="runContextAction('folder')"
            :disabled="!gameFolder(contextMenu.game.exe_path)"
          >
            Open folder
          </button>
          <button
            @click="runContextAction('thread')"
            :disabled="!contextMenu.game.f95_url"
          >
            Open F95 thread
          </button>
          <button
            @click="runContextAction('check')"
            :disabled="!contextMenu.game.f95_url || updatingId !== null"
          >
            Check update
          </button>
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
          <button @click="runContextAction('favorite')">
            {{ contextMenu.game.is_favorite ? "Unfavorite" : "Favorite" }}
          </button>
          <button
            @click="runContextAction('remove')"
            style="color: var(--danger-text)"
          >
            Remove
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
  </div>
</template>

<style scoped>
.library-cards--grid {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr));
}
.library-cards--compact {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 180px), 1fr));
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
  color: var(--text-inverse);
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  font-size: 0.65rem;
  font-weight: 700;
}
.card-indicator {
  background: var(--overlay-scrim-strong);
  border-radius: 0.3rem;
  padding: 0.25rem;
}
.library-table-wrap {
  overflow-x: auto;
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
  max-width: calc(100vw - 16px);
  max-height: calc(100vh - 16px);
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
.game-context-menu button:disabled,
.update-version-badge:disabled {
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

.play-btn {
  background: var(--brand);
  color: var(--text-inverse);
  box-shadow: var(--shadow-brand);
  transition: transform 0.15s ease, filter 0.15s ease, box-shadow 0.15s ease;
}
.play-btn:hover {
  filter: brightness(1.15);
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
  accent-color: var(--brand);
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

.compact-play-btn {
  background: rgba(17, 24, 39, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.28);
  color: #f8fafc;
  transition: transform 0.15s ease, background-color 0.15s ease, border-color 0.15s ease;
}

.compact-play-btn:hover {
  background: rgba(17, 24, 39, 0.78);
  border-color: rgba(255, 255, 255, 0.42);
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
  transition: opacity 0.18s ease, background-color 0.18s ease, color 0.18s ease, transform 0.18s ease;
}

.update-overlay-btn:hover {
  background: var(--overlay-scrim);
  color: var(--overlay-control-text);
}

.update-version-badge {
  background: var(--success-bg);
  border: 1px solid var(--success-border);
  color: var(--success-text);
}

.compact-update-badge {
  background: var(--success-text);
  color: var(--text-inverse);
}

.library-sort-btn {
  transition: background-color 0.15s ease, color 0.15s ease, transform 0.15s ease;
}

.filter-tag-btn {
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
</style>
