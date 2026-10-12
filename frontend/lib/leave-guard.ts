/** One unsaved-changes guard at a time (an open MCP server page). Navigation that would leave the page asks first. */
export type LeaveGuard = (proceed: () => void, cancel: () => void) => void;
let guard: LeaveGuard | null = null;
/** Back/forward while a guard is set: receives the target URL and decides; the app router is not told yet. */
let popGuard: ((target: string) => void) | null = null;

export function setLeaveGuard(next: LeaveGuard | null) {
  guard = next;
}

export function setPopGuard(next: ((target: string) => void) | null) {
  popGuard = next;
}

// Installed when the shell loads, before the app router registers its own popstate listener: a capture listener on
// window runs first, so a guarded back/forward can be stopped before the router moves.
if (typeof window !== 'undefined') {
  window.addEventListener('popstate', event => {
    if (!popGuard) return;
    event.stopImmediatePropagation();
    popGuard(location.pathname + location.search + location.hash);
  }, true);
}

/** Runs `proceed` now, or after the user chooses to leave in the guard's dialog (`cancel` when they stay). */
export function guardedNavigate(proceed: () => void, cancel: () => void = () => {}) {
  if (guard) guard(proceed, cancel);
  else proceed();
}

/** A one-shot message for the page that opens next (survives a route change, not a reload). */
const NOTICE_KEY = 'maf:mcp-notice';
export type FlashNotice = { ok: boolean; text: string; focusAdd?: boolean };
export function flashNotice(notice: FlashNotice) {
  try { sessionStorage.setItem(NOTICE_KEY, JSON.stringify(notice)); } catch { /* storage may be blocked: no message */ }
}
export function takeNotice(): FlashNotice | null {
  try {
    const raw = sessionStorage.getItem(NOTICE_KEY);
    sessionStorage.removeItem(NOTICE_KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value.text === 'string' ? value : null;
  } catch {
    return null;
  }
}
