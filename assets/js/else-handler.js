/* ============================================================
   RETRO PIXEL STREAM — ERROR & TOAST HANDLER MODULE
   ============================================================ */

let toastTimer = null;

// Utility: Show Retro Pixel Toast Notification
function showToast(message, isPersistent = false) {
    const toast = document.getElementById("pixelToast");
    const toastMsg = document.getElementById("toastMsg");
    if (toast && toastMsg) {
        if (toastTimer) {
            clearTimeout(toastTimer);
            toastTimer = null;
        }

        toastMsg.textContent = message;
        toast.style.display = "block";

        // Jika notifikasi bersifat persisten (error belum selesai), jangan sembunyikan otomatis
        if (!isPersistent) {
            toastTimer = setTimeout(() => {
                toast.style.display = "none";
                toastTimer = null;
            }, 3500);
        }
    }
}

// Sembunyikan Toast saat kasus/error selesai
function hideToast() {
    const toast = document.getElementById("pixelToast");
    if (toast) {
        if (toastTimer) {
            clearTimeout(toastTimer);
            toastTimer = null;
        }
        toast.style.display = "none";
    }
}

// Monitor Status Koneksi Internet Real-Time (Online / Offline)
function checkInitialOnlineStatus() {
    if (!navigator.onLine) {
        showToast("Koneksi Internet Terputus...", true);
    }
}

window.addEventListener("offline", function () {
    showToast("Koneksi Internet Terputus...", true);
});

window.addEventListener("online", function () {
    showToast("Koneksi Internet Terhubung Kembali!");
    
    // Otomatis pulihkan pemutar video HLS tanpa perlu diklik manual oleh pengguna
    if (typeof hlsInstance !== "undefined" && hlsInstance) {
        try {
            hlsInstance.startLoad();
            const videoEl = document.getElementById("hlsPlayer");
            if (videoEl && videoEl.paused) {
                videoEl.play().catch(e => console.warn("Auto resume play on online failed:", e));
            }
        } catch (err) {
            console.warn("Auto reload HLS on reconnect error:", err);
        }
    }
});

// Jalankan pengecekan awal saat DOM selesai dimuat
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", checkInitialOnlineStatus);
} else {
    checkInitialOnlineStatus();
}
