// Helper untuk mensinkronisasi tampilan tombol UI Subtitle dengan status asli HTML5 TextTracks
function updateSubtitleUIStatus() {
    const btn = document.getElementById("btnToggleSub");
    const tracks = videoElement ? videoElement.textTracks : null;
    if (!btn) return;

    let isAnyShowing = false;
    if (tracks) {
        for (let i = 0; i < tracks.length; i++) {
            if (tracks[i].mode === "showing") {
                isAnyShowing = true;
                break;
            }
        }
    }

    if (isAnyShowing) {
        btn.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: ON`;
        btn.className = "pixel-btn primary";
        btn.style.borderColor = "var(--accent-purple)";
        btn.style.color = "var(--accent-cyan)";
    } else {
        btn.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: OFF`;
        btn.className = "pixel-btn";
        btn.style.borderColor = "var(--text-dim)";
        btn.style.color = "var(--text-muted)";
    }
}

// Play Media HLS & Set Subtitles
function playMedia(media, cardElement) {
    const playerWrapper = document.getElementById("playerWrapper");
    if (playerWrapper) {
        playerWrapper.style.display = "flex";
    }

    window.currentPlayingMediaId = media ? media.id : "";
    window.currentPlayingMediaFolder = media ? media.folder : "";
    window.currentPlayingMediaObj = media || null;
    
    if (typeof syncUrlHash === "function") {
        syncUrlHash();
    }
    if (typeof renderDirectoryGrid === "function") {
        renderDirectoryGrid();
    }

    document.querySelectorAll(".grid-card").forEach(c => c.classList.remove("active"));
    if (cardElement) cardElement.classList.add("active");

    const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1";
    const GITHUB_RAW_BASE = "https://raw.githubusercontent.com/krasyid822/stream/main/";

    if (videoElement && media.poster_url) {
        videoElement.poster = (!isLocalhost && !media.poster_url.startsWith("http"))
            ? GITHUB_RAW_BASE + media.poster_url.replace(/^\//, "")
            : media.poster_url;
    }

    // Bersihkan track subtitle lama dari elemen <video>
    for (let i = videoElement.textTracks.length - 1; i >= 0; i--) {
        videoElement.textTracks[i].mode = 'disabled';
    }
    while (videoElement.getElementsByTagName("track").length > 0) {
        videoElement.removeChild(videoElement.getElementsByTagName("track")[0]);
    }

    // Tambahkan track subtitle WebVTT (.vtt) jika ada
    if (media.subtitles && media.subtitles.length > 0) {
        media.subtitles.forEach((subUrl, idx) => {
            const track = document.createElement("track");
            track.kind = "subtitles";
            track.label = `Indonesian (${idx + 1})`;
            track.srclang = "id";
            track.default = (idx === 0);

            const vttTargetUrl = (!isLocalhost && !subUrl.startsWith("http"))
                ? GITHUB_RAW_BASE + subUrl.replace(/^\//, "")
                : subUrl;

            // Trik CORS Safe Subtitle: Fetch VTT dan konversi ke Blob URL lokal agar tidak diblokir browser
            if (!isLocalhost && vttTargetUrl.startsWith("http")) {
                fetch(vttTargetUrl)
                    .then(response => response.text())
                    .then(vttText => {
                        const blob = new Blob([vttText], { type: "text/vtt" });
                        track.src = URL.createObjectURL(blob);
                    })
                    .catch(err => {
                        console.warn("Gagal memuat subtitle CORS VTT, fallback ke URL direct:", err);
                        track.src = vttTargetUrl;
                    });
            } else {
                track.src = vttTargetUrl;
            }

            videoElement.appendChild(track);
        });

        // Setel langsung mode track pertama ke 'showing'
        setTimeout(() => {
            for (let i = 0; i < videoElement.textTracks.length; i++) {
                videoElement.textTracks[i].mode = (i === 0) ? "showing" : "disabled";
            }
            updateSubtitleUIStatus();
        }, 100);
    } else {
        updateSubtitleUIStatus();
    }

    if (videoElement && videoElement.textTracks) {
        videoElement.textTracks.onchange = updateSubtitleUIStatus;
        videoElement.textTracks.addEventListener("change", updateSubtitleUIStatus);
    }

    const hlsUrl = media.master_url;

    const safePlay = () => {
        const playPromise = videoElement.play();
        if (playPromise !== undefined) {
            playPromise.catch(error => {
                if (error.name === "NotAllowedError") {
                    // Biarkan video tetap ter-pause secara bersih sesuai Autoplay Policy browser
                    videoElement.pause();
                } else if (error.name !== "AbortError") {
                    console.warn("Play error:", error);
                }
            });
        }
    };

    if (Hls.isSupported()) {
        if (hlsInstance) {
            hlsInstance.destroy();
        }

        /* ============================================================
           ADAPTIVE DYNAMIC RAM BUFFER ALLOCATION
           Mendeteksi kapasitas RAM perangkat (navigator.deviceMemory)
           ============================================================ */
        const deviceRamGb = (navigator.deviceMemory || 4); // Default asumsi 4GB jika API tidak tersedia
        
        // Alokasikan panjang buffer maju (forward buffer) proporsional dengan RAM
        let dynamicForwardBuffer = 60;
        if (deviceRamGb <= 2) {
            dynamicForwardBuffer = 30;
        } else if (deviceRamGb >= 8) {
            dynamicForwardBuffer = 120;
        }

        // Trik GitHub RAW CDN: Alihkan request file .m3u8 & .ts ke GitHub RAW jika web berjalan di GitHub Pages/online
        const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1";
        const GITHUB_RAW_BASE = "https://raw.githubusercontent.com/krasyid822/stream/main/";

        const finalHlsUrl = (!isLocalhost && !hlsUrl.startsWith("http")) 
            ? GITHUB_RAW_BASE + hlsUrl.replace(/^\//, "") 
            : hlsUrl;

        hlsInstance = new Hls({
            debug: false,
            enableWorker: true,
            lowLatencyMode: false,
            maxBufferLength: dynamicForwardBuffer,
            maxMaxBufferLength: dynamicForwardBuffer * 2,
            maxBufferSize: deviceRamGb * 1024 * 1024 * 16,
            backBufferLength: Infinity,
            maxBackBufferLength: Infinity,
            testBandwidth: false,
            capLevelToPlayerSize: true,
            abrEmaFastLive: 3.0,
            abrEmaSlowLive: 9.0,
            abrEmaFastVoD: 3.0,
            abrEmaSlowVoD: 9.0,
            abrBandwidthFactor: 0.85,
            abrBandwidthUpFactor: 0.7,
            bandwidthEstimate: 1500000,
            xhrSetup: function (xhr, url) {
                // Ubah URL segmen HLS (.m3u8 & .ts) ke GitHub RAW jika dipanggil relatif dari GitHub Pages
                if (!isLocalhost && !url.startsWith("http") && !url.startsWith("blob:")) {
                    const relativePath = url.replace(location.origin + "/", "");
                    xhr.open("GET", GITHUB_RAW_BASE + relativePath, true);
                }
            }
        });

        hlsInstance.loadSource(finalHlsUrl);
        hlsInstance.attachMedia(videoElement);

        hlsInstance.on(Hls.Events.MANIFEST_PARSED, function (event, data) {
            console.log("[HLS] Manifest parsed successfully, playing...");
            populateResolutions();
            if (hlsInstance.levels && hlsInstance.levels.length > 0) {
                const firstRes = hlsInstance.levels[hlsInstance.firstLevel] || hlsInstance.levels[0];
                const resSelect = document.getElementById("resSelect");
                if (resSelect && firstRes) {
                    resSelect.options[0].text = `Auto (${firstRes.height}p)`;
                }
            }
            safePlay();
        });

        // Event listener saat level kualitas berubah secara otomatis (ABR)
        hlsInstance.on(Hls.Events.LEVEL_SWITCHED, function (event, data) {
            const resSelect = document.getElementById("resSelect");
            if (resSelect && hlsInstance.currentLevel === -1 && hlsInstance.levels[data.level]) {
                const currentRes = hlsInstance.levels[data.level];
                resSelect.options[0].text = `Auto (${currentRes.height}p)`;
            }
        });

        hlsInstance.on(Hls.Events.ERROR, function (event, data) {
            // Abaikan warning internal non-fatal HLS (bufferStalledError, bufferSeekOverHole)
            if (!data.fatal) return;

            console.warn("[HLS Fatal Error]:", data.type, data.details);

            const isOfflineError = !navigator.onLine || 
                data.details === Hls.ErrorDetails.MANIFEST_LOAD_ERROR || 
                data.details === Hls.ErrorDetails.LEVEL_LOAD_ERROR ||
                data.details === Hls.ErrorDetails.FRAG_LOAD_ERROR;

            if (isOfflineError || data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                showToast("Koneksi Internet Terputus...", true);
            } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                showToast("Memulihkan media video...", true);
                hlsInstance.recoverMediaError();
            } else {
                showToast("Terjadi kesalahan pada pemutar video.", true);
            }
        });
    } else if (videoElement.canPlayType("application/vnd.apple.mpegurl")) {
        videoElement.src = hlsUrl;
        videoElement.addEventListener("loadedmetadata", function () {
            safePlay();
        });
    }

    if (window.innerWidth <= 768) {
        videoElement.scrollIntoView({ behavior: "smooth", block: "center" });
    }
}

// Mengisi opsi resolusi ke dropdown #resSelect
function populateResolutions() {
    const resSelect = document.getElementById("resSelect");
    if (!resSelect || !hlsInstance) return;

    resSelect.innerHTML = `<option value="-1" style="background: var(--bg-panel); color: var(--text-main);">Auto</option>`;

    hlsInstance.levels.forEach((level, idx) => {
        const option = document.createElement("option");
        option.value = idx;
        option.textContent = `${level.height}p`;
        option.style.background = "var(--bg-panel)";
        option.style.color = "var(--text-main)";
        resSelect.appendChild(option);
    });
}

// Mengubah resolusi video secara manual / kembali ke AUTO
function changeResolution(levelIndex) {
    if (!hlsInstance) return;
    const targetLevel = parseInt(levelIndex);

    hlsInstance.currentLevel = targetLevel;

    if (targetLevel === -1) {
        showToast("Resolusi: ABR AUTO");
    } else {
        const selected = hlsInstance.levels[targetLevel];
        if (selected) {
            showToast(`Resolusi dikunci ke: ${selected.height}p`);
        }
    }
}



// Toggle Subtitle On/Off
function toggleSubtitle() {
    const btn = document.getElementById("btnToggleSub");
    const tracks = videoElement ? videoElement.textTracks : null;
    
    if (tracks && tracks.length > 0) {
        let currentlyOn = false;
        for (let i = 0; i < tracks.length; i++) {
            if (tracks[i].mode === "showing") {
                currentlyOn = true;
                break;
            }
        }

        if (currentlyOn) {
            for (let i = 0; i < tracks.length; i++) {
                tracks[i].mode = "disabled";
            }
            if (btn) {
                btn.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: OFF`;
                btn.className = "pixel-btn";
                btn.style.borderColor = "var(--text-dim)";
                btn.style.color = "var(--text-muted)";
            }
            showToast("Subtitle dimatikan.");
        } else {
            for (let i = 0; i < tracks.length; i++) {
                tracks[i].mode = (i === 0) ? "showing" : "disabled";
            }
            if (btn) {
                btn.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: ON`;
                btn.className = "pixel-btn primary";
                btn.style.borderColor = "var(--accent-purple)";
                btn.style.color = "var(--accent-cyan)";
            }
            showToast("Subtitle diaktifkan.");
        }
    } else {
        showToast("Tidak ada subtitle yang tersedia untuk media ini.");
    }
}
