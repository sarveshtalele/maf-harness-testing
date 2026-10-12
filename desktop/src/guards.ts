// Pure rules for the desktop window and the IPC bridge: no Electron import, so node:test can drive them with fake
// events. main.ts wires them to ipcMain, webContents and the default session.
import { isAppOrigin, isEvidenceUrl } from './util';

/** A minimal view of an IPC invoke event: only the sending frame's URL matters. */
export interface SenderEvent {
  senderFrame: { url: string } | null;
}

/** IPC is for the app's own page only: not an evidence report frame, not another origin, not before the UI is up. */
export function isTrustedSender(event: SenderEvent | undefined, uiPort: number | null): boolean {
  const url = event?.senderFrame?.url ?? '';
  return uiPort !== null && isAppOrigin(url, uiPort) && !isEvidenceUrl(url);
}

/** Frames (including the sandboxed report frame) may navigate only to the app origin. */
export function isFrameNavigationAllowed(url: string, uiPort: number | null): boolean {
  return uiPort !== null && isAppOrigin(url, uiPort);
}

/** Every permission request is refused: the app needs no camera, microphone, notifications or location. */
export const denyPermissionRequest = (_webContents: unknown, _permission: string, callback: (granted: boolean) => void): void => callback(false);
export const denyPermissionCheck = (): boolean => false;
