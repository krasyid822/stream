#!/usr/bin/env python3
import os
import subprocess
import sys

def run_cmd(cmd, check=True):
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)

def smart_pull():
    print("==========================================================")
    print("📥 SMART GIT PULL & LOCAL .TS CACHE CLEANER")
    print("==========================================================")

    # 1. Jalankan git pull dari remote
    print("[1/3] Mengambil pembaruan terbaru dari GitHub (git pull)...")
    pull_res = subprocess.run(["git", "pull", "origin", "main"], text=True)
    if pull_res.returncode != 0:
        print("[-] Gagal melakukan git pull. Silakan periksa koneksi atau konflik git.")
        sys.exit(pull_res.returncode)
    print("[✓] Git pull berhasil!")

    # 2. Cari semua file .ts lokal di dalam direktori proyek (kecuali .git)
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    print("\n[2/3] Memindai dan membersihkan file segmen .ts di penyimpanan lokal...")

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
        print("[+] Tidak ada file .ts lokal yang perlu dibersihkan.")
    else:
        print(f"[+] Ditemukan {len(ts_files)} file .ts lokal ({total_bytes / (1024 * 1024):.2f} MB).")
        print("[*] Menghapus file .ts lokal untuk menghemat ruang disk...")
        deleted_count = 0
        for fpath in ts_files:
            try:
                os.remove(fpath)
                deleted_count += 1
            except Exception as e:
                print(f"[-] Gagal menghapus {fpath}: {e}")

        print(f"[✓] Berhasil menghapus {deleted_count} file .ts lokal.")

    # 3. Asumsikan file .ts tidak berubah di git (assume-unchanged) agar git tidak menganggapnya terhapus/modified
    print("\n[3/3] Memperbarui index git agar file .ts di remote GitHub tetap aman...")
    
    # Ambil daftar semua file .ts yang terdaftar di git tracking
    ls_files_res = subprocess.run(["git", "ls-files", "*.ts"], capture_output=True, text=True, check=True)
    tracked_ts = [line.strip() for line in ls_files_res.stdout.splitlines() if line.strip().endswith(".ts")]

    if tracked_ts:
        # Jalankan git update-index --assume-unchanged dalam batch (500 file per batch)
        for i in range(0, len(tracked_ts), 500):
            chunk = tracked_ts[i:i+500]
            subprocess.run(["git", "update-index", "--assume-unchanged"] + chunk, check=True)
        print(f"[✓] {len(tracked_ts)} file .ts diatur sebagai 'assume-unchanged' (aman dari git delete/push).")
    else:
        print("[+] Tidak ada file .ts yang terdaftar di git index saat ini.")

    print("\n==========================================================")
    print("🎉 SELESAI! Repo berhasil di-pull & disk lokal bersih dari .ts")
    print("   (File .ts di GitHub tetap utuh dan diakses via CDN RAW)")
    print("==========================================================")

if __name__ == "__main__":
    smart_pull()
