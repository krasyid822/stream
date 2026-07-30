/* ============================================================
   RETRO PIXEL STREAM — DIRECTORY EXPLORER MODULE
   ============================================================ */

let metadataStore = [];
let categoriesStore = [];
let currentFolder = "";
let hlsInstance = null;

// DOM Elements
const gridExplorer = document.getElementById("gridExplorer");
const breadcrumbPath = document.getElementById("breadcrumbPath");
const itemCount = document.getElementById("itemCount");
const statusBadge = document.getElementById("statusBadge");
const videoElement = document.getElementById("hlsPlayer");

// Synchronize & Sync URL Hash for Deep-Linking & Reload Persistence with Pretty Path Slashes & Underscore Spaces
function syncUrlHash() {
    let hashPath = "";

    if (currentFolder) {
        // Ganti spasi dengan underscore di URL hash
        const cleanFolder = currentFolder.replace(/^\/+|\/+$/g, "").replace(/ /g, "_");
        hashPath = "#/" + cleanFolder;
    }

    // Hanya tampilkan parameter episode jika media diputar berada di dalam currentFolder yang sedang dibuka
    if (window.currentPlayingMediaObj && window.currentPlayingMediaObj.folder === currentFolder) {
        const episodeName = window.currentPlayingMediaObj.name.replace(/ /g, "_");
        hashPath += (hashPath ? "?" : "#?") + "ep=" + encodeURIComponent(episodeName);
    }

    if (hashPath) {
        history.replaceState(null, "", hashPath);
    } else {
        history.replaceState(null, "", window.location.pathname + window.location.search);
    }
}

function parseUrlHash() {
    const rawHash = window.location.hash.substring(1);
    if (!rawHash) return { dir: "", ep: "" };

    let dirPath = "";
    let episodeName = "";

    const queryIdx = rawHash.indexOf("?");
    if (queryIdx !== -1) {
        dirPath = decodeURIComponent(rawHash.substring(0, queryIdx)).replace(/^\/+|\/+$/g, "").replace(/_/g, " ");
        const queryStr = rawHash.substring(queryIdx + 1);
        const params = new URLSearchParams(queryStr);
        episodeName = (params.get("ep") || params.get("play") || "").replace(/_/g, " ");
    } else {
        dirPath = decodeURIComponent(rawHash.replace(/^\/+|\/+$/g, "")).replace(/_/g, " ");
    }

    return { dir: dirPath, ep: episodeName };
}

let aliasesStore = {};
let sourcesStore = {};

// Fetch Metadata & Aliases secara terpisah tanpa duplikasi data
async function loadMetadata() {
    try {
        const [metaRes, aliasRes] = await Promise.all([
            fetch("metadata.json?t=" + new Date().getTime()),
            fetch("aliases.json?t=" + new Date().getTime()).catch(() => null)
        ]);

        const data = await metaRes.json();
        metadataStore = data.media || [];
        categoriesStore = data.categories || [];

        if (aliasRes && aliasRes.ok) {
            const aliasData = await aliasRes.json();
            aliasesStore = aliasData.aliases || {};
            sourcesStore = aliasData.sources || {};

            // Merge alias dan source ke item media secara dinamis di runtime (TANPA DUPLIKASI FILE)
            metadataStore.forEach(item => {
                const folderKey = item.folder || "";
                const folderLeaf = folderKey.split("/").pop() || "";
                
                // Ambil daftar alias berdasarkan folderLeaf (misal: "am" atau "gsyos")
                const matchedAliases = aliasesStore[folderLeaf] || aliasesStore[folderKey] || [];
                item.aliases = matchedAliases;

                // Ambil objek source penyedia
                const matchedSource = sourcesStore[folderKey] || sourcesStore[folderLeaf] || null;
                if (matchedSource) {
                    item.source = matchedSource;
                }
            });
        }

        // Baca URL Hash jika ada saat reload / share link
        const { dir, ep } = parseUrlHash();
        currentFolder = dir;

        renderDirectoryGrid();

        // Otomatis putar media jika nama episode ada di URL hash (#/.../Season 1?ep=...)
        if (ep) {
            const targetMedia = metadataStore.find(m => m.name === ep || m.id === ep);
            if (targetMedia) {
                if (!dir && targetMedia.folder) {
                    currentFolder = targetMedia.folder;
                    renderDirectoryGrid();
                }
                const cardEl = Array.from(document.querySelectorAll(".grid-card.media-card")).find(
                    c => c.querySelector(".card-name")?.textContent === targetMedia.name
                );
                playMedia(targetMedia, cardEl);
            }
        }
    } catch (err) {
        console.error("Gagal membaca metadata.json:", err);
        statusBadge.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> METADATA MISSING`;
    }
}

// Event listener saat tombol Back/Forward browser diklik
window.addEventListener("popstate", function () {
    const { dir, play } = parseUrlHash();
    if (dir !== currentFolder) {
        currentFolder = dir;
        renderDirectoryGrid();
    }
    if (play && play !== window.currentPlayingMediaId) {
        const targetMedia = metadataStore.find(m => m.id === play);
        if (targetMedia) {
            playMedia(targetMedia, null);
        }
    }
});

// Event listener global untuk menutup balloon popup saat klik di luar
document.addEventListener("click", function (e) {
    if (!e.target.closest(".info-btn-wrapper")) {
        document.querySelectorAll(".info-btn-wrapper.active").forEach(w => {
            w.classList.remove("active");
            const parentCard = w.closest(".grid-card");
            if (parentCard) parentCard.style.zIndex = "";
        });
    }
});

let searchQuery = "";

// Handle Real-Time Search Filter
function handleSearch(query) {
    searchQuery = query.trim().toLowerCase();
    renderDirectoryGrid();
}

// Render Directory Grid View
function renderDirectoryGrid() {
    if (!gridExplorer) return;
    gridExplorer.innerHTML = "";

    const searchBoxContainer = document.getElementById("searchBoxContainer");

    const folders = new Set();
    const mediaItems = [];
    const searchGroups = {}; // Objek untuk mengelompokkan hasil pencarian berdasarkan folder

    // Mode Pencarian Global (jika pengguna mengetik di kotak pencarian)
    if (searchQuery !== "") {
        if (searchBoxContainer) searchBoxContainer.classList.add("floating-search");
        gridExplorer.style.display = "block";
        metadataStore.forEach(item => {
            const matchName = item.name.toLowerCase().includes(searchQuery);
            const matchPath = item.path.toLowerCase().includes(searchQuery);
            const matchAlias = item.aliases && item.aliases.some(alias => alias.toLowerCase().includes(searchQuery));
            if (matchName || matchPath || matchAlias) {
                const folderKey = item.folder || "root";
                if (!searchGroups[folderKey]) {
                    searchGroups[folderKey] = [];
                }
                searchGroups[folderKey].push(item);
            }
        });
    } else {
        if (searchBoxContainer) searchBoxContainer.classList.remove("floating-search");
        gridExplorer.style.display = "";
        // Mode Penjelajahan Direktori Biasa
        if (currentFolder === "") {
            categoriesStore.forEach(cat => folders.add(cat));
        }

        metadataStore.forEach(item => {
            const itemFolder = item.folder || "";

            if (currentFolder === "") {
                const rootPart = itemFolder.split("/")[0];
                if (rootPart) {
                    folders.add(rootPart);
                } else {
                    mediaItems.push(item);
                }
            } else if (itemFolder === currentFolder) {
                mediaItems.push(item);
            } else if (itemFolder.startsWith(currentFolder + "/")) {
                const subRel = itemFolder.substring(currentFolder.length + 1);
                const nextSubFolder = subRel.split("/")[0];
                folders.add(nextSubFolder);
            }
        });
    }

    let count = 0;

    // Helper untuk menangani Long-Press pada mobile & Touch toggle balloon
    const attachBalloonInteractions = (wrapperEl) => {
        let pressTimer = null;
        const cardEl = wrapperEl.closest(".grid-card");

        if (cardEl) {
            // Long Press pada kartu untuk Mobile Touch Screens
            cardEl.addEventListener("touchstart", (e) => {
                if (e.target.closest(".info-btn")) return;
                pressTimer = setTimeout(() => {
                    wrapperEl.classList.toggle("active");
                }, 500);
            }, { passive: true });

            cardEl.addEventListener("touchend", () => {
                if (pressTimer) clearTimeout(pressTimer);
            });

            cardEl.addEventListener("touchmove", () => {
                if (pressTimer) clearTimeout(pressTimer);
            });
        }

        // Helper untuk menyesuaikan posisi adaptif pintar (kiri/kanan & atas/bawah)
        const adjustPopupPosition = () => {
            const popup = wrapperEl.querySelector(".pixel-balloon-popup");
            const btn = wrapperEl.querySelector(".info-btn");
            if (!popup || !btn) return;

            // Reset class alignment & direction
            popup.classList.remove("align-left", "align-right", "pop-down");

            const rect = btn.getBoundingClientRect();

            // 1. Deteksi Horisontal (Kiri vs Kanan):
            // Jika tombol 'i' kurang dari 220px dari tepi kiri layar, gunakan align-left agar popup tidak terpotong di tepi kiri.
            if (rect.left < 220) {
                popup.classList.add("align-left");
            } else {
                popup.classList.add("align-right");
            }

            // 2. Deteksi Vertikal (Atas vs Bawah): Jika tombol 'i' berada dekat batas atas viewport (< 280px dari atas)
            if (rect.top < 280) {
                popup.classList.add("pop-down");
            }
        };

        // Tap/Click manual pada tombol info untuk toggle balloon popup (buka & tutup)
        const btn = wrapperEl.querySelector(".info-btn");
        if (btn) {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const isCurrentlyActive = wrapperEl.classList.contains("active");
                
                // Tutup seluruh balloon popup lain yang sedang terbuka
                document.querySelectorAll(".info-btn-wrapper.active").forEach(w => {
                    w.classList.remove("active");
                    const parentCard = w.closest(".grid-card");
                    if (parentCard) parentCard.style.zIndex = "";
                });

                // Toggle
                if (!isCurrentlyActive) {
                    adjustPopupPosition();
                    wrapperEl.classList.add("active");
                    const parentCard = wrapperEl.closest(".grid-card");
                    if (parentCard) parentCard.style.zIndex = "999";
                }
            });
        }
    };

    // Helper membuat elemen kartu media tunggal
    const createMediaCardElement = (media) => {
        const mediaCard = document.createElement("div");
        const isPlaying = (window.currentPlayingMediaId && window.currentPlayingMediaId === media.id);
        mediaCard.className = `grid-card media-card${isPlaying ? " playing" : ""}`;

        let thumbHtml = `<i class="fa-solid fa-film"></i>`;
        if (media.poster_url) {
            const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1" || location.hostname === "0.0.0.0";
            const posterUrl = (!isLocalhost && !media.poster_url.startsWith("http")) 
                ? "https://raw.githubusercontent.com/krasyid822/stream/main/" + media.poster_url.replace(/^\//, "")
                : media.poster_url;

            thumbHtml = `
                <div style="width: 100%; aspect-ratio: 16/9; overflow: hidden; border: 1px solid var(--accent-cyan); margin-bottom: 0.5rem; background: #000;">
                    <img src="${posterUrl}" alt="${media.name}" style="width: 100%; height: 100%; object-fit: cover;">
                </div>`;
        }

        const aliasesText = (media.aliases && media.aliases.length > 0) ? media.aliases.slice(0, 2).join(", ") : "None";
        let sourceHtml = "RAW";
        if (media.source) {
            const iconImg = media.source.icon ? `<img src="${media.source.icon}" alt="icon" style="height:14px; width:auto; object-fit:contain; vertical-align:middle; margin-right:4px; border-radius:2px;">` : "";
            sourceHtml = `${iconImg}<a href="${media.source.url}" target="_blank" style="color:var(--accent-cyan); text-decoration:none;">${media.source.provider}</a>`;
        }

        mediaCard.innerHTML = `
            <div class="info-btn-wrapper">
                <button class="info-btn">i</button>
                <div class="pixel-balloon-popup">
                    <div style="color: var(--accent-pink); font-weight: bold; margin-bottom: 0.2rem;">STREAM INFO</div>
                    <div>TITLE: ${media.name}</div>
                    <div>FOLDER: ${media.folder}</div>
                    <div>SOURCE: ${sourceHtml}</div>
                    <div>SUBS: ${media.subtitles ? media.subtitles.length : 0} Track(s)</div>
                    <div>AKA: ${aliasesText}</div>
                </div>
            </div>
            <span class="pixel-badge purple card-badge">HLS</span>
            ${thumbHtml}
            <div class="card-name">${media.name}</div>
        `;

        mediaCard.onclick = (e) => {
            if (e.target.closest(".info-btn-wrapper")) return;
            playMedia(media, mediaCard);
        };

        const wrapperEl = mediaCard.querySelector(".info-btn-wrapper");
        attachBalloonInteractions(wrapperEl);

        return mediaCard;
    };

    // MODE PENCARIAN: Render dalam blok-blok baris hierarki direktori/season yang terpisah
    if (searchQuery !== "") {
        const playingFolder = window.currentPlayingMediaFolder || "";
        const isMobile = (window.innerWidth <= 768);

        const sortedFolderKeys = Object.keys(searchGroups).sort((a, b) => {
            if (playingFolder) {
                // Di Mobile: Pemutar video di bawah grid -> dekatkan folder aktif ke PALING BAWAH
                // Di Desktop: Pemutar video di atas grid -> dekatkan folder aktif ke PALING ATAS
                if (a === playingFolder) return isMobile ? 1 : -1;
                if (b === playingFolder) return isMobile ? -1 : 1;
            }
            return a.localeCompare(b);
        });
        
        sortedFolderKeys.forEach(folderPath => {
            const isCurrentPlayingGroup = (playingFolder && folderPath === playingFolder);
            const groupBlock = document.createElement("div");
            groupBlock.className = `search-group-block${isCurrentPlayingGroup ? " playing-group" : ""}`;
            groupBlock.setAttribute("data-folder-path", folderPath);

            const headerEl = document.createElement("div");
            headerEl.className = "search-group-header";
            headerEl.innerHTML = `<i class="fa-solid fa-folder-tree"></i> ${folderPath.toUpperCase()}`;
            groupBlock.appendChild(headerEl);

            const gridEl = document.createElement("div");
            gridEl.className = "search-group-grid";

            searchGroups[folderPath].forEach(media => {
                const card = createMediaCardElement(media);
                gridEl.appendChild(card);
                count++;
            });

            groupBlock.appendChild(gridEl);
            gridExplorer.appendChild(groupBlock);
        });
    } else {
        // Render Tombol Back (hanya jika sedang di dalam folder dan bukan mode pencarian global)
        if (currentFolder !== "") {
            const backCard = document.createElement("div");
            backCard.className = "grid-card folder-card";
            backCard.innerHTML = `
                <i class="fa-solid fa-folder-minus" style="color: var(--accent-pink);"></i>
                <div class="card-name">.. (Kembali)</div>
            `;
            backCard.onclick = () => {
                const parts = currentFolder.split("/");
                parts.pop();
                navigateToFolder(parts.join("/"));
            };
            gridExplorer.appendChild(backCard);
            count++;
        }

        // MODE EXPLORER BIASA: Render Folder Cards & Media Cards
        const sortedFolders = Array.from(folders).sort();
        sortedFolders.forEach(folderName => {
            const folderCard = document.createElement("div");
            folderCard.className = "grid-card folder-card";
            const folderPath = currentFolder === "" ? folderName : `${currentFolder}/${folderName}`;
            
            let iconHtml = `<i class="fa-solid fa-folder-closed" style="color: var(--accent-yellow);"></i>`;
            const folderLower = folderName.toLowerCase();

            if (currentFolder === "") {
                if (folderLower === "anime") {
                    iconHtml = `
                        <div class="pixel-flag jp-flag" title="Anime (Japan)">
                            <div class="jp-sun"></div>
                        </div>`;
                } else if (folderLower === "donghua") {
                    iconHtml = `
                        <div class="pixel-flag cn-flag" title="Donghua (China)">
                            <i class="fa-solid fa-star cn-star-big"></i>
                            <div class="cn-stars-small">
                                <i class="fa-solid fa-star"></i>
                                <i class="fa-solid fa-star"></i>
                            </div>
                        </div>`;
                } else if (folderLower === "indonesia" || folderLower === "lokal") {
                    iconHtml = `
                        <div class="pixel-flag id-flag" title="Indonesia">
                            <div class="id-red"></div>
                            <div class="id-white"></div>
                        </div>`;
                }
            }

            folderCard.innerHTML = `
                <div class="info-btn-wrapper">
                    <button class="info-btn">i</button>
                    <div class="pixel-balloon-popup">
                        <div style="color: var(--accent-cyan); font-weight: bold; margin-bottom: 0.2rem;">FOLDER INFO</div>
                        <div>NAME: ${folderName}</div>
                        <div>PATH: ${folderPath}</div>
                    </div>
                </div>
                <span class="pixel-badge card-badge">DIR</span>
                ${iconHtml}
                <div class="card-name">${folderName}</div>
            `;

            folderCard.onclick = (e) => {
                if (e.target.closest(".info-btn-wrapper")) return;
                navigateToFolder(folderPath);
            };

            const wrapperEl = folderCard.querySelector(".info-btn-wrapper");
            attachBalloonInteractions(wrapperEl);

            gridExplorer.appendChild(folderCard);
            count++;
        });

        mediaItems.forEach(media => {
            const card = createMediaCardElement(media);
            gridExplorer.appendChild(card);
            count++;
        });
    }

    if (itemCount) itemCount.textContent = `${count} Items`;
    updateBreadcrumb();
}

function navigateToFolder(path) {
    currentFolder = path;
    syncUrlHash();
    renderDirectoryGrid();
}

function updateBreadcrumb() {
    if (!breadcrumbPath) return;

    if (!currentFolder) {
        breadcrumbPath.innerHTML = "";
        return;
    }

    const parts = currentFolder.split("/");
    let accumulated = "";
    let html = "";

    parts.forEach(part => {
        accumulated = accumulated ? `${accumulated}/${part}` : part;
        const pathRef = accumulated;
        html += ` <i class="fa-solid fa-chevron-right" style="font-size:0.7rem;"></i> <span class="breadcrumb-item" onclick="navigateToFolder('${pathRef}')">${part}</span>`;
    });

    breadcrumbPath.innerHTML = html;
}

// Pindahkan blok folder yang sedang diputar secara langsung di DOM (TANPA RELOAD GRID)
function movePlayingGroupBlockToPlayer(folderPath) {
    if (!gridExplorer || !folderPath) return;

    const targetBlock = gridExplorer.querySelector(`.search-group-block[data-folder-path="${CSS.escape(folderPath)}"]`);
    if (!targetBlock) return;

    const isMobile = (window.innerWidth <= 768);

    if (isMobile) {
        // Di Mobile: Pindahkan blok folder aktif ke PALING BAWAH (dekat player di bawah)
        gridExplorer.appendChild(targetBlock);
    } else {
        // Di Desktop: Pindahkan blok folder aktif ke PALING ATAS (dekat player di atas)
        if (gridExplorer.firstChild && gridExplorer.firstChild !== targetBlock) {
            gridExplorer.insertBefore(targetBlock, gridExplorer.firstChild);
        }
    }
}

// Auto-reorder grid saat pengguna mengubah ukuran jendela browser (Responsive Switch)
let resizeDebounceTimer = null;
let lastIsMobileState = (window.innerWidth <= 768);

window.addEventListener("resize", function () {
    const currentIsMobileState = (window.innerWidth <= 768);
    if (currentIsMobileState !== lastIsMobileState) {
        lastIsMobileState = currentIsMobileState;
        if (resizeDebounceTimer) clearTimeout(resizeDebounceTimer);
        resizeDebounceTimer = setTimeout(() => {
            if (typeof renderDirectoryGrid === "function") {
                renderDirectoryGrid();
            }
        }, 150);
    }
});

// Initialize Explorer on DOM Ready
window.addEventListener("DOMContentLoaded", loadMetadata);
