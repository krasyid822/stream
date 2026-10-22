const TOKEN = document.querySelector('meta[name="ui-token"]').content;
const UI_VERSION = document.querySelector('meta[name="ui-version"]').content;
const $ = (id) => document.getElementById(id);
const state = {
  releases: [], selected: null, remoteAssets: [], preview: null, jobTimer: null,
  jobActive: false, pendingReload: false, config: {types: [], categories: []},
  special: null, baseAliases: [], originalTitle: "", malResults: [], malSelected: null,
};

function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.className = "toast"; }, 4200);
}

async function api(path, opts = {}) {
  const headers = Object.assign({"Content-Type": "application/json", "X-UI-Token": TOKEN}, opts.headers || {});
  const res = await fetch(path, Object.assign({}, opts, {headers}));
  let data = {};
  try { data = await res.json(); } catch (_) { data = {error: "Respons bukan JSON."}; }
  if (!res.ok || data.error) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function fmtSize(n) {
  n = Number(n || 0);
  if (n >= 1024**3) return (n/1024**3).toFixed(2) + " GB";
  if (n >= 1024**2) return (n/1024**2).toFixed(1) + " MB";
  if (n >= 1024) return (n/1024).toFixed(0) + " KB";
  return n + " B";
}

let coverPreviewTimer = null;
let coverPreviewToken = 0;

function isHttpImageUrl(raw) {
  try {
    const url = new URL(raw);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch (_) {
    return false;
  }
}

function getCoverUrl() {
  return ($("coverPreviewUrl").textContent || "").replace(/[\r\n\t]+/g, "").trim();
}

function setCoverUrl(url) {
  $("coverPreviewUrl").textContent = url || "";
  renderCoverPreview(getCoverUrl());
}

function updateCoverPreview(immediate = false) {
  clearTimeout(coverPreviewTimer);
  const raw = getCoverUrl();
  if (immediate) {
    renderCoverPreview(raw);
    return;
  }
  coverPreviewTimer = setTimeout(() => renderCoverPreview(raw), 350);
}

function renderCoverPreview(raw) {
  const box = $("coverPreview");
  const img = $("coverPreviewImg");
  const placeholder = $("coverPreviewPlaceholder");
  const status = $("coverPreviewStatus");
  const token = ++coverPreviewToken;

  img.onload = null;
  img.onerror = null;

  if (!raw) {
    box.dataset.state = "empty";
    img.classList.remove("ready");
    img.removeAttribute("src");
    placeholder.textContent = "Belum ada URL";
    status.textContent = "Tempel URL gambar untuk melihat pratinjau.";
    return;
  }

  box.dataset.state = "loading";
  img.classList.remove("ready");
  img.removeAttribute("src");
  placeholder.textContent = "Memuat…";

  if (!isHttpImageUrl(raw)) {
    box.dataset.state = "error";
    placeholder.textContent = "URL belum valid";
    status.textContent = "Gunakan URL gambar lengkap dengan http:// atau https://.";
    return;
  }

  status.textContent = "Memuat gambar…";
  img.onload = () => {
    if (token !== coverPreviewToken) return;
    box.dataset.state = "ready";
    img.classList.add("ready");
    status.textContent = `Sampul dimuat · ${img.naturalWidth}×${img.naturalHeight} px`;
  };
  img.onerror = () => {
    if (token !== coverPreviewToken) return;
    box.dataset.state = "error";
    img.classList.remove("ready");
    placeholder.textContent = "Gagal memuat gambar";
    status.textContent = "Gambar tidak bisa dimuat. Cek URL, hotlink, atau format file.";
  };
  img.src = raw;
  if (img.complete && img.naturalWidth) img.onload();
}

function currentRawMode() {
  const selected = document.querySelector('input[name="rawMode"]:checked');
  return selected ? selected.value : "raw";
}

function setRawMode(mode) {
  const value = mode === "hardsub" ? "hardsub" : "raw";
  document.querySelectorAll('input[name="rawMode"]').forEach((radio) => {
    radio.checked = radio.value === value;
  });
}

function isNativeTitle(value) {
  return /[\u0400-\u052f\u0600-\u06ff\u0900-\u097f\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]/.test(value || "");
}

function updateAliasesFromTitle() {
  const title = $("title").value.trim();
  const aliases = [];
  if (title) {
    aliases.push(title);
    for (const alias of state.baseAliases || []) {
      if (alias === title || alias === state.originalTitle) continue;
      aliases.push(alias);
    }
  }
  const unique = Array.from(new Set(aliases));
  const first = unique.shift() || "";
  const latin = unique.filter((alias) => !isNativeTitle(alias));
  const native = unique.filter((alias) => isNativeTitle(alias));
  $("aliases").value = [first, ...latin, ...native].filter(Boolean).join("\n");
}

function slugFromTag() {
  return $("tag").value.trim().toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function computedDefaultDest() {
  const category = $("category").value.trim();
  const slug = slugFromTag();
  const season = $("season").value.trim();
  if (!category || !slug) return "";
  return season ? `${category}/${slug}/${season}` : `${category}/${slug}`;
}

function updateDestHint() {
  const dest = computedDefaultDest();
  $("destHint").textContent = dest ? `Folder default: ${dest}` : "Folder default: isi kategori dan tag release.";
  document.querySelectorAll("#assetRows .asset-dest, #mappingRows .map-dest").forEach((field) => {
    if (field.dataset.auto === "1") field.value = dest;
  });
}

function renderConfigSelects(typeValue = "", categoryValue = "") {
  const typeSelect = $("episodeType");
  const categorySelect = $("category");
  const type = typeValue || typeSelect.value || state.config.types[0] || "Episodes";
  const category = categoryValue || categorySelect.value || state.config.categories[0] || "";
  const types = Array.from(new Set([...(state.config.types || []), type].filter(Boolean)));
  const categories = Array.from(new Set([...(state.config.categories || []), category].filter(Boolean)));
  typeSelect.innerHTML = types.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("")
    + '<option value="__add__">+ Tambah tipe…</option>';
  categorySelect.innerHTML = categories.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("")
    + '<option value="__add__">+ Tambah kategori…</option>';
  typeSelect.value = type;
  categorySelect.value = category;
  typeSelect.dataset.previous = type;
  categorySelect.dataset.previous = category;
  updateDestHint();
}

async function loadConfig() {
  try {
    const data = await api("/api/config");
    state.config = data.config || {types: [], categories: []};
  } catch (e) {
    toast("Gagal memuat release_ui.ini: " + e.message, true);
  }
  renderConfigSelects();
}

async function addConfigChoice(kind, select) {
  const label = kind === "types" ? "tipe" : "kategori";
  const previous = select.dataset.previous || select.value;
  const raw = prompt(`Nama ${label} baru:`, "");
  if (raw == null || !raw.trim()) {
    select.value = previous;
    return;
  }
  const value = raw.trim();
  const invalid = kind === "types" ? /[\/\\\r\n,]/ : /[\s/\\,]/;
  if (!value || value.length > 40 || invalid.test(value)) {
    toast(kind === "types"
      ? "Nama tipe maksimal 40 karakter, tanpa / \\ , atau baris baru."
      : "Nama kategori maksimal 40 karakter, tanpa spasi, / \\ , atau baris baru.", true);
    select.value = previous;
    return;
  }
  if (!state.config[kind].some((v) => v.toLowerCase() === value.toLowerCase())) {
    state.config[kind].push(value);
  }
  try {
    const data = await api("/api/config", {
      method: "POST",
      body: JSON.stringify({types: state.config.types, categories: state.config.categories}),
    });
    state.config = data.config;
    renderConfigSelects(kind === "types" ? value : "", kind === "categories" ? value : "");
    toast(`${label[0].toUpperCase() + label.slice(1)} disimpan ke release_ui.ini.`);
  } catch (e) {
    toast("Gagal menyimpan konfigurasi: " + e.message, true);
    renderConfigSelects();
  }
}

function setWorkspaceVisible(visible) {
  $("workspace").classList.toggle("hidden", !visible);
}

function folderCounts() {
  return Array.from(document.querySelectorAll("#assetRows tr"))
    .map((tr) => Number(tr.dataset.videoCount || 0))
    .filter((n) => n > 0);
}

function clearMalResults() {
  state.malResults = [];
  state.malSelected = null;
  $("malEpisodeBox").classList.add("hidden");
  $("malEpisodeNote").textContent = "";
  $("malEpisodeNote").style.color = "";
  $("malCandidates").classList.add("hidden");
  $("malCandidates").innerHTML = "";
}

function updateMalComparison() {
  if (!state.malSelected) return;
  const episodes = Number(state.malSelected.episodes || 0);
  const counts = folderCounts();
  let text = `MAL: ${state.malSelected.title} · ${episodes || "?"} episode.`;
  let mismatch = false;
  if (counts.length) {
    const unique = Array.from(new Set(counts));
    if (unique.length === 1) {
      text += ` Folder berisi ${unique[0]} video.`;
      mismatch = Boolean(episodes && unique[0] !== episodes);
    } else {
      text += ` Beberapa folder: ${unique.join(", ")} video.`;
    }
  }
  if (mismatch) text += " Periksa kecocokan jumlah episode.";
  $("malEpisodeNote").textContent = text;
  $("malEpisodeNote").style.color = mismatch ? "var(--accent2)" : "";
}

function selectMalResult(index) {
  const result = state.malResults[index];
  if (!result) return;
  state.malSelected = result;
  if (Number(result.episodes) > 0) $("episodeCount").value = Number(result.episodes);
  $("malCandidates").value = String(index);
  updateMalComparison();
}

function renderMalResults() {
  const box = $("malCandidates");
  box.innerHTML = state.malResults.map((result, index) => {
    const eps = result.episodes ? `${result.episodes} eps` : "? eps";
    const media = result.mediaType ? ` · ${result.mediaType}` : "";
    return `<option value="${index}">${esc(result.title)} — ${eps}${esc(media)}</option>`;
  }).join("");
  box.classList.toggle("hidden", state.malResults.length === 0);
  $("malEpisodeBox").classList.remove("hidden");
}

async function checkMalEpisodes() {
  const title = $("title").value.trim();
  if (!title) { toast("Isi Judul release dulu sebelum cek MAL.", true); return; }
  const season = $("season").value.trim();
  const button = $("btnMalEpisodes");
  button.disabled = true;
  button.textContent = "…";
  try {
    const aliases = $("aliases").value.split(/\r?\n/).map((v) => v.trim()).filter(Boolean);
    const data = await api("/api/mal-season", {
      method: "POST",
      body: JSON.stringify({q: title, season, aliases}),
    });
    state.malResults = data.results || [];
    if (!state.malResults.length) {
      clearMalResults();
      $("malEpisodeBox").classList.remove("hidden");
      $("malEpisodeNote").textContent = "MAL tidak menemukan kandidat untuk judul/season ini.";
      toast("MAL tidak menemukan kandidat.", true);
      return;
    }
    renderMalResults();
    selectMalResult(0);
    const selected = state.malResults[0];
    toast(`MAL: ${selected.title} · ${selected.episodes || "?"} episode.`);
  } catch (e) {
    toast("Cek MAL gagal: " + e.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Cek MAL";
  }
}

function scheduleFolderInfo(tr) {
  clearTimeout(tr._folderTimer);
  tr._folderTimer = setTimeout(() => checkFolderInfo(tr), 450);
}

async function checkFolderInfo(tr, showToast = false) {
  const pathField = tr.querySelector(".asset-path");
  const infoEl = tr.querySelector(".asset-info");
  const raw = pathField.value.trim();
  delete tr.dataset.videoCount;
  if (!raw) {
    infoEl.textContent = "";
    infoEl.className = "asset-info hint";
    updateMalComparison();
    return;
  }
  infoEl.textContent = "Memeriksa folder…";
  infoEl.className = "asset-info hint";
  try {
    const data = await api("/api/folder-info?path=" + encodeURIComponent(raw)
      + "&resolution=" + encodeURIComponent($("resolution").value.trim()));
    const info = data.folder;
    tr.dataset.videoCount = String(info.count);
    infoEl.textContent = `${info.count} video · ${fmtSize(info.totalSize)} · arsip ${info.suggestedName}`;
    infoEl.className = "asset-info ok";
    if (showToast) toast(`Folder RAW: ${info.count} video terdeteksi.`);
    updateMalComparison();
  } catch (e) {
    infoEl.textContent = e.message;
    infoEl.className = "asset-info error";
    updateMalComparison();
  }
}

function addAssetRow(path = "", dest = "") {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td>
      <input class="asset-path" placeholder="/path/ke/folder-raw-satu-season">
      <div class="asset-info hint"></div>
    </td>
    <td><input class="asset-dest" placeholder="anime/judul/1"></td>
    <td><button class="icon-btn danger" title="Hapus baris">✕</button></td>`;
  const pathField = tr.querySelector(".asset-path");
  const destField = tr.querySelector(".asset-dest");
  pathField.value = path;
  destField.value = dest || computedDefaultDest();
  if (!dest) destField.dataset.auto = "1";
  destField.addEventListener("input", () => { delete destField.dataset.auto; });
  pathField.addEventListener("input", () => scheduleFolderInfo(tr));
  pathField.addEventListener("change", () => checkFolderInfo(tr, true));
  tr.querySelector("button").addEventListener("click", () => tr.remove());
  $("assetRows").appendChild(tr);
  if (path) checkFolderInfo(tr);
}

function addMappingRow(name = "", dest = "") {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input class="map-name" placeholder="nama_file.7z"></td>
    <td><input class="map-dest" placeholder="anime/judul/1"></td>
    <td><button class="icon-btn danger" title="Hapus baris">✕</button></td>`;
  tr.querySelector(".map-name").value = name;
  const mapDest = tr.querySelector(".map-dest");
  mapDest.value = dest || computedDefaultDest();
  if (!dest) mapDest.dataset.auto = "1";
  mapDest.addEventListener("input", () => { delete mapDest.dataset.auto; });
  tr.querySelector("button").addEventListener("click", () => tr.remove());
  $("mappingRows").appendChild(tr);
}

function readRows(selector, cls, key) {
  const out = [];
  document.querySelectorAll(selector + " tr").forEach((tr) => {
    const val = tr.querySelector("." + cls).value.trim();
    const dest = tr.querySelector(".asset-dest, .map-dest").value.trim();
    if (val || dest) out.push({[key]: val, dest});
  });
  return out;
}

function readForm() {
  if (state.special === "password") {
    return {
      tag: "password",
      special: "password",
      title: $("specialTitle").value.trim(),
      password_variants: $("passwordVariants").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
      save_metadata: false,
      commit_metadata: false,
      draft_only: false,
      publish_draft: false,
      clobber: false,
      clobber_ack: false,
      mal_id: null,
      mal_title: "",
      confirm: $("confirmTag").value.trim(),
    };
  }
  return {
    tag: $("tag").value.trim(),
    title: $("title").value.trim(),
    raw_mode: currentRawMode(),
    resolution: $("resolution").value.trim(),
    episode_count: Number($("episodeCount").value || 0),
    episode_type: $("episodeType").value,
    cover_url: getCoverUrl(),
    aliases: $("aliases").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
    assets: readRows("#assetRows", "asset-path", "path"),
    mappings: readRows("#mappingRows", "map-name", "name"),
    provider_url: $("providerUrl").value.trim(),
    provider_name: $("providerName").value.trim(),
    provider_icon: $("providerIcon").value.trim(),
    save_metadata: true,
    commit_metadata: false,
    draft_only: $("optDraftOnly").checked,
    publish_draft: false,
    clobber: true,
    clobber_ack: true,
    mal_id: state.malSelected ? state.malSelected.id : null,
    mal_title: state.malSelected ? state.malSelected.title : "",
    confirm: $("confirmTag").value.trim(),
  };
}

function invalidatePreview() {
  state.preview = null;
  $("btnApply").disabled = true;
  $("confirmHint").textContent = "pratinjau ulang setelah mengubah form";
}

function setMode(mode) {
  const badge = $("modeBadge");
  if (mode === "edit") { badge.textContent = "EDIT"; badge.className = "badge edit"; }
  else { badge.textContent = "BARU"; badge.className = "badge new"; }
}

function setSpecialMode(mode) {
  state.special = mode || null;
  document.querySelectorAll(".regular-only").forEach((el) => el.classList.toggle("hidden", Boolean(mode)));
  $("specialCard").classList.toggle("hidden", !mode);
  $("applyCard").classList.toggle("hidden", mode === "metadata-guide");
  if (!mode) {
    $("specialContent").innerHTML = "";
    $("btnPreview").disabled = false;
  }
}

function splitDest(dest) {
  const parts = String(dest || "").trim().split("/").filter(Boolean);
  return {
    category: parts[0] || "",
    season: parts.length > 2 && /^\d+$/.test(parts[2] || "") ? parts[2] : "",
  };
}

function renderSpecialCard(parsed) {
  const mode = parsed.special;
  setSpecialMode(mode);
  state.preview = null;
  $("previewCard").classList.add("hidden");
  $("jobCard").classList.add("hidden");
  $("confirmTag").value = "";
  $("confirmHint").textContent = "—";
  $("btnApply").disabled = true;

  if (mode === "password") {
    $("specialHeading").textContent = "Kunci Arsip (tag password)";
    $("specialBadge").textContent = "KHUSUS";
    $("specialContent").innerHTML = `
      <div class="grid">
        <div class="col-12"><div class="special-note">
          Tag <span class="mono">password</span> adalah hukum khusus runner HLS. UI hanya mengubah daftar variasi;
          runner akan mencoba setiap baris sampai ada yang cocok. Jangan mengubah nama tag.
        </div></div>
        <div class="col-5"><label>Judul release</label><input id="specialTitle" autocomplete="off"></div>
        <div class="col-12"><label>Variasi password (satu per baris)</label><textarea id="passwordVariants" rows="6" placeholder="kunci-utama&#10;kunci-cadangan"></textarea></div>
        <div class="col-12"><div class="hint">Body release akan ditulis ulang sebagai daftar baris berkode backtick, sesuai format runner.</div></div>
      </div>`;
    $("specialTitle").value = parsed.title || "Key for RAW Archive";
    $("passwordVariants").value = (parsed.passwordVariants || []).join("\n");
    $("btnPreview").disabled = false;
    setMode("edit");
    return;
  }

  if (mode === "metadata-guide") {
    $("specialHeading").textContent = "Metadata-Guide (hukum khusus)";
    $("specialBadge").textContent = "READ-ONLY";
    $("specialContent").innerHTML = `
      <div class="special-note">
        Tag <span class="mono">Metadata-Guide</span> adalah dokumentasi format resmi, bukan media.
        UI ini <b>tidak boleh mengubah</b> body/title-nya. Jika format panduan berubah,
        sesuaikan parser/UI terlebih dahulu.
        ${parsed.url ? `<br><a href="${esc(parsed.url)}" target="_blank" rel="noreferrer">Buka release Metadata-Guide ↗</a>` : ""}
      </div>
      <details style="margin-top:10px"><summary class="hint">Lihat body saat ini</summary><pre>${esc(parsed.body || "")}</pre></details>`;
    $("btnPreview").disabled = true;
    $("btnApply").disabled = true;
    $("modeBadge").textContent = "KHUSUS";
    $("modeBadge").className = "badge danger";
  }
}

function resetForm() {
  state.selected = null;
  state.remoteAssets = [];
  state.preview = null;
  state.baseAliases = [];
  state.originalTitle = "";
  clearMalResults();
  setSpecialMode(null);
  $("tag").value = "";
  $("title").value = "";
  setRawMode("raw");
  $("resolution").value = "480p";
  $("episodeCount").value = "12";
  renderConfigSelects("Episodes", state.config.categories[0] || "");
  $("season").value = "";
  updateDestHint();
  setCoverUrl("");
  $("aliases").value = "";
  $("providerUrl").value = "";
  $("providerName").value = "";
  $("providerIcon").value = "";
  $("iconCandidates").innerHTML = "";
  $("providerNote").textContent = "";
  $("assetRows").innerHTML = "";
  $("mappingRows").innerHTML = "";
  addAssetRow();
  $("remoteAssets").classList.add("hidden");
  $("remoteAssets").innerHTML = "";
  $("previewCard").classList.add("hidden");
  $("jobCard").classList.add("hidden");
  $("confirmTag").value = "";
  $("confirmHint").textContent = "—";
  $("btnApply").disabled = true;
  $("optDraftOnly").checked = false;
  setMode("new");
  setWorkspaceVisible(true);
  document.querySelectorAll(".release-item").forEach(el => el.classList.remove("active"));
}

function fillForm(parsed) {
  state.selected = parsed.tag;
  state.remoteAssets = parsed.remoteAssets || [];
  clearMalResults();
  setWorkspaceVisible(true);
  $("tag").value = parsed.tag || "";
  if (parsed.special) {
    renderSpecialCard(parsed);
    document.querySelectorAll(".release-item").forEach(el => el.classList.toggle("active", el.dataset.tag === parsed.tag));
    return;
  }

  setSpecialMode(null);
  state.baseAliases = parsed.aliases || [];
  state.originalTitle = parsed.title || "";
  $("title").value = parsed.title || "";
  setRawMode(parsed.rawMode || "raw");
  $("resolution").value = parsed.resolution || "480p";
  $("episodeCount").value = parsed.episodeCount || 1;
  const firstDest = parsed.dest || (parsed.mappings && parsed.mappings[0] && parsed.mappings[0].dest) || "";
  const destParts = splitDest(firstDest);
  renderConfigSelects(parsed.episodeType || "Episodes", destParts.category);
  $("season").value = destParts.season;
  updateDestHint();
  updateAliasesFromTitle();
  setCoverUrl(parsed.cover || "");
  const p = parsed.provider || {};
  $("providerUrl").value = p.url || "";
  $("providerIcon").value = p.icon || "";
  $("providerName").value = p.provider || "";
  $("assetRows").innerHTML = "";
  addAssetRow("", firstDest || computedDefaultDest());
  $("mappingRows").innerHTML = "";
  (parsed.mappings || []).forEach(m => addMappingRow(m.name, m.dest));
  $("remoteAssets").classList.remove("hidden");
  renderRemoteAssets();
  $("previewCard").classList.add("hidden");
  $("jobCard").classList.add("hidden");
  $("confirmTag").value = "";
  $("confirmHint").textContent = "—";
  $("btnApply").disabled = true;
  $("optDraftOnly").checked = false;
  setMode("edit");
}

function renderRemoteAssets() {
  const box = $("remoteAssets");
  const assets = state.remoteAssets || [];
  if (!assets.length) {
    box.innerHTML = '<div class="hint">Belum ada aset di release ini.</div>';
    return;
  }
  box.innerHTML = '<div class="hint" style="margin-bottom:6px">Aset yang sudah ada di GitHub (jangan mengunggah ulang kecuali ingin mengganti):</div>' +
    assets.map(a => `<div class="item"><span class="asset-name">${esc(a.name)}</span><span>${esc(fmtSize(a.size))}</span></div>`).join("");
}

function renderReleases() {
  const q = $("releaseSearch").value.trim().toLowerCase();
  const list = state.releases.filter(r =>
    !q || String(r.tagName || "").toLowerCase().includes(q) || String(r.name || "").toLowerCase().includes(q)
  );
  const box = $("releaseList");
  if (!list.length) { box.innerHTML = '<div class="hint">Tidak ada release yang cocok.</div>'; return; }
  box.innerHTML = list.map(r => {
    const badges = [];
    if (r.isDraft) badges.push('<span class="mini draft">draft</span>');
    if (r.isPrerelease) badges.push('<span class="mini pre">pre</span>');
    return `<div class="release-item ${state.selected === r.tagName ? "active" : ""}" data-tag="${esc(r.tagName)}">
      <div class="tag">${esc(r.tagName)}</div>
      <div class="name">${esc(r.name || "")}</div>
      <div class="meta">${badges.join("")}</div>
    </div>`;
  }).join("");
  box.querySelectorAll(".release-item").forEach(el => {
    el.addEventListener("click", () => loadRelease(el.dataset.tag));
  });
}

async function refreshReleases() {
  try {
    const data = await api("/api/releases");
    state.releases = data.releases || [];
    renderReleases();
  } catch (e) {
    toast("Gagal memuat rilis: " + e.message, true);
  }
}

async function loadRelease(tag) {
  try {
    toast("Memuat " + tag + "…");
    const data = await api("/api/release?tag=" + encodeURIComponent(tag));
    fillForm(data.parsed);
    renderReleases();
    window.scrollTo({top: 0, behavior: "smooth"});
    toast("Release " + tag + " dimuat.");
  } catch (e) {
    toast("Gagal memuat release: " + e.message, true);
  }
}

async function loadSavedSource() {
  const dest = computedDefaultDest();
  if (!dest) return;
  try {
    const data = await api("/api/source?dest=" + encodeURIComponent(dest));
    const s = data.source;
    if (!s) return;
    let used = false;
    if (!$("providerUrl").value.trim() && s.url) { $("providerUrl").value = s.url; used = true; }
    if (!$("providerName").value.trim() && s.provider) { $("providerName").value = s.provider; used = true; }
    if (!$("providerIcon").value.trim() && s.icon) { $("providerIcon").value = s.icon; used = true; }
    if (used) toast("Provider tersimpan di aliases.json dipakai otomatis.");
  } catch (_) { /* opsional; abaikan bila metadata belum ada */ }
}

async function providerSearch() {
  const url = $("providerUrl").value.trim();
  if (!url) { toast("Isi URL provider dulu.", true); return; }
  const box = $("iconCandidates");
  box.innerHTML = '<div class="hint">Mencari kandidat ikon…</div>';
  try {
    const data = await api("/api/provider", {method: "POST", body: JSON.stringify({url, dest: computedDefaultDest()})});
    if (!$("providerName").value.trim() && data.provider) $("providerName").value = data.provider;
    $("providerNote").textContent = data.error ? ("Halaman tidak bisa dibaca (" + data.error + "); tetap ada kandidat favicon.") : "Pilih salah satu kandidat ikon.";
    const cands = data.candidates || [];
    if (!cands.length) { box.innerHTML = '<div class="hint">Tidak ada kandidat.</div>'; return; }
    box.innerHTML = cands.map((c, i) => `
      <div class="icon-card" data-url="${esc(c.url)}" title="${esc(c.url)}">
        <img src="${esc(c.url)}" alt="" loading="lazy" onerror="this.style.opacity=.2">
        <span>${esc(c.label || "icon")}</span>
      </div>`).join("");
    box.querySelectorAll(".icon-card").forEach(card => {
      card.addEventListener("click", () => {
        box.querySelectorAll(".icon-card").forEach(x => x.classList.remove("selected"));
        card.classList.add("selected");
        $("providerIcon").value = card.dataset.url;
      });
    });
    const current = $("providerIcon").value.trim();
    if (current) {
      const match = Array.from(box.querySelectorAll(".icon-card")).find(c => c.dataset.url === current);
      if (match) match.classList.add("selected");
    }
  } catch (e) {
    box.innerHTML = "";
    toast("Ikon: " + e.message, true);
  }
}

function renderPreview(plan) {
  state.preview = plan;
  $("previewCard").classList.remove("hidden");
  const action = $("previewAction");
  action.textContent = plan.action === "create" ? "BUAT BARU" : "EDIT";
  action.className = "badge " + (plan.action === "create" ? "new" : "edit");

  const errs = plan.errors || [];
  $("previewErrors").innerHTML = errs.length ? '<b style="color:var(--danger)">Error:</b><ul class="errors">' + errs.map(e => `<li>${esc(e)}</li>`).join("") + "</ul>" : "";
  const warns = plan.warnings || [];
  $("previewWarnings").innerHTML = warns.length ? '<b style="color:var(--accent2)">Peringatan:</b><ul class="warnings">' + warns.map(e => `<li>${esc(e)}</li>`).join("") + "</ul>" : "";
  $("previewBody").textContent = plan.body || "(tidak ada body)";
  $("previewCommands").textContent = (plan.commands || []).join("\n") || "(tidak ada perintah)";
  const changes = plan.metadataChanges || [];
  const metaNote = changes.length && plan.saveMetadata === false
    ? '<div class="hint" style="color:var(--accent2);margin-bottom:6px">Pratinjau ini tidak akan menulis aliases.json.</div>'
    : "";
  $("previewMetadata").innerHTML = metaNote + (changes.length
    ? changes.map(c => `<div class="meta-change"><b>${esc(c.section)} → ${esc(c.key)}</b><br>${esc(JSON.stringify(c.after, null, 2))}</div>`).join("")
    : "Tidak ada perubahan aliases.json.");
  if (plan.ok) {
    // Tampilkan mapping hasil resolusi agar bisa dirapikan bila multi-folder.
    if (plan.mappings && plan.mappings.length) {
      const current = readRows("#mappingRows", "map-name", "name");
      const same = current.length === plan.mappings.length &&
        current.every((m, i) => m.name === plan.mappings[i].name && m.dest === plan.mappings[i].dest);
      if (!same) {
        $("mappingRows").innerHTML = "";
        plan.mappings.forEach(m => addMappingRow(m.name, m.dest));
      }
    }
  }
  const confirmed = $("confirmTag").value.trim() === plan.tag;
  $("btnApply").disabled = !(plan.ok && confirmed);
  $("confirmHint").textContent = plan.ok ? ("target: " + plan.tag) : "perbaiki error dulu";
}

async function preview() {
  $("btnPreview").disabled = true;
  try {
    const data = await api("/api/preview", {method: "POST", body: JSON.stringify(readForm())});
    renderPreview(data.plan);
    if (data.plan.ok) toast("Pratinjau siap. Cek body & peringatan sebelum menyimpan.");
    else toast("Pratinjau menemukan error.", true);
  } catch (e) {
    toast("Pratinjau gagal: " + e.message, true);
  } finally {
    $("btnPreview").disabled = false;
  }
}

async function apply() {
  const form = readForm();
  if (!state.preview || !state.preview.ok) { toast("Buat pratinjau dulu.", true); return; }
  if (form.confirm !== state.preview.tag) { toast("Ketik tag dengan tepat untuk konfirmasi.", true); return; }
  if (form.clobber && !form.clobber_ack) { toast("Centang konfirmasi clobber.", true); return; }
  try {
    $("btnApply").disabled = true;
    const data = await api("/api/apply", {method: "POST", body: JSON.stringify(form)});
    $("jobCard").classList.remove("hidden");
    $("jobLog").textContent = "Menjalankan job…";
    $("jobStatus").textContent = "running";
    $("jobStatus").className = "badge status-running";
    state.jobActive = true;
    pollJob(data.job_id);
  } catch (e) {
    state.jobActive = false;
    toast("Gagal memulai job: " + e.message, true);
    $("btnApply").disabled = false;
  }
}

async function checkUiVersion() {
  try {
    const res = await fetch("/api/ui-version", {cache: "no-store"});
    const data = await res.json();
    if (!data.version || data.version === UI_VERSION) return;
    if (state.jobActive) {
      if (!state.pendingReload) {
        toast("Komponen UI diperbarui; halaman dimuat ulang setelah job selesai.");
        state.pendingReload = true;
      }
      return;
    }
    location.reload();
  } catch (_) { /* server sedang sibuk; coba lagi nanti */ }
}

async function pollJob(id) {
  clearTimeout(state.jobTimer);
  try {
    const data = await api("/api/job?id=" + encodeURIComponent(id));
    const job = data.job;
    $("jobLog").textContent = (job.log || []).join("\n") || "(kosong)";
    $("jobLog").scrollTop = $("jobLog").scrollHeight;
    $("jobStatus").textContent = job.status;
    $("jobStatus").className = "badge " + (job.status === "success" ? "status-success" : (job.status === "error" ? "status-error" : "status-running"));
    if (job.status === "running") {
      state.jobTimer = setTimeout(() => pollJob(id), 1200);
    } else {
      state.jobActive = false;
      $("btnApply").disabled = false;
      refreshReleases();
      toast(job.status === "success" ? "Job selesai." : "Job gagal — cek log.", job.status !== "success");
      if (state.pendingReload) {
        state.pendingReload = false;
        setTimeout(() => location.reload(), 900);
      }
    }
  } catch (e) {
    state.jobTimer = setTimeout(() => pollJob(id), 2500);
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  setMode("new");
  await loadConfig();
  resetForm();
  setWorkspaceVisible(false);
  $("btnRefresh").addEventListener("click", refreshReleases);
  $("releaseSearch").addEventListener("input", renderReleases);
  const coverEditor = $("coverPreviewUrl");
  coverEditor.addEventListener("input", () => {
    if (!coverEditor.textContent.trim()) coverEditor.innerHTML = "";
    updateCoverPreview();
  });
  coverEditor.addEventListener("blur", () => {
    const clean = getCoverUrl();
    if (coverEditor.textContent !== clean) coverEditor.textContent = clean;
    renderCoverPreview(clean);
  });
  coverEditor.addEventListener("keydown", (e) => {
    if (e.key === "Enter") e.preventDefault();
  });
  coverEditor.addEventListener("paste", (e) => {
    const text = ((e.clipboardData || window.clipboardData).getData("text") || "").replace(/\s+/g, "");
    if (!text) return;
    e.preventDefault();
    document.execCommand("insertText", false, text);
    updateCoverPreview();
  });
  $("btnNew").addEventListener("click", resetForm);
  $("btnMalEpisodes").addEventListener("click", checkMalEpisodes);
  $("malCandidates").addEventListener("change", () => selectMalResult(Number($("malCandidates").value)));
  $("btnAddAsset").addEventListener("click", () => addAssetRow());
  $("btnAddMapping").addEventListener("click", () => addMappingRow());
  $("btnApplyDefault").addEventListener("click", () => {
    const dest = computedDefaultDest();
    document.querySelectorAll("#assetRows tr").forEach(tr => {
      const field = tr.querySelector(".asset-dest");
      if (!field.value.trim()) {
        field.value = dest;
        field.dataset.auto = "1";
      }
    });
  });
  $("btnProvider").addEventListener("click", providerSearch);
  $("title").addEventListener("input", () => {
    updateAliasesFromTitle();
    clearMalResults();
  });
  $("tag").addEventListener("input", updateDestHint);
  $("tag").addEventListener("change", loadSavedSource);
  $("season").addEventListener("input", () => {
    updateDestHint();
    clearMalResults();
  });
  $("season").addEventListener("change", loadSavedSource);
  $("resolution").addEventListener("change", () => {
    document.querySelectorAll("#assetRows tr").forEach((tr) => {
      if (tr.querySelector(".asset-path").value.trim()) checkFolderInfo(tr);
    });
  });
  $("episodeType").addEventListener("change", () => {
    if ($("episodeType").value === "__add__") addConfigChoice("types", $("episodeType"));
  });
  $("category").addEventListener("change", () => {
    if ($("category").value === "__add__") {
      addConfigChoice("categories", $("category"));
      return;
    }
    updateDestHint();
    loadSavedSource();
  });
  $("btnPreview").addEventListener("click", preview);
  $("btnApply").addEventListener("click", apply);
  $("confirmTag").addEventListener("input", () => {
    const plan = state.preview;
    $("btnApply").disabled = !(plan && plan.ok && $("confirmTag").value.trim() === plan.tag);
  });
  document.addEventListener("input", (e) => {
    if (e.target.closest("main") && e.target.id !== "confirmTag" && state.preview) {
      invalidatePreview();
    }
  });
  document.addEventListener("change", (e) => {
    if (e.target.closest("main") && e.target.id !== "confirmTag" && state.preview) {
      invalidatePreview();
    }
  });
  await refreshReleases();
  setInterval(checkUiVersion, 2500);
});
