<script setup>
import { ref, watch, computed } from "vue";
import { api } from "../../services/api";

const props = defineProps({
  modelValue: Boolean,
  game: Object,
});
const emit = defineEmits(["update:modelValue", "updated", "deleted", "launch"]);

// Editable fields
const title = ref("");
const exePath = ref("");
const f95Url = ref("");
const version = ref("");
const developer = ref("");
const commandLineArgs = ref("");
const coverImage = ref("");
const status = ref("");
const tags = ref([]);
const engine = ref("");
const newTag = ref("");
const latestVersion = ref("");
const runJapaneseLocale = ref(false);
const runWayland = ref(false);
const autoInjectCe = ref(false);
const customPrefix = ref("");
const protonVersion = ref("");
const useCustomPrefix = ref(false);
const availableRunners = ref([]);
const ceInstalled = ref(false);

// F95Zone rating (read-only, from scraper)
const f95Rating = ref("");

// Personal ratings (0-5 scale)
const ratingGraphics = ref(0);
const ratingStory = ref(0);
const ratingFappability = ref(0);
const ratingGameplay = ref(0);

const saving = ref(false);
const deleting = ref(false);

const statuses = [
  { value: "", label: "Not Started" },
  { value: "completed", label: "✅ Completed" },
  { value: "in_progress", label: "🎮 In Progress" },
  { value: "replaying", label: "🔄 Replaying" },
  { value: "waiting_update", label: "⏳ Waiting for Update" },
  { value: "abandoned", label: "🚫 Abandoned" },
];

const averagePersonalRating = computed(() => {
  const sum =
    ratingGraphics.value +
    ratingStory.value +
    ratingFappability.value +
    ratingGameplay.value;
  const avg = sum / 4;
  return avg > 0 ? avg.toFixed(1) : "—";
});

const hasUpdate = computed(() => {
  return (
    latestVersion.value &&
    version.value &&
    latestVersion.value.trim() !== version.value.trim()
  );
});

// Sync props → local refs when game changes
watch(
  () => props.game,
  (g) => {
    if (g) {
      title.value = g.title || "";
      exePath.value = g.exe_path || "";
      f95Url.value = g.f95_url || "";
      version.value = g.version || "";
      developer.value = g.developer || "";
      commandLineArgs.value = g.command_line_args || "";
      coverImage.value = g.cover_image_path || "";
      status.value = g.status || "";
      engine.value = g.engine || "";
      runJapaneseLocale.value = g.run_japanese_locale ? true : false;
      runWayland.value = g.run_wayland ? true : false;
      autoInjectCe.value = g.auto_inject_ce ? true : false;
      customPrefix.value = g.custom_prefix || "";
      protonVersion.value = g.proton_version || "";
      useCustomPrefix.value = !!g.custom_prefix || !!g.proton_version;
      if (typeof g.tags === "string" && g.tags) {
        tags.value = g.tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean);
      } else if (Array.isArray(g.tags)) {
        tags.value = [...g.tags];
      } else {
        tags.value = [];
      }
      latestVersion.value = g.latest_version || "";
      f95Rating.value = g.rating || "";

      ratingGraphics.value = g.rating_graphics || 0;
      ratingStory.value = g.rating_story || 0;
      ratingFappability.value = g.rating_fappability || 0;
      ratingGameplay.value = g.rating_gameplay || 0;
    }
  },
  { immediate: true },
);

watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      try {
        const ceCheck = await api.isCheatEngineInstalled();
        ceInstalled.value = ceCheck?.installed || false;
      } catch (e) {
        console.error("Failed to check cheat engine status", e);
      }
      try {
        const res = await api.getAvailableRunners();
        if (res?.success) {
          availableRunners.value = res.runners;
        }
      } catch (e) {
        console.error("Failed to get runners", e);
      }
    }
  },
);

const close = () => {
  emit("update:modelValue", false);
};

const browseExe = async () => {
  const p = await api.browseFile();
  if (p) exePath.value = p;
};

const browseCustomPrefix = async () => {
  const p = await api.browseDirectory();
  if (p) customPrefix.value = p;
};

const installDepsToPrefix = async () => {
  if (!customPrefix.value && !protonVersion.value) return;
  alert("Dependencies installation has started in the background. It may take several minutes to complete.");
  await api.installRpgmakerDependencies(customPrefix.value, protonVersion.value);
};

const save = async () => {
  if (!props.game) return;
  saving.value = true;
  try {
    await api.updateGame(props.game.id, {
      title: title.value,
      exe_path: exePath.value,
      f95_url: f95Url.value,
      version: version.value,
      developer: developer.value,
      command_line_args: commandLineArgs.value,
      cover_image_path: coverImage.value,
      status: status.value,
      tags: tags.value.join(", "),
      engine: engine.value,
      run_japanese_locale: runJapaneseLocale.value,
      run_wayland: runWayland.value,
      auto_inject_ce: autoInjectCe.value,
      custom_prefix: useCustomPrefix.value ? customPrefix.value : "",
      proton_version: useCustomPrefix.value ? protonVersion.value : "",
      latest_version: latestVersion.value,
      rating_graphics: ratingGraphics.value,
      rating_story: ratingStory.value,
      rating_fappability: ratingFappability.value,
      rating_gameplay: ratingGameplay.value,
    });
    emit("updated");
    close();
  } catch (e) {
    console.error("Failed to save game", e);
  } finally {
    saving.value = false;
  }
};

const deleteGame = async () => {
  if (
    !props.game ||
    !confirm("Are you sure you want to remove this game from your library?")
  )
    return;
  deleting.value = true;
  try {
    await api.deleteGame(props.game.id);
    emit("deleted");
    close();
  } catch (e) {
    console.error("Failed to delete", e);
  } finally {
    deleting.value = false;
  }
};

const launchGame = () => {
  if (props.game) {
    emit("launch", props.game);
  }
};

const saveResults = ref([]);
const searchingSaves = ref(false);
const showSavePanel = ref(false);

const findSaves = async () => {
  if (!props.game) return;
  searchingSaves.value = true;
  showSavePanel.value = true;
  try {
    const results = await api.findSaveFiles(
      props.game.exe_path,
      title.value,
      engine.value,
      useCustomPrefix.value ? customPrefix.value : "",
      useCustomPrefix.value ? protonVersion.value : ""
    );
    saveResults.value = results || [];
  } catch (e) {
    console.error("Failed to find saves", e);
    saveResults.value = [];
  } finally {
    searchingSaves.value = false;
  }
};

const openSaveFolder = async (path) => {
  try {
    await api.openFolder(path);
  } catch (e) {
    console.error("Failed to open folder", e);
  }
};

const addTag = () => {
  const t = newTag.value.trim();
  if (t && !tags.value.includes(t)) {
    tags.value.push(t);
  }
  newTag.value = "";
};

const removeTag = (tag) => {
  tags.value = tags.value.filter((t) => t !== tag);
};

const formatPlaytime = (seconds) => {
  if (!seconds) return "0.0 hrs";
  return (seconds / 3600).toFixed(1) + " hrs";
};

const formatLastPlayed = (dateString) => {
  if (!dateString) return "Never";
  return new Date(dateString).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric'
  });
};

const openInBrowser = async () => {
  if (f95Url.value) {
    await api.openInBrowser(f95Url.value);
  }
};
</script>

<template>
  <div
    v-if="modelValue && game"
    class="fixed inset-0 z-50 flex items-center justify-center p-4"
  >
    <!-- Overlay -->
    <div
      class="absolute inset-0 bg-black/70 backdrop-blur-sm"
      @click="close"
    ></div>

    <!-- Modal Content -->
    <div
      class="modal-content w-full max-w-2xl rounded-2xl shadow-2xl relative overflow-hidden transform transition-all flex flex-col max-h-[90vh]"
    >
      <!-- Header with Cover Image Background -->
      <div
        class="relative h-40 overflow-hidden"
        style="
          background: linear-gradient(
            135deg,
            var(--bg-raised),
            var(--bg-inset)
          );
        "
      >
        <img
          v-if="coverImage"
          :src="coverImage"
          class="absolute inset-0 w-full h-full object-cover opacity-40 blur-sm"
        />
        <div
          class="absolute inset-0"
          style="
            background: linear-gradient(
              to top,
              var(--bg-surface),
              transparent,
              transparent
            );
          "
        ></div>

        <div
          class="absolute bottom-4 left-6 right-6 flex items-end justify-between"
        >
          <div>
            <h3 class="text-xl font-bold text-white drop-shadow-lg">
              {{ title || "Untitled Game" }}
            </h3>
            <div class="flex items-center gap-2 mt-0.5">
              <p
                v-if="developer"
                class="text-sm"
                style="color: var(--text-secondary)"
              >
                by {{ developer }}
              </p>
              <span
                v-if="engine"
                class="text-xs font-bold px-2 py-0.5 rounded-md"
                style="
                  background: rgba(90, 57, 104, 0.3);
                  color: #b380cc;
                  border: 1px solid rgba(90, 57, 104, 0.4);
                "
                >{{ engine }}</span
              >
              <span
                v-if="game?.playtime_seconds"
                class="text-xs font-bold px-2 py-0.5 rounded-md text-gray-300"
                style="background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2);"
              >
                ⏱ {{ formatPlaytime(game.playtime_seconds) }}
              </span>
              <span
                v-if="game?.last_played"
                class="text-xs font-bold px-2 py-0.5 rounded-md text-gray-400"
                style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1);"
              >
                Last played: {{ formatLastPlayed(game.last_played) }}
              </span>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <div
              v-if="f95Rating"
              class="bg-black/60 backdrop-blur-md text-yellow-400 text-xs font-bold px-2.5 py-1 rounded-lg border border-white/10 flex items-center gap-1"
            >
              <svg class="w-3.5 h-3.5 fill-yellow-400" viewBox="0 0 20 20">
                <path
                  d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"
                />
              </svg>
              F95: {{ f95Rating }}
            </div>
            <div
              class="bg-black/60 backdrop-blur-md text-xs font-bold px-2.5 py-1 rounded-lg border border-white/10"
              style="color: var(--brand)"
            >
              You: {{ averagePersonalRating }}
            </div>
          </div>
        </div>

        <button
          @click="close"
          class="absolute top-3 right-3 text-gray-400 hover:text-white transition-colors bg-black/40 rounded-lg p-1.5 backdrop-blur-md"
        >
          <svg
            class="w-5 h-5"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>

      <!-- Scrollable Body -->
      <div class="p-6 overflow-y-auto space-y-6 flex-1">
        <!-- Status Selector -->
        <div class="flex flex-wrap gap-2">
          <button
            v-for="s in statuses"
            :key="s.value"
            @click="status = s.value"
            class="px-3 py-1.5 rounded-full text-xs font-semibold transition-all"
            :style="
              status === s.value
                ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'
            "
          >
            {{ s.label }}
          </button>
        </div>

        <!-- Game Info Fields -->
        <div class="grid grid-cols-2 gap-4">
          <div class="col-span-2">
            <label class="modal-label">Title</label>
            <input v-model="title" type="text" class="modal-input w-full" />
          </div>

          <div class="col-span-2">
            <label class="modal-label">Executable Path</label>
            <div class="flex gap-2">
              <input
                v-model="exePath"
                type="text"
                class="modal-input flex-1 font-mono"
              />
              <button @click="browseExe" class="modal-btn">Browse</button>
            </div>
          </div>

          <div>
            <label class="modal-label">Version</label>
            <input
              v-model="version"
              type="text"
              placeholder="v1.0"
              class="modal-input w-full font-mono"
              style="color: var(--brand)"
            />
          </div>

          <div>
            <label class="modal-label">Developer</label>
            <input v-model="developer" type="text" class="modal-input w-full" />
          </div>

          <div class="col-span-2">
            <label class="modal-label">F95Zone URL</label>
            <input
              v-model="f95Url"
              type="text"
              placeholder="https://f95zone.to/threads/..."
              class="modal-input w-full"
              style="color: var(--brand)"
            />
          </div>

          <div class="col-span-2">
            <label class="modal-label">Command Line Arguments</label>
            <input
              v-model="commandLineArgs"
              type="text"
              placeholder="gamemoderun %command% --fullscreen"
              class="modal-input w-full font-mono"
            />
          </div>

          <div
            class="col-span-2 flex items-center justify-between p-3 rounded-lg"
            style="
              background: var(--bg-raised);
              border: 1px solid var(--border);
            "
          >
            <div>
              <p class="text-sm font-medium" style="color: var(--text-primary)">
                Run with Japanese Locale
              </p>
              <p class="text-xs" style="color: var(--text-muted)">
                Enable this if the game has unreadable text (Mojibake) or
                crashes due to missing Japanese fonts.
              </p>
            </div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                v-model="runJapaneseLocale"
                class="sr-only peer"
              />
              <div
                class="toggle-track peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--brand)]"
              ></div>
            </label>
          </div>

          <div
            class="col-span-2 flex items-center justify-between p-3 rounded-lg"
            style="
              background: var(--bg-raised);
              border: 1px solid var(--border);
            "
          >
            <div>
              <p class="text-sm font-medium" style="color: var(--text-primary)">
                Run in Wayland Compatibility Mode
              </p>
              <p class="text-xs" style="color: var(--text-muted)">
                Enable this if your system uses Wayland and the game crashes or
                freezes on launch.
              </p>
            </div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                v-model="runWayland"
                class="sr-only peer"
              />
              <div
                class="toggle-track peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--brand)]"
              ></div>
            </label>
          </div>

          <div
            class="col-span-2 flex items-center justify-between p-3 rounded-lg transition-colors"
            :style="
              ceInstalled
                ? 'background: var(--bg-raised); border: 1px solid var(--border)'
                : 'background: var(--bg-inset); border: 1px solid var(--border); opacity: 0.6'
            "
          >
            <div>
              <p
                class="text-sm font-medium flex items-center gap-2"
                style="color: var(--text-primary)"
              >
                Auto-Launch & Inject Cheat Engine
                <span
                  v-if="!ceInstalled"
                  class="text-[10px] uppercase px-2 py-0.5 rounded font-bold"
                  style="
                    background: var(--bg-overlay);
                    color: var(--text-muted);
                    border: 1px solid var(--border);
                  "
                  >Not Installed</span
                >
              </p>
              <p class="text-xs mt-1" style="color: var(--text-muted)">
                Spawns Cheat Engine inside the same virtual Wine sandbox
                immediately after the game starts.
              </p>
            </div>
            <label
              class="relative inline-flex items-center"
              :class="ceInstalled ? 'cursor-pointer' : 'cursor-not-allowed'"
            >
              <input
                type="checkbox"
                v-model="autoInjectCe"
                :disabled="!ceInstalled"
                class="sr-only peer"
              />
              <div
                class="toggle-track peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-600"
              ></div>
            </label>
          </div>

          <div class="col-span-2">
            <label class="modal-label">Cover Image URL</label>
            <input
              v-model="coverImage"
              type="text"
              placeholder="https://..."
              class="modal-input w-full"
            />
          </div>

          <div>
            <label class="modal-label">Engine</label>
            <input
              v-model="engine"
              type="text"
              placeholder="Unity, RPGM, Ren'Py..."
              class="modal-input w-full"
              style="color: #b380cc"
            />
          </div>

          <!-- Advanced Launch Options -->
          <div class="col-span-2 mt-2">
            <div
              class="flex items-center justify-between p-3 rounded-lg"
              style="
                background: var(--bg-raised);
                border: 1px solid var(--border);
              "
            >
              <div>
                <p class="text-sm font-medium" style="color: var(--text-primary)">
                  Use Custom Wine Prefix & Proton Version
                </p>
                <p class="text-xs" style="color: var(--text-muted)">
                  Isolate this game's save files and dependencies from the global prefix.
                </p>
              </div>
              <label class="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  v-model="useCustomPrefix"
                  class="sr-only peer"
                />
                <div
                  class="toggle-track peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--brand)]"
                ></div>
              </label>
            </div>

            <div v-if="useCustomPrefix" class="mt-3 p-4 rounded-lg space-y-4" style="background: var(--bg-inset); border: 1px dashed var(--border);">
              <div>
                <label class="modal-label">Custom Prefix Path</label>
                <div class="flex gap-2">
                  <input
                    v-model="customPrefix"
                    type="text"
                    placeholder="/home/user/games/my_game_prefix"
                    class="modal-input flex-1 font-mono text-xs"
                  />
                  <button @click="browseCustomPrefix" class="modal-btn">Browse</button>
                </div>
              </div>

              <div>
                <label class="modal-label">Proton Version</label>
                <select
                  v-model="protonVersion"
                  class="modal-input w-full"
                >
                  <option value="">(Use Global Default)</option>
                  <option
                    v-for="runner in availableRunners"
                    :key="runner.path"
                    :value="runner.path"
                  >
                    {{ runner.name }}
                  </option>
                </select>
              </div>

              <div class="pt-2 border-t border-gray-700/50">
                <button
                  @click="installDepsToPrefix"
                  class="w-full text-xs font-medium px-4 py-2 rounded-lg transition-colors"
                  style="background: rgba(90, 57, 104, 0.2); color: #b380cc; border: 1px solid rgba(90, 57, 104, 0.4);"
                >
                  Install RPGMaker DLLs to this Prefix
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Personal Ratings Section -->
        <div>
          <h4
            class="text-sm font-bold mb-3 flex items-center gap-2"
            style="color: var(--text-secondary)"
          >
            <svg
              class="w-4 h-4"
              style="color: var(--brand)"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"
              />
            </svg>
            Your Ratings
          </h4>
          <div class="space-y-3">
            <div
              v-for="cat in [
                { label: '🎨 Graphics', model: 'ratingGraphics' },
                { label: '📖 Story', model: 'ratingStory' },
                { label: '🔥 Fappability', model: 'ratingFappability' },
                { label: '🎮 Gameplay', model: 'ratingGameplay' },
              ]"
              :key="cat.model"
              class="flex items-center gap-3"
            >
              <span
                class="text-xs w-28 shrink-0"
                style="color: var(--text-secondary)"
                >{{ cat.label }}</span
              >
              <div class="flex gap-1">
                <button
                  v-for="star in 5"
                  :key="star"
                  @click="
                    cat.model === 'ratingGraphics'
                      ? (ratingGraphics = star)
                      : cat.model === 'ratingStory'
                        ? (ratingStory = star)
                        : cat.model === 'ratingFappability'
                          ? (ratingFappability = star)
                          : (ratingGameplay = star)
                  "
                  class="transition-all hover:scale-110"
                >
                  <svg
                    class="w-5 h-5"
                    :class="[
                      star <=
                      (cat.model === 'ratingGraphics'
                        ? ratingGraphics
                        : cat.model === 'ratingStory'
                          ? ratingStory
                          : cat.model === 'ratingFappability'
                            ? ratingFappability
                            : ratingGameplay)
                        ? 'text-yellow-400 fill-yellow-400'
                        : 'fill-current',
                    ]"
                    :style="
                      star >
                      (cat.model === 'ratingGraphics'
                        ? ratingGraphics
                        : cat.model === 'ratingStory'
                          ? ratingStory
                          : cat.model === 'ratingFappability'
                            ? ratingFappability
                            : ratingGameplay)
                        ? 'color: var(--border)'
                        : ''
                    "
                    viewBox="0 0 20 20"
                  >
                    <path
                      d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"
                    />
                  </svg>
                </button>
              </div>
              <span
                class="text-xs font-mono ml-auto"
                style="color: var(--text-muted)"
                >{{
                  cat.model === "ratingGraphics"
                    ? ratingGraphics
                    : cat.model === "ratingStory"
                      ? ratingStory
                      : cat.model === "ratingFappability"
                        ? ratingFappability
                        : ratingGameplay
                }}/5</span
              >
            </div>
          </div>
        </div>

        <!-- Tags (editable) -->
        <div>
          <h4 class="modal-label mb-2">Tags</h4>
          <div class="flex flex-wrap gap-1.5 mb-2">
            <span
              v-for="tag in tags"
              :key="tag"
              class="text-xs px-2 py-0.5 rounded-full flex items-center gap-1"
              style="
                background: var(--bg-raised);
                border: 1px solid var(--border);
                color: var(--text-secondary);
              "
            >
              {{ tag }}
              <button
                @click="removeTag(tag)"
                class="hover:text-red-400 transition-colors ml-0.5"
                style="color: var(--text-muted)"
              >
                &times;
              </button>
            </span>
          </div>
          <div class="flex gap-2">
            <input
              v-model="newTag"
              type="text"
              placeholder="Add a tag..."
              @keydown.enter.prevent="addTag"
              class="modal-input flex-1 !py-1.5 !text-xs"
            />
            <button @click="addTag" class="modal-btn">Add</button>
          </div>
        </div>
      </div>

      <!-- Save File Finder Panel -->
      <div
        v-if="showSavePanel"
        class="px-6 py-3"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <div class="flex items-center justify-between mb-2">
          <h4
            class="text-xs font-semibold uppercase tracking-wider"
            style="color: var(--text-secondary)"
          >
            Save File Locations
          </h4>
          <button
            @click="showSavePanel = false"
            class="text-xs hover:text-white transition-colors"
            style="color: var(--text-muted)"
          >
            ✕ Close
          </button>
        </div>
        <div
          v-if="searchingSaves"
          class="text-xs py-2 flex items-center gap-2"
          style="color: var(--text-secondary)"
        >
          <svg
            class="w-3 h-3 animate-spin"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <circle cx="12" cy="12" r="10" />
            <path d="M12 2a10 10 0 0 1 10 10" />
          </svg>
          Searching...
        </div>
        <div
          v-else-if="saveResults.length === 0"
          class="text-xs py-2"
          style="color: var(--text-muted)"
        >
          No save files found. The game may store saves in an unexpected
          location.
        </div>
        <div v-else class="space-y-1.5 max-h-32 overflow-y-auto">
          <button
            v-for="(result, i) in saveResults"
            :key="i"
            @click="openSaveFolder(result.path)"
            class="w-full flex items-center gap-3 p-2 rounded-lg transition-all text-left group"
            style="
              background: var(--bg-surface);
              border: 1px solid var(--border);
            "
            onmouseover="this.style.borderColor = 'var(--border-hover)'"
            onmouseout="this.style.borderColor = 'var(--border)'"
          >
            <span class="text-lg">📂</span>
            <div class="flex-1 min-w-0">
              <p
                class="text-xs font-medium truncate"
                style="color: var(--text-primary)"
              >
                {{ result.description }}
              </p>
              <p
                class="text-[10px] truncate font-mono"
                style="color: var(--text-muted)"
              >
                {{ result.path }}
              </p>
            </div>
            <span
              class="text-[10px] px-1.5 py-0.5 rounded shrink-0"
              style="
                background: var(--bg-raised);
                color: var(--text-muted);
                border: 1px solid var(--border);
              "
              >{{ result.type }}</span
            >
          </button>
        </div>
        <p class="text-[10px] mt-2 italic" style="color: var(--text-muted)">
          ⚠ Auto-detection is best-effort. Some games store saves in
          non-standard locations.
        </p>
      </div>

      <!-- Footer Actions -->
      <div
        class="px-6 py-4 flex items-center justify-between"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <div class="flex gap-2">
          <button
            @click="deleteGame"
            :disabled="deleting"
            class="text-red-400 hover:text-red-300 hover:bg-red-500/10 px-3 py-2 rounded-lg text-xs font-medium transition-all"
          >
            {{ deleting ? "Removing..." : "🗑 Remove" }}
          </button>
          <button
            v-if="f95Url"
            @click="openInBrowser"
            class="hover:bg-purple-500/10 px-3 py-2 rounded-lg text-xs font-medium transition-all flex items-center gap-1"
            style="color: #b380cc"
          >
            <svg
              class="w-3.5 h-3.5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path
                d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"
              />
            </svg>
            Open in Browser
          </button>
          <button
            @click="findSaves"
            :disabled="searchingSaves"
            class="text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 px-3 py-2 rounded-lg text-xs font-medium transition-all flex items-center gap-1"
          >
            <svg
              class="w-3.5 h-3.5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path
                d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"
              />
            </svg>
            {{ searchingSaves ? "Searching..." : "Find Saves" }}
          </button>
        </div>
        <div class="flex gap-3">
          <button
            @click="launchGame"
            class="bg-green-600 hover:bg-green-500 text-white px-5 py-2 rounded-lg text-sm font-bold transition-all shadow-lg shadow-green-900/20 flex items-center gap-2"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="currentColor"
              stroke="none"
            >
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
            Play
          </button>
          <button
            @click="save"
            :disabled="saving"
            class="text-white px-5 py-2 rounded-lg text-sm font-bold transition-all disabled:opacity-50"
            style="background: var(--brand); box-shadow: var(--shadow-brand)"
          >
            {{ saving ? "Saving..." : "Save Changes" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
}

.modal-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 0.25rem;
}

.modal-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.875rem;
  color: var(--text-primary);
  transition: all 0.15s ease;
}
.modal-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.modal-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  transition: all 0.15s ease;
}
.modal-btn:hover {
  background: var(--border-hover);
}

.toggle-track {
  width: 2.75rem;
  height: 1.5rem;
  background: var(--bg-overlay);
  border-radius: 9999px;
}
</style>
