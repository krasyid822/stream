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
    
    // Pindahkan blok folder episode secara halus ke posisi dekat player (TANPA RELOAD DOM)
    if (typeof movePlayingGroupBlockToPlayer === "function" && media && media.folder) {
        movePlayingGroupBlockToPlayer(media.folder);
    }

    // Cukup update class active/playing pada kartu yang diklik tanpa merusak DOM
    document.querySelectorAll(".grid-card.media-card").forEach(c => c.classList.remove("active", "playing"));
    if (cardElement) {
        cardElement.classList.add("active", "playing");
    }

    // DI MOBILE: Eksekusi autoscroll meluncur langsung ke playerWrapper tanpa efek reload
    if (window.innerWidth <= 768 && playerWrapper) {
        playerWrapper.scrollIntoView({ behavior: "smooth", block: "end" });
    }

    if (typeof syncUrlHash === "function") {
        syncUrlHash();
    }

    const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1" || location.hostname === "0.0.0.0";
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

    const btnSub = document.getElementById("btnToggleSub");

    // Tambahkan track subtitle WebVTT (.vtt) jika ada (Softsub)
    if (media.subtitles && media.subtitles.length > 0) {
        if (btnSub) btnSub.style.display = "inline-flex";

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
        // Sembunyikan tombol Subtitle untuk video yang tidak memiliki file subtitle / Hardsub
        if (btnSub) btnSub.style.display = "none";
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

        // Trik GitHub RAW CDN: Alihkan request file .m3u8 & .ts ke GitHub RAW jika web berjalan di GitHub Pages/online
        const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1" || location.hostname === "0.0.0.0";
        const GITHUB_RAW_BASE = "https://raw.githubusercontent.com/krasyid822/stream/main/";

        const finalHlsUrl = (!isLocalhost && !hlsUrl.startsWith("http")) 
            ? GITHUB_RAW_BASE + hlsUrl.replace(/^\//, "") 
            : hlsUrl;

        /* ============================================================
           INDEXEDDB STORAGE CACHE & HYBRID RAM MANAGEMENT
           - Mengalihkan segmen .ts dari RAM ke Storage Sementara (IndexedDB)
           - Memantau ketersediaan RAM via Performance API jika didukung
           - Mencegah kuota terbuang saat seek jauh & menjaga RAM tetap ringan
           ============================================================ */
        const DB_NAME = "HlsSegmentCacheDB";
        const DB_STORE = "segments";
        let segmentDB = null;

        // Inisialisasi IndexedDB Storage Sementara
        const initSegmentCacheDB = function () {
            return new Promise((resolve) => {
                try {
                    const req = indexedDB.open(DB_NAME, 1);
                    req.onupgradeneeded = function (e) {
                        const db = e.target.result;
                        if (!db.objectStoreNames.contains(DB_STORE)) {
                            db.createObjectStore(DB_STORE);
                        }
                    };
                    req.onsuccess = function (e) {
                        segmentDB = e.target.result;
                        resolve(true);
                    };
                    req.onerror = function () {
                        resolve(false);
                    };
                } catch (e) {
                    resolve(false);
                }
            });
        };

        initSegmentCacheDB();

        // Simpan blob segmen ke IndexedDB Storage Sementara
        const saveSegmentToStorage = function (url, arrayBuffer) {
            if (!segmentDB || !arrayBuffer) return;
            try {
                // Duplikasi (clone) ArrayBuffer agar tidak menjadi 'detached' saat diserahkan ke SourceBuffer HLS.js
                const bufferCopy = arrayBuffer.slice(0);
                const tx = segmentDB.transaction(DB_STORE, "readwrite");
                const store = tx.objectStore(DB_STORE);
                store.put(bufferCopy, url);
            } catch (e) {
                // Tangani error secara silent agar playback tetap berjalan mulus
            }
        };

        // Ambil blob segmen dari IndexedDB jika sudah pernah diunduh
        const getSegmentFromStorage = function (url) {
            return new Promise((resolve) => {
                if (!segmentDB) return resolve(null);
                try {
                    const tx = segmentDB.transaction(DB_STORE, "readonly");
                    const store = tx.objectStore(DB_STORE);
                    const req = store.get(url);
                    req.onsuccess = function () {
                        resolve(req.result || null);
                    };
                    req.onerror = function () {
                        resolve(null);
                    };
                } catch (e) {
                    resolve(null);
                }
            });
        };

        hlsInstance = new Hls({
            debug: false,
            enableWorker: true,
            lowLatencyMode: false,
            maxBufferLength: 30,             // 3 segmen maju (~30 detik) di RAM
            maxMaxBufferLength: 35,
            maxBufferSize: 0,                // RAM Murni 3 segmen
            backBufferLength: 30,            // 3 segmen mundur (~30 detik) di RAM
            maxBackBufferLength: 30,
            testBandwidth: false,
            capLevelToPlayerSize: false,     // Jangan batasi level resolusi berdasarkan dimensi elemen player HTML
            abrEmaFastLive: 1.0,
            abrEmaSlowLive: 3.0,
            abrEmaFastVoD: 1.0,              // Deteksi lonjakan kecepatan jaringan secara instan (1 detik)
            abrEmaSlowVoD: 3.0,
            abrBandwidthFactor: 0.9,
            abrBandwidthUpFactor: 0.95,      // Sangat responsif menaikkan resolusi ke level lebih tinggi saat jaringan cepat (95%)
            xhrSetup: function (xhr, url) {
                const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1" || location.hostname === "0.0.0.0";
                const GITHUB_RAW_BASE = "https://raw.githubusercontent.com/krasyid822/stream/main/";
                
                if (!isLocalhost && !url.startsWith("http") && !url.startsWith("blob:")) {
                    const relativePath = url.replace(location.origin + "/", "").replace(/^\//, "");
                    xhr.open("GET", GITHUB_RAW_BASE + relativePath, true);
                }
            }
        });

        // Intersepsi Loader HLS: Cek Storage Sementara (IndexedDB) sebelum download internet
        hlsInstance.on(Hls.Events.FRAG_LOADED, function (event, data) {
            isFetchingChunk = false;
            if (data && data.frag && data.payload) {
                // Simpan segmen yang telah selesai diunduh ke IndexedDB
                saveSegmentToStorage(data.frag.url, data.payload);
            }
            checkAndManageBufferThreshold();
        });

        /* ============================================================
           ALGORITMA DUAL-DIRECTION THRESHOLD (SEEKING & PLAYING)
           ============================================================ */
        let isFetchingChunk = false;

        const checkAndManageBufferThreshold = function () {
            if (!hlsInstance || !videoElement) return;

            // Kunci pengunduhan HANYA saat video benar-benar dipause pengguna
            if (videoElement.paused) {
                hlsInstance.stopLoad();
                return;
            }

            // Saat video PLAYING: Biarkan Hls.js mengelola alokasi 30 detik (3 segmen maju) secara alami
            // tanpa ada pembatalan / stopLoad() manual yang menghambat koneksi 4G Slow
            if (!hlsInstance.loadingEnabled) {
                hlsInstance.startLoad();
            }
        };

        // Pemulihan Otomatis jika buffer sempat terhenti akibat jaringan (Stalling Protection)
        hlsInstance.on(Hls.Events.BUFFER_STALLED, function () {
            if (hlsInstance && videoElement && !videoElement.paused) {
                isFetchingChunk = true;
                hlsInstance.startLoad();
            }
        });

        // Evaluasi buffer setiap kali 1 segmen selesai dimuat
        hlsInstance.on(Hls.Events.FRAG_LOADED, function (event, data) {
            isFetchingChunk = false;
            checkAndManageBufferThreshold();
        });

        // Pemantau posisi waktu pemutaran video secara berkala
        videoElement.ontimeupdate = checkAndManageBufferThreshold;

        // Listener penggeseran timeline (Seeking Maju / Mundur)
        videoElement.onseeking = function () {
            if (!hlsInstance || !videoElement) return;

            const currentTime = videoElement.currentTime;
            const buffered = videoElement.buffered;
            let bufferedAhead = 0;
            let bufferedBehind = 0;

            for (let i = 0; i < buffered.length; i++) {
                if (buffered.start(i) <= currentTime && currentTime <= buffered.end(i)) {
                    bufferedAhead = buffered.end(i) - currentTime;
                    bufferedBehind = currentTime - buffered.start(i);
                    break;
                }
            }

            // Cerdas: Jika menggeser timeline ke posisi yang sisa buffer maju ATAU mundurnya tinggal <= 1 segmen (10s),
            // segera izinkan unduh 3 segmen adegan di posisi tersebut!
            if (bufferedAhead <= 10 || bufferedBehind <= 10) {
                isFetchingChunk = true;
                hlsInstance.startLoad();
            }
        };

        videoElement.onpause = function () {
            if (hlsInstance) {
                hlsInstance.stopLoad();
            }
        };

        videoElement.onplay = function () {
            isFetchingChunk = false;
            if (hlsInstance) {
                hlsInstance.startLoad();
            }
            checkAndManageBufferThreshold();
        };

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

        // Synchronize tampilan indikator Auto (Resolusi) saat ABR HLS menyesuaikan level kualitas secara adaptif
        const updateAutoResolutionLabel = function (targetLevelIndex) {
            const resSelect = document.getElementById("resSelect");
            if (!resSelect || !hlsInstance) return;

            // HANYA perbarui teks opsi 0 jika user sedang memilih mode "Auto" (value "-1")
            if (resSelect.value !== "-1") return;

            let activeLevel = typeof targetLevelIndex === "number" ? targetLevelIndex : hlsInstance.currentLevel;
            if (activeLevel === -1) {
                activeLevel = hlsInstance.loadLevel >= 0 ? hlsInstance.loadLevel : (hlsInstance.firstLevel || 0);
            }

            if (hlsInstance.levels && hlsInstance.levels[activeLevel]) {
                const currentRes = hlsInstance.levels[activeLevel];
                if (resSelect.options && resSelect.options.length > 0) {
                    resSelect.options[0].text = `Auto (${currentRes.height}p)`;
                }
            }
        };

        // Event listener saat level ABR mulai berpindah, selesai berpindah, atau memuat segmen baru
        hlsInstance.on(Hls.Events.LEVEL_SWITCHING, function (event, data) {
            if (data && typeof data.level !== "undefined") {
                updateAutoResolutionLabel(data.level);
            }
        });

        hlsInstance.on(Hls.Events.LEVEL_SWITCHED, function (event, data) {
            if (data && typeof data.level !== "undefined") {
                updateAutoResolutionLabel(data.level);
            }
        });

        hlsInstance.on(Hls.Events.FRAG_LOADING, function (event, data) {
            if (data && data.frag && typeof data.frag.level !== "undefined") {
                updateAutoResolutionLabel(data.frag.level);
            }
        });

        hlsInstance.on(Hls.Events.FRAG_CHANGED, function (event, data) {
            if (data && data.frag && typeof data.frag.level !== "undefined") {
                updateAutoResolutionLabel(data.frag.level);
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
                // Simpan posisi waktu pemutaran saat ini agar tidak ter-reset ke detik 0
                const savedTime = videoElement ? videoElement.currentTime : 0;
                hlsInstance.recoverMediaError();
                if (videoElement && savedTime > 0) {
                    videoElement.currentTime = savedTime;
                }
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

    // Gunakan nextLevel agar pergantian resolusi berjalan seamless pada segmen berikutnya (TIDAK RESET DETIK VDEO)
    hlsInstance.nextLevel = targetLevel;

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
