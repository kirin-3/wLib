// SPDX-License-Identifier: GPL-3.0-or-later
import { FOCUSABLE, modalStack } from "./modalKeyboard.ts";
import type { Direction } from "./controllerInput.ts";

export interface Rect { left: number; right: number; top: number; bottom: number }
export function pickNext<T extends Rect>(current: Rect, candidates: readonly T[], direction: Direction): T | null {
  const horizontal = direction === "left" || direction === "right";
  const positive = direction === "right" || direction === "down";
  const center = (start: number, end: number) => (start + end) / 2;
  const cross = horizontal ? center(current.top, current.bottom) : center(current.left, current.right);
  let best: T | null = null, bestScore = Infinity;
  for (const candidate of candidates) {
    const along = horizontal ? center(candidate.left, candidate.right) : center(candidate.top, candidate.bottom);
    const edge = horizontal ? (positive ? current.right : current.left) : (positive ? current.bottom : current.top);
    if (positive ? along <= edge : along >= edge) continue;
    const other = horizontal ? center(candidate.top, candidate.bottom) : center(candidate.left, candidate.right);
    const overlap = horizontal ? candidate.bottom > current.top && candidate.top < current.bottom : candidate.right > current.left && candidate.left < current.right;
    const nearEdge = horizontal ? (positive ? candidate.left : candidate.right) : (positive ? candidate.top : candidate.bottom);
    const score = Math.max(0, positive ? nearEdge - edge : edge - nearEdge) + 2 * Math.abs(other - cross) - (overlap ? 1 : 0);
    if (score < bestScore) { best = candidate; bestScore = score; }
  }
  return best;
}
export function scopeRoot(): HTMLElement {
  const modal = modalStack[modalStack.length - 1]?.container.value;
  const overlays = [...document.querySelectorAll<HTMLElement>('[role="menu"], [data-controller-select]')].filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== "hidden" && !el.closest('[inert], [aria-hidden="true"]') && (!modal || modal.contains(el) || el.hasAttribute("data-controller-select")));
  return overlays[overlays.length - 1] ?? modal ?? document.querySelector<HTMLElement>("#app") ?? document.body;
}
export function focusCandidates(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden" && !el.matches(':disabled, [aria-disabled="true"]') && !el.closest('[inert], [aria-hidden="true"]'));
}
export function primaryCandidate<T extends { primary: boolean }>(candidates: readonly T[]): T | null {
  return candidates.find(candidate => candidate.primary) ?? candidates[0] ?? null;
}
export function moveFocus(direction: Direction): HTMLElement | null {
  const root = scopeRoot(), candidates = focusCandidates(root);
  const active = document.activeElement;
  let target: HTMLElement | null;
  if (!(active instanceof HTMLElement) || !candidates.includes(active)) {
    target = primaryCandidate(candidates.map(element => ({ element, primary: element.hasAttribute("data-controller-primary") })))?.element ?? null;
  } else {
    const boxes = candidates.filter(el => el !== active).map(element => ({ element, ...focusRect(element) }));
    target = pickNext(focusRect(active), boxes, direction)?.element ?? null;
  }
  if (target) {
    target.focus({ preventScroll: true });
    const instant = document.documentElement.classList.contains("tv-mode") || document.documentElement.dataset.motion === "off" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    focusVisual(target).scrollIntoView({ block: "nearest", inline: "nearest", behavior: instant ? "instant" : "smooth" });
  }
  return target;
}
function rectObject(rect: DOMRect): Rect { return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }; }
function focusVisual(element: HTMLElement): HTMLElement {
  return element.matches('input[type="checkbox"], input[type="radio"], input.sr-only') ? element.closest("label") ?? element : element;
}
function focusRect(element: HTMLElement): Rect {
  return rectObject(focusVisual(element).getBoundingClientRect());
}
