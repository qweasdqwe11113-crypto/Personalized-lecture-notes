'use strict';
/* window.zhiyeDesktop: the few things a page may ask of the client. The start-up screen (file://)
   follows the start-up; the workspace page may restart its service after saving model settings. */
const {contextBridge, ipcRenderer} = require('electron');

const api = {
  info: () => ipcRenderer.invoke('desktop:info'),
  openLogs: () => ipcRenderer.invoke('desktop:open-logs'),
  openDataDir: () => ipcRenderer.invoke('desktop:open-data'),
};

if (location.protocol === 'file:') {
  Object.assign(api, {
    state: () => ipcRenderer.invoke('launcher:state'),
    onState(callback) {
      const listener = (_event, state) => callback(state);
      ipcRenderer.on('launcher:state', listener);
      return () => ipcRenderer.removeListener('launcher:state', listener);
    },
    retry: () => ipcRenderer.invoke('launcher:retry'),
    chooseDataDir: () => ipcRenderer.invoke('launcher:choose-data'),
  });
} else {
  api.restartBackend = () => ipcRenderer.invoke('desktop:restart-service');
}

contextBridge.exposeInMainWorld('zhiyeDesktop', Object.freeze(api));
