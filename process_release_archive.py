#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import glob

# Daftar tag release yang sudah diproses sebelumnya dan TIDAK boleh diproses lagi
IGNORED_RELEASE_TAGS = {"AM", "FULLDIVE-RPG", "GSYOS", "PASSWORD"}

ARCHIVE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".7z.001", ".zpaq", ".tar", ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".part01.rar", ".part1.rar",
    ".001"
)

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v")

def run_cmd(cmd, check=True, cwd=None):
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, cwd=cwd)

def fetch_passwords_from_password_tag():
    """Mengambil kunci/password enkripsi dari release tag 'password' via gh cli."""
    passwords = ["kurniawan"]  # Default known release encryption password
    try:
        res = subprocess.run(["gh", "release", "view", "password", "--json", "body"], capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            body = data.get("body", "")
            for line in body.splitlines():
                clean_l = line.strip().strip('"`')
                if clean_l and clean_l not in passwords:
                    passwords.append(clean_l)
            print(f"[+] Berhasil mengambil {len(passwords)} kunci dari tag release 'password'.")
        else:
            print(f"[-] Peringatan gh release view password: {res.stderr.strip()}")
    except Exception as e:
        print(f"[-] Peringatan saat mengambil release tag password: {e}")
    return passwords

def parse_release_body_lines(body_text):
    """
    Parse baris release body:
    1. Mencari pemetaan path berkas: misal 'anime/zero_tskma/1/Otakudesu_ZeroTskma_480p.7z'
    2. Mengumpulkan judul alternatif / aliases (Zero no Tsukaima, The Familiar of Zero, dll)
    """
    file_mapping = {}  # { 'Otakudesu_ZeroTskma_480p.7z': 'anime/zero_tskma/1' }
    aliases = []

    if not body_text:
        return file_mapping, aliases

    lines = [l.strip() for l in body_text.splitlines() if l.strip()]

    for line in lines:
        # Cek apakah baris berupa path folder + file: <kategori>/<judul>/[season]/<nama_file>
        m_path = re.search(r'^([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+(?:/[a-zA-Z0-9_\-]+)?)/([^/\s]+\.[a-zA-Z0-9_\.]+)$', line)
        if m_path:
            folder_part = m_path.group(1).strip('/')
            file_part = m_path.group(2).strip()
            file_mapping[file_part.lower()] = folder_part
            continue

        # Cek baris judul / alias (abaikan info resolusi, jumlah episode, atau markdown headers)
        if re.match(r'^(?:\d+p|\d+\s*episodes?|#+)', line, re.IGNORECASE):
            continue

        clean_title = re.sub(r'^[–\-\*•\s]+', '', line).strip()
        if clean_title and clean_title not in aliases:
            aliases.append(clean_title)

    return file_mapping, aliases

def extract_archive_single(archive_path, output_dir, passwords):
    """Ekstrak arsip tunggal atau split dengan mencoba daftar password."""
    os.makedirs(output_dir, exist_ok=True)
    lower_path = archive_path.lower()
    is_zpaq = lower_path.endswith('.zpaq')

    for pwd in passwords:
        success = False
        print(f"[*] Ekstrak '{os.path.basename(archive_path)}' (Password: {'(tanpa password)' if not pwd else '***'})...")
        
        if is_zpaq:
            cmd = ["zpaq", "x", archive_path, "-to", output_dir, "-force"]
            if pwd:
                cmd.extend(["-key", pwd])
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                success = True
        else:
            pwd_flag = f"-p{pwd}" if pwd else "-p"
            cmd = ["7z", "x", f"-o{output_dir}", "-y", pwd_flag, archive_path]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                success = True
            elif "Wrong password" not in res.stderr and "Wrong password" not in res.stdout:
                if lower_path.endswith('.rar'):
                    unrar_cmd = ["unrar", "x", "-y"]
                    if pwd:
                        unrar_cmd.append(f"-p{pwd}")
                    else:
                        unrar_cmd.append("-p-")
                    unrar_cmd.extend([archive_path, output_dir])
                    unrar_res = subprocess.run(unrar_cmd, capture_output=True, text=True)
                    if unrar_res.returncode == 0:
                        success = True

        if success:
            print(f"[✓] Berhasil mengekstrak {os.path.basename(archive_path)}!")
            return True

    print(f"[-] Gagal mengekstrak {os.path.basename(archive_path)}.")
    return False

def recursive_extract(archive_path, output_dir, passwords):
    """Ekstrak arsip utama dan periksa jika di dalamnya terdapat arsip bersarang (nested)."""
    temp_stage = os.path.join(output_dir, "_temp_" + os.path.splitext(os.path.basename(archive_path))[0])
    os.makedirs(temp_stage, exist_ok=True)

    if not extract_archive_single(archive_path, temp_stage, passwords):
        return []

    # Periksa apakah ada arsip bersarang di dalam hasil ekstrak
    depth = 0
    while depth < 3:
        depth += 1
        nested_archives = []
        for root, _, files in os.walk(temp_stage):
            for f in sorted(files):
                full_f = os.path.join(root, f)
                lf = f.lower()
                if re.search(r'\.part0*2\.rar$', lf) or re.search(r'\.00[2-9]$', lf) or re.search(r'\.0[1-9][0-9]$', lf):
                    continue
                if any(lf.endswith(ext) for ext in ARCHIVE_EXTENSIONS) or re.search(r'\.part0*1\.rar$', lf):
                    nested_archives.append(full_f)

        if not nested_archives:
            break

        print(f"[🔄] Ditemukan {len(nested_archives)} arsip bersarang. Mengekstrak kembali...")
        for n_arch in nested_archives:
            target_out = os.path.dirname(n_arch)
            if extract_archive_single(n_arch, target_out, passwords):
                try:
                    os.remove(n_arch)
                except Exception:
                    pass

    # Kumpulkan semua file video
    found_videos = []
    for root, _, files in os.walk(temp_stage):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                found_videos.append(os.path.join(root, f))

    return found_videos

def update_aliases_and_sources(workspace_root, folder_rel_path, aliases, first_archive_name):
    """Perbarui aliases.json dengan aliases dan info provider."""
    aliases_path = os.path.join(workspace_root, "aliases.json")
    data = {"aliases": {}, "sources": {}}
    if os.path.exists(aliases_path):
        try:
            with open(aliases_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[-] Gagal membaca aliases.json: {e}")

    if "aliases" not in data:
        data["aliases"] = {}
    if "sources" not in data:
        data["sources"] = {}

    folder_parts = folder_rel_path.split('/')
    leaf_id = folder_parts[1] if len(folder_parts) > 1 else folder_rel_path

    provider_name = ""
    if first_archive_name:
        p_match = re.match(r'^([a-zA-Z0-9]+)_', first_archive_name)
        if p_match:
            provider_name = p_match.group(1)

    existing_aliases = data["aliases"].get(leaf_id, [])
    
    if provider_name and provider_name not in aliases and provider_name not in existing_aliases:
        aliases.append(provider_name)

    for a in aliases:
        if a not in existing_aliases:
            existing_aliases.append(a)

    if existing_aliases:
        data["aliases"][leaf_id] = existing_aliases

    if folder_rel_path not in data["sources"] and provider_name:
        data["sources"][folder_rel_path] = {
            "provider": f"{provider_name}.id" if not provider_name.lower().endswith(('.id', '.com', '.org')) else provider_name,
            "url": f"https://{provider_name.lower()}.id",
            "icon": "",
            "note": f"Sumber mentah dari Release {first_archive_name}"
        }

    with open(aliases_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[✓] aliases.json diperbarui untuk '{folder_rel_path}'.")

def main():
    if len(sys.argv) < 4:
        print("Penggunaan: process_release_archive.py <download_dir> <release_tag> <release_body_file>")
        sys.exit(1)

    download_dir = sys.argv[1]
    release_tag = sys.argv[2]
    release_body_file = sys.argv[3]

    print("==========================================================")
    print(f"📦 PIPELINE RELEASE: {release_tag}")
    print("==========================================================")

    # 1. Cek apakah release tag termasuk dalam daftar yang diabaikan (sudah diproses sebelumnya)
    clean_tag_upper = release_tag.strip().upper()
    if clean_tag_upper in IGNORED_RELEASE_TAGS:
        print(f"[!] Tag release '{release_tag}' sudah pernah diproses sebelumnya atau merupakan tag password. Melewati.")
        sys.exit(0)

    workspace_root = os.path.abspath(os.path.dirname(__file__))

    body_text = ""
    if os.path.exists(release_body_file):
        with open(release_body_file, "r", encoding="utf-8") as f:
            body_text = f.read()

    # 2. Ambil Kunci Password dari Tag 'password' & Release Body
    passwords = fetch_passwords_from_password_tag()
    for line in body_text.splitlines():
        cl = line.strip().strip('"`')
        if cl and cl not in passwords and not cl.startswith('#') and not cl.startswith('http') and '/' not in cl:
            passwords.append(cl)
    if "" not in passwords:
        passwords.append("")

    # 3. Parse Pemetaan File & Aliases dari Release Body
    file_mapping, aliases = parse_release_body_lines(body_text)
    print(f"[+] Pemetaan folder terdeteksi: {json.dumps(file_mapping, indent=2)}")
    print(f"[+] Daftar aliases judul terdeteksi: {aliases}")

    # 4. Kumpulkan Arsip yang Diunduh
    downloaded_files = glob.glob(os.path.join(download_dir, "*"))
    if not downloaded_files:
        print("[-] Tidak ada aset arsip yang ditemukan di folder download!")
        sys.exit(0)

    # Filter primary archive (abaikan part split kedua dst)
    primary_archives = []
    for f in sorted(downloaded_files):
        lf = os.path.basename(f).lower()
        if re.search(r'\.part0*2\.rar$', lf) or re.search(r'\.00[2-9]$', lf) or re.search(r'\.0[1-9][0-9]$', lf):
            continue
        if any(lf.endswith(ext) for ext in ARCHIVE_EXTENSIONS) or re.search(r'\.part0*1\.rar$', lf):
            primary_archives.append(f)

    print(f"[+] Ditemukan {len(primary_archives)} file arsip utama untuk diproses.")

    all_processed_videos = []

    # 5. Ekstrak Setiap Arsip ke Folder RAW/<kategori>/<judul>/[season] yang Sesuai
    for arch in primary_archives:
        arch_name = os.path.basename(arch)
        arch_lower = arch_name.lower()
        
        # Tentukan folder tujuan dari pemetaan release body
        dest_folder = file_mapping.get(arch_lower)
        if not dest_folder:
            # Fallback jika tidak tertulis spesifik di mapping: anime/<tag_slug>/1
            tag_slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', release_tag).lower()
            dest_folder = f"anime/{tag_slug}"

        raw_dest_dir = os.path.join(workspace_root, "RAW", dest_folder)
        os.makedirs(raw_dest_dir, exist_ok=True)

        print(f"\n==================================================")
        print(f"📂 Memproses: {arch_name} -> RAW/{dest_folder}")
        print(f"==================================================")

        temp_extract_dir = os.path.join(workspace_root, "RAW_TEMP")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)

        extracted_videos = recursive_extract(arch, temp_extract_dir, passwords)
        
        for v_path in extracted_videos:
            v_name = os.path.basename(v_path)
            v_final = os.path.join(raw_dest_dir, v_name)
            shutil.move(v_path, v_final)
            all_processed_videos.append(v_final)
            print(f" -> Video dipindahkan: RAW/{dest_folder}/{v_name}")

        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        update_aliases_and_sources(workspace_root, dest_folder, aliases, arch_name)

    if not all_processed_videos:
        print("[-] Tidak ditemukan video yang berhasil diekstrak dari arsip.")
        sys.exit(1)

    # 6. Jalankan generate_hls.py
    print("\n==========================================================")
    print("🎬 MENJALANKAN GENERATOR HLS ABR & SUBTITLE PIPELINE")
    print("==========================================================")
    generate_script = os.path.join(workspace_root, "generate_hls.py")
    cmd_gen = [sys.executable, generate_script] + all_processed_videos
    run_cmd(cmd_gen, check=True)

    print("\n==========================================================")
    print("🎉 SEMUA VIDEO BERHASIL DIPROSES KE HLS!")
    print("==========================================================")

if __name__ == "__main__":
    main()
