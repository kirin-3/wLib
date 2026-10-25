<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { toCssPx } from "../utils/interfaceScalePreference";
import { useModalKeyboard } from "../utils/modalKeyboard";

const props = defineProps<{
  images: string[];
  startIndex: number | null;
}>();

const emit = defineEmits<{
  close: [];
}>();

const container = ref<HTMLElement | null>(null);
const isOpen = computed(() => props.startIndex !== null);
const currentIndex = ref(0);
const failed = ref(false);
const loaded = ref(false);
const naturalWidth = ref(0);
const naturalHeight = ref(0);
const windowWidth = ref(typeof window !== "undefined" ? window.innerWidth : 1280);
const windowHeight = ref(typeof window !== "undefined" ? window.innerHeight : 800);

const close = () => emit("close");

useModalKeyboard(container, () => isOpen.value, close);

watch(
  () => props.startIndex,
  (value) => {
    if (value !== null) {
      currentIndex.value = value;
      failed.value = false;
      loaded.value = false;
      naturalWidth.value = 0;
      naturalHeight.value = 0;
    }
  },
  { immediate: true },
);

watch(currentIndex, () => {
  failed.value = false;
  loaded.value = false;
  naturalWidth.value = 0;
  naturalHeight.value = 0;
});

const currentImage = computed(() => props.images[currentIndex.value] ?? "");

const goTo = (index: number) => {
  const total = props.images.length;
  if (total === 0) return;
  currentIndex.value = ((index % total) + total) % total;
};

const next = () => goTo(currentIndex.value + 1);
const prev = () => goTo(currentIndex.value - 1);

const onImageLoad = (event: Event) => {
  const target = event.target as HTMLImageElement | null;
  if (target) {
    naturalWidth.value = target.naturalWidth;
    naturalHeight.value = target.naturalHeight;
  }
  loaded.value = true;
};

const onImageError = () => {
  failed.value = true;
  loaded.value = true;
};

const onResize = () => {
  windowWidth.value = window.innerWidth;
  windowHeight.value = window.innerHeight;
};

onMounted(() => {
  window.addEventListener("resize", onResize);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
});

const fittedStyle = computed(() => {
  if (!naturalWidth.value || !naturalHeight.value) {
    return { maxWidth: "calc(var(--app-vw)*0.9)", maxHeight: "calc(var(--app-vh)*0.9)" };
  }
  const scale = Math.min(
    (0.9 * toCssPx(windowWidth.value)) / naturalWidth.value,
    (0.9 * toCssPx(windowHeight.value)) / naturalHeight.value,
  );
  return {
    width: `${Math.round(naturalWidth.value * scale)}px`,
    height: `${Math.round(naturalHeight.value * scale)}px`,
    maxWidth: "calc(var(--app-vw)*0.9)",
    maxHeight: "calc(var(--app-vh)*0.9)",
  };
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="screenshot-lightbox-backdrop"
      @click="close"
    >
      <div
        ref="container"
        tabindex="-1"
        role="dialog"
        aria-modal="true"
        aria-label="Screenshot viewer"
        class="screenshot-lightbox-dialog"
        @keydown.left.stop.prevent="prev"
        @keydown.right.stop.prevent="next"
      >
        <button
          type="button"
          class="screenshot-lightbox-close"
          aria-label="Close viewer"
          @click.stop="close"
        >
          ✕
        </button>
        <button
          v-if="images.length > 1"
          type="button"
          class="screenshot-lightbox-nav screenshot-lightbox-prev"
          aria-label="Previous image"
          @click.stop="prev"
        >
          ‹
        </button>
        <div class="screenshot-lightbox-stage">
          <img
            v-if="!failed && currentImage"
            :key="currentImage"
            :src="currentImage"
            alt="Game screenshot"
            class="screenshot-lightbox-image"
            :style="fittedStyle"
            @load="onImageLoad"
            @error="onImageError"
            @click.stop
          />
          <div
            v-else
            class="screenshot-lightbox-placeholder"
            :style="fittedStyle"
            role="img"
            aria-label="Image unavailable"
            @click.stop
          >
            Image unavailable
          </div>
          <p class="screenshot-lightbox-counter">
            {{ currentIndex + 1 }} of {{ images.length }}
          </p>
        </div>
        <button
          v-if="images.length > 1"
          type="button"
          class="screenshot-lightbox-nav screenshot-lightbox-next"
          aria-label="Next image"
          @click.stop="next"
        >
          ›
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.screenshot-lightbox-backdrop {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  background: rgba(0, 0, 0, 0.85);
}

.screenshot-lightbox-dialog {
  position: relative;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  outline: none;
  max-width: 100%;
  max-height: 100%;
}

.screenshot-lightbox-stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  max-width: 100%;
}

.screenshot-lightbox-image {
  display: block;
  object-fit: contain;
  border-radius: 0.5rem;
  background: #000;
  opacity: 1;
  transition: opacity 0.15s ease;
}

.screenshot-lightbox-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 16rem;
  min-height: 10rem;
  padding: 2rem;
  border-radius: 0.5rem;
  background: #1a1a1a;
  color: #ffffff;
  font-size: 0.875rem;
}

.screenshot-lightbox-counter {
  color: #ffffff;
  font-size: 0.875rem;
  font-weight: 600;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
}

.screenshot-lightbox-close,
.screenshot-lightbox-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
  font-size: 1.5rem;
  line-height: 1;
  border: 1px solid rgba(255, 255, 255, 0.25);
  cursor: pointer;
  transition: background 0.15s ease;
}

.screenshot-lightbox-close:hover,
.screenshot-lightbox-nav:hover {
  background: rgba(255, 255, 255, 0.25);
}

.screenshot-lightbox-close {
  position: absolute;
  top: -0.75rem;
  right: -0.75rem;
  font-size: 1.125rem;
}
</style>
