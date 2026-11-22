"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("releaseBoard", {
  board: (options = {}) => ipcRenderer.invoke("board:get", options),
  git: (options = {}) => ipcRenderer.invoke("git:get", options),
  compare: (options = {}) => ipcRenderer.invoke("compare:get", options),
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
  runTask: (request) => ipcRenderer.invoke("task:run", request),
  cancelTask: (id) => ipcRenderer.invoke("task:cancel", id),
  pauseTask: (id) => ipcRenderer.invoke("task:pause", id),
  resumeTask: (id) => ipcRenderer.invoke("task:resume", id),
  pipelineStatus: (options = {}) => ipcRenderer.invoke("pipeline:status", options),
  repairPreview: (options = {}) => ipcRenderer.invoke("repair:preview", options),
  resolveConflicts: (options = {}) => ipcRenderer.invoke("pipeline:resolve", options),
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
