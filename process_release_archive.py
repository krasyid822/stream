#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import glob
import time

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

    # Periksa apakah ada arsip bersarang (nested) di dalam hasil ekstrak (hingga 5 lapis bersarang)
    depth = 0
    while depth < 5:
        depth += 1
        found_nested_work = False
        
        for root, _, files in os.walk(temp_stage):
            if not files:
                continue
            # Kelompokkan arsip bersarang (termasuk jika arsip di dalam adalah split archive lagi)
            nested_groups = group_release_archives(files)
            valid_nested_groups = [
                g for g in nested_groups 
                if any(g["base_name"].lower().endswith(ext) for ext in ARCHIVE_EXTENSIONS) or g["is_split"]
            ]

            if valid_nested_groups:
                found_nested_work = True
                print(f"[🔄] Layer #{depth}: Ditemukan {len(valid_nested_groups)} grup arsip bersarang di '{os.path.basename(root)}'. Mengekstrak & mendekripsi...")
                for ng in valid_nested_groups:
                    primary_nested = os.path.join(root, ng["primary_file"])
                    if os.path.exists(primary_nested):
                        if extract_archive_single(primary_nested, root, passwords):
                            # Hapus semua part arsip bersarang yang sudah selesai diekstrak
                            for p_file in ng["all_parts"]:
                                p_path = os.path.join(root, p_file)
                                try:
                                    if os.path.exists(p_path):
                                        os.remove(p_path)
                                except Exception:
                                    pass

        if not found_nested_work:
            break

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
        "note": f"Sumber mentah dari Release {first_archive_name or ''}"
    }

    # Cari apakah sudah ada key folder yang mirip di sources (normalisasi '-' vs '_')
    norm_dest = re.sub(r'[^a-zA-Z0-9]', '', folder_rel_path).lower()
    actual_source_key = folder_rel_path
    for existing_k in list(data["sources"].keys()):
        if re.sub(r'[^a-zA-Z0-9]', '', existing_k).lower() == norm_dest:
            actual_source_key = existing_k
            break

    data["sources"][actual_source_key] = source_entry
    # Simpan juga pada root folder judul (misal: anime/gsyos) agar semua season mewarisi logo/sumbernya
    title_folder = "/".join(path_parts[:2])
    data["sources"][title_folder] = source_entry

    with open(aliases_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[✓] aliases.json diperbarui untuk '{actual_source_key}' dengan sumber & icon logo!")

def sync_metadata_from_stream_drive(workspace_root):
    """Memperbarui metadata.json secara akurat berdasarkan pohon file sebenarnya di repositori stream_drive."""
    try:
        res = subprocess.run(["gh", "api", "repos/krasyid822/stream_drive/git/trees/main?recursive=1"], capture_output=True, text=True)
        if res.returncode != 0:
            return
        tree_data = json.loads(res.stdout)
        
        vtt_by_folder = {}
        for item in tree_data.get("tree", []):
            p = item["path"]
            if p.endswith(".vtt"):
                folder_hls = "/".join(p.split("/")[:-1])
                if folder_hls not in vtt_by_folder:
                    vtt_by_folder[folder_hls] = []
                vtt_by_folder[folder_hls].append(p)

        categories = set()
        media_items = []

        for item in tree_data.get("tree", []):
            path = item["path"]
            if path.endswith("master.m3u8"):
                parts = path.split("/")
                if len(parts) >= 3:
                    cat = parts[0]
                    categories.add(cat)
                    hls_folder = parts[-2]
                    parent_folder = "/".join(parts[:-2])
                    hls_path = "/".join(parts[:-1])
                    
                    ep_name = hls_folder
                    if ep_name.endswith("_hls"):
                        ep_name = ep_name[:-4]
                    
                    media_id = path.replace("/", "_").replace(" ", "_").replace(".", "_")
                    poster_path = hls_path + "/poster.jpg"
                    subtitles = vtt_by_folder.get(hls_path, [])
                    
                    media_items.append({
                        "id": media_id,
                        "name": ep_name,
                        "folder": parent_folder,
                        "path": hls_path,
                        "master_url": path,
                        "poster_url": poster_path,
                        "subtitles": subtitles,
                        "type": "hls_stream"
                    })

        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s["name"])]

        media_items.sort(key=lambda x: (x["folder"], natural_sort_key(x)))

        metadata_path = os.path.join(workspace_root, "metadata.json")
        output_metadata = {
            "categories": sorted(list(categories)),
            "media": media_items,
            "updated_at": str(time.time() if 'time' in globals() else 0)
        }

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(output_metadata, f, indent=2, ensure_ascii=False)
        print(f"[✓] metadata.json disinkronkan dengan {len(media_items)} media dari stream_drive!")
    except Exception as e:
        print(f"[-] Catatan sinkronisasi metadata: {e}")

def check_if_already_processed_in_drive(dest_folder):
    """Mengecek apakah folder tujuan sudah memiliki master.m3u8 di repositori stream_drive (fleksibel terhadap '-' vs '_')."""
    try:
        clean_f = dest_folder.strip("/")
        parts = clean_f.split("/")
        category = parts[0]
        target_title = parts[1] if len(parts) > 1 else ""

        # Normalisasi slug untuk pencocokan toleran ('fulldive_rpg' == 'fulldive-rpg')
        norm_target = re.sub(r'[^a-zA-Z0-9]', '', target_title).lower()

        # 1. Cek langsung path tepat
        res = subprocess.run(["gh", "api", f"repos/krasyid822/stream_drive/contents/{clean_f}"], capture_output=True, text=True)
        if res.returncode == 0:
            items = json.loads(res.stdout)
            if isinstance(items, list):
                if any(item.get("name", "").endswith("_hls") for item in items):
                    return True
                for item in items:
                    if item.get("type") == "dir":
                        sub_res = subprocess.run(["gh", "api", f"repos/krasyid822/stream_drive/contents/{clean_f}/{item['name']}"], capture_output=True, text=True)
                        if sub_res.returncode == 0:
                            sub_items = json.loads(sub_res.stdout)
                            if isinstance(sub_items, list) and any(si.get("name", "").endswith("_hls") for si in sub_items):
                                return True

        # 2. Jika tidak ditemukan secara direct, cek seluruh subfolder di dalam kategori (misal: anime/)
        res_cat = subprocess.run(["gh", "api", f"repos/krasyid822/stream_drive/contents/{category}"], capture_output=True, text=True)
        if res_cat.returncode == 0:
            cat_items = json.loads(res_cat.stdout)
            if isinstance(cat_items, list):
                for c_item in cat_items:
                    folder_name = c_item.get("name", "")
                    norm_folder = re.sub(r'[^a-zA-Z0-9]', '', folder_name).lower()
                    if norm_target and norm_target == norm_folder:
                        # Ditemukan kecocokan nama folder (misal: fulldive-rpg vs fulldive_rpg)
                        matched_path = f"{category}/{folder_name}"
                        sub_res = subprocess.run(["gh", "api", f"repos/krasyid822/stream_drive/contents/{matched_path}"], capture_output=True, text=True)
                        if sub_res.returncode == 0:
                            sub_items = json.loads(sub_res.stdout)
                            if isinstance(sub_items, list) and any(si.get("name", "").endswith("_hls") for si in sub_items):
                                return True
    except Exception as e:
        print(f"[-] Catatan pengecekan stream_drive: {e}")
    return False

def get_split_archive_info(filename):
    """
    Menganalisis apakah sebuah nama file merupakan bagian dari split archive.
    Mengembalikan (base_stem, part_num, is_primary, is_split)
    Mendukung format:
      - .part1.rar / .part01.rar / .part001.rar
      - .001 / .002 / ...
      - .r00 / .r01 / ... (RAR lama)
      - .z01 / .z02 / ... / .zip
      - .7z.001 / .7z.002 / ...
      - .tar.gz.aa / .tar.gz.ab / ...
    """
    fn = filename.strip()
    
    # 1. Format .part01.rar / .part1.rar / .part01.7z / .part01.zip
    m1 = re.search(r'^(.*?)\.part0*([0-9]+)\.([a-zA-Z0-9]+)$', fn, re.IGNORECASE)
    if m1:
        base = f"{m1.group(1)}.{m1.group(3)}"
        part_idx = int(m1.group(2))
        return (base, part_idx, (part_idx == 1), True)

    # 2. Format 7z/Generic chunk .001, .002, .003, ...
    m2 = re.search(r'^(.*?)\.0*([0-9]+)$', fn, re.IGNORECASE)
    if m2:
        base = m2.group(1)
        part_idx = int(m2.group(2))
        return (base, part_idx, (part_idx == 1), True)

    # 3. Format RAR lawas (.rar, .r00, .r01, .r02, ...)
    m3 = re.search(r'^(.*?)\.(rar|r[0-9]{2,3})$', fn, re.IGNORECASE)
    if m3:
        base = f"{m3.group(1)}.rar"
        ext = m3.group(2).lower()
        if ext == "rar":
            return (base, 1, True, True)
        else:
            p_num = int(ext[1:]) + 2
            return (base, p_num, False, True)

    # 4. Format Zip Split (.zip, .z01, .z02, ...)
    m4 = re.search(r'^(.*?)\.(zip|z[0-9]{2,3})$', fn, re.IGNORECASE)
    if m4:
        base = f"{m4.group(1)}.zip"
        ext = m4.group(2).lower()
        if ext == "zip":
            return (base, 9999, False, True) # file .zip di akhir pada multi-part zip
        else:
            p_num = int(ext[1:])
            return (base, p_num, (p_num == 1), True)

    # 5. Bukan split archive (arsip tunggal biasa)
    return (fn, 1, True, False)

def group_release_archives(asset_list):
    """
    Mengelompokkan daftar aset rilis menjadi grup arsip logis.
    Format return: list of dict:
      {
        "base_name": "...",
        "primary_file": "...", # File utama untuk diekstrak (part 1)
        "all_parts": ["...", "..."], # Semua bagian yang harus diunduh
        "is_split": True/False
      }
    """
    groups = {}
    for fn in sorted(asset_list):
        base, part_idx, is_prim, is_split = get_split_archive_info(fn)
        base_key = base.lower()
        if base_key not in groups:
            groups[base_key] = {
                "base_name": base,
                "primary_file": fn,
                "parts": [],
                "is_split": is_split
            }
        groups[base_key]["parts"].append((part_idx, fn))
        if is_prim:
            groups[base_key]["primary_file"] = fn

    result = []
    for base_key, g in groups.items():
        sorted_parts = [p[1] for p in sorted(g["parts"], key=lambda x: x[0])]
        # Jika is_split tapi primary_file belum tentu index 1, ambil index pertama
        primary = g["primary_file"] if g["primary_file"] in sorted_parts else sorted_parts[0]
        result.append({
            "base_name": g["base_name"],
            "primary_file": primary,
            "all_parts": sorted_parts,
            "is_split": len(sorted_parts) > 1 or g["is_split"]
        })
    return result

def main():
    if len(sys.argv) < 3:
        print("Penggunaan: process_release_archive.py <download_dir> <release_tag> [release_body_file]")
        sys.exit(1)

    # Mode pre-check sebelum download
    is_precheck = False
    if sys.argv[1] == "--check-skip":
        is_precheck = True
        release_tag = sys.argv[2]
        release_body_file = sys.argv[3] if len(sys.argv) > 3 else "release_body.txt"
        download_dir = "release_downloads"
    else:
        download_dir = sys.argv[1]
        release_tag = sys.argv[2]
        release_body_file = sys.argv[3] if len(sys.argv) > 3 else "release_body.txt"

    print("==========================================================")
    print(f"📦 PIPELINE RELEASE: {release_tag}")
    print("==========================================================")

    # 1. Cek apakah release tag termasuk dalam daftar yang diabaikan (sudah diproses sebelumnya)
    clean_tag_upper = release_tag.strip().upper()
    if clean_tag_upper in IGNORED_RELEASE_TAGS:
        print(f"[!] Tag release '{release_tag}' adalah tag khusus (misal password). Melewati.")
        sys.exit(100 if is_precheck else 0)

    workspace_root = os.path.abspath(os.path.dirname(__file__))

    body_text = ""
    if os.path.exists(release_body_file):
        with open(release_body_file, "r", encoding="utf-8") as f:
            body_text = f.read().strip()

    # Jika body_text kosong (misal trigger via workflow_dispatch), ambil langsung via gh cli
    if not body_text:
        try:
            res_body = subprocess.run(["gh", "release", "view", release_tag, "--json", "body"], capture_output=True, text=True)
            if res_body.returncode == 0:
                data_b = json.loads(res_body.stdout)
                body_text = data_b.get("body", "").strip()
                print(f"[+] Berhasil mengambil release body untuk tag '{release_tag}' dari GitHub API.")
        except Exception:
            pass

    # 2. Parse Pemetaan File, Aliases, dan Info Sumber dari Release Body
    file_mapping, aliases, source_info = parse_release_body_lines(body_text)
    print(f"[+] Pemetaan folder terdeteksi: {json.dumps(file_mapping, indent=2)}")
    print(f"[+] Daftar aliases judul terdeteksi: {aliases}")
    if source_info:
        print(f"[+] Info sumber penyedia terdeteksi: {json.dumps(source_info, indent=2)}")

    # 3. Periksa status apakah semua folder target rilis ini SUDAH ADA di stream_drive
    target_folders = list(set(file_mapping.values()))
    if not target_folders:
        tag_slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', release_tag).lower()
        target_folders = [f"anime/{tag_slug}"]

    all_already_in_drive = True
    for tf in target_folders:
        update_aliases_and_sources(workspace_root, tf, aliases, "", source_info)
        if not check_if_already_processed_in_drive(tf):
            all_already_in_drive = False

    if all_already_in_drive:
        print(f"\n[⚡] PRE-CHECK SKIP: Seluruh konten untuk rilis '{release_tag}' SUDAH LENGKAP di stream_drive!")
        sync_metadata_from_stream_drive(workspace_root)
        print("[+] Metadata & aliases.json telah diperbarui tanpa perlu mengunduh aset atau transcode.")
        sys.exit(100 if is_precheck else 0)

    if is_precheck:
        print("[+] Konten belum ada di stream_drive. Melanjutkan ke proses download & transcode...")
        sys.exit(0)

    # 4. Ambil Kunci Password dari Tag 'password' & Release Body
    passwords = fetch_passwords_from_password_tag()
    for line in body_text.splitlines():
        cl = line.strip().strip('"`')
        if cl and cl not in passwords and not cl.startswith('#') and not cl.startswith('http') and '/' not in cl:
            passwords.append(cl)
    if "" not in passwords:
        passwords.append("")

    # 5. Dapatkan Daftar Aset yang Tersedia di Release
    release_assets = []
    try:
        res_assets = subprocess.run(["gh", "release", "view", release_tag, "--json", "assets"], capture_output=True, text=True)
        if res_assets.returncode == 0:
            data_ast = json.loads(res_assets.stdout)
            release_assets = [a["name"] for a in data_ast.get("assets", [])]
    except Exception as e:
        print(f"[-] Gagal mendapatkan daftar aset release: {e}")

    raw_candidates = release_assets if release_assets else list(file_mapping.keys())
    archive_groups = group_release_archives(raw_candidates)

    print(f"\n[🔍] Terdeteksi {len(archive_groups)} grup arsip rilis:")
    for ag in archive_groups:
        print(f" -> Basis: '{ag['base_name']}' | Split: {ag['is_split']} ({len(ag['all_parts'])} bagian: {ag['all_parts']}) | Primary: '{ag['primary_file']}'")

    os.makedirs(download_dir, exist_ok=True)
    archives_to_process = []

    for ag in archive_groups:
        primary_file = ag["primary_file"]
        base_name = ag["base_name"]
        
        # Tentukan folder tujuan dari pemetaan
        dest_f = file_mapping.get(primary_file.lower()) or file_mapping.get(base_name.lower())
        if not dest_f:
            tag_slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', release_tag).lower()
            dest_f = f"anime/{tag_slug}"

        # Cek apakah folder HLS tujuan sudah ada di stream_drive
        if check_if_already_processed_in_drive(dest_f):
            print(f"\n[⚡] SKIP DOWNLOAD: Arsip '{base_name}' dilewati karena HLS '{dest_f}' sudah ada di stream_drive.")
            update_aliases_and_sources(workspace_root, dest_f, aliases, primary_file, source_info)
            continue

        print(f"\n[📥] Mengunduh seluruh bagian arsip untuk: '{base_name}' ({len(ag['all_parts'])} bagian) -> Target: '{dest_f}'...")
        for part_file in ag["all_parts"]:
            print(f"    -> Downloading part: {part_file}")
            run_cmd(["gh", "release", "download", release_tag, "-p", part_file, "--dir", download_dir, "--clobber"], check=False)

        local_primary_arch = os.path.join(download_dir, primary_file)
        if os.path.exists(local_primary_arch):
            archives_to_process.append((local_primary_arch, dest_f))
        else:
            # Fallback jika nama file primary tidak cocok persis
            for p in ag["all_parts"]:
                candidate_p = os.path.join(download_dir, p)
                if os.path.exists(candidate_p):
                    archives_to_process.append((candidate_p, dest_f))
                    break

    all_processed_videos = []

    # 6. Ekstrak Setiap Arsip yang Berhasil Diunduh Lengkap ke Folder RAW/<kategori>/<judul>/[season]
    for arch, dest_folder in archives_to_process:
        arch_name = os.path.basename(arch)
        update_aliases_and_sources(workspace_root, dest_folder, aliases, arch_name, source_info)

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
        print("\n[✓] Semua video dalam rilis ini sudah selesai diproses dan di-host di stream_drive.")
    else:
        # 7. Jalankan generate_hls.py
        print("\n==========================================================")
        print("🎬 MENJALANKAN GENERATOR HLS ABR & SUBTITLE PIPELINE")
        print("==========================================================")
        generate_script = os.path.join(workspace_root, "generate_hls.py")
        cmd_gen = [sys.executable, generate_script] + all_processed_videos
        run_cmd(cmd_gen, check=True)

    # 8. Bersihkan file arsip download & sementara agar tidak ter-push ke Git
    print("\n[🧹] Membersihkan file arsip dan berkas unduhan sementara...")
    shutil.rmtree(download_dir, ignore_errors=True)
    if os.path.exists(release_body_file):
        try:
            os.remove(release_body_file)
        except Exception:
            pass

    print("\n==========================================================")
    print("🎉 PIPELINE PROSES RELEASE SELESAI!")
    print("==========================================================")

if __name__ == "__main__":
    main()
