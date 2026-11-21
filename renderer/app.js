"use strict";

const api = window.releaseBoard;

const state = {
  board: null,
  config: null,
  view: "overview",
  taskId: null,
  taskRoot: "",
  taskLog: [],
  timer: null,
};

const titles = {
  overview: ["Overview", "Release state across every configured root"],
  git: ["Git inspector", "Local branches, remote tracking and commit divergence"],
  packaging: ["Packaging inspector", "Debian metadata, versions, tags and signing preflight"],
  launchpad: ["Launchpad monitor", "Upload, build and publication state per series"],
  trace: ["Release traceability", "Signed tags, embedded provenance and the release ledger"],
  tasks: ["Lifecycle tasks", "Build, test, package and upload through the shared CLI"],
  settings: ["Settings", "Configured roots and release identity"],
};

function h(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text instanceof Node) node.append(text);
  else if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function pill(text, kind = "") {
  return h("span", `pill ${kind}`.trim(), text);
}

function statusKind(value) {
  const text = String(value || "").toLowerCase();
  if (["published", "built", "clean", "ok", "pass", "true"].includes(text)) return "good";
  if (["building", "pending", "uploaded", "warning", "warn"].includes(text)) return "warn";
  if (["failed", "error", "blocked", "missing", "deleted"].includes(text)) return "bad";
  return "";
}

function rootHealth(root) {
  if (!root || !root.isGit) return "bad";
  if (!root.clean || root.branches?.diverged?.length) return "warn";
  return "good";
}

function notice(message, error = false) {
  const node = document.getElementById("notice");
  node.textContent = message || "";
  node.classList.toggle("hidden", !message);
  node.classList.toggle("error", Boolean(error));
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("hidden", section.id !== `view-${view}`);
  });
  const [title, subtitle] = titles[view] || ["Release Board", ""];
  document.getElementById("view-title").textContent = title;
  document.getElementById("view-subtitle").textContent = subtitle;
  renderView(view);
}

function renderSidebar() {
  const ledger = state.board?.config?.ledger || "";
  const ledgerNode = document.getElementById("ledger-path");
  ledgerNode.textContent = ledger ? `Ledger · ${ledger.split("/").slice(-2).join("/")}` : "";
  ledgerNode.title = ledger;
}

function stat(value, label, kind = "") {
  const node = h("div", `stat ${kind}`.trim());
  node.append(h("div", "value", value));
  node.append(h("div", "label", label));
  return node;
}

function sectionTitle(text, action) {
  const node = h("div", "section-title");
  node.append(h("h2", "", text));
  if (action) node.append(action);
  return node;
}

function kv(rows) {
  const list = h("dl", "kv");
  for (const [key, value] of rows) {
    list.append(h("dt", "", key));
    const dd = h("dd", "");
    if (value instanceof Node) dd.append(value);
    else dd.textContent = value ?? "—";
    list.append(dd);
  }
  return list;
}

function button(label, className, onClick) {
  const node = h("button", className, label);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function externalLink(label, url) {
  const node = h("span", "link", label);
  node.addEventListener("click", () => {
    if (url) api.openUrl(url);
  });
  return node;
}

function addIssues(parent, errors, warnings) {
  for (const error of errors || []) parent.append(h("div", "issue bad", `ERROR · ${error}`));
  for (const warning of warnings || []) parent.append(h("div", "issue warn", `WARN · ${warning}`));
}

function renderOverview() {
  const node = document.getElementById("view-overview");
  clear(node);
  const board = state.board;
  if (!board) {
    node.append(h("div", "card muted", "No release data yet."));
    return;
  }
  const summary = board.summary || {};
  const cards = h("div", "grid cards");
  cards.append(stat(summary.published ?? 0, "Published series", "good"));
  cards.append(stat(summary.building ?? 0, "Building / pending", "warn"));
  cards.append(stat(summary.failed ?? 0, "Failed builds", "bad"));
  cards.append(stat(summary.missing ?? 0, "Missing series"));
  cards.append(stat(summary.dirtyRoots ?? 0, "Dirty roots", summary.dirtyRoots ? "warn" : ""));
  cards.append(stat(summary.packagingErrors ?? 0, "Packaging errors", summary.packagingErrors ? "bad" : ""));
  cards.append(stat(summary.traceRows ?? 0, "Trace-backed series", summary.traceRows ? "good" : ""));
  node.append(cards);

  node.append(sectionTitle("Roots"));
  const rootGrid = h("div", "grid two");
  for (const root of board.roots || []) {
    const card = h("div", "card");
    const head = h("div", "card-head");
    const left = h("div");
    left.append(h("h3", "", root.configuredName || root.name));
    left.append(h("div", "card-sub", root.path));
    head.append(left);
    head.append(pill(root.isGit ? (root.clean ? "clean" : `${root.dirty} dirty`) : "missing", rootHealth(root)));
    card.append(head);
    if (root.isGit) {
      const headNode = root.head || {};
      card.append(
        kv([
          ["Branch", root.branch],
          ["Sync", `ahead ${root.ahead || 0} · behind ${root.behind || 0}`],
          ["HEAD", `${headNode.short || ""} ${headNode.subject || ""}`],
          ["Tags", (root.headTags || []).join(", ") || "—"],
        ])
      );
    } else {
      card.append(h("div", "issue bad", root.error || "not a Git repository"));
    }
    rootGrid.append(card);
  }
  node.append(rootGrid);

  node.append(sectionTitle("Package matrix"));
  const card = h("div", "card scroll-x");
  const table = h("table");
  const thead = h("thead");
  const hr = h("tr");
  for (const label of ["Package", "Series", "Expected", "Published", "Upload", "Build", "Commit", "Tag"]) {
    hr.append(h("th", "", label));
  }
  thead.append(hr);
  table.append(thead);
  const tbody = h("tbody");
  for (const row of board.rows || []) {
    const tr = h("tr");
    tr.append(h("td", "mono nowrap", row.package));
    tr.append(h("td", "nowrap", row.series));
    tr.append(h("td", "mono nowrap", row.expectedVersion));
    tr.append(h("td", "mono nowrap", row.publishedVersion || "—"));
    tr.append(h("td", "nowrap", pill(row.uploadState, statusKind(row.uploadState))));
    tr.append(h("td", "nowrap", pill(row.buildState || row.state, statusKind(row.buildState || row.state))));
    const commitCell = h("td", "nowrap");
    if (row.traceOk) {
      commitCell.append(h("span", "mono", row.commitShort));
      if (!row.traceMatchesExpected && row.traceVersion) {
        commitCell.append(h("div", "small muted", `prev ${row.traceVersion}`));
      }
      commitCell.title = `${row.traceEvent || "release"} · ${row.traceVersion || ""} · recorded in the release ledger`;
    } else {
      commitCell.append(h("span", "mono muted", row.headShort || "—"));
      commitCell.append(h("div", "small muted", "unverified"));
      commitCell.title = "Local HEAD only; no release evidence";
    }
    tr.append(commitCell);
    tr.append(h("td", "mono nowrap", row.tag));
    tbody.append(tr);
  }
  table.append(tbody);
  card.append(table);
  node.append(card);
}

function renderGit() {
  const node = document.getElementById("view-git");
  clear(node);
  const board = state.board;
  if (!board) return;
  const toolbar = h("div", "toolbar");
  toolbar.append(
    button("Fetch remotes", "ghost", async () => {
      notice("Fetching remotes…");
      const result = await api.git({ fetch: true });
      if (!result.ok) notice(result.error || "fetch failed", true);
      else notice("");
      await refresh(false);
    })
  );
  toolbar.append(h("span", "muted small", "Fetches all remotes; inspection is read-only."));
  node.append(toolbar);

  const grid = h("div", "grid");
  for (const root of board.roots || []) {
    const card = h("div", "card scroll-x");
    const head = h("div", "card-head");
    const left = h("div");
    left.append(h("h3", "", root.configuredName || root.name));
    left.append(h("div", "card-sub", root.path));
    head.append(left, pill(root.isGit ? root.branch : "missing", rootHealth(root)));
    card.append(head);
    if (!root.isGit) {
      card.append(h("div", "issue bad", root.error || "not a Git repository"));
      grid.append(card);
      continue;
    }
    const branches = root.branches || {};
    const headNode = root.head || {};
    card.append(
      kv([
        ["Remote", root.remote || "—"],
        ["Branch", `${root.branch} → ${root.upstream || "no upstream"}`],
        ["Ahead / behind", `+${root.ahead || 0} / -${root.behind || 0}`],
        ["HEAD", `${headNode.short || ""} · ${headNode.subject || ""}`],
        ["Tags at HEAD", (root.headTags || []).join(", ") || "—"],
      ])
    );
    const lists = h("div", "stack");
    if (branches.localOnly?.length) lists.append(h("div", "issue warn", `Local only: ${branches.localOnly.join(", ")}`));
    if (branches.remoteOnly?.length) lists.append(h("div", "issue", `Remote only: ${branches.remoteOnly.join(", ")}`));
    if (branches.diverged?.length) lists.append(h("div", "issue warn", `Diverged: ${branches.diverged.join(", ")}`));
    if (!lists.childElementCount) lists.append(h("div", "muted small", "All local branches match origin."));
    if (branches.details?.length) {
      const table = h("table");
      const thead = h("thead");
      const hr = h("tr");
      for (const label of ["Branch", "Local", "Remote", "Ahead", "Behind", "State"]) hr.append(h("th", "", label));
      thead.append(hr);
      table.append(thead);
      const tbody = h("tbody");
      for (const detail of branches.details) {
        const tr = h("tr");
        tr.append(h("td", "nowrap", detail.name));
        tr.append(h("td", "mono", detail.localSha || "—"));
        tr.append(h("td", "mono", detail.remoteSha || "—"));
        tr.append(h("td", "mono", detail.ahead ?? "—"));
        tr.append(h("td", "mono", detail.behind ?? "—"));
        tr.append(h("td", "", pill(detail.status, detail.status === "in-sync" ? "good" : detail.status === "diverged" ? "warn" : "")));
        tbody.append(tr);
      }
      table.append(tbody);
      lists.append(table);
    }
    card.append(lists);
    grid.append(card);
  }
  node.append(grid);
}

function renderPackaging() {
  const node = document.getElementById("view-packaging");
  clear(node);
  const board = state.board;
  if (!board) return;
  const grid = h("div", "grid two");
  for (const pkg of board.packages || []) {
    const card = h("div", "card");
    const head = h("div", "card-head");
    const left = h("div");
    left.append(h("h3", "", pkg.source));
    left.append(h("div", "card-sub", `${pkg.version} · ${pkg.distribution}`));
    head.append(left, pill(pkg.ok ? "metadata ok" : "blocked", pkg.ok ? "good" : "bad"));
    card.append(head);
    const tag = pkg.tag || {};
    const series = pkg.series || [];
    card.append(
      kv([
        ["PPA", pkg.ppa || pkg.series?.[0]?.ppa || "—"],
        ["Series", series.map((row) => row.series).join(", ") || "—"],
        ["Tag", `${tag.name || "—"} · ${tag.present ? (tag.signed ? "signed" : "unsigned") : "missing"}`],
        ["Tag at HEAD", tag.pointsAtHead ? "yes" : "no"],
        ["Source format", pkg.sourceFormat || "—"],
        ["Binaries", (pkg.control?.binaries || []).map((item) => `${item.package} (${item.architecture})`).join(", ") || "—"],
      ])
    );
    const files = pkg.versionFiles || [];
    if (files.length) {
      const block = h("div", "card-section");
      block.append(h("div", "card-section-title", "Version files"));
      const chips = h("div", "chips");
      for (const file of files) {
        chips.append(pill(`${file.file} ${file.matches ? "✓" : "✗"}`, file.matches ? "good" : "bad"));
      }
      block.append(chips);
      card.append(block);
    }
    addIssues(card, pkg.errors, pkg.warnings);
    grid.append(card);
  }
  node.append(grid);
}

function renderLaunchpad() {
  const node = document.getElementById("view-launchpad");
  clear(node);
  const board = state.board;
  if (!board) return;
  const toolbar = h("div", "toolbar");
  toolbar.append(
    button("Refresh Launchpad", "ghost", async () => {
      notice("Refreshing Launchpad…");
      const result = await api.launchpad({ refresh: true });
      if (!result.ok) notice(result.error || "Launchpad refresh failed", true);
      else notice("");
      await refresh(false);
    })
  );
  toolbar.append(
    h("span", "muted small", `Snapshot ${board.summary?.launchpadFetchedAt || "—"}${board.summary?.launchpadCached ? " (cached)" : ""}`)
  );
  node.append(toolbar);

  const grid = h("div", "grid");
  for (const pkg of board.packages || []) {
    const card = h("div", "card scroll-x");
    const head = h("div", "card-head");
    const left = h("div");
    left.append(h("h3", "", pkg.source));
    left.append(h("div", "card-sub", `${pkg.ppa || (pkg.series || [])[0]?.ppa || ""} · ${pkg.baseVersion}`));
    head.append(left);
    card.append(head);
    const table = h("table");
    const thead = h("thead");
    const hr = h("tr");
    for (const label of ["Series", "Published", "Upload", "Build", "State", "Links"]) hr.append(h("th", "", label));
    thead.append(hr);
    table.append(thead);
    const tbody = h("tbody");
    for (const row of pkg.series || []) {
      const tr = h("tr");
      tr.append(h("td", "nowrap", row.series));
      tr.append(h("td", "mono nowrap", row.publishedVersion || "—"));
      tr.append(h("td", "nowrap", pill(row.uploadState, statusKind(row.uploadState))));
      const buildPill = pill(row.buildState || "—", statusKind(row.buildState));
      if (row.builds?.length) {
        buildPill.title = row.builds.map((item) => `${item.arch}: ${item.state}`).join("\n");
      }
      tr.append(h("td", "nowrap", buildPill));
      tr.append(h("td", "nowrap", pill(row.state, statusKind(row.state))));
      const links = h("td", "split nowrap");
      if (row.dscUrl) links.append(externalLink("dsc", row.dscUrl));
      if (row.sourceUrl) links.append(externalLink("PPA", row.sourceUrl));
      if (row.buildUrl) links.append(externalLink("build", row.buildUrl));
      if (row.logUrl && row.state === "failed") links.append(externalLink("log", row.logUrl));
      tr.append(links);
      tbody.append(tr);
    }
    table.append(tbody);
    card.append(table);
    grid.append(card);
  }
  node.append(grid);
}

function renderTrace() {
  const node = document.getElementById("view-trace");
  clear(node);
  const board = state.board;
  if (!board) return;
  const toolbar = h("div", "toolbar");
  toolbar.append(
    button("Verify evidence", "ghost", async () => {
      notice("Verifying tags, hashes and Launchpad state…");
      const result = await api.verifyTrace();
      notice("");
      renderVerifyResult(result.data);
    })
  );
  toolbar.append(h("span", "muted small", `Ledger ${board.config?.ledger || ""} · ${board.ledger?.entries ?? 0} entries`));
  node.append(toolbar);
  const result = h("div", "stack hidden");
  result.id = "verify-result";
  node.append(result);

  const entries = board.ledger?.latest || [];
  const card = h("div", "card");
  card.append(h("h3", "", "Recent ledger events"));
  if (!entries.length) {
    card.append(h("div", "card-sub", "No release events recorded yet. Build and upload a package to start the chain."));
  } else {
    const table = h("table");
    const thead = h("thead");
    const hr = h("tr");
    for (const label of ["Event", "Package", "Version", "Series", "Commit", "Recorded"]) hr.append(h("th", "", label));
    thead.append(hr);
    table.append(thead);
    const tbody = h("tbody");
    for (const entry of entries.slice().reverse()) {
      const tr = h("tr");
      tr.append(h("td", "", pill(entry.event, statusKind(entry.event))));
      tr.append(h("td", "mono", entry.package || "—"));
      tr.append(h("td", "mono", entry.version || "—"));
      tr.append(h("td", "", entry.series || "—"));
      tr.append(h("td", "mono", (entry.commit || "").slice(0, 12) || "—"));
      tr.append(h("td", "small muted", entry.recordedAt || "—"));
      tbody.append(tr);
    }
    table.append(tbody);
    card.append(table);
  }
  node.append(card);
}

function renderVerifyResult(data) {
  const node = document.getElementById("verify-result");
  if (!node) return;
  clear(node);
  if (!data) {
    node.classList.add("hidden");
    return;
  }
  node.classList.remove("hidden");
  const card = h("div", "card");
  const head = h("div", "card-head");
  head.append(h("h3", "", "Evidence verification"));
  head.append(
    pill(!data.latest ? "empty" : data.ok ? "pass" : "fail", !data.latest ? "" : data.ok ? "good" : "bad")
  );
  card.append(head);
  if (!data.results?.length) {
    card.append(h("div", "muted small", "No ledger entries to verify."));
  }
  for (const result of data.results || []) {
    const block = h("div", "stack");
    block.append(h("h3", "", `${result.package} ${result.version} · ${result.series}`));
    for (const check of result.checks || []) {
      block.append(h("div", `issue ${check.ok ? "" : "bad"}`, `${check.ok ? "PASS" : "FAIL"} · ${check.name} · ${check.detail || ""}`));
    }
    card.append(block);
  }
  node.append(card);
}

function appendConsole(text, stream) {
  state.taskLog.push({ text: String(text), stream });
  const consoleNode = document.getElementById("task-console");
  if (!consoleNode) return;
  const line = h("div", stream === "stderr" ? "issue bad" : "", String(text).replace(/\n$/, ""));
  consoleNode.append(line);
  consoleNode.scrollTop = consoleNode.scrollHeight;
}

function clearConsole() {
  state.taskLog = [];
  const consoleNode = document.getElementById("task-console");
  if (!consoleNode) return;
  clear(consoleNode);
  consoleNode.append(h("div", "muted", "Task output appears here."));
}

function renderTasks() {
  const node = document.getElementById("view-tasks");
  clear(node);
  const config = state.config;
  if (!config) return;
  const roots = config.roots || [];
  const toolbar = h("div", "toolbar");
  const label = h("label", "field");
  label.append(h("span", "", "Project root"));
  const select = h("select");
  for (const root of roots) {
    const option = h("option", "", `${root.name} · ${root.path}`);
    option.value = root.path;
    select.append(option);
  }
  select.value = state.taskRoot || roots[0]?.path || "";
  state.taskRoot = select.value;
  select.addEventListener("change", () => {
    state.taskRoot = select.value;
  });
  label.append(select);
  toolbar.append(label);
  node.append(toolbar);

  const grid = h("div", "task-grid");
  const tasks = [
    ["check", "Run preflight", "Clean tree, upstream sync, tag, signing and Launchpad drift."],
    ["test", "Run tests", "Execute the profile test command in the selected root."],
    ["tag", "Sign release tag", "Create a signed annotated tag for the local base version."],
    ["push", "Push main + tag", "Push the release branch and signed tag to origin."],
    ["binary", "Build local .deb", "Build a testable binary package in a temporary source tree."],
    ["build", "Build source", "Sign the tag and build source packages for every configured series."],
    ["upload", "Upload latest", "Upload built source changes files after confirmation."],
    ["watch", "Watch publication", "Poll Launchpad until every selected series is published or fails."],
  ];
  for (const [name, title, description] of tasks) {
    const card = h("button", "task-card");
    card.type = "button";
    card.append(h("strong", "", title));
    card.append(h("span", "", description));
    card.addEventListener("click", () => runTask(name));
    grid.append(card);
  }
  node.append(grid);

  const header = h("div", "section-title");
  header.append(h("h2", "", "Output"));
  const clearButton = button("Clear", "subtle", clearConsole);
  header.append(clearButton);
  node.append(header);
  const consoleNode = h("div", "console");
  consoleNode.id = "task-console";
  if (!state.taskLog.length) {
    consoleNode.append(h("div", "muted", "Task output appears here."));
  } else {
    for (const entry of state.taskLog) {
      consoleNode.append(h("div", entry.stream === "stderr" ? "issue bad" : "", entry.text.replace(/\n$/, "")));
    }
  }
  node.append(consoleNode);
}

function renderSettings() {
  const node = document.getElementById("view-settings");
  clear(node);
  const config = state.config;
  if (!config) return;
  const card = h("div", "card");
  card.append(h("h3", "", "Project roots"));
  card.append(h("div", "card-sub", "Each root is opened as a separate project in one Zed window."));
  const rootBox = h("div", "stack card-block");
  rootBox.id = "settings-roots";
  for (const root of config.roots || []) {
    rootBox.append(rootEditRow(root.name, root.path));
  }
  card.append(rootBox);
  const add = button("Add root", "ghost", () => {
    rootBox.append(rootEditRow("", ""));
  });
  const addBar = h("div", "toolbar card-block");
  addBar.append(add);
  card.append(addBar);

  const form = h("div", "form-grid");
  const userLabel = h("label", "field");
  userLabel.append(h("span", "", "Launchpad user"));
  const userInput = h("input");
  userInput.type = "text";
  userInput.value = config.launchpadUser || "";
  userLabel.append(userInput);
  const keyLabel = h("label", "field");
  keyLabel.append(h("span", "", "Signing key"));
  const keyInput = h("input");
  keyInput.type = "text";
  keyInput.value = config.signKey || "";
  keyLabel.append(keyInput);
  const refreshLabel = h("label", "field");
  refreshLabel.append(h("span", "", "Refresh seconds"));
  const refreshInput = h("input");
  refreshInput.type = "number";
  refreshInput.min = "15";
  refreshInput.value = config.refreshSeconds || 60;
  refreshLabel.append(refreshInput);
  form.append(userLabel, keyLabel, refreshLabel);
  card.append(h("h3", "card-section-title", "Release identity"));
  card.append(form);

  const actions = h("div", "toolbar card-actions");
  const save = button("Save settings", "primary", async () => {
    const roots = [...rootBox.querySelectorAll(".root-edit")].map((row) => ({
      name: row.querySelector(".root-name").value.trim(),
      path: row.querySelector(".root-path").value.trim(),
    }));
    const patch = {
      roots,
      launchpadUser: userInput.value.trim(),
      signKey: keyInput.value.trim(),
      refreshSeconds: Number(refreshInput.value) || 60,
    };
    const result = await api.saveConfig(patch);
    if (!result.ok) {
      notice(result.error || "failed to save settings", true);
      return;
    }
    notice("Settings saved.");
    await refresh(false, true);
    setView("settings");
  });
  actions.append(save);
  const reset = button("Reload", "ghost", async () => {
    await refresh(false, true);
    renderSettings();
  });
  actions.append(reset);
  card.append(actions);
  node.append(card);
}

function rootEditRow(name, path) {
  const row = h("div", "root-edit");
  const nameInput = h("input");
  nameInput.type = "text";
  nameInput.className = "root-name";
  nameInput.placeholder = "Name";
  nameInput.value = name || "";
  const pathInput = h("input");
  pathInput.type = "text";
  pathInput.className = "root-path";
  pathInput.placeholder = "/home/you/Project";
  pathInput.value = path || "";
  const remove = button("Remove", "subtle", () => row.remove());
  row.append(nameInput, pathInput, remove);
  return row;
}

function renderView(view) {
  if (view === "overview") renderOverview();
  else if (view === "git") renderGit();
  else if (view === "packaging") renderPackaging();
  else if (view === "launchpad") renderLaunchpad();
  else if (view === "trace") renderTrace();
  else if (view === "tasks") renderTasks();
  else if (view === "settings") renderSettings();
}

async function refresh(withLaunchpad = false, forceConfig = false) {
  const [boardResult, configResult] = await Promise.all([
    api.board({ refresh: withLaunchpad }),
    forceConfig ? api.config() : Promise.resolve({ ok: true, data: state.config }),
  ]);
  if (!boardResult.ok || !boardResult.data) {
    notice(boardResult.error || "failed to load release state", true);
    return;
  }
  state.board = boardResult.data;
  if (configResult?.ok && configResult.data) state.config = configResult.data;
  renderSidebar();
  // Keep the Settings form intact while it is being edited. Navigating back
  // to Settings, saving, or pressing Reload re-renders it explicitly.
  if (state.view !== "settings") renderView(state.view);
}

async function runTask(name) {
  if (state.taskId) {
    notice("A task is already running. Cancel it first.", true);
    return;
  }
  const root = state.taskRoot;
  if (!root) {
    notice("Select a project root first.", true);
    return;
  }
  const prompts = {
    tag: {
      title: "Sign release tag",
      message: "Create a signed release tag for the local version?",
      detail: "The tag is created on the current HEAD. It must be pushed before uploading.",
    },
    build: {
      title: "Build source packages",
      message: "Sign the release tag and build source packages for every configured series?",
      detail: "Build runs from a temporary git archive; the working tree is not modified. Launchpad signing key and gpg-agent are used.",
    },
    binary: {
      title: "Build local .deb",
      message: "Build a binary package for local installation testing?",
      detail: "The build is unsigned and stays on this host. It is not uploaded.",
    },
    push: {
      title: "Push release",
      message: "Push main and the signed release tag to origin?",
      detail: "The preflight requires main to be in sync and the tag to be signed at HEAD.",
    },
    upload: {
      title: "Upload to Launchpad",
      message: "Upload built source packages to their PPAs?",
      detail: "This performs a real signed source upload. The preflight must pass and the tag must be pushed.",
    },
    watch: {
      title: "Watch publication",
      message: "Poll Launchpad until publication completes?",
      detail: "The task can run for up to three hours.",
    },
  };
  if (prompts[name]) {
    const answer = await api.confirm(prompts[name]);
    if (!answer.ok) return;
  }
  clearConsole();
  const consoleNode = document.getElementById("task-console");
  if (consoleNode) {
    consoleNode.append(h("div", "muted", `$ release-board ${name} · ${root}`));
  }
  state.taskLog.push({ text: `$ release-board ${name} · ${root}`, stream: "stdout" });
  const result = await api.runTask({ name, root });
  if (!result.ok) {
    notice(result.error || "task failed to start", true);
    return;
  }
  state.taskId = result.id;
  notice("");
}

async function main() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.getElementById("open-zed").addEventListener("click", async () => {
    const result = await api.openZed();
    if (!result.ok) notice(result.error || "could not open Zed", true);
    else notice("Opened configured roots in Zed.");
  });

  api.onTaskOutput((payload) => {
    if (payload.id !== state.taskId) return;
    appendConsole(payload.chunk, payload.stream);
  });
  api.onTaskDone((payload) => {
    if (payload.id !== state.taskId) return;
    state.taskId = null;
    appendConsole(`\n[task finished: ${payload.code === 0 ? "success" : `exit ${payload.code}`}]\n`, payload.code === 0 ? "stdout" : "stderr");
    notice(payload.code === 0 ? "Task finished." : `Task exited with ${payload.code}.`, payload.code !== 0);
    refresh(false);
  });

  const configResult = await api.config();
  if (configResult.ok) state.config = configResult.data;
  await refresh(false);
  state.timer = setInterval(() => {
    if (!state.taskId) refresh(false);
  }, Math.max(15, Number(state.config?.refreshSeconds || 60)) * 1000);
  setView("overview");
}

main();
