// SPDX-License-Identifier: GPL-3.0-or-later
import { inject, type InjectionKey, type Ref } from "vue";

/** The title of the SettingsGroup around a component. A row in a group uses the next heading level, and a help link names it. */
export const settingsGroupTitle: InjectionKey<Ref<string>> = Symbol("settings-group-title");
export const settingsVisibleRows: InjectionKey<Ref<Set<string> | null>> = Symbol("settings-visible-rows");
export function useSettingsRowVisibility() {
  const rows = inject(settingsVisibleRows, null);
  return (id: string) => !rows?.value || rows.value.has(id);
}
