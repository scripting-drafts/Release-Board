"use strict";

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { execFile, spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const APP_ROOT = path.resolve(__dirname, "..");
const CLI = path.join(APP_ROOT, "bin", "release-board");
const TASK_TIMEOUT = 6 * 60 * 60 * 1000;

// The board is a light 2D interface; software rendering avoids Wayland/Vulkan
// and GPU shader-cache failures on NVIDIA desktops.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu-shader-disk-cache");
const running = new Map();
let mainWindow = null;
let configCache = null;
let configCacheAt = 0;

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function cli(args, timeout = 120000) {
  return new Promise((resolve) => {
    execFile(
      "python3",
      [CLI, ...args],
      { cwd: APP_ROOT, timeout, maxBuffer: 32 * 1024 * 1024, env: process.env },
      (error, stdout, stderr) => {
        const parsed = parseJson(stdout);
        if (parsed) {
          resolve({ ok: !error, data: parsed, error: error ? String(error.message) : "", stderr });
        } else {
          resolve({
            ok: false,
            data: null,
            error: error ? String(error.message) : "invalid JSON from release-board",
            stderr: stderr || stdout,
          });
        }
      }
    );
  });
}

async function getConfig(force = false) {
  if (!force && configCache && Date.now() - configCacheAt < 5000) return configCache;
  const result = await cli(["config", "--json"]);
  configCache = result.data || { roots: [] };
  configCacheAt = Date.now();
  return configCache;
}

function configRoots(config) {
  return (config.roots || []).map((item) => path.resolve(item.path));
}

function validateRoot(root, config) {
  const resolved = path.resolve(String(root || ""));
  if (!configRoots(config).includes(resolved)) {
    throw new Error("root is not configured");
  }
  return resolved;
}

function safeExternal(url) {
  try {
    const parsed = new URL(url);
    return (
      parsed.protocol === "https:" &&
      (parsed.hostname === "launchpad.net" || parsed.hostname.endsWith(".launchpad.net"))
    );
  } catch {
    return false;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: "#071417",
    title: "Release Board",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  mainWindow.removeMenu();
  mainWindow.loadFile(path.join(APP_ROOT, "renderer", "index.html"));
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (safeExternal(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event) => event.preventDefault());
}

function registerIpc() {
  ipcMain.handle("board:get", async (_event, options = {}) => {
    const args = ["board", "--json"];
    if (options.refresh) args.push("--refresh");
    return cli(args, 180000);
  });

  ipcMain.handle("git:get", async (_event, options = {}) => {
    const args = ["git", "--json"];
    if (options.fetch) args.push("--fetch");
    return cli(args, 180000);
  });

  ipcMain.handle("pkg:get", async () => cli(["pkg", "--json"], 120000));
  ipcMain.handle("launchpad:get", async (_event, options = {}) => {
    const args = ["launchpad", "--json"];
    if (options.refresh) args.push("--refresh");
    return cli(args, 120000);
  });
  ipcMain.handle("trace:get", async () => cli(["trace", "--json"], 120000));
  ipcMain.handle("trace:verify", async () => cli(["trace", "--verify", "--json"], 180000));

  ipcMain.handle("config:get", async () => {
    const config = await getConfig(true);
    return { ok: true, data: config };
  });

  ipcMain.handle("config:save", async (_event, patch) => {
    if (!patch || typeof patch !== "object" || Array.isArray(patch)) {
      return { ok: false, error: "invalid configuration patch" };
    }
    if (Array.isArray(patch.roots)) {
      if (patch.roots.length === 0 || patch.roots.length > 8) {
        return { ok: false, error: "configure between 1 and 8 roots" };
      }
      for (const root of patch.roots) {
        if (!root || typeof root.path !== "string" || !path.isAbsolute(root.path)) {
          return { ok: false, error: "each root needs an absolute path" };
        }
        if (!fs.existsSync(root.path) || !fs.statSync(root.path).isDirectory()) {
          return { ok: false, error: `root does not exist: ${root.path}` };
        }
      }
    }
    const result = await cli(["config", "--patch-json", JSON.stringify(patch), "--json"]);
    configCache = null;
    return result;
  });

  ipcMain.handle("zed:open", async () => cli(["workspace"], 30000));

  ipcMain.handle("open:url", async (_event, url) => {
    if (!safeExternal(url)) return { ok: false, error: "blocked URL" };
    await shell.openExternal(url);
    return { ok: true };
  });

  ipcMain.handle("confirm:ask", async (_event, request) => {
    const result = await dialog.showMessageBox(mainWindow, {
      type: "warning",
      buttons: ["Cancel", "Continue"],
      defaultId: 0,
      cancelId: 0,
      title: String(request?.title || "Confirm"),
      message: String(request?.message || "Continue?"),
      detail: String(request?.detail || ""),
    });
    return { ok: result.response === 1 };
  });

  ipcMain.handle("task:run", async (event, request) => {
    const config = await getConfig();
    const root = validateRoot(request?.root, config);
    const allowed = {
      check: ["check", "--root", root],
      test: ["test", "--root", root],
      tag: ["tag", "--root", root],
      push: ["push-release", "--root", root],
      binary: ["build-binary", "--root", root],
      build: ["build-source", "--root", root, "--tag"],
      upload: ["upload-latest", "--root", root, "--confirm"],
      watch: ["watch", "--root", root],
    };
    const args = allowed[request?.name];
    if (!args) return { ok: false, error: "unsupported task" };
    if (running.size > 0) return { ok: false, error: "another task is already running" };

    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const child = spawn("python3", [CLI, ...args], {
      cwd: root,
      env: { ...process.env, NO_COLOR: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    running.set(id, child);
    const send = (chunk, stream) => {
      if (!event.sender.isDestroyed()) {
        event.sender.send("task:output", { id, chunk: String(chunk), stream });
      }
    };
    child.stdout.on("data", (chunk) => send(chunk, "stdout"));
    child.stderr.on("data", (chunk) => send(chunk, "stderr"));
    child.on("error", (error) => send(`${error.message}\n`, "stderr"));
    child.on("close", (code, signal) => {
      running.delete(id);
      if (!event.sender.isDestroyed()) {
        event.sender.send("task:done", { id, code, signal });
      }
    });
    return { ok: true, id };
  });

  ipcMain.handle("task:cancel", async (_event, id) => {
    const child = running.get(String(id));
    if (!child) return { ok: false, error: "task not running" };
    child.kill("SIGTERM");
    return { ok: true };
  });
}

app.whenReady().then(async () => {
  registerIpc();
  createWindow();

  const shotArg = process.argv.find((arg) => arg.startsWith("--screenshot="));
  if (shotArg) {
    const target = shotArg.split("=", 2)[1];
    const viewArg = process.argv.find((arg) => arg.startsWith("--view="));
    const view = viewArg ? viewArg.split("=", 2)[1].replace(/[^a-z]/g, "") : "";
    const clickArg = process.argv.find((arg) => arg.startsWith("--click="));
    const click = clickArg ? clickArg.split("=", 2)[1] : "";
    mainWindow.webContents.once("did-finish-load", () => {
      setTimeout(async () => {
        if (view) {
          try {
            await mainWindow.webContents.executeJavaScript(
              `document.querySelector('[data-view="${view}"]')?.click(); true`
            );
          } catch {
            // Screenshot only.
          }
        }
        if (click) {
          try {
            await mainWindow.webContents.executeJavaScript(
              `document.querySelector(${JSON.stringify(click)})?.click(); true`
            );
          } catch {
            // Screenshot only.
          }
        }
        await new Promise((resolve) => setTimeout(resolve, click ? 5000 : 1200));
        const image = await mainWindow.webContents.capturePage();
        fs.writeFileSync(target, image.toPNG());
        app.quit();
      }, 8000);
    });
  }
});

app.on("window-all-closed", () => app.quit());
