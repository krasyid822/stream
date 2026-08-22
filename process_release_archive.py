#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import glob

# Pastikan semua output print langsung tampil real-time di GitHub Actions
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(line_buffering=True)

# Tag release yang diabaikan (tag 'password' menyimpan kunci enkripsi, bukan arsip video)
IGNORED_RELEASE_TAGS = {"PASSWORD"}

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
    Parse baris release body format multi-blok:
    Blok 1: Header info (resolusi, episode)
    Blok 2 (antara ===): Aliases judul
    Blok 3: Pemetaan path folder berkas arsip (misal: anime/gsyos/2/Doronime.id.GSYOS.Part.2.480p.h265.zpaq)
    Blok 4 (setelah === ketiga): Info sumber (Baris 1: Website URL, Baris 2: Icon/Logo URL)
    """
    file_mapping = {}  # { 'doronime.id.gsyos.part.2.480p.h265.zpaq': 'anime/gsyos/2' }
    aliases = []
    source_info = {}   # { 'url': '...', 'icon': '...', 'provider': '...' }

    if not body_text:
        return file_mapping, aliases, source_info

    # Pisahkan blok berdasarkan separator baris '===='
    blocks = re.split(r'\r?\n\s*={3,}\s*\r?\n', body_text.strip())

    # 1. Parse Aliases (biasanya ada di blok index 1 jika format standar)
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        for line in lines:
            # Cek path folder arsip
            m_path = re.search(r'^([^\s/]+/[^\s/]+(?:/[^\s/]+)?)/([^\s/]+\.[a-zA-Z0-9_\.]+)$', line)
            if m_path:
                folder_part = m_path.group(1).strip('/')
                file_part = m_path.group(2).strip()
                file_mapping[file_part.lower()] = folder_part
                continue

            # Cek URL website / logo
            if line.startswith('http://') or line.startswith('https://'):
                if 'url' not in source_info:
                    source_info['url'] = line
                    # Ekstrak nama domain sebagai nama provider (misal: doronime.id)
                    domain_match = re.search(r'https?://(?:www\.)?([^/]+)', line)
                    if domain_match:
                        source_info['provider'] = domain_match.group(1)
                elif 'icon' not in source_info:
                    source_info['icon'] = line
                continue

            # Lewati baris resolusi / episode
            if re.match(r'^(?:\d+p|\d+\s*episodes?|part\s*\d+|#+)', line, re.IGNORECASE):
                continue

            clean_title = re.sub(r'^[–\-\*•\s]+', '', line).strip()
            if clean_title and clean_title not in aliases:
                aliases.append(clean_title)

    return file_mapping, aliases, source_info

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
            res = subprocess.run(cmd)
            if res.returncode == 0:
                success = True
        else:
            pwd_flag = f"-p{pwd}" if pwd else "-p"
            cmd = ["7z", "x", f"-o{output_dir}", "-y", pwd_flag, archive_path]
            res = subprocess.run(cmd)
            if res.returncode == 0:
                success = True
            elif lower_path.endswith('.rar'):
                unrar_cmd = ["unrar", "x", "-y"]
                if pwd:
                    unrar_cmd.append(f"-p{pwd}")
                else:
                    unrar_cmd.append("-p-")
                unrar_cmd.extend([archive_path, output_dir])
                unrar_res = subprocess.run(unrar_cmd)
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

def update_aliases_and_sources(workspace_root, folder_rel_path, aliases, first_archive_name, source_info=None):
    """Memperbarui aliases.json dengan aliases dan info sumber otentik (Website URL + Icon/Logo)."""
    aliases_path = os.path.join(workspace_root, "aliases.json")
    data = {"aliases": {}, "folder_info": {}, "sources": {}}
    
    if os.path.exists(aliases_path):
        try:
            with open(aliases_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    if "aliases" not in data:
        data["aliases"] = {}
    if "sources" not in data:
        data["sources"] = {}

    path_parts = folder_rel_path.strip("/").split("/")
    leaf_id = path_parts[1] if len(path_parts) > 1 else path_parts[0]

    provider_name = ""
    if source_info and source_info.get("provider"):
        provider_name = source_info["provider"]
    elif first_archive_name:
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

    # Simpan info source (URL, Icon, Provider)
    source_entry = {
        "provider": (source_info.get("provider") if source_info else None) or (f"{provider_name}.id" if provider_name and not provider_name.lower().endswith(('.id', '.com', '.org')) else provider_name) or "RAW Source",
        "url": (source_info.get("url") if source_info else None) or (f"https://{provider_name.lower()}" if provider_name else ""),
        "icon": (source_info.get("icon") if source_info else None) or "",
        "note": f"Sumber mentah dari Release {first_archive_name}"
    }

    data["sources"][folder_rel_path] = source_entry
    # Simpan juga pada root folder judul (misal: anime/gsyos) agar semua season mewarisi logo/sumbernya
    title_folder = "/".join(path_parts[:2])
    data["sources"][title_folder] = source_entry

    with open(aliases_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[✓] aliases.json diperbarui untuk '{folder_rel_path}' dengan sumber & icon logo!")

def check_if_already_processed_in_drive(dest_folder):
    """Mengecek apakah folder tujuan sudah memiliki master.m3u8 di repositori stream_drive."""
    try:
        url = f"https://api.github.com/repos/krasyid822/stream_drive/contents/{dest_folder}"
        res = subprocess.run(["gh", "api", f"repos/krasyid822/stream_drive/contents/{dest_folder}"], capture_output=True, text=True)
        if res.returncode == 0:
            items = json.loads(res.stdout)
            # Jika ada subfolder _hls yang berisi master.m3u8, artinya sudah diproses
            if any(item.get("name", "").endswith("_hls") for item in items):
                return True
    except Exception:
        pass
    return False

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

    # 3. Parse Pemetaan File, Aliases, dan Info Sumber dari Release Body
    file_mapping, aliases, source_info = parse_release_body_lines(body_text)
    print(f"[+] Pemetaan folder terdeteksi: {json.dumps(file_mapping, indent=2)}")
    print(f"[+] Daftar aliases judul terdeteksi: {aliases}")
    if source_info:
        print(f"[+] Info sumber penyedia terdeteksi: {json.dumps(source_info, indent=2)}")

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

        # Perbarui metadata aliases dan sources terlebih dahulu
        update_aliases_and_sources(workspace_root, dest_folder, aliases, arch_name, source_info)

        # Cek apakah folder konten media ini sudah pernah diproses dan ada di stream_drive
        if check_if_already_processed_in_drive(dest_folder):
            print(f"\n[⚡] SKIP: Konten '{dest_folder}' ({arch_name}) SUDAH SELESAI diproses sebelumnya di stream_drive. Melewati proses ekstraksi & transcode FFmpeg.")
            continue

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

    if not all_processed_videos:
        print("\n[✓] Semua video dalam rilis ini sudah selesai diproses dan di-host di stream_drive sebelumnya.")
        print("[+] Metadata dan aliases.json telah diperbarui tanpa perlu transcode ulang.")
    else:
        # 6. Jalankan generate_hls.py
        print("\n==========================================================")
        print("🎬 MENJALANKAN GENERATOR HLS ABR & SUBTITLE PIPELINE")
        print("==========================================================")
        generate_script = os.path.join(workspace_root, "generate_hls.py")
        cmd_gen = [sys.executable, generate_script] + all_processed_videos
        run_cmd(cmd_gen, check=True)

    # 7. Bersihkan file arsip download & sementara agar tidak ter-push ke Git
    print("\n[🧹] Membersihkan file arsip dan berkas unduhan sementara...")
    shutil.rmtree(download_dir, ignore_errors=True)
    if os.path.exists(release_body_file):
        try:
            os.remove(release_body_file)
        except Exception:
            pass

    print("\n==========================================================")
    print("🎉 SEMUA VIDEO BERHASIL DIPROSES KE HLS!")
    print("==========================================================")

if __name__ == "__main__":
    main()
