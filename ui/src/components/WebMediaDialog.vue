<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { IconMovie } from "@tabler/icons-vue";
import type { GameRecord, WebMediaInfo } from "../services/api";
import { useWebMediaConversion } from "../composables/useWebMediaConversion";
import { useModalKeyboard } from "../utils/modalKeyboard";
import {
  canConvert,
  ffmpegInstallHint,
  ffmpegProblem,
  initialStep,
  progressPercent,
  progressText,
  unsupportedText,
  type WebMediaStep,
} from "../utils/webMedia";

const props = defineProps<{
  modelValue: boolean;
  game: GameRecord | null;
  media: WebMediaInfo | null;
  /** The launch result code that opened the dialog. */
  code: string;
}>();
const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
  (event: "convert"): void;
  (event: "use-browser"): void;
  (event: "play-anyway"): void;
}>();

const dialog = ref<HTMLElement | null>(null);
const step = ref<WebMediaStep>("choices");
const busy = ref(false);
const { webMediaStatus, cancel } = useWebMediaConversion();

const close = () => emit("update:modelValue", false);
useModalKeyboard(dialog, () => props.modelValue, close);

watch(() => props.modelValue, async (open) => {
  if (!open) return;
  step.value = initialStep(props.code);
  busy.value = false;
  await nextTick();
  dialog.value?.focus();
});

const ffmpeg = computed(() => props.media?.ffmpeg ?? null);
const convertible = computed(() => canConvert(ffmpeg.value));
const problem = computed(() => ffmpegProblem(ffmpeg.value));
const hint = computed(() => ffmpegInstallHint(ffmpeg.value));
const status = computed(() => (props.game ? webMediaStatus.value[props.game.id] : undefined));
const running = computed(() => Boolean(status.value?.running));
const title = computed(() => {
  if (step.value === "progress") return running.value ? "Converting videos" : "Video conversion";
  return "Videos cannot play in the built-in player";
});

/** The parent starts the job, then sets `step` to progress through `showProgress`. */
const showProgress = () => { step.value = "progress"; };
defineExpose({ showProgress });

const confirmConvert = () => {
  if (busy.value) return;
  busy.value = true;
  emit("convert");
};

const choose = (event: "use-browser" | "play-anyway") => {
  if (busy.value) return;
  busy.value = true;
  if (event === "use-browser") emit("use-browser");
  else emit("play-anyway");
};

const stop = async () => {
  if (props.game) await cancel(props.game.id);
};
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-[150] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
      <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="web-media-title" tabindex="-1" class="media-dialog w-full max-w-lg rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none">
        <div class="px-6 pt-6 pb-4 overflow-y-auto min-h-0 space-y-4">
          <div class="flex gap-4">
            <span class="media-icon" aria-hidden="true"><IconMovie class="w-5 h-5" /></span>
            <div class="min-w-0 pt-1">
              <h3 id="web-media-title" class="text-lg font-bold" style="color: var(--text-primary)">{{ title }}</h3>
              <p v-if="game" class="mt-1 text-sm truncate" style="color: var(--text-secondary)">{{ game.title }}</p>
            </div>
          </div>

          <template v-if="step === 'choices'">
            <p class="text-sm" style="color: var(--text-secondary)">{{ unsupportedText(media?.count ?? 0) }} The game may show no video.</p>
            <ul class="space-y-2 text-sm" style="color: var(--text-secondary)">
              <li><strong style="color: var(--text-primary)">Convert videos</strong> replaces the game's video files with AV1 or VP9 and Opus copies that the built-in player can play.</li>
              <li><strong style="color: var(--text-primary)">Use system browser</strong> opens this game in your browser from now on. Saves made in the built-in player do not carry over.</li>
              <li><strong style="color: var(--text-primary)">Play anyway</strong> starts the built-in player. wLib does not ask again until the game's videos change.</li>
            </ul>
            <div v-if="!convertible" class="media-note rounded-lg p-3 text-sm" role="note">
              <p class="font-semibold" style="color: var(--text-primary)">{{ problem }}</p>
              <p class="mt-1" style="color: var(--text-secondary)">{{ hint.note }}</p>
              <code v-if="hint.command" class="media-code mt-2 block font-mono text-xs break-all select-all">{{ hint.command }}</code>
            </div>
          </template>

          <template v-else-if="step === 'confirm'">
            <p class="text-sm" style="color: var(--text-secondary)">wLib converts {{ media?.count ?? 0 }} {{ media?.count === 1 ? "video" : "videos" }} in the background. This can take a long time.</p>
            <p class="media-warning rounded-lg p-3 text-sm font-semibold" role="alert">The original video files are replaced. This cannot be undone. A game update brings the original files back.</p>
          </template>

          <template v-else>
            <p class="text-sm" style="color: var(--text-secondary)">{{ progressText(status) }}<span v-if="status?.failed">, {{ status.failed }} failed</span></p>
            <div class="media-bar h-2 rounded-full overflow-hidden" role="progressbar" :aria-valuenow="progressPercent(status)" aria-valuemin="0" aria-valuemax="100">
              <div class="media-bar-fill h-full" :style="{ width: `${progressPercent(status)}%` }"></div>
            </div>
            <p v-if="running && status?.current" class="font-mono text-xs break-all" style="color: var(--text-muted)">{{ status.current }}</p>
            <p v-if="running" class="text-xs" style="color: var(--text-muted)">You can close this dialog. The conversion continues, and wLib tells you when it ends.</p>
            <p v-if="status?.cancelled && running" class="text-xs" style="color: var(--text-muted)">Stopping after the current video...</p>
            <p v-if="status?.error" class="media-reason text-sm" role="alert">{{ status.error }}</p>
          </template>
        </div>

        <div class="px-6 py-4 flex flex-wrap items-center justify-end gap-3" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
          <template v-if="step === 'choices'">
            <button class="media-cancel px-4 py-2.5 rounded-lg text-sm font-medium mr-auto" @click="close">Cancel</button>
            <button class="media-secondary px-4 py-2.5 rounded-lg text-sm font-medium" :disabled="busy" @click="choose('play-anyway')">Play anyway</button>
            <button class="media-secondary px-4 py-2.5 rounded-lg text-sm font-medium" :disabled="busy" @click="choose('use-browser')">Use system browser</button>
            <button v-if="convertible" class="media-primary px-5 py-2.5 rounded-lg text-sm font-bold" :disabled="busy" @click="step = 'confirm'">Convert videos</button>
          </template>
          <template v-else-if="step === 'confirm'">
            <button class="media-cancel px-5 py-2.5 rounded-lg text-sm font-medium" @click="step = 'choices'">Back</button>
            <button class="media-danger px-5 py-2.5 rounded-lg text-sm font-bold" :disabled="busy" @click="confirmConvert">Replace and convert</button>
          </template>
          <template v-else>
            <button v-if="running" class="media-secondary px-5 py-2.5 rounded-lg text-sm font-medium" :disabled="status?.cancelled" @click="stop">Cancel conversion</button>
            <button class="media-primary px-5 py-2.5 rounded-lg text-sm font-bold" @click="close">Close</button>
          </template>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.media-dialog { background: var(--bg-surface); border: 1px solid var(--border); box-shadow: var(--shadow-modal); }
.media-icon { display: inline-flex; align-items: center; justify-content: center; width: 2.5rem; height: 2.5rem; flex-shrink: 0; border-radius: 9999px; background: var(--brand-glow); color: var(--brand); }
.media-note { background: var(--bg-inset); border: 1px solid var(--border); }
.media-code { background: var(--bg-overlay); color: var(--text-primary); border-radius: 0.375rem; padding: 0.375rem 0.5rem; }
.media-warning { background: var(--danger-bg); border: 1px solid var(--danger-border); color: var(--danger-text); }
.media-reason { color: var(--danger-text); }
.media-bar { background: var(--bg-inset); border: 1px solid var(--border); }
.media-bar-fill { background: var(--brand); transition: width 0.3s ease; }
.media-cancel { color: var(--text-secondary); }
.media-cancel:hover { color: var(--text-primary); background: var(--bg-overlay); }
.media-secondary { color: var(--text-primary); border: 1px solid var(--border); }
.media-secondary:hover:not(:disabled) { background: var(--bg-overlay); }
.media-primary { background: var(--brand); color: var(--on-brand); box-shadow: var(--shadow-brand); }
.media-primary:hover:not(:disabled) { filter: brightness(1.08); }
.media-danger { background: #dc2626; color: #fff; box-shadow: 0 4px 14px rgba(220, 38, 38, 0.3); }
.media-danger:hover:not(:disabled) { background: #b91c1c; }
.media-secondary:disabled, .media-primary:disabled, .media-danger:disabled { opacity: 0.5; cursor: not-allowed; }
.media-cancel:focus-visible, .media-secondary:focus-visible, .media-primary:focus-visible, .media-danger:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--brand-glow); }
</style>
