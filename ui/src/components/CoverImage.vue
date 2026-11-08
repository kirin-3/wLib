<script setup lang="ts">
// A cover for dense views: the small still copy, with the small animated copy
// stacked on top. CSS (html[data-cover-animation]) decides when the animated
// layer shows, so changing the mode or hovering never re-renders this. An
// animated cover with no still copy gets its first frame drawn on a canvas.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { coverAnimError, coverCardSources, gameCoverError } from "../utils/gameCover";

type CoverGame = Parameters<typeof coverCardSources>[0];

const props = defineProps<{ game: NonNullable<CoverGame>; alt?: string; imgClass?: string | string[] }>();
const sources = computed(() => coverCardSources(props.game));

// Matches the card copies' size; a larger frame only costs memory.
const FRAME_MAX_WIDTH = 640;

const canvas = ref<HTMLCanvasElement | null>(null);
let observer: IntersectionObserver | null = null;
let run = 0;

function drawFrame(target: HTMLCanvasElement, src: string): void {
  const current = ++run;
  // An image outside the document does not animate, so it holds frame one.
  const image = new Image();
  image.decoding = "async";
  image.onload = () => {
    if (current !== run) return;
    const scale = Math.min(1, FRAME_MAX_WIDTH / Math.max(1, image.naturalWidth));
    target.width = Math.max(1, Math.round(image.naturalWidth * scale));
    target.height = Math.max(1, Math.round(image.naturalHeight * scale));
    target.getContext("2d")?.drawImage(image, 0, 0, target.width, target.height);
  };
  image.onerror = () => {
    // Marks the source failed, so the next cover source is used instead.
    if (current === run) gameCoverError(new Event("error"), props.game);
  };
  image.src = src;
}

function stopWatching(): void {
  observer?.disconnect();
  observer = null;
}

watch(
  [canvas, () => sources.value.frame, () => sources.value.original],
  ([target, frame, src]) => {
    stopWatching();
    run++;
    if (!target || !frame || !src) return;
    if (typeof IntersectionObserver === "undefined") {
      drawFrame(target, src);
      return;
    }
    // Like loading="lazy": draw once the cover comes near the screen.
    observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        stopWatching();
        drawFrame(target, src);
      },
      { rootMargin: "200px" },
    );
    observer.observe(target);
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  stopWatching();
  run++;
});
</script>

<template>
  <canvas
    v-if="sources.frame"
    ref="canvas"
    :class="imgClass"
    role="img"
    :aria-label="alt ?? ''"
  />
  <img
    v-else-if="sources.still || sources.original"
    :src="sources.still || sources.original"
    :class="imgClass"
    :alt="alt ?? ''"
    loading="lazy"
    decoding="async"
    @error="gameCoverError($event, game)"
  />
  <img
    v-if="sources.anim"
    :src="sources.anim"
    class="cover-anim"
    :class="imgClass"
    alt=""
    aria-hidden="true"
    loading="lazy"
    decoding="async"
    @error="coverAnimError($event)"
  />
</template>
