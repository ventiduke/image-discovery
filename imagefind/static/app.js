// Image Discovery page: content folder picker, folder list and match report.
(() => {
  "use strict";
  const TOKEN = window.IF_TOKEN;
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => n.toLocaleString("en-US");
  const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;
  // Rounded down, so 5 unmatched files out of 10,000 reads 99.9%, not 100%.
  const pct = (part, whole) => (whole ? `${Math.floor((1000 * part) / whole) / 10}%` : "–");

  let library = null;
  let status = { state: "none" };
  let selected = new Set(); // folder names; kept in the URL hash
  let polling = null;

  async function api(path, body) {
    const res = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", "X-IF-Token": TOKEN },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return res.json();
  }

  // Small DOM builder: el("td", { className: "num" }, "12", childNode, ...)
  function el(tag, props, ...children) {
    const node = Object.assign(document.createElement(tag), props || {});
    for (const child of children.flat()) {
      if (child !== null && child !== undefined && child !== false) node.append(child);
    }
    return node;
  }

  // "weaponSkins.chromas" -> "Weapon skins › chromas"
  function kindLabel(kind) {
    const words = (s) => s.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
    const label = kind.split(".").map(words).join(" › ");
    return label.charAt(0).toUpperCase() + label.slice(1);
  }

  // -- URL state ------------------------------------------------------------

  function readHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const names = library ? library.folders.map((f) => f.name) : [];
    if (!params.has("folders")) return new Set(names);
    const wanted = params.get("folders").split(",").filter(Boolean);
    return new Set(wanted.filter((n) => names.includes(n)));
  }

  function writeHash() {
    const all = library.folders.every((f) => selected.has(f.name));
    const params = new URLSearchParams();
    if (!all) params.set("folders", [...selected].join(","));
    const hash = params.toString();
    history.replaceState(null, "", hash ? `#${hash}` : location.pathname);
  }

  // -- rendering -----------------------------------------------------------

  function stat(label, value, detail, small) {
    return el("div", { className: "stat" },
      el("div", { className: "label", textContent: label }),
      el("div", { className: small ? "value small" : "value", textContent: value }),
      detail ? el("div", { className: "detail", textContent: detail }) : null);
  }

  function render() {
    $("layout").hidden = !library;
    $("change").hidden = !library;
    // The header truncates long paths from the left (direction: rtl); the
    // left-to-right marks stop it moving the leading / or C:\ to the end.
    $("content-path").textContent = library ? `\u200E${library.path}\u200E` : "";
    $("content-path").title = library ? library.path : "";
    if (!library) return;

    renderSummary();
    $("warnings").replaceChildren(...library.warnings.map((text) => el("li", { textContent: text })));

    const list = $("folders");
    list.replaceChildren(...library.folders.map((folder) => {
      const box = el("input", { type: "checkbox", checked: selected.has(folder.name) });
      box.addEventListener("change", () => {
        if (box.checked) selected.add(folder.name); else selected.delete(folder.name);
        writeHash();
        renderSelection();
      });
      return el("li", { className: folder.images ? "" : "empty" },
        el("label", {}, box,
          el("span", { className: "name", textContent: folder.name }),
          el("span", { className: "count", textContent: fmt(folder.images) })));
    }));
    renderStatus();
    renderSelection();
    renderCoverage();
    renderUnmatched();
  }

  function renderSummary() {
    const report = status.state === "ready" ? status.report : null;
    const cards = [
      stat("Images", fmt(library.images), plural(library.folders.length, "folder")),
      report
        ? stat("Matched to catalog", pct(report.matched, report.images),
          `${fmt(report.matched)} of ${fmt(report.images)} images`)
        : stat("Matched to catalog", "…", status.state === "error" ? "Indexing failed" : "Indexing"),
      report
        ? stat("Catalog entries with images", fmt(report.entries_with_images),
          `of ${fmt(report.entries)} entries`)
        : null,
      stat("Catalog", library.version || "No version", library.catalog || "No catalog file", true),
    ];
    $("summary").replaceChildren(...cards.filter(Boolean));
  }

  function renderStatus() {
    const box = $("status");
    if (status.state === "indexing") {
      const total = status.total || 0;
      const done = Math.min(status.done || 0, total);
      box.hidden = false;
      box.className = "panel";
      box.replaceChildren(
        el("h2", { textContent: "Indexing…" }),
        el("p", { textContent: done ? `${fmt(done)} of ${fmt(total)} images` : "Reading the catalog…" }),
        el("progress", { max: total || 1, value: done }));
    } else if (status.state === "error") {
      box.hidden = false;
      box.className = "panel error";
      box.replaceChildren(el("h2", { textContent: "Couldn't index this folder" }),
        el("p", { textContent: status.error }));
    } else {
      box.hidden = true;
    }
  }

  function renderSelection() {
    const chosen = library.folders.filter((f) => selected.has(f.name));
    const images = chosen.reduce((sum, f) => sum + f.images, 0);
    const all = $("all");
    all.checked = chosen.length === library.folders.length && chosen.length > 0;
    all.indeterminate = chosen.length > 0 && !all.checked;

    const box = $("folder-report");
    if (!chosen.length) {
      box.replaceChildren(el("h2", { textContent: "No folders selected" }),
        el("p", { textContent: "Tick one or more folders on the left." }));
      return;
    }
    const heading = el("h2", { textContent: `${plural(images, "image")} in ${plural(chosen.length, "folder")}` });
    if (status.state !== "ready") {
      box.replaceChildren(heading, el("p", { textContent: "The match report appears once indexing finishes." }));
      return;
    }
    const byName = new Map(status.report.folders.map((f) => [f.name, f]));
    const rows = chosen.map((folder) => byName.get(folder.name) ||
      { name: folder.name, images: 0, matched: 0, unmatched: 0, variants: {}, kinds: {} });
    const total = rows.reduce((t, r) => ({ images: t.images + r.images, matched: t.matched + r.matched }),
      { images: 0, matched: 0 });
    const chips = (counts, label) => el("div", { className: "chips" },
      Object.entries(counts).map(([k, n]) =>
        el("span", { className: "chip" }, label(k), el("span", { className: "num", textContent: ` ${fmt(n)}` }))));

    box.replaceChildren(heading,
      el("p", { textContent: "How each folder's files match the catalog. The image grid and search come next." }),
      el("div", { className: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {},
          el("th", { textContent: "Folder" }),
          el("th", { className: "num", textContent: "Images" }),
          el("th", { className: "num", textContent: "Matched" }),
          el("th", { className: "num", textContent: "Unmatched" }),
          el("th", { textContent: "Variants" }),
          el("th", { textContent: "Catalog kind" }))),
        el("tbody", {}, rows.map((r) => el("tr", {},
          el("td", { textContent: r.name }),
          el("td", { className: "num", textContent: fmt(r.images) }),
          el("td", { className: "num", textContent: fmt(r.matched) }),
          el("td", { className: r.unmatched ? "num bad" : "num", textContent: fmt(r.unmatched) }),
          el("td", {}, chips(r.variants, (v) => v)),
          el("td", {}, chips(r.kinds, kindLabel))))),
        rows.length > 1
          ? el("tfoot", {}, el("tr", {},
            el("td", { textContent: "Total" }),
            el("td", { className: "num", textContent: fmt(total.images) }),
            el("td", { className: "num", textContent: fmt(total.matched) }),
            el("td", { className: "num", textContent: fmt(total.images - total.matched) }),
            el("td"), el("td")))
          : null)));
  }

  function renderCoverage() {
    const box = $("coverage");
    box.hidden = status.state !== "ready" || !status.report.coverage.length;
    if (box.hidden) return;
    box.replaceChildren(
      el("h2", { textContent: "Catalog coverage" }),
      el("p", { textContent: "How many entries of each kind in the catalog have at least one image." }),
      el("div", { className: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {},
          el("th", { textContent: "Catalog kind" }),
          el("th", { className: "num", textContent: "Entries" }),
          el("th", { className: "num", textContent: "With images" }),
          el("th", { className: "bar-col", textContent: "" }))),
        el("tbody", {}, status.report.coverage.map((c) => {
          const bar = el("div", { className: "bar" }, el("div", { className: "fill" }));
          bar.firstChild.style.width = c.entries ? `${(100 * c.with_images) / c.entries}%` : "0";
          return el("tr", {},
            el("td", { textContent: kindLabel(c.kind) }),
            el("td", { className: "num", textContent: fmt(c.entries) }),
            el("td", { className: "num", textContent: `${fmt(c.with_images)} (${pct(c.with_images, c.entries)})` }),
            el("td", { className: "bar-col" }, bar));
        })))));
  }

  function renderUnmatched() {
    const box = $("unmatched");
    const report = status.state === "ready" ? status.report : null;
    box.hidden = !report || !report.unmatched_total;
    if (box.hidden) return;
    const more = report.unmatched_total - report.unmatched.length;
    box.replaceChildren(el("details", {},
      el("summary", {}, el("h2", { textContent: `${plural(report.unmatched_total, "file")} match no catalog entry` })),
      el("p", { textContent: "Their names aren't a catalog ID or label, so they'll have no metadata." }),
      el("ul", { className: "paths" }, report.unmatched.map((p) => el("li", {}, el("code", { textContent: p })))),
      more > 0 ? el("p", { textContent: `… and ${fmt(more)} more. Run uv run imagefind --report for the full list.` }) : null));
  }

  // -- index status -------------------------------------------------------------

  async function refreshStatus() {
    try { status = await api("/api/status"); } catch (e) { return; }
    if (library) render();
    clearTimeout(polling);
    if (status.state === "indexing") polling = setTimeout(refreshStatus, 400);
  }

  // -- open panel -----------------------------------------------------------

  function showPanel(show) {
    $("open-panel").hidden = !show;
    $("close-panel").hidden = !library;
    $("msg").textContent = "";
    if (show) $("path").value = library ? library.path : "";
  }

  function handle(result) {
    if (result.library) {
      const changed = !library || library.path !== result.library.path;
      library = result.library;
      if (changed) history.replaceState(null, "", location.pathname);
      selected = readHash();
      status = { state: "indexing", done: 0, total: library.images };
      showPanel(false);
      render();
      refreshStatus();
      return;
    }
    if (result.cancelled) { $("msg").textContent = ""; return; }
    $("msg").textContent = result.error || "Something went wrong.";
    if (result.noDialog) $("path").focus();
  }

  const unreachable = () => { $("msg").textContent = "Can't reach the tool. Is it still running?"; };

  $("pick").addEventListener("click", async () => {
    const btn = $("pick");
    btn.disabled = true;
    $("msg").textContent = "";
    // The dialog is a separate window that may open behind the browser.
    const shown = setTimeout(() => { $("waiting").hidden = false; }, 700);
    try { handle(await api("/api/pick", {})); }
    catch (e) { unreachable(); }
    finally { clearTimeout(shown); $("waiting").hidden = true; btn.disabled = false; }
  });

  $("cancel-pick").addEventListener("click", () => { api("/api/pick-cancel", {}).catch(() => {}); });

  $("open-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("msg").textContent = "";
    try { handle(await api("/api/open", { path: $("path").value })); }
    catch (e) { unreachable(); }
  });

  $("change").addEventListener("click", () => showPanel(true));
  $("close-panel").addEventListener("click", () => showPanel(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && library && !$("open-panel").hidden) showPanel(false);
  });

  $("all").addEventListener("change", () => {
    selected = new Set($("all").checked ? library.folders.map((f) => f.name) : []);
    writeHash();
    render();
  });

  window.addEventListener("hashchange", () => {
    if (!library) return;
    selected = readHash();
    render();
  });

  $("exit").addEventListener("click", async () => {
    clearTimeout(polling);
    try { await api("/api/shutdown", {}); } catch (e) {}
    if (window.__ifKeepalive) window.__ifKeepalive.stop();
    $("open-panel").hidden = true;
    $("layout").hidden = true;
    $("stopped").hidden = false;
  });

  // -- start ----------------------------------------------------------------

  api("/api/library")
    .then((result) => {
      library = result.library;
      if (!library) { showPanel(true); return; }
      selected = readHash();
      render();
      refreshStatus();
    })
    .catch(() => { showPanel(true); unreachable(); });
})();
