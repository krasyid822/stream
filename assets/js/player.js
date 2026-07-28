/* ============================================================
   RETRO PIXEL STREAM — PLAYER & SUBTITLE CONTROLLER MODULE
   ============================================================ */

// Utility: Show Retro Pixel Toast Notification
function showToast(message) {
    const toast = document.getElementById("pixelToast");
    const toastMsg = document.getElementById("toastMsg");
    if (toast && toastMsg) {
        toastMsg.textContent = message;
        toast.style.display = "block";
        setTimeout(() => {
            toast.style.display = "none";
        }, 3000);
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

    document.querySelectorAll(".grid-card").forEach(c => c.classList.remove("active"));
    if (cardElement) cardElement.classList.add("active");

    if (videoElement && media.poster_url) {
        videoElement.poster = media.poster_url;
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
            track.src = subUrl;
            track.default = (idx === 0);
            videoElement.appendChild(track);
        });

        // Setel langsung mode track pertama ke 'showing'
        setTimeout(() => {
            for (let i = 0; i < videoElement.textTracks.length; i++) {
                videoElement.textTracks[i].mode = (i === 0) ? "showing" : "disabled";
            }
            const btn = document.getElementById("btnToggleSub");
            if (btn) {
                btn.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: ON`;
                btn.className = "pixel-btn primary";
                btn.style.borderColor = "var(--accent-purple)";
                btn.style.color = "var(--accent-cyan)";
            }
        }, 100);
    } else {
        // Reset tombol ke OFF jika media tidak memiliki subtitle
        const btn = document.getElementById("btnToggleSub");
        if (btn) {
            btn.innerHTML = `<i class="fa-solid fa-closed-captioning"></i> SUBTITLE: OFF`;
            btn.className = "pixel-btn";
            btn.style.borderColor = "var(--text-dim)";
            btn.style.color = "var(--text-muted)";
        }
    }

    const hlsUrl = media.master_url;

    const safePlay = () => {
        const playPromise = videoElement.play();
        if (playPromise !== undefined) {
            playPromise.catch(error => {
                if (error.name !== "AbortError") {
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
        // Perangkat RAM kecil (<=2GB): 30 detik | Perangkat RAM sedang (4GB): 60 detik | Perangkat RAM besar (>=8GB): 120 detik
        let dynamicForwardBuffer = 60;
        if (deviceRamGb <= 2) {
            dynamicForwardBuffer = 30;
        } else if (deviceRamGb >= 8) {
            dynamicForwardBuffer = 120;
        }

        hlsInstance = new Hls({
            debug: false,
            enableWorker: true,
            lowLatencyMode: false,
            
            /* ============================================================
               1. ADAPTIVE BUFFER & ZERO RE-DOWNLOAD (PERMANENT CACHE)
               ============================================================ */
            maxBufferLength: dynamicForwardBuffer,  // Alokasi buffer maju dinamis sesuai RAM ready
            maxMaxBufferLength: dynamicForwardBuffer * 2,
            maxBufferSize: deviceRamGb * 1024 * 1024 * 16, // Alokasi memori RAM fleksibel untuk HLS buffer
            
            // SET HINGGA INFINITY: Segmen yang sudah ter-download disimpan di memori
            // selama RAM mencukupi dan TIDAK PERNAH DI-DOWNLOAD ULANG saat mundur/rewind!
            backBufferLength: Infinity,
            maxBackBufferLength: Infinity,
            
            /* ============================================================
               2. EFISIENSI KUOTA & DETEKSI LAYAR
               ============================================================ */
            testBandwidth: false,          // Matikan tes bandwidth redundant untuk hemat kuota
            capLevelToPlayerSize: true,    // Sesuaikan resolusi maksimum dengan ukuran fisik monitor/layar HP
            
            /* ============================================================
               3. AKURASI DETEKSI KECEPATAN DOWNLOAD (MOVING AVERAGE ABR)
               ============================================================ */
            abrEmaFastLive: 3.0,
            abrEmaSlowLive: 9.0,
            abrEmaFastVoD: 3.0,
            abrEmaSlowVoD: 9.0,           // Rata-rataMoving Average kecepatan download selama streaming
            abrBandwidthFactor: 0.85,     // 85% safety factor dari bandwidth nyata
            abrBandwidthUpFactor: 0.7,    // Menjaga kestabilan sebelum menaikkan kualitas resolusi

            /* Ambang batas estimasi awal yang aman (360p ~1.5M, 480p ~3M, 720p ~6M, 1080p ~12M) */
            bandwidthEstimate: 1500000
        });

        hlsInstance.loadSource(hlsUrl);
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

        let retryCount = 0;
        const MAX_RETRIES = 3;

        hlsInstance.on(Hls.Events.ERROR, function (event, data) {
            if (data.fatal) {
                console.error("[HLS Error Fatal]:", data.type, data.details);
                if (retryCount >= MAX_RETRIES) {
                    console.error("[HLS] Max retries reached. Stopping player to prevent freeze.");
                    hlsInstance.destroy();
                    return;
                }
                
                retryCount++;
                switch (data.type) {
                    case Hls.ErrorTypes.NETWORK_ERROR:
                        console.log(`[HLS] Network error retry (${retryCount}/${MAX_RETRIES})...`);
                        hlsInstance.startLoad();
                        break;
                    case Hls.ErrorTypes.MEDIA_ERROR:
                        console.log(`[HLS] Media error retry (${retryCount}/${MAX_RETRIES})...`);
                        hlsInstance.recoverMediaError();
                        break;
                    default:
                        console.error("[HLS] Unrecoverable error, destroying instance.");
                        hlsInstance.destroy();
                        break;
                }
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
