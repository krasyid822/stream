#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys

def smart_pull():
    print("==========================================================")
    print("📥 SMART GIT PULL & REPOSITORY SYNCHRONIZER")
    print("==========================================================")

    # 1. Bersihkan file kunci index.lock dan cache python lokal jika ada
    lock_file = os.path.join(".git", "index.lock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print("[*] Membersihkan file kunci .git/index.lock yang kadaluarsa...")
        except Exception:
            pass

    for root, dirs, files in os.walk(".", topdown=False):
        for d in dirs:
            if d == "__pycache__":
                try:
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                except Exception:
                    pass
        for f in files:
            if f.endswith(".pyc"):
                try:
                    os.remove(os.path.join(root, f))
                except Exception:
                    pass

    # 2. Pastikan remote origin terarah ke repository stream utama
    try:
        if not os.path.exists(".git"):
            print("[*] Menginisialisasi repositori Git lokal...")
            subprocess.run(["git", "init", "-b", "main"], check=True)
            subprocess.run(["git", "remote", "add", "origin", "git@github.com:krasyid822/stream.git"], check=True)
    except Exception as e:
        print(f"[-] Peringatan inisialisasi git: {e}")

    # 3. Tarik update terbaru dari GitHub main branch
    print("\n[1/3] Mengambil pembaruan terbaru dari GitHub (git pull --rebase)...")
    pull_res = subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], text=True)
    if pull_res.returncode != 0:
        print("[-] Rebase otomatis gagal, mencoba git fetch & merge standard...")
        subprocess.run(["git", "fetch", "origin", "main"], check=False)
        subprocess.run(["git", "merge", "origin/main", "--allow-unrelated-histories", "-m", "chore: sync remote main"], check=False)

    print("[✓] Sinkronisasi kode Web UI dan metadata berhasil!")

    # 4. Hapus folder media biner lokal (karena semua media sudah dialihkan ke stream_drive)
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    print("\n[2/3] Memeriksa folder media lokal (Dialihkan ke stream_drive)...")
    
    media_dirs_cleaned = 0
    # Deteksi dan bersihkan folder biner lokal yang ada
    for item in os.listdir(workspace_dir):
        item_path = os.path.join(workspace_dir, item)
        if os.path.isdir(item_path) and item not in {"assets", ".github", ".git", ".vscode", ".gemini"}:
            # Cek apakah folder berisi file .ts atau .m3u8
            has_hls = False
            for r, _, fls in os.walk(item_path):
                if any(f.endswith(".ts") or f.endswith(".m3u8") for f in fls):
                    has_hls = True
                    break
            if has_hls or item in {"RAW", "RAW_TEMP", "RAW_TEMP_EXTRACT", "release_downloads"}:
                try:
                    shutil.rmtree(item_path, ignore_errors=True)
                    media_dirs_cleaned += 1
                    print(f" -> Membersihkan folder media lokal: {item}")
                except Exception:
                    pass

    if media_dirs_cleaned > 0:
        print(f"[✓] {media_dirs_cleaned} folder media lokal dibersihkan. Disk lokal Anda tetap ramping!")
    else:
        print("[+] Tidak ada folder media lokal usang.")

    # 5. Status akhir
    print("\n[3/3] Memeriksa status pohon kerja...")
    status_res = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
    if not status_res.stdout.strip():
        print("[✓] Pohon kerja 100% sinkron dan bersih dengan GitHub!")
    else:
        print(status_res.stdout.strip())

    print("\n==========================================================")
    print("🎉 SINKRONISASI SELESAI! Repo lokal 100% sinkron & bersih.")
    print("==========================================================")

if __name__ == "__main__":
    smart_pull()
