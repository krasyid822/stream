/*
 * RETRO PIXEL STREAM — MODAL PENGATURAN (TUNDA 5)
 * ------------------------------------------------
 * Menampilkan pemakaian cache PWA, batas ukuran cache, dan tombol bersihkan.
 * Tidak ada tombol/aksi unduh di sini.
 */
(function () {
    "use strict";

    const el = (id) => document.getElementById(id);
    let lastInfo = null;

    function formatBytes(bytes) {
        const n = Number(bytes) || 0;
        if (n < 1024) return n + " B";
        const units = ["KB", "MB", "GB", "TB"];
        let value = n / 1024;
        let i = 0;
        while (value >= 1024 && i < units.length - 1) {
            value /= 1024;
            i++;
        }
        return (value >= 10 ? value.toFixed(1) : value.toFixed(2)) + " " + units[i];
    }

    function setText(id, text) {
        const node = el(id);
        if (node) node.textContent = text;
    }

    function setStatus(text, color) {
        const node = el("settingsSwStatus");
        if (!node) return;
        node.textContent = text;
        node.style.color = color || "var(--accent-green)";
    }

    function render(info) {
        lastInfo = info;
        if (!info) {
            setText("settingsCacheUsage", "Tidak tersedia");
            setText("settingsCacheDetail", "Service worker belum aktif (butuh HTTPS/localhost).");
            setStatus("OFFLINE-ONLY / TIDAK AKTIF", "var(--accent-pink)");
            return;
        }

        setText("settingsCacheUsage", `${formatBytes(info.mediaBytes)} terpakai`);
        setText(
            "settingsCacheDetail",
            `${info.mediaEntries} segmen video · ${info.shellEntries} aset aplikasi · ` +
            `total origin ${formatBytes(info.usage)} / ${formatBytes(info.quota)}`
        );

        const pct = info.limitBytes > 0 ? Math.min(100, (info.usage / info.limitBytes) * 100) : 0;
        const bar = el("settingsCacheBar");
        if (bar) bar.style.width = pct.toFixed(1) + "%";

        const select = el("settingsCacheLimit");
        if (select) {
            const match = Array.from(select.options).find(
                (opt) => Number(opt.value) === Number(info.limitBytes)
            );
            if (match) select.value = match.value;
        }

        setStatus("AKTIF · SIAP OFFLINE", "var(--accent-green)");

        const persistBtn = el("settingsPersistBtn");
        if (persistBtn) {
            persistBtn.style.display = info.persisted ? "none" : "inline-flex";
        }

        const installBtn = el("settingsInstallBtn");
        if (installBtn) {
            const can = window.pwa && window.pwa.canInstall && window.pwa.canInstall();
            installBtn.style.display = can ? "inline-flex" : "none";
        }
    }

    async function refresh() {
        if (!window.pwa || !window.pwa.supported()) {
            render(null);
            return;
        }
        const info = await window.pwa.getCacheInfo();
        render(info);
    }

    function openSettings() {
        const overlay = el("settingsOverlay");
        if (!overlay) return;
        overlay.classList.add("open");
        overlay.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
        refresh();
    }

    function closeSettings() {
        const overlay = el("settingsOverlay");
        if (!overlay) return;
        overlay.classList.remove("open");
        overlay.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
    }

    // --- Modal konfirmasi/alert internal (pengganti window.confirm/alert) ---
    let dialogResolve = null;
    let dialogPreviousFocus = null;
    let dialogSettingsWasOpen = false;

    function showDialog(options) {
        const overlay = el("confirmOverlay");
        if (!overlay) return Promise.resolve(false);

        const opts = Object.assign({
            title: "KONFIRMASI",
            message: "",
            confirmText: "LANJUT",
            cancelText: "BATAL",
            showCancel: true,
            danger: false
        }, options || {});

        // Jika ada dialog yang masih terbuka, anggap batal dulu.
        if (dialogResolve) {
            const previous = dialogResolve;
            dialogResolve = null;
            previous(false);
        }

        return new Promise((resolve) => {
            dialogResolve = resolve;
            dialogPreviousFocus = document.activeElement;
            dialogSettingsWasOpen = Boolean(
                el("settingsOverlay") && el("settingsOverlay").classList.contains("open")
            );

            setText("confirmTitleText", opts.title);
            setText("confirmMessage", opts.message);

            const okBtn = el("confirmOkBtn");
            const cancelBtn = el("confirmCancelBtn");
            const closeBtn = el("confirmCloseBtn");

            if (okBtn) {
                okBtn.className = "pixel-btn " + (opts.danger ? "danger" : "primary");
                okBtn.textContent = "";
                const icon = document.createElement("i");
                icon.className = opts.danger ? "fa-solid fa-trash-can" : "fa-solid fa-check";
                okBtn.append(icon, document.createTextNode(" " + opts.confirmText));
            }
            if (cancelBtn) {
                cancelBtn.textContent = opts.cancelText;
                cancelBtn.style.display = opts.showCancel ? "" : "none";
            }
            if (closeBtn) closeBtn.style.display = opts.showCancel ? "" : "none";

            overlay.classList.add("open");
            overlay.setAttribute("aria-hidden", "false");
            document.body.style.overflow = "hidden";

            window.setTimeout(() => {
                if (okBtn) okBtn.focus();
            }, 0);
        });
    }

    function closeDialog(result) {
        const overlay = el("confirmOverlay");
        if (!overlay) return;

        overlay.classList.remove("open");
        overlay.setAttribute("aria-hidden", "true");
        document.body.style.overflow = dialogSettingsWasOpen ? "hidden" : "";

        if (dialogPreviousFocus && document.contains(dialogPreviousFocus)) {
            dialogPreviousFocus.focus();
        }
        dialogPreviousFocus = null;

        const resolve = dialogResolve;
        dialogResolve = null;
        if (resolve) resolve(Boolean(result));
    }

    function confirmDialog(message, options) {
        return showDialog(Object.assign({}, options || {}, {
            message,
            showCancel: true
        }));
    }

    function alertDialog(message, options) {
        return showDialog(Object.assign({}, options || {}, {
            message,
            showCancel: false,
            confirmText: (options && options.confirmText) || "OK"
        }));
    }

    async function clearCache() {
        if (!window.pwa) return;
        const ok = await confirmDialog(
            "Hapus cache video yang pernah ditonton? Video tetap bisa diputar lagi (butuh internet).",
            { title: "HAPUS CACHE", confirmText: "HAPUS", danger: true }
        );
        if (!ok) return;
        const btn = el("settingsClearBtn");
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> MENGHAPUS...';
        }
        const info = await window.pwa.clearCache();
        render(info);
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-trash-can"></i> BERSIHKAN CACHE';
        }
    }

    async function saveLimit() {
        if (!window.pwa) return;
        const select = el("settingsCacheLimit");
        if (!select) return;
        const bytes = Number(select.value);
        const info = await window.pwa.setCacheLimit(bytes);
        render(info);
        setText("settingsCacheDetail", `Batas cache disimpan: ${formatBytes(bytes)}.`);
    }

    async function persistStorage() {
        if (!window.pwa) return;
        const btn = el("settingsPersistBtn");
        const ok = await window.pwa.requestPersistentStorage();
        if (btn) {
            btn.innerHTML = ok
                ? '<i class="fa-solid fa-shield-halved"></i> PENYIMPANAN PERSISTEN AKTIF'
                : '<i class="fa-solid fa-shield"></i> PENYIMPANAN PERSISTEN DITOLAK';
        }
        refresh();
    }

    async function installApp() {
        if (!window.pwa) return;
        await window.pwa.install();
        refresh();
    }

    function bind() {
        const openBtn = el("settingsBtn");
        if (openBtn) openBtn.addEventListener("click", openSettings);

        const closeBtn = el("settingsCloseBtn");
        if (closeBtn) closeBtn.addEventListener("click", closeSettings);

        const overlay = el("settingsOverlay");
        if (overlay) {
            overlay.addEventListener("click", (event) => {
                if (event.target === overlay) closeSettings();
            });
        }

        const confirmOverlay = el("confirmOverlay");
        if (confirmOverlay) {
            confirmOverlay.addEventListener("click", (event) => {
                if (event.target === confirmOverlay) closeDialog(false);
            });
        }
        const confirmOkBtn = el("confirmOkBtn");
        if (confirmOkBtn) confirmOkBtn.addEventListener("click", () => closeDialog(true));
        const confirmCancelBtn = el("confirmCancelBtn");
        if (confirmCancelBtn) confirmCancelBtn.addEventListener("click", () => closeDialog(false));
        const confirmCloseBtn = el("confirmCloseBtn");
        if (confirmCloseBtn) confirmCloseBtn.addEventListener("click", () => closeDialog(false));

        document.addEventListener("keydown", (event) => {
            if (event.key !== "Escape") return;
            if (confirmOverlay && confirmOverlay.classList.contains("open")) {
                closeDialog(false);
                return;
            }
            closeSettings();
        });

        const clearBtn = el("settingsClearBtn");
        if (clearBtn) clearBtn.addEventListener("click", clearCache);

        const saveBtn = el("settingsSaveLimitBtn");
        if (saveBtn) saveBtn.addEventListener("click", saveLimit);

        const persistBtn = el("settingsPersistBtn");
        if (persistBtn) persistBtn.addEventListener("click", persistStorage);

        const installBtn = el("settingsInstallBtn");
        if (installBtn) installBtn.addEventListener("click", installApp);

        const online = el("settingsOnlineStatus");
        const updateOnline = () => {
            if (!online) return;
            online.textContent = navigator.onLine ? "ONLINE" : "OFFLINE";
            online.style.color = navigator.onLine ? "var(--accent-green)" : "var(--accent-pink)";
        };
        updateOnline();
        window.addEventListener("online", updateOnline);
        window.addEventListener("offline", updateOnline);

        if (window.pwa) {
            window.pwa.on("installable", () => refresh());
            window.pwa.on("installed", () => refresh());
            window.pwa.on("controller", () => refresh());
        }
    }

    // Diekspos untuk tombol di index.html
    window.openSettings = openSettings;
    window.closeSettings = closeSettings;
    window.refreshSettings = refresh;

    // Pengganti window.confirm/alert dengan modal internal.
    window.pixelConfirm = confirmDialog;
    window.pixelAlert = alertDialog;
    window.pixelDialog = showDialog;

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bind);
    } else {
        bind();
    }
})();
