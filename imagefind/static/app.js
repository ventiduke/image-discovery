// Image Discovery page: folder picker, filters, image grid, details panel and match report.
(() => {
  "use strict";
  const TOKEN = window.IF_TOKEN;
  const PAGE = 200; // images per request
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => n.toLocaleString("en-US");
  const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;
  // Rounded down, so 5 unmatched files out of 10,000 reads 99.9%, not 100%.
  const pct = (part, whole) => (whole ? `${Math.floor((1000 * part) / whole) / 10}%` : "–");
  const bytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`);

  let library = null;
  let status = { state: "none" };
  let polling = null;
  // What's shown; kept in the URL hash so a view can be bookmarked or reloaded.
  const state = {
    folders: new Set(), view: "images", sort: "name",
    q: "", langs: false, colors: [], kinds: [], variants: [],
  };
  // The grid: images loaded so far. `run` counts restarts, so a page that
  // arrives after a restart is dropped.
  const grid = { key: "", run: 0, loaded: 0, total: null, loading: false, items: [] };
  let facets = null;
  let current = -1; // index into grid.items of the image in the details panel

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

  // Per-viewer preferences; storage can be unavailable (private windows).
  const prefs = {
    get(key, fallback) { try { return localStorage.getItem(`imagefind.${key}`) ?? fallback; } catch (e) { return fallback; } },
    set(key, value) { try { localStorage.setItem(`imagefind.${key}`, value); } catch (e) { /* ignore */ } },
  };

  function toast(text) {
    const t = el("div", { className: "toast", textContent: text, role: "status" });
    document.body.append(t);
    setTimeout(() => t.remove(), 1600);
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  // -- URL state ------------------------------------------------------------

  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const names = library ? library.folders.map((f) => f.name) : [];
    const list = (key) => (p.get(key) || "").split(",").filter(Boolean);
    state.folders = new Set(p.has("folders") ? list("folders").filter((n) => names.includes(n)) : names);
    state.view = p.get("view") === "report" ? "report" : "images";
    state.sort = ["name", "folder", "size"].includes(p.get("sort")) ? p.get("sort") : "name";
    state.q = p.get("q") || "";
    state.langs = p.get("langs") === "1";
    state.colors = list("colors").map(Number).filter((n) => Number.isInteger(n));
    state.kinds = list("kinds");
    state.variants = list("variants");
  }

  function writeHash() {
    const p = new URLSearchParams();
    if (!library.folders.every((f) => state.folders.has(f.name))) p.set("folders", [...state.folders].join(","));
    if (state.view !== "images") p.set("view", state.view);
    if (state.sort !== "name") p.set("sort", state.sort);
    if (state.q) p.set("q", state.q);
    if (state.langs) p.set("langs", "1");
    for (const key of ["colors", "kinds", "variants"]) {
      if (state[key].length) p.set(key, state[key].join(","));
    }
    const hash = p.toString();
    history.replaceState(null, "", hash ? `#${hash}` : location.pathname);
  }

  // Query string for /api/images and /api/facets.
  function filterParams() {
    const p = new URLSearchParams();
    for (const name of library.folders.map((f) => f.name)) if (state.folders.has(name)) p.append("folder", name);
    if (state.q) p.set("q", state.q);
    if (state.langs) p.set("langs", "true");
    for (const c of state.colors) p.append("color", c);
    for (const k of state.kinds) p.append("kind", k);
    for (const v of state.variants) p.append("variant", v);
    return p;
  }

  // Something that changes which images match: save it, then refresh.
  function filtersChanged() {
    writeHash();
    updateAllBox();
    renderActiveFilters();
    renderReport();
    loadGrid();
    loadFacets();
  }

  // -- page layout --------------------------------------------------------------

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

    $("warnings").replaceChildren(...library.warnings.map((text) => el("li", { textContent: text })));
    $("folders").replaceChildren(...library.folders.map((folder) => {
      const box = el("input", { type: "checkbox", checked: state.folders.has(folder.name) });
      box.addEventListener("change", () => {
        if (box.checked) state.folders.add(folder.name); else state.folders.delete(folder.name);
        filtersChanged();
      });
      return el("li", { className: folder.images ? "" : "empty" },
        el("label", {}, box,
          el("span", { className: "name", textContent: folder.name }),
          el("span", { className: "count", textContent: fmt(folder.images) })));
    }));
    $("search").value = state.q;
    $("langs").checked = state.langs;
    updateAllBox();
    renderView();
    renderStatus();
    renderActiveFilters();
    renderFacets();
    renderReport();
  }

  function updateAllBox() {
    const n = library.folders.filter((f) => state.folders.has(f.name)).length;
    const all = $("all");
    all.checked = n === library.folders.length && n > 0;
    all.indeterminate = n > 0 && !all.checked;
  }

  function renderView() {
    for (const name of ["images", "report"]) {
      $(`tab-${name}`).setAttribute("aria-selected", String(state.view === name));
      $(`view-${name}`).hidden = state.view !== name;
    }
    $("grid-controls").hidden = state.view !== "images";
    $("search-box").hidden = state.view !== "images";
    $("sort").value = state.sort;
    renderCount();
  }

  function renderCount() {
    const box = $("result-count");
    if (state.view !== "images") { box.textContent = ""; return; }
    if (!state.folders.size) { box.textContent = "No folders selected"; return; }
    box.textContent = grid.total === null ? "" : plural(grid.total, "image");
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
    const work = status.thumbs;
    const progress = $("thumbs-progress");
    progress.hidden = !(work && work.running);
    if (!progress.hidden) {
      progress.textContent = `Preparing thumbnails and colors in the background: ${fmt(work.done)} of ${fmt(work.total)}. ` +
        "Browsing works meanwhile; the color filter covers more images as this goes on.";
    }
  }

  // -- active filters -------------------------------------------------------------

  function colorBin(id) {
    return facets ? facets.colors.find((c) => c.id === id) : null;
  }

  function renderActiveFilters() {
    const chips = [];
    const chip = (content, title, remove) => {
      const b = el("button", { className: "filter-chip", title: `Remove: ${title}` }, content, el("span", { className: "x", textContent: "×" }));
      b.addEventListener("click", () => { remove(); syncControls(); filtersChanged(); });
      chips.push(b);
    };
    if (state.q) chip(`“${state.q}”`, "search", () => { state.q = ""; });
    for (const c of state.colors) {
      const bin = colorBin(c);
      const dot = el("span", { className: "dot" });
      dot.style.background = bin ? bin.hex : "#888";
      chip([dot, bin ? bin.name : `Color ${c}`], "color", () => { state.colors = state.colors.filter((x) => x !== c); });
    }
    for (const k of state.kinds) chip(kindLabel(k), "kind", () => { state.kinds = state.kinds.filter((x) => x !== k); });
    for (const v of state.variants) chip(`variant: ${v}`, "variant", () => { state.variants = state.variants.filter((x) => x !== v); });
    const box = $("active-filters");
    box.hidden = !chips.length;
    if (!chips.length) { box.replaceChildren(); return; }
    const clear = el("button", { className: "link", textContent: "Clear all" });
    clear.addEventListener("click", () => {
      Object.assign(state, { q: "", colors: [], kinds: [], variants: [] });
      syncControls();
      filtersChanged();
    });
    box.replaceChildren(el("span", { className: "label", textContent: "Showing:" }), ...chips, clear);
  }

  function syncControls() {
    $("search").value = state.q;
    $("langs").checked = state.langs;
  }

  // -- filter lists (colors, kinds, variants) ----------------------------------------

  function facetsQuery() {
    return filterParams().toString();
  }

  async function loadFacets() {
    if (!library || status.state !== "ready" || !state.folders.size) { facets = null; renderFacets(); return; }
    const key = facetsQuery();
    let result;
    try { result = await api(`/api/facets?${key}`); } catch (e) { return; }
    if (key !== facetsQuery() || result.error) return; // filters changed meanwhile
    facets = result;
    renderFacets();
    renderActiveFilters();
  }

  function toggle(list, value) {
    return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
  }

  function renderFacets() {
    const ready = facets && status.state === "ready";
    // Colors
    const hint = $("colors-hint");
    if (!ready) {
      hint.textContent = status.state === "ready" ? "Tick a folder to see colors." : "Available once indexing finishes.";
      $("swatches").replaceChildren();
    } else {
      const partial = facets.colors_analyzed < facets.colors_total;
      hint.textContent = partial
        ? `Colors worked out for ${fmt(facets.colors_analyzed)} of ${fmt(facets.colors_total)} images so far.`
        : "Main colors of each image. Pick several to need all of them.";
      $("swatches").replaceChildren(...facets.colors.map((c) => {
        const on = state.colors.includes(c.id);
        const b = el("button", {
          className: c.count || on ? "swatch" : "swatch empty",
          title: `${c.name}: ${plural(c.count, "image")}`,
          ariaLabel: `${c.name}, ${plural(c.count, "image")}`,
        }, c.count ? el("span", { className: "n", textContent: c.count > 999 ? `${Math.floor(c.count / 1000)}k` : c.count }) : null);
        b.setAttribute("aria-pressed", String(on));
        b.style.background = c.hex;
        b.addEventListener("click", () => { state.colors = toggle(state.colors, c.id); filtersChanged(); });
        return b;
      }));
    }
    // Kinds and variants
    const facetList = (id, items, key, label, field) => {
      $(id).replaceChildren(...(ready ? items : []).map((item) => {
        const value = item[key];
        const b = el("button", { className: "facet-btn" }, label(value), el("span", { className: "n", textContent: ` ${fmt(item.count)}` }));
        b.setAttribute("aria-pressed", String(state[field].includes(value)));
        b.addEventListener("click", () => { state[field] = toggle(state[field], value); filtersChanged(); });
        return el("li", {}, b);
      }));
    };
    facetList("kind-list", ready ? facets.kinds : [], "kind", kindLabel, "kinds");
    facetList("variant-list", ready ? facets.variants : [], "variant", (v) => v, "variants");
  }

  // -- image grid -------------------------------------------------------------

  // Start the grid over when what's shown changed (or force it).
  function loadGrid(force) {
    if (!library || status.state !== "ready") {
      $("grid").replaceChildren();
      Object.assign(grid, { key: "", run: grid.run + 1, total: null, loading: false, items: [] });
      $("grid-msg").textContent = status.state === "error" ? "" : "Images appear here once indexing finishes.";
      renderCount();
      return;
    }
    const p = filterParams();
    p.set("sort", state.sort);
    const key = p.toString();
    if (!force && key === grid.key) return;
    Object.assign(grid, { key, run: grid.run + 1, loaded: 0, total: null, loading: false, items: [] });
    $("grid").replaceChildren();
    $("main").scrollTop = 0;
    if (!state.folders.size) {
      $("grid-msg").textContent = "Tick one or more folders on the left.";
      renderCount();
      return;
    }
    $("grid-msg").textContent = "Loading…";
    loadMore();
  }

  async function loadMore() {
    if (grid.loading || (grid.total !== null && grid.loaded >= grid.total)) return;
    const { key, run } = grid;
    grid.loading = true;
    const p = new URLSearchParams(key);
    p.set("offset", grid.loaded);
    p.set("limit", PAGE);
    let page;
    try {
      page = await api(`/api/images?${p}`);
    } catch (e) {
      page = { error: "Can't reach the tool. Is it still running?" };
    }
    if (run !== grid.run) return;
    grid.loading = false;
    if (page.error) { $("grid-msg").textContent = page.error; return; }
    grid.total = page.total;
    const start = grid.items.length;
    grid.items.push(...page.images);
    grid.loaded += page.images.length;
    const many = state.folders.size > 1;
    $("grid").append(...page.images.map((image, i) => tile(image, start + i, many)));
    $("grid-msg").textContent = grid.total ? "" : "Nothing matches. Try removing a filter.";
    renderCount();
    if (grid.loaded < grid.total && sentinelVisible()) loadMore();
  }

  function tile(image, i, showFolder) {
    const filename = image.path.split("/").pop();
    const img = el("img", { src: image.thumb, alt: image.name, loading: "lazy", decoding: "async" });
    img.addEventListener("error", () => img.replaceWith(el("span", { className: "no-preview", textContent: "No preview" })));
    const size = image.width && image.height ? `${image.width}×${image.height}` : "";
    const a = el("a", {
      className: image.entry_key ? "tile" : "tile unmatched",
      href: image.file, target: "_blank", rel: "noopener",
      title: `${image.name}${image.kind ? ` · ${kindLabel(image.kind)}` : ""}\n${image.path}${size ? `\n${size}` : ""}`,
    },
    el("div", { className: "thumb" }, img),
    el("div", { className: "caption" },
      el("span", { className: "tile-name", textContent: image.entry_key ? image.name : filename }),
      el("div", { className: "tile-meta" },
        image.variant ? el("span", { className: "chip", textContent: image.variant }) : null,
        el("span", { textContent: showFolder ? image.folder : size }))));
    a.dataset.index = i;
    a.addEventListener("click", (e) => {
      // Ctrl/Cmd/middle click still opens the full image in a new tab.
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      openDetail(i);
    });
    return a;
  }

  function sentinelVisible() {
    const rect = $("sentinel").getBoundingClientRect();
    return rect.top < window.innerHeight + 800;
  }

  new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting) && state.view === "images" && grid.key) loadMore();
  }, { root: $("main"), rootMargin: "800px" }).observe($("sentinel"));

  function setTileSize(px) {
    $("grid").style.setProperty("--tile", `${px}px`);
    $("tile-size").value = px;
  }

  // -- details panel -------------------------------------------------------------

  async function openDetail(i) {
    if (i < 0 || i >= grid.items.length) return;
    current = i;
    for (const t of document.querySelectorAll(".tile.current")) t.classList.remove("current");
    const node = $("grid").querySelector(`[data-index="${i}"]`);
    if (node) { node.classList.add("current"); node.scrollIntoView({ block: "nearest" }); }
    await showDetail(grid.items[i].path);
    // Near the end of what's loaded: fetch more so → keeps working.
    if (i >= grid.items.length - 5) loadMore();
  }

  async function showDetail(path) {
    const drawer = $("drawer");
    const wasHidden = drawer.hidden;
    drawer.hidden = false;
    document.body.classList.add("drawer-open");
    $("d-prev").disabled = current <= 0;
    $("d-next").disabled = current < 0 || current >= (grid.total ?? 0) - 1;
    let d;
    try { d = await api(`/api/image?${new URLSearchParams({ path })}`); } catch (e) { d = { error: "Can't reach the tool." }; }
    if (d.error) { $("d-name").textContent = "Not available"; $("d-sub").textContent = ""; $("d-body").replaceChildren(el("p", { textContent: d.error })); return; }
    renderDetail(d);
    if (wasHidden) drawer.focus();
  }

  function closeDetail() {
    $("drawer").hidden = true;
    document.body.classList.remove("drawer-open");
    for (const t of document.querySelectorAll(".tile.current")) t.classList.remove("current");
    current = -1;
  }

  // A small clickable image + label for related items.
  function mini(item, label, onPick, isCurrent) {
    const b = el("button", { className: "mini", title: label, disabled: !item.image && !onPick });
    const inner = item.image
      ? el("img", { src: item.image.thumb, alt: "", loading: "lazy" })
      : el("span", { className: "none", textContent: "No image" });
    b.append(el("div", { className: "thumb" }, inner), el("span", { className: "label", textContent: label }));
    if (isCurrent) b.setAttribute("aria-current", "true");
    if (item.image || onPick) b.addEventListener("click", () => (onPick ? onPick() : showRelated(item.image.path)));
    return b;
  }

  function showRelated(path) {
    // Jump within the grid if the image is loaded there; else show it on its own.
    const i = grid.items.findIndex((x) => x.path === path);
    if (i >= 0) { openDetail(i); return; }
    current = -1;
    for (const t of document.querySelectorAll(".tile.current")) t.classList.remove("current");
    showDetail(path);
  }

  function renderDetail(d) {
    const { image, entry } = d;
    const filename = image.path.split("/").pop();
    $("d-name").textContent = entry ? entry.name || "(no name)" : filename;
    $("d-sub").textContent = [entry ? kindLabel(entry.kind) : "Not in the catalog", image.folder].join(" · ");
    const body = [];

    const preview = el("img", { src: image.file, alt: entry ? entry.name : filename });
    body.push(el("div", { className: "preview" }, preview));
    const copy = (text, what) => {
      const b = el("button", { className: "secondary", textContent: `Copy ${what}` });
      b.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(text); toast(`Copied ${what}`); } catch (e) { toast("Couldn't copy"); }
      });
      return b;
    };
    body.push(el("div", { className: "actions" },
      el("a", { className: "secondary", href: image.file, target: "_blank", rel: "noopener", textContent: "Open full image" }),
      copy(`${library.path}${library.path.includes("\\") ? "\\" : "/"}${image.path.replaceAll("/", library.path.includes("\\") ? "\\" : "/")}`, "path"),
      entry ? copy(entry.id || entry.label, "ID") : null));

    if (d.variants && d.variants.length > 1) {
      body.push(el("h3", { textContent: `Variants (${d.variants.length})` }),
        el("div", { className: "strip" }, d.variants.map((v) =>
          mini({ image: v }, v.variant || "base", null, v.path === image.path))));
    }

    if (d.colors.length) {
      const bar = el("div", { className: "color-bar" });
      const total = d.colors.reduce((s, c) => s + c.share, 0);
      for (const c of d.colors) {
        const seg = el("div", { title: `${c.name} ${Math.round(c.share * 100)}%` });
        seg.style.background = c.hex;
        seg.style.flex = String(c.share / total);
        bar.append(seg);
      }
      body.push(el("h3", { textContent: "Colors" }), bar,
        el("div", { className: "color-legend" }, d.colors.map((c) => {
          const dot = el("span", { className: "dot" });
          dot.style.background = c.hex;
          const b = el("button", { title: `Show images with ${c.name.toLowerCase()}` }, dot, `${c.name} ${Math.round(c.share * 100)}%`);
          b.addEventListener("click", () => {
            if (!state.colors.includes(c.id)) state.colors = [...state.colors, c.id];
            filtersChanged();
          });
          return b;
        })));
    } else if (!d.analyzed) {
      body.push(el("h3", { textContent: "Colors" }), el("p", { className: "hint", textContent: "Not worked out yet." }));
    }

    if (entry) {
      const rows = [];
      const row = (label, value) => rows.push(el("tr", {}, el("th", { textContent: label }), el("td", {}, value)));
      if (entry.description) row("Description", entry.description);
      for (const r of d.references) {
        const b = el("button", { className: "ref", textContent: r.name || r.key, disabled: !r.image });
        if (r.image) b.addEventListener("click", () => showRelated(r.image.path));
        row(`${r.field} → ${kindLabel(r.kind)}`, b);
      }
      const refFields = new Set(d.references.map((r) => r.field));
      for (const [k, v] of Object.entries(entry.fields)) if (!refFields.has(k)) row(k, String(v));
      row("Catalog section", entry.section);
      if (entry.id) row("ID", el("code", { textContent: entry.id }));
      if (entry.label && entry.label !== entry.id) row("Label", el("code", { textContent: entry.label }));
      row("File", el("code", { textContent: image.path }));
      if (image.width) row("Size", `${image.width} × ${image.height} px`);
      body.push(el("h3", { textContent: "Details" }), el("table", { className: "facts" }, el("tbody", {}, rows)));

      if (d.parent) {
        body.push(el("h3", { textContent: `Part of · ${kindLabel(d.parent.kind)}` }),
          el("div", { className: "strip" }, mini(d.parent, d.parent.name)));
      }
      if (d.children.length) {
        body.push(el("h3", { textContent: `Contains (${fmt(d.children_total)})` }),
          el("div", { className: "strip" }, d.children.map((c) => mini(c, c.name))));
      }
      if (d.referenced_by.length) {
        body.push(el("h3", { textContent: `Linked from (${fmt(d.referenced_by_total)})` }),
          el("p", { className: "hint", textContent: "Items whose catalog entry points here, such as a weapon's skins." }),
          el("div", { className: "strip" }, d.referenced_by.map((c) => mini(c, c.name))));
        if (d.referenced_by_total > d.referenced_by.length) {
          body.push(el("p", { className: "hint", textContent: `Showing ${d.referenced_by.length}.` }));
        }
      }
      const langs = Object.entries(entry.names);
      if (langs.length) {
        body.push(el("details", {}, el("summary", { textContent: `Name in other languages (${langs.length})` }),
          el("dl", { className: "lang-list" }, langs.map(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })]))));
      }
      body.push(el("details", {}, el("summary", { textContent: "Catalog entry (raw JSON)" }),
        el("pre", { className: "raw", textContent: JSON.stringify(entry.raw, null, 2) })));
    } else {
      body.push(el("h3", { textContent: "Details" }), el("table", { className: "facts" }, el("tbody", {},
        el("tr", {}, el("th", { textContent: "File" }), el("td", {}, el("code", { textContent: image.path }))),
        image.width ? el("tr", {}, el("th", { textContent: "Size" }), el("td", { textContent: `${image.width} × ${image.height} px` })) : null)),
      el("p", { className: "hint", textContent: "This file's name isn't a catalog ID or label, so there's no metadata for it." }));
    }
    $("d-body").replaceChildren(...body);
    $("d-body").scrollTop = 0;
  }

  $("d-close").addEventListener("click", closeDetail);
  $("d-prev").addEventListener("click", () => openDetail(current - 1));
  $("d-next").addEventListener("click", () => openDetail(current + 1));
  document.addEventListener("keydown", (e) => {
    if ($("drawer").hidden || e.target.matches("input, select, textarea")) return;
    if (e.key === "Escape") closeDetail();
    else if (e.key === "ArrowRight" && current >= 0) { e.preventDefault(); openDetail(current + 1); }
    else if (e.key === "ArrowLeft" && current > 0) { e.preventDefault(); openDetail(current - 1); }
  });

  // -- match report -----------------------------------------------------------

  function renderReport() {
    renderSummary();
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

  function renderSelection() {
    const chosen = library.folders.filter((f) => state.folders.has(f.name));
    const images = chosen.reduce((sum, f) => sum + f.images, 0);
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
      el("p", { textContent: "How each folder's files match the catalog." }),
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

  // What the catalog holds: fields per kind, with how often they're filled in.
  async function loadCatalogOverview() {
    const box = $("catalog-overview");
    if (status.state !== "ready") { box.hidden = true; return; }
    let result;
    try { result = await api("/api/catalog"); } catch (e) { return; }
    if (result.error) return;
    const share = (n, of) => `${fmt(n)} (${pct(n, of)})`;
    box.hidden = false;
    box.replaceChildren(
      el("h2", { textContent: "What the catalog holds" }),
      el("p", { textContent: "For each kind of item: how many have a description, names in other languages, " +
        "and which other fields, with example values. Every item also has an English name and an ID or label." }),
      el("div", { className: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {},
          el("th", { textContent: "Catalog kind" }),
          el("th", { className: "num", textContent: "Entries" }),
          el("th", { className: "num", textContent: "Description" }),
          el("th", { className: "num", textContent: "Other languages" }),
          el("th", { textContent: "Other fields" }))),
        el("tbody", {}, result.kinds.map((k) => el("tr", {},
          el("td", { textContent: kindLabel(k.kind) }),
          el("td", { className: "num", textContent: fmt(k.entries) }),
          el("td", { className: "num", textContent: share(k.description, k.entries) }),
          el("td", { className: "num", textContent: share(k.other_languages, k.entries) }),
          el("td", {}, k.fields.length
            ? el("div", { className: "chips" }, k.fields.map((f) => el("span", {
              className: "chip",
              title: `e.g. ${f.examples.map((x) => JSON.stringify(x)).join(", ")}`,
            }, f.field, el("span", { className: "num", textContent: ` ${pct(f.count, k.entries)}` }))))
            : el("span", { className: "hint", textContent: "–" }))))))),
      el("p", { className: "hint", textContent: "Hover a field for example values. Fields holding another item's ID " +
        "(like a skin's weaponId) show as links in an image's details." }));
  }

  // -- index status -------------------------------------------------------------

  async function refreshStatus() {
    const wasReady = status.state === "ready";
    const wasWorking = status.thumbs && status.thumbs.running;
    try { status = await api("/api/status"); } catch (e) { return; }
    if (!library) return;
    const working = status.thumbs && status.thumbs.running;
    if (status.state === "ready" && wasReady) {
      renderStatus(); // only the background progress changes
      if (wasWorking) loadFacets(); // color counts grow as colors are worked out
    } else {
      renderStatus();
      renderReport();
      loadGrid(true);
      loadFacets();
      loadCatalogOverview();
    }
    clearTimeout(polling);
    if (status.state === "indexing") polling = setTimeout(refreshStatus, 400);
    else if (working) polling = setTimeout(refreshStatus, 3000);
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
      readHash();
      status = { state: "indexing", done: 0, total: library.images };
      facets = null;
      closeDetail();
      showPanel(false);
      render();
      loadGrid(true);
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

  // -- controls -------------------------------------------------------------

  $("all").addEventListener("change", () => {
    state.folders = new Set($("all").checked ? library.folders.map((f) => f.name) : []);
    render();
    filtersChanged();
  });

  for (const tab of document.querySelectorAll(".tabs button")) {
    tab.addEventListener("click", () => {
      state.view = tab.dataset.view;
      writeHash();
      renderView();
      if (state.view === "images") loadGrid();
    });
  }

  const searchChanged = debounce(() => {
    state.q = $("search").value.trim();
    filtersChanged();
  }, 250);
  $("search").addEventListener("input", searchChanged);
  $("langs").addEventListener("change", () => { state.langs = $("langs").checked; if (state.q) filtersChanged(); else writeHash(); });

  $("sort").addEventListener("change", () => {
    state.sort = $("sort").value;
    writeHash();
    loadGrid();
  });

  setTileSize(Number(prefs.get("tileSize", 180)) || 180);
  $("tile-size").addEventListener("input", () => {
    setTileSize($("tile-size").value);
    prefs.set("tileSize", $("tile-size").value);
    if (sentinelVisible()) loadMore();
  });

  window.addEventListener("hashchange", () => {
    if (!library) return;
    readHash();
    render();
    loadGrid();
    loadFacets();
  });

  $("exit").addEventListener("click", async () => {
    clearTimeout(polling);
    try { await api("/api/shutdown", {}); } catch (e) {}
    if (window.__ifKeepalive) window.__ifKeepalive.stop();
    $("open-panel").hidden = true;
    $("layout").hidden = true;
    $("drawer").hidden = true;
    $("stopped").hidden = false;
  });

  // -- start ----------------------------------------------------------------

  api("/api/library")
    .then((result) => {
      library = result.library;
      if (!library) { showPanel(true); return; }
      readHash();
      render();
      refreshStatus();
    })
    .catch(() => { showPanel(true); unreachable(); });
})();
