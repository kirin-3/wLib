<script setup lang="ts">
// A cover for dense views: the small still copy, with the small animated copy
// stacked on top. CSS (html[data-cover-animation]) decides when the animated
// layer shows, so changing the mode or hovering never re-renders this.
import { computed } from "vue";
import { coverAnimError, coverCardSources, gameCoverError } from "../utils/gameCover";

type CoverGame = Parameters<typeof coverCardSources>[0];

const props = defineProps<{ game: NonNullable<CoverGame>; alt?: string; imgClass?: string | string[] }>();
const sources = computed(() => coverCardSources(props.game));
</script>

<template>
  <img
    v-if="sources.still || sources.original"
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
