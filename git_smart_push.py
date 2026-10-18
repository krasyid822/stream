#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import tempfile

# Batas maksimum ukuran staging per batch push (500 MB agar sangat aman dari batas 2.0 GB GitHub)
MAX_BATCH_BYTES = 500 * 1024 * 1024 

def run_cmd(cmd, check=True):
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)

def run_cmd_push(cmd):
    print(f"[CMD] {' '.join(cmd)}")
    # Mengalirkan stdout & stderr secara real-time ke terminal agar progress % upload kelihatan
    return subprocess.run(cmd, check=False)

IGNORED_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".zpaq", ".tar", ".gz", ".bz2", ".xz", ".tgz",
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".pyc"
)
IGNORED_PATH_PREFIXES = ("release_downloads", "RAW_TEMP", "RAW_TEMP_EXTRACT", ".git", "__pycache__")

def is_ignored_file(path):
    norm = path.replace("\\", "/").lstrip("./")
    if any(norm.startswith(p) or f"/{p}/" in f"/{norm}/" for p in IGNORED_PATH_PREFIXES):
        return True
    if any(norm.lower().endswith(ext) or f"{ext}." in norm.lower() for ext in IGNORED_EXTENSIONS):
        return True
    if "release_body.txt" in norm:
        return True
    return False

def get_untracked_and_modified_files():
    """Mengambil daftar semua file yang belum dikomit (untracked & modified), termasuk ekspansi folder."""
    # WAJIB pakai -z (NUL-separated & tanpa quoting). Tanpa -z, git meng-escape path yang
    # mengandung karakter khusus menjadi oktal (mis. 'série_animée' -> 's\303\251rie_anim\303\251e')
    # sehingga os.path.exists() selalu False dan SELURUH media ikut ter-skip diam-diam.
    res = subprocess.run(["git", "status", "--porcelain", "-uall", "-z"], check=True, text=True, capture_output=True)
    entries = res.stdout.split("\0")
    files = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        # Pada mode -z, entri rename/copy diikuti 1 field lagi berisi path LAMA
        if entry[0] in ("R", "C"):
            i += 1
        filepath = entry[3:]
        if filepath and os.path.exists(filepath) and not is_ignored_file(filepath):
            if os.path.isdir(filepath):
                for root, _, filenames in os.walk(filepath):
                    for fname in filenames:
                        full_f = os.path.join(root, fname)
                        if not is_ignored_file(full_f):
                            files.append(full_f)
            else:
                files.append(filepath)
    return files

def get_file_size(filepath):
    try:
        return os.path.getsize(filepath)
    except Exception:
        return 0

# File yang boleh "kalah" kalau benar-benar bentrok: ini di-generate otomatis oleh
# pipeline, jadi isinya akan dibangun ulang sendiri pada run berikutnya. Untuk file
# di luar daftar ini, konflik yang tidak bisa digabung akan membatalkan rebase.
CONFLICT_TAKE_LOCAL = {"metadata.json"}


def in_rebase():
    """True kalau ada rebase yang belum selesai (bikin repo 'macet')."""
    return os.path.isdir(os.path.join(".git", "rebase-merge")) or \
           os.path.isdir(os.path.join(".git", "rebase-apply"))


def conflicted_files():
    """Daftar file yang masih berstatus konflik (unmerged)."""
    res = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"],
                         text=True, capture_output=True)
    return [l for l in res.stdout.splitlines() if l.strip()]


def _stage_blob(stage, path):
    """Ambil isi file pada indeks rebase: 1=base, 2=upstream(remote), 3=commit lokal."""
    res = subprocess.run(["git", "show", f":{stage}:{path}"], capture_output=True)
    return res.stdout if res.returncode == 0 else None


def three_way_merge(path, prefer_local=False):
    """
    Gabungkan versi lokal + versi remote memakai basis yang sama (three-way merge).

    Dipakai karena `git pull --rebase` KADANG hanya menyisakan konflik di satu-dua
    baris (mis. 'updated_at') sementara seluruh perubahan lain bisa dipertahankan.

    PENTING: kalau konflik, kita TIDAK mengambil seluruh file versi lokal (itu akan
    membuang entri yang ditambahkan remote). Yang dipakai hanya baris yang benar-benar
    bentrok, lewat `git merge-file --ours`. Jadi tambahan dari remote tetap utuh.

    Return:
      "clean"  - berhasil digabung tanpa bentrok
      "forced" - ada bentrok, baris itu memakai versi lokal (hanya bila prefer_local)
      None     - tidak bisa / tidak boleh diselesaikan otomatis
    """
    base = _stage_blob(1, path)
    remote = _stage_blob(2, path)   # saat rebase, "ours" = upstream/remote
    local = _stage_blob(3, path)    # "theirs" = commit lokal yang sedang diterapkan
    if base is None or remote is None or local is None:
        return None

    with tempfile.TemporaryDirectory() as td:
        f_local = os.path.join(td, "local")
        f_base = os.path.join(td, "base")
        f_remote = os.path.join(td, "remote")
        for fp, data in ((f_local, local), (f_base, base), (f_remote, remote)):
            with open(fp, "wb") as fh:
                fh.write(data)

        # Tanpa --ours: exit!=0 berarti benar-benar ada baris yang bentrok.
        probe = subprocess.run(["git", "merge-file", "-p", f_local, f_base, f_remote],
                               capture_output=True)
        if probe.returncode == 0:
            merged, status = probe.stdout, "clean"
        elif prefer_local:
            merged = subprocess.run(["git", "merge-file", "-p", "--ours",
                                     f_local, f_base, f_remote],
                                    capture_output=True).stdout
            status = "forced"
        else:
            return None

    if not merged:
        return None
    with open(path, "wb") as fh:
        fh.write(merged)
    return status


def resolve_rebase_conflicts(max_rounds=25):
    """
    Selesaikan rebase yang tersangkut konflik.

    Urutan penanganan per file konflik:
      1. Coba three-way merge  -> perubahan lokal DAN remote sama-sama dipertahankan.
      2. Kalau tetap bentrok dan file-nya ada di CONFLICT_TAKE_LOCAL -> pakai versi lokal.
      3. Selain itu -> menyerah (return False) supaya pemanggil membatalkan rebase,
         bukan menimpa file penting tanpa sepengetahuan pengguna.
    """
    rounds = 0
    while in_rebase() and rounds < max_rounds:
        rounds += 1
        conflicts = conflicted_files()

        if conflicts:
            gagal = []
            for path in conflicts:
                boleh_paksa = os.path.basename(path) in CONFLICT_TAKE_LOCAL
                status = three_way_merge(path, prefer_local=boleh_paksa)
                if status is None:
                    gagal.append(path)
                    continue
                if status == "forced":
                    print(f"[⚠️] '{path}' bentrok di sebagian baris; baris itu memakai versi "
                          f"LOKAL, sisanya (termasuk tambahan dari remote) tetap dipertahankan.")
                else:
                    print(f"[✓] '{path}' digabung 3-arah (perubahan lokal + remote utuh).")
                subprocess.run(["git", "add", "--", path], check=False)

            if gagal:
                print(f"[!] Konflik tidak bisa diselesaikan otomatis: {', '.join(gagal)}")
                return False

        env = dict(os.environ, GIT_EDITOR="true", GIT_SEQUENCE_EDITOR="true")
        res = subprocess.run(["git", "rebase", "--continue"], text=True, env=env)
        if res.returncode != 0 and not in_rebase():
            return True  # rebase selesai (git keluar dengan "tak ada rebase yang jalan")
    return not in_rebase()


def safe_push(batch_label):
    print(f"[🚀] Mendorong {batch_label} ke GitHub remote...")
    push_res = run_cmd_push(["git", "push", "origin", "main"])
    if push_res.returncode != 0:
        print("[!] Push ditolak remote (ada komit baru). Menjalankan rebase otomatis dengan autostash...")
        pull_res = subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], text=True)
        if pull_res.returncode != 0:
            print("[!] Rebase otomatis terhenti (kemungkinan konflik).")
            if resolve_rebase_conflicts():
                print("[✓] Rebase berhasil diselesaikan. Mencoba push ulang...")
            else:
                # PENTING: jangan tinggalkan repo dalam keadaan rebase setengah jalan,
                # karena semua git add/commit berikutnya akan gagal dan repo rusak.
                print("[-] Konflik TIDAK dapat diselesaikan otomatis.")
                print("    → Membatalkan rebase supaya repo tidak tertinggal rusak.")
                subprocess.run(["git", "rebase", "--abort"], check=False)
                print("    → Perubahan lokal Anda tetap tersimpan di commit terakhir.")
                print("    → Selesaikan manual: git status  lalu  git rebase --continue")
                sys.exit(1)
        push_res = run_cmd_push(["git", "push", "origin", "main"])

    if push_res.returncode != 0:
        print(f"[-] Gagal melakukan push pada {batch_label}.")
        sys.exit(1)
    print(f"[✓] {batch_label} BERHASIL terunggah!")

def chunk_push():
    print("==========================================================")
    print("🚀 SMART AUTO GIT CHUNK PUSHER (Max ~1.2GB per Push Batch)")
    print("==========================================================")

    # 0. Jaring pengaman: kalau ada rebase yang menggantung dari run sebelumnya,
    #    bereskan dulu. Kalau tidak, semua git add/commit di bawah akan gagal.
    if in_rebase():
        print("[!] Terdeteksi rebase yang belum selesai dari eksekusi sebelumnya.")
        if resolve_rebase_conflicts():
            print("[✓] Rebase sebelumnya berhasil diselesaikan.")
        else:
            subprocess.run(["git", "rebase", "--abort"], check=False)
            print("[-] Rebase dibatalkan. Periksa 'git status' sebelum push lagi.")
            sys.exit(1)

    # 1. Bersihkan file kunci index.lock dan folder cache __pycache__
    lock_file = os.path.join(".git", "index.lock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
            print("[*] Membersihkan file kunci .git/index.lock yang kadaluarsa...")
        except Exception:
            pass

    # Hapus folder __pycache__ dan file .pyc
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

    # 2. Terapkan konfigurasi speed-up jaringan & multi-thread Git
    try:
        subprocess.run(["git", "config", "core.sparseCheckout", "false"], check=False)
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
                run_cmd(["git", "add", "--sparse"] + chunk)

            commit_msg = f"feat(media): batch upload part #{batch_count} ({len(current_batch)} files)"
            run_cmd(["git", "commit", "-m", commit_msg])

            safe_push(f"Batch #{batch_count}")
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
            run_cmd(["git", "add", "--sparse"] + chunk)

        commit_msg = f"feat(media): batch upload part #{batch_count} ({len(current_batch)} files)"
        run_cmd(["git", "commit", "-m", commit_msg])

        safe_push(f"Batch Terakhir #{batch_count}")

    print("\n==========================================================")
    print("🎉 SEMUA MEDIA BERHASIL DI-PUSH SEPENUHNYA KE GITHUB!")
    print("==========================================================")

if __name__ == "__main__":
    chunk_push()
