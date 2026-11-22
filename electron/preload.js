"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("releaseBoard", {
  board: (options = {}) => ipcRenderer.invoke("board:get", options),
  git: (options = {}) => ipcRenderer.invoke("git:get", options),
  compare: (options = {}) => ipcRenderer.invoke("compare:get", options),
  compareOpen: (options = {}) => ipcRenderer.invoke("compare:open", options),
  compareSources: (options = {}) => ipcRenderer.invoke("compare:openSources", options),
  compareComparison: (options = {}) => ipcRenderer.invoke("compare:openComparison", options),
  compareFile: (options = {}) => ipcRenderer.invoke("compare:openFile", options),
  pkg: () => ipcRenderer.invoke("pkg:get"),
  launchpad: (options = {}) => ipcRenderer.invoke("launchpad:get", options),
  trace: () => ipcRenderer.invoke("trace:get"),
  verifyTrace: () => ipcRenderer.invoke("trace:verify"),
  config: () => ipcRenderer.invoke("config:get"),
  saveConfig: (patch) => ipcRenderer.invoke("config:save", patch),
  openZed: () => ipcRenderer.invoke("zed:open"),
  openUrl: (url) => ipcRenderer.invoke("open:url", url),
  confirm: (request) => ipcRenderer.invoke("confirm:ask", request),
  runTask: (request) => ipcRenderer.invoke("task:run", request),
  cancelTask: (id) => ipcRenderer.invoke("task:cancel", id),
  onTaskOutput: (callback) => {
    const handler = (_event, payload) => callback(payload);
    ipcRenderer.on("task:output", handler);
    return () => ipcRenderer.removeListener("task:output", handler);
  },
  onTaskDone: (callback) => {
    const handler = (_event, payload) => callback(payload);
    ipcRenderer.on("task:done", handler);
    return () => ipcRenderer.removeListener("task:done", handler);
  },
});
