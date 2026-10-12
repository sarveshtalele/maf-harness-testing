'use client';
import Link from 'next/link';
import { useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from 'react';
import { Section } from './page';

export function ResizeHandle({ label: name, axis = 'horizontal', start, reset }: { label: string; axis?: 'horizontal' | 'both'; reset?: () => void; start: () => (dx: number, dy: number) => void }) {
  const drag = useRef<{ x: number; y: number; move: (dx: number, dy: number) => void } | null>(null);
  return <button className={`resize-handle resize-${axis}`} aria-label={name} title={`${name}: drag or use arrow keys${reset ? '; double-click or Home to restore size' : ''}`} onDoubleClick={reset} onPointerDown={e => {
    if (e.button !== 0) return;
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, move: start() };
  }} onPointerMove={e => { const d = drag.current; if (d) d.move(e.clientX - d.x, e.clientY - d.y); }} onPointerUp={e => {
    drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  }} onLostPointerCapture={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onKeyDown={e => {
    if (e.key === 'Home' && reset) { e.preventDefault(); reset(); return; }
    const delta: Record<string, [number, number]> = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] };
    if (delta[e.key] && (axis === 'both' || e.key === 'ArrowLeft' || e.key === 'ArrowRight')) { e.preventDefault(); start()(...delta[e.key]); }
  }}><span aria-hidden="true">{axis === 'both' ? '◢' : '⋮'}</span></button>;
}

const PREFERENCE_EVENT = 'maf:preference';
/** In-memory copy for browsers that block storage: the value still applies for this page view. */
const memory = new Map<string, string>();
function readPreference(key: string): string | null {
  try { const stored = localStorage.getItem(key); if (stored != null) return stored; } catch { /* storage blocked */ }
  return memory.get(key) ?? null;
}
/**
 * A saved preference. The server and the first client render use `initial`, so hydration matches; the stored value is
 * applied right after hydration, before paint (useSyncExternalStore), so returning users never see the default flash.
 */
export function usePreference<T>(key: string, initial: T): [T, (value: T) => void] {
  const raw = useSyncExternalStore(
    notify => { window.addEventListener('storage', notify); window.addEventListener(PREFERENCE_EVENT, notify); return () => { window.removeEventListener('storage', notify); window.removeEventListener(PREFERENCE_EVENT, notify); }; },
    () => readPreference(key),
    () => null,
  );
  const value = useMemo<T>(() => { if (raw == null) return initial; try { return JSON.parse(raw) as T; } catch { return initial; } }, [raw, initial]);
  const save = (next: T) => {
    const encoded = JSON.stringify(next);
    memory.set(key, encoded);
    try { localStorage.setItem(key, encoded); } catch { /* Layout still works without storage. */ }
    window.dispatchEvent(new Event(PREFERENCE_EVENT));
  };
  return [value, save];
}

/**
 * Focus return for a modal: call the returned `remember(event.currentTarget)` in the handler that opens the dialog.
 * When `open` becomes false, focus goes back to that trigger (if it is still in the document).
 */
export function useReturnFocus(open: boolean): (trigger: EventTarget | null) => void {
  const trigger = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!open && trigger.current) { trigger.current.focus(); trigger.current = null; }
  }, [open]);
  return (element: EventTarget | null) => { trigger.current = element instanceof HTMLElement ? element : null; };
}

/** A collapsible section on a page. `expandHref` adds the ⤢ link that opens the section as its own page. */
export function Panel({ title, children, storageKey, actions, defaultOpen = true, expandHref }: { title: string; children: ReactNode; storageKey: string; actions?: ReactNode; defaultOpen?: boolean; expandHref?: string }) {
  const id = storageKey.replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-|-$/g, '') || undefined;
  return <Section id={id} className="section-card" title={title} actions={actions} defaultOpen={defaultOpen} expandHref={expandHref}>{children}</Section>;
}
