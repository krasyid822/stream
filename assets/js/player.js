/* ============================================================
   RETRO PIXEL STREAM — VIDEO & HLS PLAYER ENGINE MODULE
   ============================================================ */

let currentPlayingHls = null;
let currentSubtitleTracks = [];
let isSubtitleActive = false;

// Format URL CDN untuk membaca HLS dari repository stream_drive
function getHlsStreamUrl(rawPath) {
    if (!rawPath) return "";
    const cleanPath = rawPath.replace(/^\/+/, "");
    const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1" || location.hostname === "0.0.0.0";
    
    // Encode setiap segmen path agar karakter khusus seperti '#' atau spasi aman bagi HTTP request
    const encodedSegments = cleanPath.split("/").map(seg => encodeURIComponent(seg)).join("/");

    if (isLocalhost) {
        return cleanPath;
    }
    // Menggunakan CDN GitHub raw dari repositori stream_drive
    return "https://raw.githubusercontent.com/krasyid822/stream_drive/main/" + encodedSegments;
}

function showToast(msg) {
    const toast = document.getElementById("pixelToast");
    const toastMsg = document.getElementById("toastMsg");
    if (!toast || !toastMsg) return;
    toastMsg.textContent = msg;
    toast.style.display = "block";
    setTimeout(() => {
        toast.style.display = "none";
    }, 4000);
}

// Main Play Media Function
function playMedia(media, cardElement) {
    const playerWrapper = document.getElementById("playerWrapper");
    const video = document.getElementById("hlsPlayer");
    const statusBadge = document.getElementById("statusBadge");
    const resSelect = document.getElementById("resSelect");
    const btnToggleSub = document.getElementById("btnToggleSub");

    if (!video) return;

    if (playerWrapper) playerWrapper.style.display = "flex";

    // Set Global State
    window.currentPlayingMediaId = media.id;
    window.currentPlayingMediaObj = media;

    // Highlight Active Card
    document.querySelectorAll(".grid-card.media-card").forEach(c => c.classList.remove("playing"));
    if (cardElement) {
        cardElement.classList.add("playing");
    } else {
        const matchingCard = Array.from(document.querySelectorAll(".grid-card.media-card")).find(
            c => c.querySelector(".card-name")?.textContent === media.name
        );
        if (matchingCard) matchingCard.classList.add("playing");
    }

    // Scroll & Move Block jika di pencarian
    if (typeof movePlayingGroupBlockToPlayer === "function") {
        movePlayingGroupBlockToPlayer(media.folder);
    }

    if (statusBadge) {
        statusBadge.innerHTML = `<i class="fa-solid fa-play" style="color: var(--accent-green);"></i> PLAYING: ${media.name}`;
    }

    // Update URL hash & Comment Widget
    if (typeof syncUrlHash === "function") syncUrlHash();
    if (typeof updateCommentWidgetRoom === "function") updateCommentWidgetRoom();

    const streamUrl = getHlsStreamUrl(media.master_url || (media.path + "/master.m3u8"));
    const posterUrl = media.poster_url ? getHlsStreamUrl(media.poster_url) : "";

    video.poster = posterUrl;

    if (currentPlayingHls) {
        currentPlayingHls.destroy();
        currentPlayingHls = null;
    }

    // Reset Subtitles UI
    currentSubtitleTracks = media.subtitles || [];
    isSubtitleActive = false;
    if (btnToggleSub) {
        btnToggleSub.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: OFF`;
        btnToggleSub.style.color = "var(--text-muted)";
        btnToggleSub.style.borderColor = "var(--text-dim)";
        btnToggleSub.style.display = currentSubtitleTracks.length > 0 ? "inline-flex" : "none";
    }

    // Remove existing text tracks
    Array.from(video.querySelectorAll("track")).forEach(t => t.remove());

    // Inisialisasi HLS.js
    if (Hls.isSupported()) {
        const hls = new Hls({
            enableWorker: true,
            lowLatencyMode: false,
            backBufferLength: 90
        });

        hls.loadSource(streamUrl);
        hls.attachMedia(video);
        currentPlayingHls = hls;

        hls.on(Hls.Events.MANIFEST_PARSED, function (event, data) {
            if (resSelect) {
                resSelect.innerHTML = `<option value="-1" style="background: var(--bg-panel); color: var(--text-main);">Auto</option>`;
                hls.levels.forEach((level, index) => {
                    const opt = document.createElement("option");
                    opt.value = index;
                    opt.textContent = `${level.height}p`;
                    opt.style.background = "var(--bg-panel)";
                    opt.style.color = "var(--text-main)";
                    resSelect.appendChild(opt);
                });
            }
            video.play().catch(() => {});
        });

        hls.on(Hls.Events.ERROR, function (event, data) {
            if (data.fatal) {
                switch (data.type) {
                    case Hls.ErrorTypes.NETWORK_ERROR:
                        console.warn("HLS Network Error, attempting recovery...", data);
                        hls.startLoad();
                        break;
                    case Hls.ErrorTypes.MEDIA_ERROR:
                        console.warn("HLS Media Error, attempting recovery...", data);
                        hls.recoverMediaError();
                        break;
                    default:
                        console.error("Fatal HLS Error:", data);
                        showToast(`Gagal memuat HLS Stream: ${media.name}`);
                        hls.destroy();
                        break;
                }
            }
        });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        // Native Safari / iOS Support
        video.src = streamUrl;
        video.addEventListener("loadedmetadata", () => {
            video.play().catch(() => {});
        });
    }

    // Scroll player into view jika di mobile
    if (window.innerWidth <= 768 && playerWrapper) {
        playerWrapper.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

// Function ganti resolusi
function changeResolution(levelIndex) {
    if (!currentPlayingHls) return;
    currentPlayingHls.currentLevel = parseInt(levelIndex, 10);
}

// Function toggle subtitle (Menggunakan Blob URL agar 100% aman dari batasan Cross-Origin CORS)
async function toggleSubtitle() {
    const video = document.getElementById("hlsPlayer");
    const btn = document.getElementById("btnToggleSub");
    if (!video || !currentSubtitleTracks.length) return;

    isSubtitleActive = !isSubtitleActive;

    if (isSubtitleActive) {
        if (!video.querySelector("track")) {
            for (let idx = 0; idx < currentSubtitleTracks.length; idx++) {
                const sub = currentSubtitleTracks[idx];
                const track = document.createElement("track");
                track.kind = "subtitles";
                
                let subPath = typeof sub === "string" ? sub : (sub.path || "");
                let subLang = typeof sub === "object" && sub.lang ? sub.lang : "id";
                let subTitle = typeof sub === "object" && sub.title ? sub.title : `Subtitle ${idx+1}`;

                if (typeof sub === "string") {
                    const match = sub.match(/sub_\d+_([a-zA-Z0-9]+)\.vtt/i);
                    if (match) {
                        subLang = match[1];
                        subTitle = `Subtitle ${idx+1} (${match[1].toUpperCase()})`;
                    }
                }

                track.label = subTitle;
                track.srclang = subLang;
                track.default = (idx === 0);

                const directUrl = getHlsStreamUrl(subPath);
                try {
                    // Fetch WebVTT text lalu buat Local Blob URL untuk mem-bypass batasan CORS elemen <track>
                    const resp = await fetch(directUrl);
                    if (resp.ok) {
                        const vttText = await resp.text();
                        const blob = new Blob([vttText], { type: "text/vtt" });
                        track.src = URL.createObjectURL(blob);
                    } else {
                        track.src = directUrl;
                    }
                } catch (e) {
                    console.warn("Gagal fetch VTT sebagai Blob, fallback ke direct URL:", e);
                    track.src = directUrl;
                }

                video.appendChild(track);
            }
        }
        for (let i = 0; i < video.textTracks.length; i++) {
            video.textTracks[i].mode = "showing";
        }
        if (btn) {
            btn.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: ON`;
            btn.style.color = "var(--accent-yellow)";
            btn.style.borderColor = "var(--accent-yellow)";
        }
    } else {
        for (let i = 0; i < video.textTracks.length; i++) {
            video.textTracks[i].mode = "hidden";
        }
        if (btn) {
            btn.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: OFF`;
            btn.style.color = "var(--text-muted)";
            btn.style.borderColor = "var(--text-dim)";
        }
    }
}
