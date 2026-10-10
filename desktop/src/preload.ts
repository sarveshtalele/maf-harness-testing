import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('desktop', {
  appVersion: (): Promise<string> => ipcRenderer.invoke('desktop:appVersion'),
  openDataFolder: (): Promise<void> => ipcRenderer.invoke('desktop:openDataFolder'),
  getModelSettings: (): Promise<unknown> => ipcRenderer.invoke('desktop:getModelSettings'),
  saveModelSettings: (input: unknown): Promise<unknown> => ipcRenderer.invoke('desktop:saveModelSettings', input),
  getMcpSecrets: (): Promise<unknown> => ipcRenderer.invoke('desktop:getMcpSecrets'),
  saveMcpSecrets: (server: string, values: Record<string, string>): Promise<unknown> => ipcRenderer.invoke('desktop:saveMcpSecrets', server, values),
  checkForUpdates: (): Promise<void> => ipcRenderer.invoke('desktop:checkForUpdates'),
});
