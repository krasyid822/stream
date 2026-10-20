/*
 * RETRO PIXEL STREAM — PWA BRIDGE (TUNDA 5)
 * -----------------------------------------
 * Mendaftarkan service worker + jembatan pesan untuk modal pengaturan.
 * TIDAK ada mekanisme unduh; cache hanya berisi video yang sudah ditonton.
 */
(function () {
    "use strict";

    let deferredInstallPrompt = null;
    let registration = null;
    const listeners = {};

    function emit(name, detail) {
        (listeners[name] || []).forEach((fn) => {
            try { fn(detail); } catch (e) { /* noop */ }
        });
        window.dispatchEvent(new CustomEvent("pwa:" + name, { detail }));
    }

    function supported() {
        return "serviceWorker" in navigator;
    }

    function swReady() {
        if (!supported()) return Promise.reject(new Error("Service worker tidak didukung"));
        return navigator.serviceWorker.ready.then((reg) => reg);
    }

    function ask(message, timeoutMs) {
        return swReady().then((reg) => new Promise((resolve, reject) => {
            const worker = reg.active || navigator.serviceWorker.controller;
            if (!worker) {
                reject(new Error("Service worker belum aktif"));
                return;
            }
            const channel = new MessageChannel();
            const timer = setTimeout(() => reject(new Error("Timeout service worker")), timeoutMs || 8000);
            channel.port1.onmessage = (event) => {
                clearTimeout(timer);
                resolve(event.data);
            };
            worker.postMessage(message, [channel.port2]);
        }));
    }

    function getCacheInfo() {
        return ask({ type: "GET_CACHE_INFO" }).catch(() => null);
    }

    function clearCache() {
        return ask({ type: "CLEAR_MEDIA" }).catch(() => null);
    }

    function setCacheLimit(bytes) {
        return ask({ type: "SET_CACHE_LIMIT", bytes: bytes }).catch(() => null);
    }

    function requestPersistentStorage() {
        if (navigator.storage && navigator.storage.persist) {
            return navigator.storage.persist().catch(() => false);
        }
        return Promise.resolve(false);
    }

    async function install() {
        if (!deferredInstallPrompt) return false;
        deferredInstallPrompt.prompt();
        const choice = await deferredInstallPrompt.userChoice;
        deferredInstallPrompt = null;
        emit("install", choice);
        return choice && choice.outcome === "accepted";
    }

    function on(name, fn) {
        (listeners[name] = listeners[name] || []).push(fn);
    }

    window.addEventListener("beforeinstallprompt", (event) => {
        event.preventDefault();
        deferredInstallPrompt = event;
        emit("installable", true);
    });

    window.addEventListener("appinstalled", () => {
        deferredInstallPrompt = null;
        emit("installed", true);
    });

    window.addEventListener("online", () => emit("online", true));
    window.addEventListener("offline", () => emit("offline", true));

    if (supported()) {
        window.addEventListener("load", () => {
            navigator.serviceWorker.register("./sw.js").then((reg) => {
                registration = reg;
                emit("registered", reg);
                reg.addEventListener("updatefound", () => emit("update", true));
            }).catch((err) => {
                console.warn("[PWA] Registrasi service worker gagal:", err);
            });
        });

        navigator.serviceWorker.addEventListener("controllerchange", () => {
            emit("controller", true);
        });
    }

    window.pwa = {
        supported,
        ready: swReady,
        registration: () => registration,
        getCacheInfo,
        clearCache,
        setCacheLimit,
        requestPersistentStorage,
        install,
        canInstall: () => Boolean(deferredInstallPrompt),
        on
    };
})();
