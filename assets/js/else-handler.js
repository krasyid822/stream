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
window.addEventListener("offline", function () {
    showToast("Koneksi Internet Terputus...", true);
});

window.addEventListener("online", function () {
    showToast("Koneksi Internet Terhubung Kembali!");
});
