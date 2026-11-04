// SPDX-License-Identifier: GPL-3.0-or-later
import { inject, type InjectionKey, type Ref } from "vue";

export const settingsVisibleRows: InjectionKey<Ref<Set<string> | null>> = Symbol("settings-visible-rows");
export function useSettingsRowVisibility() {
  const rows = inject(settingsVisibleRows, null);
  return (id: string) => !rows?.value || rows.value.has(id);
}
