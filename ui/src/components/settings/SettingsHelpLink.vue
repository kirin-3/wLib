<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- A quiet "Help" link from a Settings section or group to its page in the
     user guide (the GitHub wiki). It opens in the system browser. In a group,
     the accessible name also names the group, so screen readers can tell the
     links of one section apart. -->
<script setup lang="ts">
import { computed, inject } from "vue";
import { IconExternalLink } from "@tabler/icons-vue";
import { api } from "../../services/api";
import { settingsGroupTitle } from "../../utils/settingsVisibility";

const props = defineProps<{
  /** The wiki page file name, for example "Launching-Games". */
  page: string;
  /** The heading anchor on that page, without "#". */
  anchor?: string;
}>();

const WIKI_URL = "https://github.com/kirin-3/wLib/wiki";
const url = computed(() => `${WIKI_URL}/${props.page}${props.anchor ? `#${props.anchor}` : ""}`);
const title = computed(() => props.page.replace(/-/g, " "));
const group = inject(settingsGroupTitle, null);
const label = computed(() => `Help: ${title.value}${group?.value ? `, ${group.value}` : ""}. Opens in your browser.`);

const open = async () => {
  try {
    const result = await api.openInBrowser(url.value);
    if (result && result.success === false) window.open(url.value, "_blank", "noopener");
  } catch {
    window.open(url.value, "_blank", "noopener");
  }
};
</script>

<template>
  <button type="button" class="ui-card-btn ui-card-btn--link settings-help-link shrink-0" data-settings-help
    :aria-label="label" :title="`${title} in the user guide`" @click="open">
    Help<IconExternalLink class="w-3.5 h-3.5" aria-hidden="true" />
  </button>
</template>

<style scoped>
/* Quieter than a text action: small muted text with the link icon, no underline until hover or focus.
   The 1.5rem line box centers it on a group title's first line. */
.settings-help-link {
  color: var(--text-muted);
  font-weight: 500;
  line-height: 1.5rem;
  gap: 0.25rem;
  text-decoration: none;
}
.settings-help-link:hover:not(:disabled),
.settings-help-link:focus-visible { color: var(--text-primary); text-decoration: underline; }
</style>
