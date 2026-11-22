"use strict";

const api = window.releaseBoard;

const state = {
  board: null,
  config: null,
  view: "overview",
  taskRoot: "",
  tasks: {},
  compareRoot: "",
  compareOrigin: "main",
  compareData: null,
  compareLoading: false,
  compareError: "",
  pipelineData: null,
  pipelineLoading: false,
  gitRefreshing: false,
  userNavigated: false,
  timer: null,
  gitTimer: null,
  pipelineTimer: null,
  compareTimer: null,
};

const titles = {
  overview: ["Overview", "Release state across every configured root"],
  git: ["Git inspector", "Local branches, remote tracking and commit divergence"],
  compare: ["Source diff", "Local Sources → Origin (main) → Released Sources"],
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

function worktreeHealth(root) {
  if (!root || !root.isGit) return "bad";
  return root.clean ? "good" : "warn";
}

function syncHealth(root) {
  if (!root || !root.isGit) return "bad";
  if (!root.upstream) return "warn";
  if (root.ahead || root.behind) return "warn";
  if (root.branches?.diverged?.includes(root.branch)) return "warn";
  return "good";
}

function syncBadge(root) {
  if (!root?.isGit) return null;
  if (!root.upstream) return { text: "no upstream", kind: "warn" };
  const parts = [];
  if (root.ahead) parts.push(`ahead ${root.ahead}`);
  if (root.behind) parts.push(`behind ${root.behind}`);
  if (!parts.length) return null;
  return { text: parts.join(" · "), kind: "warn" };
}

let noticeTimer = null;

function notice(message, error = false) {
  const node = document.getElementById("notice");
  if (noticeTimer) {
    clearTimeout(noticeTimer);
    noticeTimer = null;
  }
  node.textContent = message || "";
  node.classList.toggle("hidden", !message);
  node.classList.toggle("error", Boolean(error));
  if (message) {
    noticeTimer = setTimeout(
      () => {
        node.classList.add("hidden");
        node.textContent = "";
        noticeTimer = null;
      },
      error ? 6500 : 4000
    );
  }
}

function setView(view, fromUser = false) {
  state.view = view;
  if (fromUser) state.userNavigated = true;
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
  if (view === "tasks") loadPipelineStatus();
  if (view === "compare" && state.compareRoot) loadCompare(state.compareRoot, true);
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

function clickable(node, handler, title = "") {
  node.classList.add("clickable");
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  if (title) node.title = title;
  node.addEventListener("click", handler);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handler(event);
    }
  });
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
    const badges = h("div", "split");
    badges.append(
      pill(root.isGit ? (root.clean ? "clean" : `${root.dirty} dirty`) : "missing", worktreeHealth(root))
    );
    const sync = syncBadge(root);
    if (sync) badges.append(pill(sync.text, sync.kind));
    head.append(left, badges);
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
    const seriesCell = h("td", "nowrap");
    const seriesWrap = h("span", "split");
    seriesWrap.append(h("span", "", row.series));
    if (row.untested) seriesWrap.append(pill("untested", "warn"));
    seriesCell.append(seriesWrap);
    tr.append(seriesCell);
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
    const badges = h("div", "split");
    badges.append(pill(root.isGit ? root.branch : "missing", syncHealth(root)));
    if (root.isGit) {
      badges.append(pill(root.clean ? "clean" : `${root.dirty} dirty`, worktreeHealth(root)));
    }
    head.append(left, badges);
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

function compareStatusKind(status) {
  if (status === "A" || status === "C") return "good";
  if (status === "M" || status === "T" || status === "U") return "warn";
  if (status === "D") return "bad";
  if (status === "R") return "info";
  return "";
}

function fileLabel(count) {
  return count === 1 ? "1 file" : `${count} files`;
}

function countTreeFiles(nodes) {
  return (nodes || []).reduce((total, node) => {
    return total + (node.type === "dir" ? countTreeFiles(node.children) : 1);
  }, 0);
}

function renderFileTree(nodes, depth = 0, onFile = null) {
  const box = h("div", "file-tree");
  for (const node of nodes || []) {
    if (node.type === "dir") {
      const details = h("details", "tree-dir");
      if (depth < 1) details.open = true;
      const summary = h("summary", "tree-dir-row");
      summary.append(h("span", "mono tree-name", `${node.name}/`));
      summary.append(h("span", "muted small", fileLabel(countTreeFiles(node.children))));
      summary.addEventListener("click", (event) => event.stopPropagation());
      details.append(summary, renderFileTree(node.children, depth + 1, onFile));
      box.append(details);
    } else {
      const row = h("div", "tree-file");
      row.append(pill(node.status, compareStatusKind(node.status)));
      const name = h("span", "mono tree-name", node.name);
      name.title = node.path;
      row.append(name);
      if (node.oldPath) row.append(h("span", "muted small", `← ${node.oldPath}`));
      row.append(h("span", "muted small tree-path", node.path));
      if (onFile) {
        clickable(
          row,
          (event) => {
            event.stopPropagation();
            onFile(node.path);
          },
          `Open ${node.path} diff in Zed`
        );
      }
      box.append(row);
    }
  }
  return box;
}

function compareRefCard(label, ref, extra, onOpen = null) {
  const card = h("div", "card");
  const head = h("div", "card-head");
  const left = h("div");
  left.append(h("h3", "", label));
  left.append(h("div", "card-sub", ref?.ref || "not available"));
  head.append(left);
  if (ref?.available === false || !ref?.sha) {
    head.append(pill("unavailable", ""));
    card.append(head);
    card.append(h("div", "card-sub", ref?.warning || "No release tag for this repository."));
  } else {
    if (extra) head.append(extra);
    else head.append(pill(ref.short, "info"));
    card.append(head);
    card.append(
      kv([
        ["Commit", ref.short],
        ["Subject", ref.subject || "—"],
        ["Date", ref.date || "—"],
      ])
    );
  }
  if (onOpen) {
    clickable(card, (event) => {
      event.stopPropagation();
      onOpen();
    }, "Open the three source trees in Zed");
  }
  return card;
}

function renderCompare() {
  const node = document.getElementById("view-compare");
  clear(node);
  const roots = state.config?.roots || [];
  if (!roots.length) {
    node.append(h("div", "card muted", "No roots configured."));
    return;
  }
  if (!state.compareRoot || !roots.some((root) => root.path === state.compareRoot)) {
    state.compareRoot = roots[0].path;
    state.compareData = null;
    state.compareError = "";
  }

  const toolbar = h("div", "toolbar");
  const label = h("label", "field");
  label.append(h("span", "", "Project root"));
  const select = h("select");
  for (const root of roots) {
    const option = h("option", "", `${root.name} · ${root.path}`);
    option.value = root.path;
    select.append(option);
  }
  select.value = state.compareRoot;
  select.addEventListener("change", () => {
    state.compareRoot = select.value;
    state.compareData = null;
    state.compareError = "";
    renderCompare();
  });
  label.append(select);
  toolbar.append(label);

  const originLabel = h("label", "field");
  originLabel.append(h("span", "", "Origin branch"));
  const originSelect = h("select");
  const remoteRoot = (state.board?.roots || []).find((root) => root.path === state.compareRoot);
  const branches = new Set(["main"]);
  for (const branch of remoteRoot?.branches?.remote || []) {
    if (branch.name) branches.add(branch.name);
  }
  for (const branch of [...branches].sort()) {
    const option = h("option", "", branch);
    option.value = branch;
    originSelect.append(option);
  }
  if (!branches.has(state.compareOrigin)) state.compareOrigin = "main";
  originSelect.value = state.compareOrigin;
  originSelect.addEventListener("change", () => {
    state.compareOrigin = originSelect.value;
    state.compareData = null;
    state.compareError = "";
    renderCompare();
  });
  originLabel.append(originSelect);
  toolbar.append(originLabel);

  node.append(toolbar);

  if (state.compareLoading) {
    node.append(h("div", "card muted", "Comparing local, origin and released trees…"));
    return;
  }
  if (state.compareError) {
    node.append(h("div", "card issue bad", state.compareError));
    return;
  }
  if (!state.compareData) {
    loadCompare(state.compareRoot);
    return;
  }

  const data = state.compareData;
  if (data.warnings?.length) {
    const noticeCard = h("div", "card");
    for (const warning of data.warnings) noticeCard.append(h("div", "issue warn", warning));
    node.append(noticeCard);
  }

  const openSources = () => openCompareSources();
  const chain = h("div", "grid cards");
  chain.append(compareRefCard("Local Sources", data.refs.local, data.dirty ? pill("dirty", "warn") : null, openSources));
  chain.append(compareRefCard("Origin", data.refs.origin, null, openSources));
  chain.append(compareRefCard("Released Sources", data.refs.released, null, openSources));
  node.append(chain);

  data.comparisons.forEach((comparison, index) => {
    const card = h("div", "card");
    const head = h("div", "card-head");
    const left = h("div");
    left.append(h("h3", "", comparison.label));
    left.append(
      h(
        "div",
        "card-sub",
        `${comparison.from.ref} is ${comparison.ahead} ahead / ${comparison.behind} behind ${comparison.to.ref}`
      )
    );
    head.append(left, pill(fileLabel(comparison.fileCount), comparison.fileCount ? "info" : "good"));
    card.append(head);
    if (!comparison.files?.length) {
      card.append(h("div", "card-sub", "No differences."));
    } else {
      card.append(renderFileTree(comparison.tree, 0, (path) => openCompareFile(index, path)));
    }
    clickable(card, () => openCompareComparison(index), `Open ${comparison.label} in Zed`);
    node.append(card);
  });
}

async function openCompareSources() {
  const result = await api.compareSources({ root: state.compareRoot });
  if (!result.ok || !result.data) {
    notice(result.error || "could not open source trees", true);
    return;
  }
  const zed = result.data.zed || {};
  notice(zed.opened ? "Opened the three source trees in Zed." : zed.message || "No source trees to open.");
}

async function openCompareComparison(index) {
  const result = await api.compareComparison({ root: state.compareRoot, index });
  if (!result.ok || !result.data) {
    notice(result.error || "could not open comparison", true);
    return;
  }
  const zed = result.data.zed || {};
  notice(zed.opened ? "Opened comparison in Zed." : zed.message || "No differences to open.");
}

async function openCompareFile(index, path) {
  const result = await api.compareFile({ root: state.compareRoot, index, path });
  if (!result.ok || !result.data) {
    notice(result.error || "could not open file diff", true);
    return;
  }
  const zed = result.data.zed || {};
  notice(zed.opened ? `Opened ${path} in Zed.` : zed.message || "No file diff to open.");
}

function compareSignature(data) {
  if (!data) return "";
  return JSON.stringify({
    refs: Object.fromEntries(
      Object.entries(data.refs || {}).map(([key, ref]) => [key, [ref?.ref, ref?.sha, ref?.available]])
    ),
    comparisons: (data.comparisons || []).map((item) => [
      item.label,
      item.from?.sha,
      item.to?.sha,
      item.fileCount,
      item.ahead,
      item.behind,
      (item.files || []).map((file) => [file.path, file.status]),
    ]),
  });
}

async function loadCompare(root, silent = false) {
  if (!root || state.compareLoading) return;
  const origin = state.compareOrigin || "main";
  let shouldRender = !silent;
  if (!silent) {
    state.compareLoading = true;
    state.compareError = "";
    renderCompare();
  }
  try {
    const result = await api.compare({ root, origin });
    if (state.compareRoot !== root) return;
    if (!result.ok || !result.data) {
      if (silent) {
        notice(result.error || "comparison refresh failed", true);
        return;
      }
      state.compareError = result.error || "comparison failed";
      state.compareData = null;
      shouldRender = true;
      return;
    }
    const before = compareSignature(state.compareData);
    state.compareData = result.data;
    if (silent && before !== compareSignature(state.compareData)) shouldRender = true;
  } finally {
    if (!silent) state.compareLoading = false;
    if (shouldRender) renderCompare();
  }
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
      const seriesCell = h("td", "nowrap");
      const seriesWrap = h("span", "split");
      seriesWrap.append(h("span", "", row.series));
      if (row.untested) seriesWrap.append(pill("untested", "warn"));
      seriesCell.append(seriesWrap);
      tr.append(seriesCell);
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
    for (const label of ["Event", "Package", "Version", "Series", "Evidence", "Commit", "Recorded"]) hr.append(h("th", "", label));
    thead.append(hr);
    table.append(thead);
    const tbody = h("tbody");
    for (const entry of entries.slice().reverse()) {
      const tr = h("tr");
      tr.append(h("td", "", pill(entry.event, statusKind(entry.event))));
      tr.append(h("td", "mono", entry.package || "—"));
      tr.append(h("td", "mono", entry.version || "—"));
      tr.append(h("td", "", entry.series || "—"));
      tr.append(h("td", "", entry.slsa?.standard ? pill("SLSA", "good") : pill("legacy", "warn")));
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

function taskFor(root = state.taskRoot) {
  return state.tasks[root] || null;
}

function activeTask(root = state.taskRoot) {
  const task = taskFor(root);
  return task && task.id ? task : null;
}

function appendConsole(text, stream, root = state.taskRoot) {
  const task = taskFor(root);
  if (!task) return;
  task.log.push({ text: String(text), stream });
  if (root !== state.taskRoot || state.view !== "tasks") return;
  const consoleNode = document.getElementById("task-console");
  if (!consoleNode) return;
  const line = h("div", stream === "stderr" ? "issue bad" : "", String(text).replace(/\n$/, ""));
  consoleNode.append(line);
  consoleNode.scrollTop = consoleNode.scrollHeight;
}

function repairPrompt(data) {
  if (data.action === "bumped") {
    const changed = Array.isArray(data.changed) ? data.changed : [];
    const lines = [
      `Release ${data.from} is already built or published.`,
      `Repair will bump to ${data.to}, update ${changed.length} file(s), and commit.`,
      "",
    ];
    if (changed.length) lines.push(`Changed: ${changed.join(", ")}`, "");
    lines.push("Continue?");
    return lines.join("\n");
  }
  if (data.action === "replaced") {
    return [
      `${data.tag} does not point at HEAD and has no build, upload or publish record.`,
      data.remote
        ? "Repair will delete it locally and on the remote, then recreate the signed tag at HEAD."
        : "Repair will replace the local tag with a signed tag at HEAD.",
      "",
      "Continue?",
    ].join("\n");
  }
  return `Repair action: ${data.action || "unknown"}. Continue?`;
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
    state.pipelineData = null;
    renderTasks();
    loadPipelineStatus();
  });
  label.append(select);
  toolbar.append(label);
  node.append(toolbar);

  const active = Object.entries(state.tasks).filter(([, task]) => task.id);
  if (active.length) {
    const activeRow = h("div", "toolbar card-block");
    for (const [rootPath, task] of active) {
      const rootName = roots.find((root) => root.path === rootPath)?.name || rootPath;
      const projectButton = button(
        `${rootName} · ${task.name}${task.paused ? " · paused" : ""}`,
        "subtle",
        () => {
          state.taskRoot = rootPath;
          state.pipelineData = null;
          renderTasks();
          loadPipelineStatus();
        }
      );
      activeRow.append(projectButton);
    }
    node.append(activeRow);
  }

  const selectedTask = taskFor();
  const taskActive = Boolean(selectedTask?.id);
  const paused = Boolean(selectedTask?.paused);
  const pipeline = state.pipelineData?.state || {};
  const pipelineStatus = paused
    ? "paused"
    : taskActive && pipeline.status === "running"
      ? "running"
      : pipeline.status || "idle";
  const panel = h("div", "card pipeline-panel");
  const panelHead = h("div", "card-head");
  const panelTitle = h("div");
  panelTitle.append(h("h3", "", "Release pipeline"));
  panelTitle.append(h("div", "card-sub", "Start, pause, stop, resume, repair a stale tag, or resolve a blocked step."));
  panelHead.append(panelTitle, pill(pipelineStatus, statusKind(pipelineStatus)));
  panel.append(panelHead);
  panel.append(
    kv([
      ["Package", pipeline.package ? `${pipeline.package} ${pipeline.version || ""}` : "—"],
      ["Step", pipeline.currentStep || "—"],
      ["Updated", pipeline.updatedAt || "—"],
    ])
  );
  if (pipeline.conflicts?.length) {
    const conflicts = h("div", "card-section");
    conflicts.append(h("div", "card-section-title", "Conflicts"));
    for (const file of pipeline.conflicts) conflicts.append(h("div", "issue bad", file));
    panel.append(conflicts);
  }
  const blockers = new Set();
  for (const [name, step] of Object.entries(pipeline.steps || {})) {
    if (step && step.ok === false && step.error) blockers.add(`${name}: ${step.error}`);
  }
  if (!blockers.size && pipeline.error) blockers.add(pipeline.error);
  if (blockers.size) {
    const errors = h("div", "card-section");
    errors.append(h("div", "card-section-title", "Blockers"));
    for (const message of blockers) errors.append(h("div", "issue bad", message));
    panel.append(errors);
  }
  const actions = h("div", "toolbar card-actions");
  const canResume = paused || ["stopped", "blocked", "failed"].includes(pipeline.status);
  const start = button("Start", "primary", async () => {
    if (taskActive) return;
    await runTask("pipeline");
    loadPipelineStatus();
  });
  const pause = button("Pause", "ghost", async () => {
    if (!taskActive || paused) return;
    const result = await api.pauseTask(selectedTask.id);
    if (!result.ok) {
      notice(result.error || "could not pause task", true);
      return;
    }
    selectedTask.paused = true;
    notice("Pipeline paused.");
    renderTasks();
  });
  const stop = button("Stop", "danger", async () => {
    if (!taskActive) return;
    const result = await api.cancelTask(selectedTask.id);
    if (!result.ok) {
      notice(result.error || "could not stop task", true);
      return;
    }
    selectedTask.paused = false;
    notice("Stopping pipeline…");
    loadPipelineStatus();
  });
  const resume = button("Resume", "ghost", async () => {
    if (taskActive && paused) {
      const result = await api.resumeTask(selectedTask.id);
      if (!result.ok) {
        notice(result.error || "could not resume task", true);
        return;
      }
      selectedTask.paused = false;
      notice("Pipeline resumed.");
      renderTasks();
      return;
    }
    await runTask("pipelineResume");
    loadPipelineStatus();
  });
  const repair = button("Repair tag", "ghost", async () => {
    if (taskActive) return;
    const preview = await api.repairPreview({ root: state.taskRoot });
    const data = preview?.data || {};
    if (!preview?.ok || data.ok === false) {
      const reasons = Array.isArray(data.reasons) && data.reasons.length
        ? ` (${data.reasons.join("; ")})`
        : "";
      notice(`${data.error || preview?.error || "repair preview failed"}${reasons}`, true);
      return;
    }
    if (data.action === "none") {
      notice(data.message || "Release tag is already current.");
      return;
    }
    if (!window.confirm(repairPrompt(data))) return;
    await runTask("repair");
    loadPipelineStatus();
  });
  repair.title = "Preview and repair a stale release tag";
  const resolve = button("Resolve conflicts", "ghost", async () => {
    const result = await api.resolveConflicts({ root: state.taskRoot });
    if (!result.ok) {
      notice(result.error || "could not open conflicts", true);
      return;
    }
    notice(result.conflicts?.length ? "Opened conflicted files in Zed." : "Opened the repository in Zed.");
  });
  start.disabled = taskActive;
  pause.disabled = !taskActive || paused;
  stop.disabled = !taskActive;
  resume.disabled = !canResume && !taskActive;
  repair.disabled = taskActive || !["failed", "blocked"].includes(pipeline.status);
  resolve.disabled = !pipeline.conflicts?.length && pipeline.status !== "blocked";
  actions.append(start, pause, stop, resume, repair, resolve);
  panel.append(actions);
  node.append(panel);

  const manualTitle = h("div", "section-title");
  manualTitle.append(h("h2", "", "Manual tasks"));
  node.append(manualTitle);
  const grid = h("div", "task-grid");
  const tasks = [
    ["check", "Run preflight", "Clean tree, signing and Launchpad drift."],
    ["test", "Run tests", "Execute the profile test command in the selected root."],
    ["binary", "Build local .deb", "Build a testable binary package in a temporary source tree."],
    ["build", "Build source", "Create the signed tag automatically and build source packages for every configured series."],
    ["push", "Push main + tag", "Push the release branch and signed tag to the configured remote."],
    ["upload", "Upload latest", "Upload built source changes files after confirmation."],
    ["watch", "Check publication", "Read the current Launchpad publication state once."],
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
  node.append(header);
  const consoleNode = h("div", "console");
  consoleNode.id = "task-console";
  const output = selectedTask?.log || [];
  if (!output.length) {
    consoleNode.append(h("div", "muted", "Task output appears here."));
  } else {
    for (const entry of output) {
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
    rootBox.append(rootEditRow(root.name, root.path, root.remote || "origin", root.deployment || "launchpad"));
  }
  card.append(rootBox);
  const add = button("Add root", "ghost", () => {
    rootBox.append(rootEditRow("", "", "origin", "launchpad"));
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
  const deployment = (config.deployments || {}).launchpad || {};
  const apiLabel = h("label", "field");
  apiLabel.append(h("span", "", "Launchpad API base"));
  const apiInput = h("input");
  apiInput.type = "text";
  apiInput.value = deployment.api || "https://api.launchpad.net/1.0";
  apiLabel.append(apiInput);
  const archiveLabel = h("label", "field");
  archiveLabel.append(h("span", "", "Archive base"));
  const archiveInput = h("input");
  archiveInput.type = "text";
  archiveInput.value = deployment.archive || "https://ppa.launchpadcontent.net/{user}";
  archiveLabel.append(archiveInput);
  const webLabel = h("label", "field");
  webLabel.append(h("span", "", "Launchpad web base"));
  const webInput = h("input");
  webInput.type = "text";
  webInput.value = deployment.web || "https://launchpad.net/~{user}/+archive/ubuntu";
  webLabel.append(webInput);
  form.append(userLabel, keyLabel, refreshLabel, apiLabel, archiveLabel, webLabel);
  card.append(h("h3", "card-section-title", "Release identity"));
  card.append(form);

  const actions = h("div", "toolbar card-actions");
  const save = button("Save settings", "primary", async () => {
    const roots = [...rootBox.querySelectorAll(".root-edit")].map((row) => ({
      name: row.querySelector(".root-name").value.trim(),
      path: row.querySelector(".root-path").value.trim(),
      remote: row.querySelector(".root-remote").value.trim() || "origin",
      deployment: row.querySelector(".root-deployment").value.trim() || "launchpad",
    }));
    const patch = {
      roots,
      launchpadUser: userInput.value.trim(),
      deployments: {
        launchpad: {
          user: userInput.value.trim(),
          api: apiInput.value.trim(),
          archive: archiveInput.value.trim(),
          web: webInput.value.trim(),
        },
      },
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

function rootEditRow(name, path, remote = "origin", deployment = "launchpad") {
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
  const remoteInput = h("input");
  remoteInput.type = "text";
  remoteInput.className = "root-remote";
  remoteInput.placeholder = "origin";
  remoteInput.value = remote || "origin";
  const deploymentInput = h("input");
  deploymentInput.type = "text";
  deploymentInput.className = "root-deployment";
  deploymentInput.placeholder = "launchpad";
  deploymentInput.value = deployment || "launchpad";
  const remove = button("Remove", "subtle", () => row.remove());
  row.append(nameInput, pathInput, remoteInput, deploymentInput, remove);
  return row;
}

function renderView(view) {
  if (view === "overview") renderOverview();
  else if (view === "git") renderGit();
  else if (view === "compare") renderCompare();
  else if (view === "packaging") renderPackaging();
  else if (view === "launchpad") renderLaunchpad();
  else if (view === "trace") renderTrace();
  else if (view === "tasks") renderTasks();
  else if (view === "settings") renderSettings();
}

async function refreshGitStatus() {
  if (state.gitRefreshing || !state.board) return;
  state.gitRefreshing = true;
  try {
    const before = JSON.stringify(
      (state.board.roots || []).map((root) => [root.clean, root.dirty, root.ahead, root.behind, root.head?.sha])
    );
    const result = await api.git({ fetch: false });
    if (!result.ok || !result.data) return;
    const byPath = new Map((result.data.roots || []).map((root) => [root.path, root]));
    state.board.roots = (state.board.roots || []).map((root) => byPath.get(root.path) || root);
    state.board.summary = state.board.summary || {};
    state.board.summary.dirtyRoots = result.data.summary.dirty;
    const after = JSON.stringify(
      state.board.roots.map((root) => [root.clean, root.dirty, root.ahead, root.behind, root.head?.sha])
    );
    if (before !== after && (state.view === "overview" || state.view === "git")) {
      renderView(state.view);
    }
  } finally {
    state.gitRefreshing = false;
  }
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

async function loadPipelineStatus(rerender = true) {
  if (state.pipelineLoading || !state.taskRoot) return;
  state.pipelineLoading = true;
  try {
    const before = JSON.stringify(state.pipelineData?.state || null);
    const result = await api.pipelineStatus({ root: state.taskRoot });
    if (result.ok && result.data) {
      state.pipelineData = result.data;
      const after = JSON.stringify(state.pipelineData?.state || null);
      if (rerender && state.view === "tasks" && before !== after) renderTasks();
    }
  } finally {
    state.pipelineLoading = false;
  }
}

async function runTask(name) {
  const root = state.taskRoot;
  if (!root) {
    notice("Select a project root first.", true);
    return;
  }
  if (activeTask(root)) {
    notice("A task is already running for this project.", true);
    return;
  }
  const task = {
    id: null,
    name,
    paused: false,
    status: "starting",
    log: [{ text: `$ release-board ${name} · ${root}`, stream: "stdout" }],
  };
  state.tasks[root] = task;
  renderTasks();
  const result = await api.runTask({ name, root });
  if (!result.ok) {
    task.status = "failed";
    appendConsole(result.error || "task failed to start", "stderr", root);
    notice(result.error || "task failed to start", true);
    renderTasks();
    return;
  }
  task.id = result.id;
  task.status = "running";
  notice("");
  if (name === "pipeline" || name === "pipelineResume") loadPipelineStatus();
}

async function main() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view, true));
  });
  api.onTaskOutput((payload) => {
    const entry = Object.entries(state.tasks).find(([, task]) => task.id === payload.id);
    if (!entry) return;
    appendConsole(payload.chunk, payload.stream, entry[0]);
  });
  api.onTaskDone((payload) => {
    const entry = Object.entries(state.tasks).find(([, task]) => task.id === payload.id);
    if (!entry) return;
    const [root, task] = entry;
    task.id = null;
    task.paused = false;
    task.status = payload.code === 0 ? "completed" : `exit ${payload.code}`;
    appendConsole(
      `\n[task finished: ${payload.code === 0 ? "success" : `exit ${payload.code}`}]\n`,
      payload.code === 0 ? "stdout" : "stderr",
      root
    );
    const rootName = state.config?.roots?.find((item) => item.path === root)?.name || root;
    if (payload.code === 0 && task.name === "repair") {
      notice(`${rootName}: repair finished. Resume the pipeline to rerun preflight.`);
    } else {
      notice(
        payload.code === 0 ? `${rootName}: task finished.` : `${rootName}: task exited with ${payload.code}.`,
        payload.code !== 0
      );
    }
    refresh(false);
    loadPipelineStatus();
  });

  const configResult = await api.config();
  if (configResult.ok) state.config = configResult.data;
  await refresh(false);
  state.timer = setInterval(() => {
    if (!Object.values(state.tasks).some((task) => task.id)) refresh(false);
  }, Math.max(15, Number(state.config?.refreshSeconds || 60)) * 1000);
  state.gitTimer = setInterval(refreshGitStatus, 5000);
  state.pipelineTimer = setInterval(() => {
    if (state.view === "tasks") loadPipelineStatus();
  }, 3000);
  state.compareTimer = setInterval(() => {
    if (state.view === "compare" && state.compareRoot) loadCompare(state.compareRoot, true);
  }, 10000);
  window.addEventListener("focus", () => {
    refreshGitStatus();
    if (state.view === "compare" && state.compareRoot) loadCompare(state.compareRoot, true);
  });
  // Do not overwrite a menu choice made while the initial data was loading.
  setView(state.userNavigated ? state.view : "overview");
}

main();
