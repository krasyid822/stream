#!/usr/bin/env python3
import os
import subprocess
import sys

# Batas maksimum ukuran staging per batch push (500 MB agar sangat aman dari batas 2.0 GB GitHub)
MAX_BATCH_BYTES = 500 * 1024 * 1024 

def run_cmd(cmd, check=True):
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)

def run_cmd_push(cmd):
    print(f"[CMD] {' '.join(cmd)}")
    # Mengalirkan stdout & stderr secara real-time ke terminal agar progress % upload kelihatan
    return subprocess.run(cmd, check=False)

def get_untracked_and_modified_files():
    """Mengambil daftar semua file yang belum dikomit (untracked & modified), termasuk ekspansi folder."""
    res = subprocess.run(["git", "status", "--porcelain", "-uall"], check=True, text=True, capture_output=True)
    files = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        filepath = line[3:].strip().strip('"')
        if filepath and os.path.exists(filepath):
            if os.path.isdir(filepath):
                for root, _, filenames in os.walk(filepath):
                    for fname in filenames:
                        files.append(os.path.join(root, fname))
            else:
                files.append(filepath)
    return files

def get_file_size(filepath):
    try:
        return os.path.getsize(filepath)
    except Exception:
        return 0

def chunk_push():
    print("==========================================================")
    print("🚀 SMART AUTO GIT CHUNK PUSHER (Max ~1.2GB per Push Batch)")
    print("==========================================================")

    # 1. Bersihkan file kunci index.lock yang kadaluarsa jika ada
    lock_file = os.path.join(".git", "index.lock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print("[*] Membersihkan file kunci .git/index.lock yang kadaluarsa...")
        except Exception:
            pass

    # 2. Terapkan konfigurasi speed-up jaringan & multi-thread Git
    try:
        subprocess.run(["git", "config", "core.preloadindex", "true"], check=False)
        subprocess.run(["git", "config", "core.fscache", "true"], check=False)
        subprocess.run(["git", "config", "fetch.parallel", "8"], check=False)
        subprocess.run(["git", "config", "submodule.fetchJobs", "8"], check=False)
        subprocess.run(["git", "config", "http.postBuffer", "1048576000"], check=False)
        subprocess.run(["git", "config", "pack.windowMemory", "1024m"], check=False)
        subprocess.run(["git", "config", "pack.packSizeLimit", "1024m"], check=False)
        subprocess.run(["git", "config", "pack.threads", "0"], check=False)
    except Exception:
        pass

    # 3. Pastikan .gitignore di-commit terlebih dahulu jika ada perubahan
    if os.path.exists(".gitignore"):
        run_cmd(["git", "add", ".gitignore"], check=False)
        run_cmd(["git", "commit", "-m", "chore: update .gitignore rules"], check=False)

    files = get_untracked_and_modified_files()
    if not files:
        print("[+] Tidak ada file baru atau perubahan yang perlu di-push. Pohon kerja bersih!")
        return

    print(f"[+] Ditemukan total {len(files)} file perubahan/media baru.")

    current_batch = []
    current_size = 0
    batch_count = 1

    for filepath in files:
        fsize = get_file_size(filepath)
        
        # Jika menambahkan file ini akan melebihi 1.2GB, lakukan commit & push batch yang ada dulu
        if current_size + fsize > MAX_BATCH_BYTES and current_batch:
            print(f"\n[📦] --- Memproses Batch #{batch_count} ({len(current_batch)} file, {current_size / (1024*1024):.2f} MB) ---")
            
            # Git Add dalam chunk untuk menghindari 'Argument list too long'
            for i in range(0, len(current_batch), 500):
                chunk = current_batch[i:i+500]
                run_cmd(["git", "add"] + chunk)

            commit_msg = f"feat(media): batch upload part #{batch_count} ({len(current_batch)} files)"
            run_cmd(["git", "commit", "-m", commit_msg])

            print(f"[🚀] Mendorong Batch #{batch_count} ke GitHub remote...")
            push_res = run_cmd_push(["git", "push", "origin", "main"])
            if push_res.returncode != 0:
                print(f"[-] Gagal melakukan push pada batch #{batch_count}.")
                sys.exit(1)

            print(f"[✓] Batch #{batch_count} BERHASIL terunggah!")
            batch_count += 1
            current_batch = []
            current_size = 0

        current_batch.append(filepath)
        current_size += fsize

    # Push sisa file batch terakhir
    if current_batch:
        print(f"\n[📦] --- Memproses Batch Terakhir #{batch_count} ({len(current_batch)} file, {current_size / (1024*1024):.2f} MB) ---")
        for i in range(0, len(current_batch), 500):
            chunk = current_batch[i:i+500]
            run_cmd(["git", "add"] + chunk)

        commit_msg = f"feat(media): batch upload part #{batch_count} ({len(current_batch)} files)"
        run_cmd(["git", "commit", "-m", commit_msg])

        print(f"[🚀] Mendorong Batch #{batch_count} ke GitHub remote...")
        push_res = run_cmd_push(["git", "push", "origin", "main"])
        if push_res.returncode != 0:
            print(f"[-] Gagal melakukan push pada batch #{batch_count}.")
            sys.exit(1)

        print(f"[✓] Batch #{batch_count} BERHASIL terunggah!")

    print("\n==========================================================")
    print("🎉 SEMUA MEDIA BERHASIL DI-PUSH SEPENUHNYA KE GITHUB!")
    print("==========================================================")

if __name__ == "__main__":
    chunk_push()
