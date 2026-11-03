// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";
export const inputModality = ref<"controller" | "pointer">("pointer");
export function setInputModality(value: "controller" | "pointer"): void {
  inputModality.value = value;
  if (typeof document !== "undefined") document.documentElement.dataset.input = value;
}
export function listenForPointer(): () => void {
  const pointer = () => setInputModality("pointer");
  window.addEventListener("pointermove", pointer); window.addEventListener("pointerdown", pointer);
  return () => { window.removeEventListener("pointermove", pointer); window.removeEventListener("pointerdown", pointer); };
}
