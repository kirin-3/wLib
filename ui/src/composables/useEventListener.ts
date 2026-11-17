// SPDX-License-Identifier: GPL-3.0-or-later
import { getCurrentInstance, getCurrentScope, onMounted, onScopeDispose } from "vue";

export function useEventListener<K extends keyof WindowEventMap>(
  target: Window,
  type: K,
  listener: (event: WindowEventMap[K]) => void,
  options?: boolean | AddEventListenerOptions,
): () => void;
export function useEventListener<K extends keyof DocumentEventMap>(
  target: Document,
  type: K,
  listener: (event: DocumentEventMap[K]) => void,
  options?: boolean | AddEventListenerOptions,
): () => void;
export function useEventListener(
  target: EventTarget,
  type: string,
  listener: EventListenerOrEventListenerObject,
  options?: boolean | AddEventListenerOptions,
): () => void;
export function useEventListener<E extends Event>(
  target: EventTarget,
  type: string,
  listener: ((event: E) => void) | EventListenerObject,
  options?: boolean | AddEventListenerOptions,
): () => void {
  const handler = listener as EventListenerOrEventListenerObject;
  let capture = false;
  let listening = false;
  let stopped = false;

  function start() {
    if (stopped) return;
    capture = typeof options === "boolean" ? options : options?.capture ?? false;
    target.addEventListener(type, handler, options);
    listening = true;
  }

  function stop() {
    if (stopped) return;
    stopped = true;
    if (listening) target.removeEventListener(type, handler, { capture });
  }

  if (getCurrentScope()) onScopeDispose(stop);
  if (getCurrentInstance()) onMounted(start);
  else start();

  return stop;
}
