import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('desktop', {
  appVersion: (): Promise<string> => ipcRenderer.invoke('desktop:appVersion'),
  openDataFolder: (): Promise<void> => ipcRenderer.invoke('desktop:openDataFolder'),
  checkForUpdates: (): Promise<void> => ipcRenderer.invoke('desktop:checkForUpdates'),
});
