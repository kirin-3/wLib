// SPDX-License-Identifier: GPL-3.0-or-later
import { onBeforeUnmount, type Ref } from "vue";
import type { ControllerAction } from "./controllerInput.ts";
import { modalStack } from "./modalKeyboard.ts";
import { moveFocus, scopeRoot } from "./spatialFocus.ts";

type NamedAction = "play" | "search" | "menu" | "prevTab" | "nextTab" | "pageUp" | "pageDown";
export interface ControllerHint { button: string; label: string }
export type ControllerHandlers = Partial<Record<NamedAction, () => void>> & { hints?: ControllerHint[] | (() => ControllerHint[]); back?: () => boolean };
export interface ControllerScope { scope: Ref<HTMLElement | null>; handlers: ControllerHandlers }
const scopes: ControllerScope[] = [];
export function chooseScope(entries: readonly ControllerScope[], active: Element | null, fallback: Element | null): ControllerScope | undefined {
  const target = active && active !== document.body ? active : fallback;
  return entries.filter(entry => entry.scope.value && target && (!fallback || fallback === entry.scope.value || fallback.contains(entry.scope.value)) && (entry.scope.value === target || entry.scope.value.contains(target) || ((!active || active === document.body) && target.contains(entry.scope.value))))
    .sort((a, b) => a.scope.value?.contains(b.scope.value ?? null) ? 1 : -1)[0];
}
export function useControllerActions(scope: Ref<HTMLElement | null>, handlers: ControllerHandlers): void {
  const entry = { scope, handlers };
  scopes.push(entry);
  onBeforeUnmount(() => { const i = scopes.indexOf(entry); if (i >= 0) scopes.splice(i, 1); });
}
export const handleScopedBack = (entry: ControllerScope | undefined): boolean => entry?.handlers.back?.() === true;
export const defaultHints: ControllerHint[] = [{ button: "A", label: "Select" }, { button: "B", label: "Back" }];
export function resolveHints(entry: ControllerScope | undefined): ControllerHint[] { const hints = entry?.handlers.hints; return typeof hints === "function" ? hints() : hints ?? defaultHints; }
export function currentHints(): ControllerHint[] { return resolveHints(chooseScope(scopes, document.activeElement, scopeRoot())); }
function scrollContainer(element: HTMLElement | null): HTMLElement {
  for (let node = element; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight) return node;
  }
  return document.querySelector<HTMLElement>("main") ?? document.documentElement;
}
export function controllerFocusText(element: HTMLInputElement | HTMLTextAreaElement): void {
  element.focus();
  window.dispatchEvent(new Event("wlib-controller-text"));
}
export function dispatchController(action: ControllerAction, back: () => void): void {
  const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const root = scopeRoot();
  const handler = action.type !== "move" && action.type !== "scroll" && action.type !== "activate" && action.type !== "back" ? chooseScope(scopes, active, root)?.handlers[action.type] : undefined;
  if (handler) { handler(); return; }
  if (action.type === "move") { moveFocus(action.direction); return; }
  if (action.type === "scroll") { scrollContainer(active).scrollBy({ left: action.x * 20, top: action.y * 20 }); return; }
  if (action.type === "activate") {
    if (active instanceof HTMLSelectElement) window.dispatchEvent(new CustomEvent("wlib-controller-select", { detail: active }));
    else if (active instanceof HTMLTextAreaElement || (active instanceof HTMLInputElement && ["text", "search", "email", "url", "tel", "password", "number"].includes(active.type))) controllerFocusText(active);
    else active?.click();
    return;
  }
  if (action.type === "back") {
    if (root.matches('[role="menu"], [data-controller-select]') || modalStack.length) (active ?? root).dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    else if (handleScopedBack(chooseScope(scopes, active, root))) return;
    else if (history.state?.back) back();
    return;
  }
  if (action.type === "menu") active?.dispatchEvent(new KeyboardEvent("keydown", { key: "ContextMenu", bubbles: true, cancelable: true }));
  if (action.type === "pageUp" || action.type === "pageDown") {
    // Measure in content coordinates: instant scrolling moves elements under the viewport while paging.
    const container = scrollContainer(active), offset = (element: HTMLElement) => element.getBoundingClientRect().top + container.scrollTop;
    const start = active ? offset(active) : 0;
    for (let i = 0; i < 100; i++) { const next = moveFocus(action.type === "pageUp" ? "up" : "down"); if (!next || Math.abs(offset(next) - start) >= container.clientHeight) break; }
  }
}
