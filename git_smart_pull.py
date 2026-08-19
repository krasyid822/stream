#!/usr/bin/env python3
import os
import subprocess
import sys

def run_cmd(cmd, check=True):
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)

def smart_pull():
    print("==========================================================")
    print("📥 SMART GIT PULL, BLOBLESS FILTER & LOCAL PACK CLEANER")
    print("==========================================================")

    # 1. Konfigurasi Partial Clone / Blobless filter (blob:none) untuk origin
    print("[1/5] Memastikan konfigurasi Blobless Filter (blob:none) aktif...")
    try:
        subprocess.run(["git", "config", "remote.origin.promisor", "true"], check=True)
        subprocess.run(["git", "config", "remote.origin.partialclonefilter", "blob:none"], check=True)
        print("[✓] Konfigurasi blobless (remote.origin.partialclonefilter = blob:none) aktif.")
    except Exception as e:
        print(f"[-] Peringatan saat mengatur config blobless: {e}")

    # 2. Jalankan git fetch dengan filter blob:none lalu merge origin/main
    print("\n[2/5] Mengambil pembaruan terbaru dari GitHub tanpa mengunduh riwayat blob biner...")
    fetch_res = subprocess.run(["git", "fetch", "--filter=blob:none", "origin", "main"], text=True)
    if fetch_res.returncode != 0:
        print("[-] Gagal melakukan git fetch. Silakan periksa koneksi atau remote git.")
        sys.exit(fetch_res.returncode)

    merge_res = subprocess.run(["git", "merge", "origin/main"], text=True)
    if merge_res.returncode != 0:
        print("[-] Gagal melakukan git merge. Silakan periksa konflik git.")
        sys.exit(merge_res.returncode)
    print("[✓] Sinkronisasi Git blobless berhasil!")

    # 3. Cari semua file .ts lokal di dalam direktori proyek (kecuali .git)
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    print("\n[3/5] Memindai dan membersihkan file segmen .ts di working directory...")

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
        print("[*] Menghapus file .ts lokal untuk membebaskan ruang disk...")
        deleted_count = 0
        for fpath in ts_files:
            try:
                os.remove(fpath)
                deleted_count += 1
            except Exception as e:
                print(f"[-] Gagal menghapus {fpath}: {e}")

        print(f"[✓] Berhasil menghapus {deleted_count} file .ts di working tree.")

    # 4. Asumsikan file .ts tidak berubah di git (assume-unchanged) agar git tidak menganggapnya terhapus/modified
    print("\n[4/5] Memperbarui git index (assume-unchanged) agar file .ts di GitHub tetap aman...")
    try:
        ls_files_res = subprocess.run(["git", "ls-files", "*.ts"], capture_output=True, text=True, check=True)
        tracked_ts = [line.strip() for line in ls_files_res.stdout.splitlines() if line.strip().endswith(".ts")]

        if tracked_ts:
            for i in range(0, len(tracked_ts), 500):
                chunk = tracked_ts[i:i+500]
                subprocess.run(["git", "update-index", "--assume-unchanged"] + chunk, check=True)
            print(f"[✓] {len(tracked_ts)} file .ts diatur sebagai 'assume-unchanged' (aman dari git delete/push).")
        else:
            print("[+] Tidak ada file .ts yang terdaftar di git index saat ini.")
    except Exception as e:
        print(f"[-] Catatan pada update-index: {e}")

    # 5. Membersihkan riwayat blob/pack besar di .git/objects/pack (Garbage Collection & Repack Blobless)
    print("\n[5/5] Membersihkan database .git/objects/pack lokal (Prune & Repack Blobless)...")
    try:
        # Expire reflog untuk melepaskan referensi unreferenced commit
        subprocess.run(["git", "reflog", "expire", "--expire=now", "--all"], check=False)
        # Repack dengan filter blob:none agar packfile lokal melepaskan blob .ts lama
        subprocess.run(["git", "repack", "-a", "-d", "--filter=blob:none"], check=False)
        # Bersihkan objek tak terpakai
        subprocess.run(["git", "prune", "--expire=now"], check=False)
        subprocess.run(["git", "gc", "--prune=now"], check=False)
        print("[✓] Pembersihan objek internal .git berhasil dilakukan.")
    except Exception as e:
        print(f"[-] Peringatan pembersihan git objects: {e}")

    print("\n==========================================================")
    print("🎉 SELESAI! Repo berhasil di-pull, Blobless aktif, dan disk lokal bersih!")
    print("   (File .ts di GitHub tetap utuh dan diakses via CDN RAW)")
    print("==========================================================")

if __name__ == "__main__":
    smart_pull()
