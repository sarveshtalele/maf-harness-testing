'use client';
import Link from 'next/link';
import { useEffect, useRef, useState, type ReactNode } from 'react';
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

/** Preference read on the first client render (no flash of the default), guarded for storage that is blocked or full. */
export function usePreference<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try { const stored = typeof window === 'undefined' ? null : localStorage.getItem(key); return stored == null ? initial : JSON.parse(stored); } catch { return initial; }
  });
  const save = (next: T) => { setValue(next); try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* Layout still works without storage. */ } };
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
  return <Section id={id} title={title} actions={actions} defaultOpen={defaultOpen} expandHref={expandHref}>{children}</Section>;
}
