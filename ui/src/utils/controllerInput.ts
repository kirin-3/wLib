// SPDX-License-Identifier: GPL-3.0-or-later
export type Direction = "up" | "down" | "left" | "right";
export type ControllerAction = { type: "move"; direction: Direction } | { type: "scroll"; x: number; y: number } | { type: "activate" | "back" | "play" | "search" | "menu" | "prevTab" | "nextTab" | "pageUp" | "pageDown" };
type Pad = Pick<Gamepad, "mapping" | "buttons" | "axes">;
export type RepeatState = Record<string, number>;
const buttons = ["activate", "back", "play", "search", "prevTab", "nextTab", "pageUp", "pageDown", "menu", "menu"] as const;
const directions: Direction[] = ["up", "down", "left", "right"];
export function readController(pads: readonly (Pad | null)[], focused: boolean, now: number, previous: RepeatState): { actions: ControllerAction[]; state: RepeatState } {
  const actions: ControllerAction[] = [], state: RepeatState = {};
  pads.forEach((pad, index) => {
    if (!pad || pad.mapping !== "standard") return;
    const fire = (key: string, active: boolean, action: ControllerAction, repeat: boolean) => {
      if (!active) return;
      const id = `${index}:${key}`, next = previous[id];
      // Controls held while unfocused stay ignored until released, so returning from a game fires nothing.
      if (!focused) { state[id] = Infinity; return; }
      if (next === undefined) { actions.push(action); state[id] = now + 400; }
      else if (repeat && now >= next) { actions.push(action); state[id] = now + 90; }
      else state[id] = next;
    };
    buttons.forEach((type, i) => fire(`b${i}`, !!pad.buttons[i]?.pressed, { type }, false));
    const x = pad.axes[0] ?? 0, y = pad.axes[1] ?? 0;
    const held = [y < -0.5, y > 0.5, x < -0.5, x > 0.5];
    directions.forEach((direction, i) => fire(`d${i}`, !!pad.buttons[12 + i]?.pressed || !!held[i], { type: "move", direction }, true));
    const sx = pad.axes[2] ?? 0, sy = pad.axes[3] ?? 0;
    if (focused && (Math.abs(sx) > 0.15 || Math.abs(sy) > 0.15)) actions.push({ type: "scroll", x: sx, y: sy });
  });
  return { actions, state };
}
const TEXT_FIELD = 'input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="submit"]):not([type="reset"]), textarea, [contenteditable]:not([contenteditable="false"])';
const SINGLE_LINE_FIELD = 'input:not([type="number"]):not([type="range"])';
export function actionForKey(event: KeyboardEvent): ControllerAction | null {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return null;
  const target = event.target as Element | null;
  if (target?.closest?.('[role="menu"], [role="listbox"]')) return null;
  const direction = ({ ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" } as Record<string, Direction>)[event.key];
  const vertical = direction === "up" || direction === "down";
  const field = target?.closest?.(TEXT_FIELD);
  // Up and Down leave single-line fields; every other key stays with typing.
  if (field) return direction && vertical && field.matches(SINGLE_LINE_FIELD) ? { type: "move", direction } : null;
  // Arrows on a closed native select change its value, which would trap keyboard-driven focus.
  if (target?.closest?.("select") && event.key === "Enter") return { type: "activate" };
  // Tab strips own Left and Right only.
  if (direction && !vertical && target?.closest?.('[role="tablist"]')) return null;
  if (direction) return { type: "move", direction };
  return event.key === "Escape" ? { type: "back" } : null;
}
export function startControllerInput(dispatch: (action: ControllerAction) => void): () => void {
  let frame = 0, state: RepeatState = {};
  const poll = () => {
    frame = 0;
    const pads = Array.from(navigator.getGamepads?.() ?? []);
    const result = readController(pads, document.hasFocus(), performance.now(), state);
    state = result.state;
    result.actions.forEach(dispatch);
    if (pads.some(Boolean)) frame = requestAnimationFrame(poll);
  };
  const connected = () => { if (!frame) frame = requestAnimationFrame(poll); };
  const keydown = (event: KeyboardEvent) => { const action = actionForKey(event); if (action) { event.preventDefault(); dispatch(action); } };
  window.addEventListener("gamepadconnected", connected);
  window.addEventListener("keydown", keydown);
  if (Array.from(navigator.getGamepads?.() ?? []).some(Boolean)) connected();
  return () => { if (frame) cancelAnimationFrame(frame); window.removeEventListener("gamepadconnected", connected); window.removeEventListener("keydown", keydown); };
}
