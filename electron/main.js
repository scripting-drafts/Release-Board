"use strict";

const { app, BrowserWindow, dialog, ipcMain, nativeTheme, shell } = require("electron");
const { execFile, spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const APP_ROOT = path.resolve(__dirname, "..");
const CLI = path.join(APP_ROOT, "bin", "release-board");
const ICON_DIR = path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), "release-board");
const TASK_TIMEOUT = 6 * 60 * 60 * 1000;
const screenshotTarget = (process.argv.find((arg) => arg.startsWith("--screenshot=")) || "").split("=", 2)[1] || "";
const screenshotView = (process.argv.find((arg) => arg.startsWith("--view=")) || "").split("=", 2)[1] || "";
const screenshotClick = (process.argv.find((arg) => arg.startsWith("--click=")) || "").split("=", 2)[1] || "";
const colorScheme = (process.argv.find((arg) => arg.startsWith("--colors=")) || "").split("=", 2)[1] || "";

// The board is a light 2D interface; software rendering avoids Wayland/Vulkan
// and GPU shader-cache failures on NVIDIA desktops.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-gpu-shader-disk-cache");

function iconPath(dark = nativeTheme.shouldUseDarkColors) {
  return path.join(APP_ROOT, "assets", dark ? "release-board.png" : "release-board-light.png");
}

function syncLauncherIcon(dark = nativeTheme.shouldUseDarkColors) {
  try {
    const suffix = dark ? "dark" : "light";
    const installed = path.join(ICON_DIR, `release-board-${suffix}.png`);
    const target = fs.existsSync(installed) ? installed : iconPath(dark);
    const link = path.join(ICON_DIR, "icon.png");
    fs.mkdirSync(ICON_DIR, { recursive: true });
    fs.rmSync(link, { force: true });
    fs.symlinkSync(target, link);
  } catch {
    // The window icon still follows the theme if the launcher link fails.
  }
}
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
    backgroundColor: "#070c17",
    title: "Release Board",
    icon: iconPath(),
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
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.once("did-finish-load", () => {
    if (!mainWindow.isVisible()) mainWindow.show();
  });
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
      mainWindow.show();
    }
  }, 2000);
  const loadOptions = colorScheme ? { query: { colors: colorScheme } } : undefined;
  mainWindow.loadFile(path.join(APP_ROOT, "renderer", "index.html"), loadOptions).then(() => {
    if (!screenshotTarget) return;
    setTimeout(async () => {
      const view = screenshotView.replace(/[^a-z]/g, "");
      if (view) {
        try {
          await mainWindow.webContents.executeJavaScript(
            `document.querySelector('[data-view="${view}"]')?.click(); true`
          );
        } catch {
          // Screenshot only.
        }
      }
      if (screenshotClick) {
        try {
          await mainWindow.webContents.executeJavaScript(
            `document.querySelector(${JSON.stringify(screenshotClick)})?.click(); true`
          );
        } catch {
          // Screenshot only.
        }
      }
      await new Promise((resolve) => setTimeout(resolve, screenshotClick ? 5000 : 1200));
      try {
        const image = await mainWindow.webContents.capturePage();
        fs.writeFileSync(screenshotTarget, image.toPNG());
      } catch (error) {
        fs.writeFileSync(`${screenshotTarget}.error.txt`, String(error?.stack || error));
      } finally {
        app.quit();
      }
    }, 8000);
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (safeExternal(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event) => event.preventDefault());
  mainWindow.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    if (level >= 3) {
      console.error(`[renderer] ${message} (${sourceId}:${line})`);
    }
  });
  mainWindow.webContents.on("render-process-gone", (_event, details) => {
    console.error(`[renderer-gone] ${details.reason}`);
  });
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

  ipcMain.handle("compare:get", async (_event, options = {}) => {
    const config = await getConfig();
    const root = validateRoot(options.root, config);
    const origin = /^[A-Za-z0-9._/-]+$/.test(String(options.origin || "main")) ? String(options.origin || "main") : "main";
    return cli(["compare", "--json", "--root", root, "--origin", origin], 180000);
  });

  ipcMain.handle("compare:open", async (_event, options = {}) => {
    const config = await getConfig();
    const root = validateRoot(options.root, config);
    const origin = /^[A-Za-z0-9._/-]+$/.test(String(options.origin || "main")) ? String(options.origin || "main") : "main";
    return cli(["compare", "--json", "--open", "--root", root, "--origin", origin], 300000);
  });


  ipcMain.handle("compare:openSources", async (_event, options = {}) => {
    const config = await getConfig();
    const root = validateRoot(options.root, config);
    return cli(["compare", "--json", "--open-sources", "--root", root], 300000);
  });

  ipcMain.handle("compare:openComparison", async (_event, options = {}) => {
    const config = await getConfig();
    const root = validateRoot(options.root, config);
    const index = Number(options.index);
    if (!Number.isInteger(index) || index < 0) return { ok: false, error: "invalid comparison index" };
    return cli(["compare", "--json", "--open-comparison", String(index), "--root", root], 180000);
  });

  ipcMain.handle("compare:openFile", async (_event, options = {}) => {
    const config = await getConfig();
    const root = validateRoot(options.root, config);
    const index = Number(options.index);
    if (!Number.isInteger(index) || index < 0) return { ok: false, error: "invalid comparison index" };
    const path = String(options.path || "");
    if (!path) return { ok: false, error: "missing file path" };
    return cli(
      ["compare", "--json", "--open-file", path, "--open-comparison", String(index), "--root", root],
      180000
    );
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

const hasSingleInstanceLock = app.requestSingleInstanceLock();

if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) {
      if (app.isReady()) createWindow();
      return;
    }
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    registerIpc();
    syncLauncherIcon();
    nativeTheme.on("updated", () => {
      syncLauncherIcon();
      if (mainWindow) mainWindow.setIcon(iconPath());
    });
    createWindow();
  });
}

app.on("window-all-closed", () => app.quit());
