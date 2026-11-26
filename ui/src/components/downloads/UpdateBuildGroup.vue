<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// One thread build in the download picker: its heading row and its mirrors as
// one radio group, labelled by the heading row (see utils/radioGroup.ts). The
// picker owns the single choice across all groups; this only shows it.
// The card, radio and chip styles come from UpdateDownloadModal.vue.
import {computed} from 'vue';
import {IconCheck} from '@tabler/icons-vue';
import type {UpdateBuild, UpdateMirror} from '../../services/api';
import {mirrorSubtitle, platformLabel, type HostPlatform} from '../../utils/updateDownloads';
import {onRadioGroupKeydown, radioTabStop} from '../../utils/radioGroup';

const props = defineProps<{
  build: UpdateBuild;
  /** Id of the heading row, which names the radio group. Unique in the picker. */
  groupId: string;
  /** False for a build this game cannot use (a patch for a wishlisted game). */
  selectable: boolean;
  chosenLinkId: string | null;
  platform: HostPlatform;
}>();
const emit = defineEmits<{ choose: [mirror: UpdateMirror] }>();

const title = computed(() => (props.build.section ? props.build.section + ' · ' : '') + props.build.label);
// The platform chip only when the build's label does not already say it.
const showPlatform = computed(() =>
  platformLabel(props.build.platform).replace(/\s/g, '').toLowerCase() !== props.build.label.replace(/\s/g, '').toLowerCase());
const tabStop = computed(() => radioTabStop(
  props.build.mirrors.findIndex(mirror => mirror.link_id === props.chosenLinkId),
  props.build.mirrors.map(() => !props.selectable),
));
</script>

<template>
  <div class="space-y-2">
    <div :id="groupId" class="flex items-center gap-2 flex-wrap">
      <span class="text-sm font-semibold" style="color: var(--text-primary)">{{ title }}</span>
      <span v-if="showPlatform" class="chip">{{ platformLabel(build.platform) }}</span>
      <span v-if="build.kind === 'patch'" class="chip chip-warn">Patch</span>
      <span v-if="build.kind === 'old'" class="chip chip-warn">Older version</span>
      <span v-if="!selectable" class="text-xs" style="color: var(--text-secondary)">Needs an installed copy</span>
    </div>
    <div class="mirror-grid" role="radiogroup" :aria-labelledby="groupId" :aria-disabled="!selectable || undefined" @keydown="onRadioGroupKeydown">
      <button
        v-for="(mirror, index) in build.mirrors"
        :key="mirror.link_id"
        type="button"
        class="mirror"
        :class="{ selected: chosenLinkId === mirror.link_id }"
        :disabled="!selectable"
        role="radio"
        :aria-checked="chosenLinkId === mirror.link_id"
        :tabindex="tabStop === index ? 0 : -1"
        @click="emit('choose', mirror)"
      >
        <span class="min-w-0 flex-1 text-left">
          <span class="block text-sm font-medium truncate">{{ mirror.label || mirror.host }}</span>
          <span class="block text-xs truncate" style="color: var(--text-muted)">{{ mirrorSubtitle(mirror, build, platform) }}</span>
        </span>
        <span class="radio" aria-hidden="true"><IconCheck v-if="chosenLinkId === mirror.link_id" class="w-3 h-3" stroke-width="3" /></span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.mirror-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(10.5rem, 1fr));
  gap: 0.5rem;
}
</style>
