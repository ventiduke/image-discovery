// Image Discovery page: content folder picker and folder list.
(() => {
  "use strict";
  const TOKEN = window.IF_TOKEN;
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => n.toLocaleString("en-US");
  const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;

  let library = null;
  let selected = new Set(); // folder names; kept in the URL hash

  async function api(path, body) {
    const res = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", "X-IF-Token": TOKEN },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return res.json();
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

  function stat(label, value, small) {
    const el = document.createElement("div");
    el.className = "stat";
    el.innerHTML = '<div class="label"></div><div class="value"></div>';
    el.firstChild.textContent = label;
    el.lastChild.textContent = value;
    if (small) el.lastChild.classList.add("small");
    return el;
  }

  function render() {
    $("layout").hidden = !library;
    $("change").hidden = !library;
    // The header truncates long paths from the left (direction: rtl); the
    // left-to-right marks stop it moving the leading / or C:\ to the end.
    $("content-path").textContent = library ? `\u200E${library.path}\u200E` : "";
    $("content-path").title = library ? library.path : "";
    if (!library) return;

    const summary = $("summary");
    summary.replaceChildren(
      stat("Images", fmt(library.images)),
      stat("Folders", fmt(library.folders.length)),
      stat("Catalog", library.catalog || "None", true),
      stat("Catalog version", library.version || "Unknown", true),
    );

    $("warnings").replaceChildren(...library.warnings.map((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      return li;
    }));

    const list = $("folders");
    list.replaceChildren(...library.folders.map((folder) => {
      const li = document.createElement("li");
      if (!folder.images) li.className = "empty";
      li.innerHTML = '<label><input type="checkbox"><span class="name"></span><span class="count"></span></label>';
      const box = li.querySelector("input");
      box.checked = selected.has(folder.name);
      box.addEventListener("change", () => {
        if (box.checked) selected.add(folder.name); else selected.delete(folder.name);
        writeHash();
        renderSelection();
      });
      li.querySelector(".name").textContent = folder.name;
      li.querySelector(".count").textContent = fmt(folder.images);
      return li;
    }));
    renderSelection();
  }

  function renderSelection() {
    const chosen = library.folders.filter((f) => selected.has(f.name));
    const images = chosen.reduce((sum, f) => sum + f.images, 0);
    const all = $("all");
    all.checked = chosen.length === library.folders.length && chosen.length > 0;
    all.indeterminate = chosen.length > 0 && !all.checked;

    const box = $("selection");
    box.replaceChildren();
    const h = document.createElement("h2");
    const p = document.createElement("p");
    if (!chosen.length) {
      h.textContent = "No folders selected";
      p.textContent = "Tick one or more folders on the left.";
    } else {
      h.textContent = `${plural(images, "image")} in ${plural(chosen.length, "folder")}`;
      p.textContent = "The image grid and search are the next milestones; this page lists what was found.";
    }
    box.append(h, p);
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
      showPanel(false);
      render();
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
      if (library) { selected = readHash(); render(); } else { showPanel(true); }
    })
    .catch(() => { showPanel(true); unreachable(); });
})();
