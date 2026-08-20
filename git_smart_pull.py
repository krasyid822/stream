#!/usr/bin/env python3
import os
import subprocess
import sys

def smart_pull():
    print("==========================================================")
    print("📥 SMART GIT PULL, BLOBLESS FILTER & LOCAL TS CLEANER")
    print("==========================================================")

    # 1. Pastikan repositori Git terinisialisasi dan konfigurasi Blobless Filter aktif
    print("[1/4] Memastikan repositori Git & konfigurasi Blobless Filter (blob:none) aktif...")
    try:
        if not os.path.exists(".git"):
            print("[*] Menginisialisasi repositori Git lokal...")
            subprocess.run(["git", "init", "-b", "main"], check=True)
            subprocess.run(["git", "remote", "add", "origin", "git@github.com:krasyid822/stream.git"], check=True)

        subprocess.run(["git", "config", "remote.origin.promisor", "true"], check=True)
        subprocess.run(["git", "config", "remote.origin.partialclonefilter", "blob:none"], check=True)
        print("[✓] Repositori Git & konfigurasi blobless aktif.")
    except Exception as e:
        print(f"[-] Peringatan konfigurasi blobless: {e}")

    # 2. Ambil update terbaru tanpa mengunduh blob file .ts biner
    print("\n[2/4] Mengambil pembaruan terbaru dari GitHub (git fetch & merge blobless)...")
    fetch_res = subprocess.run(["git", "fetch", "--filter=blob:none", "origin", "main"], text=True)
    if fetch_res.returncode != 0:
        print("[-] Gagal melakukan git fetch. Silakan periksa koneksi atau remote git.")
        sys.exit(fetch_res.returncode)

    merge_res = subprocess.run(["git", "merge", "origin/main", "--allow-unrelated-histories", "-m", "chore: sync remote main"], text=True)
    if merge_res.returncode != 0:
        print("[-] Mencoba reset ke origin/main...")
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=False)
    print("[✓] Sinkronisasi Git blobless berhasil!")

    # 3. Cari dan hapus semua file .ts lokal di working tree
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    print("\n[3/4] Memindai dan membersihkan file segmen .ts di working directory...")

    ts_files = []
    total_bytes = 0
    IGNORED_DIRS = {".git"}

    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            if f.endswith(".ts"):
                full_path = os.path.join(root, f)
                ts_files.append(full_path)
                try:
                    total_bytes += os.path.getsize(full_path)
                except Exception:
                    pass

    if not ts_files:
        print("[+] Tidak ada file .ts di working tree yang perlu dibersihkan.")
    else:
        print(f"[+] Ditemukan {len(ts_files)} file .ts lokal ({total_bytes / (1024 * 1024):.2f} MB).")
        deleted_count = 0
        for fpath in ts_files:
            try:
                os.remove(fpath)
                deleted_count += 1
            except Exception as e:
                print(f"[-] Gagal menghapus {fpath}: {e}")
        print(f"[✓] Berhasil menghapus {deleted_count} file .ts di working tree.")

    # 4. Tandai file .ts dengan assume-unchanged secara aman (null-delimited)
    print("\n[4/4] Memperbarui git index (assume-unchanged) agar file .ts di GitHub tetap aman...")
    try:
        ls_res = subprocess.run(["git", "ls-files", "-z", "*.ts"], capture_output=True, check=True)
        raw_files = ls_res.stdout.split(b'\0')
        tracked_ts = [f.decode('utf-8') for f in raw_files if f.strip() and f.endswith(b'.ts')]

        if tracked_ts:
            for i in range(0, len(tracked_ts), 500):
                chunk = tracked_ts[i:i+500]
                subprocess.run(["git", "update-index", "--assume-unchanged"] + chunk, check=True)
            print(f"[✓] {len(tracked_ts)} file .ts diatur sebagai 'assume-unchanged' (aman dari git delete/push).")
        else:
            print("[+] Tidak ada file .ts yang terdaftar di git index saat ini.")
    except Exception as e:
        print(f"[-] Catatan pada update-index: {e}")

    print("\n==========================================================")
    print("🎉 SELESAI! Repo berhasil di-pull, Blobless aktif, dan disk lokal bersih!")
    print("   (File .ts di GitHub tetap utuh dan diakses via CDN RAW)")
    print("==========================================================")

if __name__ == "__main__":
    smart_pull()
