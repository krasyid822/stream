#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import glob
import time
import urllib.parse

# Pastikan semua output print langsung tampil real-time di GitHub Actions
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(line_buffering=True)

# Tag release yang diabaikan (tag 'password' menyimpan kunci enkripsi, bukan arsip video)
IGNORED_RELEASE_TAGS = {"PASSWORD"}

DRIVE_REPO = "krasyid822/stream_drive"

def gh_api(path, params="", retries=3):
    """
    Wrapper gh api ke repo stream_drive yang melaporkan kegagalan dengan jelas.
    Kegagalan diam-diam sangat berbahaya: hasil kosong akan disalahartikan sebagai
    'folder belum ada di drive' sehingga pipeline meng-encode ulang semuanya.
    Endpoint tree besar kadang membalas HTTP 500 sementara, jadi dicoba ulang.
    """
    cmd = ["gh", "api", f"repos/{DRIVE_REPO}/{path}{params}"]
    last_err = None
    for attempt in range(max(1, retries)):
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout, None

        err_lines = (res.stderr or res.stdout or "").strip().splitlines()
        last_err = err_lines[0] if err_lines else "respons kosong dari GitHub"

        # 404 = memang belum ada (wajar) -> tidak perlu dicoba lagi
        if "404" in last_err or "Not Found" in last_err:
            return None, last_err

        if attempt < max(1, retries) - 1:
            # Backoff eksponensial (2,4,8,16,20... detik, dibatasi 20 detik).
            # Endpoint pohon besar (~7 MB) sering terpotong sesaat; jeda yang terlalu
            # pendek membuat semua percobaan jatuh pada kondisi jaringan yang sama.
            time.sleep(min(2 ** (attempt + 1), 20))
            continue

    print(f"[!] Gagal mengakses stream_drive ({path}): {last_err} (setelah {retries} percobaan)")
    print("[!] Pastikan secret DRIVE_PAT tersedia & punya akses ke repo stream_drive.")
    return None, last_err

ARCHIVE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".7z.001", ".zpaq", ".tar", ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".part01.rar", ".part1.rar",
    ".001"
)

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v")

# Folder kerja
STAGING_DIR = "HLS_OUT"

def _env_float(name, default):
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default

def _env_int(name, default):
    try:
        return int(float(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default

# Ukuran arsip target per shard encode (dapat di-override dari workflow_dispatch)
CHUNK_SIZE_GB = _env_float("HLS_CHUNK_SIZE_GB", 1.2)
MAX_SHARDS = _env_int("HLS_MAX_SHARDS", 6)

# Cache index folder stream_drive (menghemat panggilan API GitHub)
_DRIVE_INDEX_CACHE = {}

def run_cmd(cmd, check=True, cwd=None):
    print(f"[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, cwd=cwd)

def normalize_rel_path(path):
    """Normalisasi path relatif (slash '/', huruf kecil) untuk perbandingan lintas platform."""
    return str(path).replace(os.sep, "/").strip("/").lower()

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

    # Urutan deterministik (penting untuk sharding antar-job: semua shard harus memilih episode yang sama)
    return sorted(found_videos)

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
    """
    Memperbarui metadata.json berdasarkan isi sebenarnya repo stream_drive.

    Dua strategi:
      1) Tree API penuh (1 panggilan, cepat) — tapi responsnya ~7 MB dan GitHub
         kadang membalas HTTP 500 secara acak, jadi selalu ada risiko gagal.
      2) Jelajah per folder (payload kecil, andal) — dipakai bila strategi 1 gagal.
    Mengembalikan True bila metadata berhasil ditulis.
    """
    media_items, categories = None, None
    try:
        out, err = gh_api("git/trees/main", "?recursive=1")
        if out is not None:
            tree_data = json.loads(out)
            if tree_data.get("truncated"):
                print("[-] Peringatan: pohon stream_drive terpotong (batas 100.000 entri).")
            vtt_by_folder = {}
            for item in tree_data.get("tree", []):
                p = item["path"]
                if p.endswith(".vtt"):
                    folder_hls = "/".join(p.split("/")[:-1])
                    vtt_by_folder.setdefault(folder_hls, []).append(p)

            categories = set()
            media_items = []
            for item in tree_data.get("tree", []):
                path = item["path"]
                if not path.endswith("master.m3u8"):
                    continue
                parts = path.split("/")
                if len(parts) < 3:
                    continue
                categories.add(parts[0])
                hls_path = "/".join(parts[:-1])
                ep_name = parts[-2][:-4] if parts[-2].endswith("_hls") else parts[-2]
                media_items.append({
                    "id": path.replace("/", "_").replace(" ", "_").replace(".", "_"),
                    "name": ep_name,
                    "folder": "/".join(parts[:-2]),
                    "path": hls_path,
                    "master_url": path,
                    "poster_url": hls_path + "/poster.jpg",
                    "subtitles": vtt_by_folder.get(hls_path, []),
                    "type": "hls_stream"
                })
    except Exception as e:
        print(f"[-] Tree penuh gagal dibaca: {e}")

    if not media_items:
        print("[i] Tree penuh tidak terbaca -> beralih ke mode jelajah per folder...")
        try:
            media_items, categories = _collect_media_by_walk()
        except Exception as e:
            print(f"[-] Mode jelajah gagal: {e}")

    if not media_items:
        print("[-] Gagal sync metadata: isi stream_drive tidak bisa dibaca. "
              "metadata.json TIDAK diubah.")
        return False

    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s["name"])]

    media_items.sort(key=lambda x: (x["folder"], natural_sort_key(x)))

    metadata_path = os.path.join(workspace_root, "metadata.json")
    output_metadata = {
        "categories": sorted(list(categories)),
        "media": media_items,
        "updated_at": str(time.time())
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(output_metadata, f, indent=2, ensure_ascii=False)
    print(f"[✓] metadata.json disinkronkan dengan {len(media_items)} media dari stream_drive!")
    return True


def _list_dir_contents(path):
    """Isi satu folder di stream_drive (payload kecil), None bila gagal."""
    endpoint = f"contents/{urllib.parse.quote(path)}" if path else "contents/"
    out, _ = gh_api(endpoint)
    if out is None:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def _walk_hls_dirs(prefix="", depth=0, max_depth=5):
    """Menelusuri folder sampai menemukan folder episode (*_hls)."""
    if depth > max_depth:
        return
    items = _list_dir_contents(prefix)
    if not items:
        return
    for it in items:
        if it.get("type") != "dir":
            continue
        p = (it.get("path") or "").rstrip("/")
        if p.endswith("_hls"):
            yield p
        else:
            yield from _walk_hls_dirs(p, depth + 1, max_depth)


def _collect_media_by_walk():
    """Menyusun daftar media dengan menelusuri folder satu per satu."""
    folders = list(_walk_hls_dirs())
    print(f"[i] Menjelajah {len(folders)} folder episode di stream_drive...")
    media_items = []
    categories = set()

    for hls_path in folders:
        items = _list_dir_contents(hls_path)
        if not items:
            continue
        files = [it for it in items if it.get("type") == "file"]
        if "master.m3u8" not in {it.get("name", "") for it in files}:
            continue
        parts = hls_path.split("/")
        if len(parts) < 2:
            continue
        categories.add(parts[0])
        master_path = hls_path + "/master.m3u8"
        media_items.append({
            "id": master_path.replace("/", "_").replace(" ", "_").replace(".", "_"),
            "name": parts[-1][:-4] if parts[-1].endswith("_hls") else parts[-1],
            "folder": "/".join(parts[:-1]),
            "path": hls_path,
            "master_url": master_path,
            "poster_url": hls_path + "/poster.jpg",
            "subtitles": sorted(it["path"] for it in files if it.get("name", "").endswith(".vtt")),
            "type": "hls_stream"
        })

    return media_items, categories

def resolve_drive_folder(dest_folder):
    """
    Menentukan path folder yang benar-benar ada di stream_drive.
    Toleran terhadap perbedaan penulisan ('fulldive-rpg' vs 'fulldive_rpg', case, dsb).
    """
    clean_f = dest_folder.strip("/")
    out, _ = gh_api(f"contents/{urllib.parse.quote(clean_f)}")
    if out is not None:
        return clean_f

    parts = clean_f.split("/")
    if len(parts) >= 2:
        category = parts[0]
        target_title = parts[1]
        norm_target = re.sub(r'[^a-zA-Z0-9]', '', target_title).lower()
        out_cat, _ = gh_api(f"contents/{urllib.parse.quote(category)}")
        if out_cat is not None:
            try:
                cat_items = json.loads(out_cat)
                for c_item in cat_items:
                    norm_folder = re.sub(r'[^a-zA-Z0-9]', '', c_item.get("name", "")).lower()
                    if norm_target and norm_target == norm_folder:
                        # Pertahankan segmen di bawahnya (misal season), hanya ganti nama judulnya
                        return "/".join([category, c_item["name"]] + parts[2:])
            except Exception:
                pass
    return clean_f

def _query_drive_tree(path):
    """Memanggil Git Tree API pada sebuah subtree; None bila path tidak ada / gagal."""
    out, err = gh_api(f"git/trees/main:{path}", "?recursive=1")
    if out is None:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None

def ancestor_fallback_allowed(dest_folder, all_dest_folders):
    """
    Apakah boleh mencari content di folder INDUK bila folder tujuan tidak ada?

    Kasus sah: release body hanya merujuk satu folder musim (misal GSYOS -> 'anime/gsyos/2'),
    padahal drive menyimpannya datar di 'anime/gsyos'. Maka naik ke induknya masih sah.

    Kasus BAHAYA: body merebug beberapa musim (misal JCTK -> 'anime/jctk/1' DAN 'anime/jctk/2').
    Kalau 'anime/jctk/2' hilang tapi 'anime/jctk/1' berisi episode, naik ke induk akan membuat
    musim 2 terlihat "sudah lengkap" padahal kosong -> episode hilang tanpa error.

    Aturan: fallback ke induk hanya boleh bila judul tersebut punya SATU folder tujuan saja.
    """
    clean_f = dest_folder.strip("/").lower()
    parts = clean_f.split("/")
    if len(parts) <= 2:
        return False
    root = "/".join(parts[:2])

    siblings = set()
    for d in all_dest_folders:
        dp = d.strip("/").lower().split("/")
        if len(dp) >= 2 and "/".join(dp[:2]) == root:
            siblings.add(d.strip("/").lower())

    return len(siblings) <= 1


def drive_folder_candidates(dest_folder, allow_ancestor=True):
    """
    Daftar path folder di stream_drive yang mungkin mewakili folder tujuan ini, urut dari yang
    paling spesifik. Menutup kasus penulisan folder berbeda antara release body dan drive,
    misal body: 'anime/gsyos/2' sedangkan drive: 'anime/gsyos'.

    Perhatikan: naiknya ke folder INDUK hanya dilakukan bila allow_ancestor=True.
    """
    clean_f = dest_folder.strip("/")
    candidates = [clean_f]

    resolved = resolve_drive_folder(clean_f)
    if resolved not in candidates:
        candidates.append(resolved)

    if not allow_ancestor:
        return candidates

    # Coba versi lebih pendek (buang segmen paling bawah, minimal kategori/judul)
    parts = resolved.split("/")
    while len(parts) > 2:
        parts = parts[:-1]
        cand = "/".join(parts)
        if cand not in candidates:
            candidates.append(cand)

    return candidates

def fetch_drive_folder_index(dest_folder, allow_ancestor=True):
    """
    Mengambil daftar master.m3u8 yang sudah ada di stream_drive untuk satu folder tujuan.
    Mengembalikan himpunan path relatif terhadap subtree yang cocok (huruf kecil).
    Dipakai untuk: (1) deteksi folder sudah terproses, (2) resume per-episode.

    allow_ancestor=False membuat pemeriksaanini KETAT: hanya folder itu sendiri yang dicek,
    bukan folder induknya. Wajib dipakai untuk mendeteksi musim yang belum terproses.
    """
    clean_f = dest_folder.strip("/")
    cache_key = f"{clean_f}|anc={int(bool(allow_ancestor))}"
    if cache_key in _DRIVE_INDEX_CACHE:
        return _DRIVE_INDEX_CACHE[cache_key]

    masters = set()

    # Pakai Git Tree API pada subtree folder tujuan (1 panggilan API, bukan satu per episode)
    for cand in drive_folder_candidates(clean_f, allow_ancestor):
        tree_data = _query_drive_tree(cand)
        if tree_data is None:
            continue
        for item in tree_data.get("tree", []):
            p = item.get("path", "")
            # Git Tree API pada subtree (main:<path>) mengembalikan path RELATIF terhadap subtree tersebut
            if p.endswith("master.m3u8"):
                masters.add(normalize_rel_path(p))
        break  # memakai subtree paling spesifik yang ada

    if not masters:
        # Fallback: contents API per episode (folder mungkin belum ada sama sekali)
        resolved = drive_folder_candidates(clean_f)[0]
        out2, _ = gh_api(f"contents/{urllib.parse.quote(resolved)}")
        if out2 is not None:
            try:
                for item in json.loads(out2):
                    ep_name = item.get("name", "")
                    if ep_name.endswith("_hls") and item.get("type") == "dir":
                        probe = urllib.parse.quote(f"{resolved}/{ep_name}/master.m3u8")
                        out3, _ = gh_api(f"contents/{probe}")
                        if out3 is not None:
                            masters.add(normalize_rel_path(f"{ep_name}/master.m3u8"))
            except Exception:
                pass

    _DRIVE_INDEX_CACHE[f"{clean_f}|anc={int(bool(allow_ancestor))}"] = masters
    return masters

def check_if_already_processed_in_drive(dest_folder, allow_ancestor=True):
    """True bila folder tujuan sudah punya minimal satu episode HLS di stream_drive."""
    return bool(fetch_drive_folder_index(dest_folder, allow_ancestor))

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

# ---------------------------------------------------------------------------
# Helper bersama untuk semua mode
# ---------------------------------------------------------------------------

def load_release_body(release_body_file, release_tag):
    """Membaca release body dari file; jika kosong ambil langsung via gh cli."""
    body_text = ""
    if release_body_file and os.path.exists(release_body_file):
        with open(release_body_file, "r", encoding="utf-8") as f:
            body_text = f.read().strip()

    if not body_text:
        try:
            res_body = subprocess.run(["gh", "release", "view", release_tag, "--json", "body"], capture_output=True, text=True)
            if res_body.returncode == 0:
                data_b = json.loads(res_body.stdout)
                body_text = data_b.get("body", "").strip()
                print(f"[+] Berhasil mengambil release body untuk tag '{release_tag}' dari GitHub API.")
        except Exception:
            pass
    return body_text

def collect_passwords(body_text):
    """Menyusun daftar password enkripsi dari tag 'password' + release body."""
    passwords = fetch_passwords_from_password_tag()
    for line in body_text.splitlines():
        cl = line.strip().strip('"`')
        if cl and cl not in passwords and not cl.startswith('#') and not cl.startswith('http') and '/' not in cl:
            passwords.append(cl)
    if "" not in passwords:
        passwords.append("")
    return passwords

def fetch_release_assets(release_tag):
    """Mengambil daftar aset release (nama + ukuran dalam byte)."""
    assets = []
    try:
        res = subprocess.run(["gh", "release", "view", release_tag, "--json", "assets"], capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            assets = [{"name": a["name"], "size": a.get("size", 0)} for a in data.get("assets", [])]
    except Exception as e:
        print(f"[-] Gagal mendapatkan daftar aset release: {e}")
    return assets

def build_work_groups(release_tag, file_mapping, aliases, source_info, body_text):
    """
    Mengelompokkan aset rilis menjadi unit kerja (satu grup arsip = satu folder tujuan),
    lengkap dengan jumlah byte untuk menghitung kebutuhan sharding.
    """
    assets = fetch_release_assets(release_tag)
    raw_candidates = [a["name"] for a in assets] or list(file_mapping.keys())
    size_by_name = {a["name"]: a.get("size", 0) for a in assets}
    archive_groups = group_release_archives(raw_candidates)

    tag_slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', release_tag).lower()
    work_groups = []

    for gi, ag in enumerate(archive_groups):
        primary_file = ag["primary_file"]
        base_name = ag["base_name"]
        dest_f = file_mapping.get(primary_file.lower()) or file_mapping.get(base_name.lower()) or f"anime/{tag_slug}"
        total_bytes = sum(size_by_name.get(p, 0) for p in ag["all_parts"])
        work_groups.append({
            "group": gi,
            "base_name": base_name,
            "primary": primary_file,
            "parts": ag["all_parts"],
            "is_split": ag["is_split"],
            "dest": dest_f,
            "bytes": total_bytes,
        })
    return work_groups

def compute_shards(group_bytes, chunk_size_gb=CHUNK_SIZE_GB, max_shards=MAX_SHARDS):
    """
    Menentukan jumlah shard untuk satu grup arsip berdasarkan ukuran arsip.
    Contoh: arsip 3 GB dengan chunk 1.2 GB -> 3 shard (~1 episode 1080p per shard).
    """
    chunk_bytes = max(0.1, chunk_size_gb) * (1024 ** 3)
    if group_bytes <= 0:
        return 1
    shards = int(round(group_bytes / chunk_bytes))
    return max(1, min(max_shards, shards))

def download_release_parts(release_tag, parts, download_dir):
    """Mengunduh seluruh bagian arsip satu grup."""
    os.makedirs(download_dir, exist_ok=True)
    for part_file in parts:
        print(f"    -> Downloading part: {part_file}")
        run_cmd(["gh", "release", "download", release_tag, "-p", part_file, "--dir", download_dir, "--clobber"], check=False)

def find_primary_downloaded(download_dir, primary_file, parts):
    """Menentukan file arsip utama yang benar-benar ada di disk."""
    candidate = os.path.join(download_dir, primary_file)
    if os.path.exists(candidate):
        return candidate
    for p in parts:
        candidate = os.path.join(download_dir, p)
        if os.path.exists(candidate):
            return candidate
    return None

def write_skip_list(workspace_root, dest_folder, skip_file, allow_ancestor=True):
    """
    Menulis daftar episode yang sudah ada di stream_drive untuk dipakai generate_hls.py --skip-list.

    WAJIB memakai allow_ancestor yang sama dengan saat plan. Bila tidak, episode milik musim LAIN
    (misal '1/Episode 01_hls/master.m3u8') ikut masuk skip-list dan membuat musim ini salah
    melewati episode yang memang belum ada.
    """
    masters = fetch_drive_folder_index(dest_folder, allow_ancestor)
    if not masters:
        return 0
    with open(skip_file, "a", encoding="utf-8") as f:
        for m in sorted(masters):
            f.write(f"{normalize_rel_path(dest_folder)}/{m}\n")
    return len(masters)

def stage_hls_output(workspace_root, dest_folder, staging_dir):
    """
    Memindahkan folder hasil HLS dari folder produksi ke folder staging (untuk di-upload sebagai artifact).
    Struktur path relatif dipertahankan supaya bisa langsung di-merge di repo stream_drive.
    """
    parts = [p for p in dest_folder.strip("/").split("/") if p]
    current = workspace_root
    for part in parts:
        if not os.path.isdir(current):
            return 0
        next_dir = None
        for existing in os.listdir(current):
            if existing.lower() == part.lower() and os.path.isdir(os.path.join(current, existing)):
                next_dir = os.path.join(current, existing)
                break
        current = next_dir if next_dir else os.path.join(current, part.lower())

    if not os.path.isdir(current):
        return 0

    moved = 0
    rel_dest = os.path.relpath(current, start=workspace_root)
    stage_target = os.path.join(staging_dir, rel_dest)
    os.makedirs(stage_target, exist_ok=True)

    for name in os.listdir(current):
        if name.endswith("_hls"):
            src = os.path.join(current, name)
            dst = os.path.join(stage_target, name)
            if os.path.exists(dst):
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(src, dst)
            moved += 1
    return moved

def emit_github_outputs(payload):
    """Menulis output ke $GITHUB_OUTPUT (dipakai job plan)."""
    chunks = payload.get("chunks", [])
    gh_out = os.environ.get("GITHUB_OUTPUT")
    lines = [
        f"skip={'true' if payload.get('skip') else 'false'}",
        f"chunks_count={len(chunks)}",
        f"release_tag={payload.get('release_tag', '')}",
        "chunks=" + json.dumps(chunks),
    ]
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    for ln in lines:
        if ln.startswith("chunks="):
            continue
        print(f"[OUT] {ln}")
    print(f"[OUT] chunks={json.dumps(chunks, indent=2)}")

# ---------------------------------------------------------------------------
# MODE: --plan (job plan) -> menghasilkan daftar chunk untuk matrix job encode
# ---------------------------------------------------------------------------

def cmd_plan(release_body_file, plan_out_file):
    print("==========================================================")
    print("🗺️  MODE PLAN: menyusun manifest chunk encode")
    print("==========================================================")

    release_tag = os.environ.get("RELEASE_TAG", "").strip()
    if not release_tag:
        release_tag = subprocess.run(["git", "describe", "--tags", "--abbrev=0"], capture_output=True, text=True).stdout.strip()

    clean_tag_upper = release_tag.strip().upper()
    if clean_tag_upper in IGNORED_RELEASE_TAGS:
        print(f"[!] Tag release '{release_tag}' adalah tag khusus. Melewati.")
        emit_github_outputs({"skip": True, "release_tag": release_tag, "chunks": []})
        return 0

    workspace_root = os.path.abspath(os.path.dirname(__file__))
    body_text = load_release_body(release_body_file, release_tag)
    file_mapping, aliases, source_info = parse_release_body_lines(body_text)

    print(f"[+] Pemetaan folder terdeteksi: {json.dumps(file_mapping, indent=2)}")
    print(f"[+] Daftar aliases judul terdeteksi: {aliases}")
    if source_info:
        print(f"[+] Info sumber penyedia terdeteksi: {json.dumps(source_info, indent=2)}")

    work_groups = build_work_groups(release_tag, file_mapping, aliases, source_info, body_text)
    if not work_groups:
        print("[-] Tidak ada grup arsip yang bisa diproses.")
        emit_github_outputs({"skip": False, "release_tag": release_tag, "chunks": []})
        return 0

    # Perbarui aliases.json (akan di-commit oleh job publish)
    for wg in work_groups:
        update_aliases_and_sources(workspace_root, wg["dest"], aliases, wg["base_name"], source_info)

    # Deteksi folder tujuan yang sudah terproces di stream_drive
    all_done = True
    all_dests = [wg["dest"] for wg in work_groups]
    for wg in work_groups:
        # Musim yang punya saudara lain TIDAK boleh melihat isi folder induknya:
        # kalau tidak, musim yang kosong tersamar jadi "sudah lengkap".
        wg["allow_ancestor"] = ancestor_fallback_allowed(wg["dest"], all_dests)
        wg["done"] = check_if_already_processed_in_drive(wg["dest"], wg["allow_ancestor"])
        if not wg["done"]:
            all_done = False
        mode = "longgar" if wg["allow_ancestor"] else "ketat"
        print(f"[+] Folder '{wg['dest']}' -> {'SUDAH ada di stream_drive' if wg['done'] else 'belum ada'} (cek {mode})")

    if all_done:
        print(f"\n[⚡] PRE-CHECK SKIP: Seluruh konten untuk rilis '{release_tag}' SUDAH LENGKAP di stream_drive!")
        sync_metadata_from_stream_drive(workspace_root)
        emit_github_outputs({"skip": True, "release_tag": release_tag, "chunks": []})
        return 0

    # Susun chunk (unit kerja paralel) dari setiap grup arsip
    chunks = []
    for wg in work_groups:
        shards = compute_shards(wg["bytes"])
        for s in range(shards):
            chunks.append({
                "id": f"g{wg['group']}s{s}",
                "group": wg["group"],
                "shard": s,
                "shards": shards,
                "dest": wg["dest"],
                "allow_ancestor": wg["allow_ancestor"],
                "primary": wg["primary"],
                "parts": wg["parts"],
                "bytes": wg["bytes"],
            })
        print(f"[+] Grup '{wg['base_name']}' ({wg['bytes'] / (1024**3):.2f} GB) -> {shards} shard")

    payload = {
        "skip": False,
        "release_tag": release_tag,
        "chunk_size_gb": CHUNK_SIZE_GB,
        "max_shards": MAX_SHARDS,
        "chunks": chunks,
    }

    with open(plan_out_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"[✓] Manifest plan ditulis: {plan_out_file} ({len(chunks)} chunk)")
    emit_github_outputs(payload)
    return 0

# ---------------------------------------------------------------------------
# MODE: --encode-chunk (job encode matrix) -> download + extract + encode 1 chunk
# ---------------------------------------------------------------------------

def cmd_encode_chunk(release_body_file, chunk_file, download_dir="release_downloads"):
    print("==========================================================")
    print("🎬 MODE ENCODE CHUNK")
    print("==========================================================")

    with open(chunk_file, "r", encoding="utf-8") as f:
        chunk = json.load(f)

    workspace_root = os.path.abspath(os.path.dirname(__file__))
    release_tag = os.environ.get("RELEASE_TAG", "").strip()
    shard = int(chunk.get("shard", 0))
    shards = int(chunk.get("shards", 1))
    dest_folder = chunk["dest"]
    parts = chunk["parts"]
    # Sikap pencocokan folder harus sama persis dengan yang dipakai job plan,
    # jika tidak episode musim ini bisa salah di-skip karena milik musim lain.
    allow_ancestor = bool(chunk.get("allow_ancestor", True))

    print(f"[+] Chunk: {chunk.get('id')} | shard {shard + 1}/{shards} | tujuan: {dest_folder}")
    print(f"[+] Bagian arsip ({len(parts)}): {parts}")

    body_text = load_release_body(release_body_file, release_tag)
    passwords = collect_passwords(body_text)

    # 1. Unduh bagian arsip milik chunk ini saja
    download_release_parts(release_tag, parts, download_dir)

    # 2. Ekstrak (semua episode ikut terbaca, tapi hanya milik shard ini yang diproses)
    raw_dest_dir = os.path.join(workspace_root, "RAW", dest_folder)
    os.makedirs(raw_dest_dir, exist_ok=True)

    temp_extract_dir = os.path.join(workspace_root, "RAW_TEMP")
    shutil.rmtree(temp_extract_dir, ignore_errors=True)

    primary_arch = find_primary_downloaded(download_dir, chunk.get("primary", ""), parts)
    if not primary_arch:
        print(f"[-] File arsip utama tidak ditemukan setelah download: {chunk.get('primary')}")
        return 1

    extracted_videos = recursive_extract(primary_arch, temp_extract_dir, passwords)

    # 3. Pindahkan seluruh video ke RAW/<dest> lalu ambil hanya episode milik shard ini
    all_videos = []
    for v_path in extracted_videos:
        v_final = os.path.join(raw_dest_dir, os.path.basename(v_path))
        shutil.move(v_path, v_final)
        all_videos.append(v_final)

    # Bersihkan area ekstrak setelah semua video dipindahkan keluar
    shutil.rmtree(temp_extract_dir, ignore_errors=True)

    all_videos.sort()
    my_videos = all_videos[shard::shards] if shards > 1 else all_videos

    print(f"\n[📋] Total episode dalam arsip: {len(all_videos)}")
    print(f"[📋] Dimiliki shard {shard + 1}/{shards}: {len(my_videos)} episode")
    for v in my_videos:
        print(f"    -> {os.path.basename(v)}")

    # 4. Hapus arsip SEGERA setelah ekstrak (hemat disk runner)
    shutil.rmtree(download_dir, ignore_errors=True)

    if not my_videos:
        print("[✓] Tidak ada episode milik shard ini. Selesai.")
        return 0

    # 5. Siapkan daftar episode yang sudah ada di stream_drive (resume / idempotent)
    skip_file = os.path.join(workspace_root, "drive_skip_list.txt")
    if os.path.exists(skip_file):
        os.remove(skip_file)
    existing_count = write_skip_list(workspace_root, dest_folder, skip_file, allow_ancestor)
    if existing_count:
        print(f"[↩️] Resume: {existing_count} episode di '{dest_folder}' sudah ada di stream_drive.")

    # 6. Jalankan generator HLS
    generate_script = os.path.join(workspace_root, "generate_hls.py")
    cmd_gen = [sys.executable, "-u", generate_script] + my_videos
    if existing_count:
        cmd_gen += ["--skip-list", skip_file]
    # Tiap episode yang selesai langsung di-stage, agar hasil parsial tetap ter-upload
    # kalau job dihentikan (timeout) di tengah jalan.
    cmd_gen += ["--stage-dir", STAGING_DIR]

    print(f"\n[🚀] Menjalankan: {' '.join(cmd_gen)}")
    res = subprocess.run(cmd_gen)
    had_failures = (res.returncode == 2)
    if had_failures:
        print("[!] Ada episode yang gagal di-encode, tetapi hasil parsial tetap dilanjutkan ke publish.")
    elif res.returncode != 0:
        print(f"[-] Generator HLS berhenti dengan kode {res.returncode}.")

    # 7. Pindahkan sisa hasil HLS ke folder staging (episode yang selesai sudah di-stage
    #    satu per satu oleh generate_hls.py --stage-dir, jadi biasanya tidak ada sisa)
    staging_root = os.path.join(workspace_root, STAGING_DIR)
    moved = stage_hls_output(workspace_root, dest_folder, staging_root)
    staged_now = 0
    dest_lower = normalize_rel_path(dest_folder)
    stage_dir_path = os.path.join(staging_root, dest_folder.strip("/"))
    if os.path.isdir(stage_dir_path):
        for root, dirs, _ in os.walk(stage_dir_path):
            staged_now += sum(1 for x in dirs if x.endswith("_hls"))
    print(f"[📦] {staged_now} folder HLS di staging ({dest_lower}) — "
          f"{'siap di-upload ke stream_drive' if staged_now else 'TIDAK ADA hasil_encode'}")

    # 8. Bersihkan ruang kerja
    shutil.rmtree(os.path.join(workspace_root, "RAW"), ignore_errors=True)
    if os.path.exists(skip_file):
        try:
            os.remove(skip_file)
        except Exception:
            pass

    # Kode 2 = ada episode gagal (job ditandai merah, tapi hasil parsial tetap dipublish).
    # Kode 0 termasuk ketika semua episode shard ini sudah ada di stream_drive (resume).
    return 2 if had_failures else 0

# ---------------------------------------------------------------------------
# MODE LAMA: proses seluruh rilis dalam satu proses (dipakai untuk penggunaan lokal/debug)
# ---------------------------------------------------------------------------

def cmd_legacy(download_dir, release_tag, release_body_file):
    workspace_root = os.path.abspath(os.path.dirname(__file__))

    body_text = load_release_body(release_body_file, release_tag)
    file_mapping, aliases, source_info = parse_release_body_lines(body_text)
    print(f"[+] Pemetaan folder terdeteksi: {json.dumps(file_mapping, indent=2)}")
    print(f"[+] Daftar aliases judul terdeteksi: {aliases}")

    passwords = collect_passwords(body_text)
    work_groups = build_work_groups(release_tag, file_mapping, aliases, source_info, body_text)

    all_processed_videos = []

    for wg in work_groups:
        arch_name = wg["base_name"]
        dest_folder = wg["dest"]
        update_aliases_and_sources(workspace_root, dest_folder, aliases, arch_name, source_info)

        raw_dest_dir = os.path.join(workspace_root, "RAW", dest_folder)
        os.makedirs(raw_dest_dir, exist_ok=True)

        print(f"\n==================================================")
        print(f"📂 Memproses: {arch_name} -> RAW/{dest_folder}")
        print(f"==================================================")

        download_release_parts(release_tag, wg["parts"], download_dir)
        primary_arch = find_primary_downloaded(download_dir, wg["primary"], wg["parts"])
        if not primary_arch:
            print(f"[-] Arsip utama tidak ditemukan: {wg['primary']}")
            continue

        temp_extract_dir = os.path.join(workspace_root, "RAW_TEMP")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        extracted_videos = recursive_extract(primary_arch, temp_extract_dir, passwords)
        shutil.rmtree(temp_extract_dir, ignore_errors=True)

        for v_path in extracted_videos:
            v_final = os.path.join(raw_dest_dir, os.path.basename(v_path))
            shutil.move(v_path, v_final)
            all_processed_videos.append(v_final)
            print(f" -> Video dipindahkan: RAW/{dest_folder}/{os.path.basename(v_path)}")

        # Hapus arsip grup ini segera setelah ekstrak (hemat disk)
        shutil.rmtree(download_dir, ignore_errors=True)

    if not all_processed_videos:
        print("\n[✓] Semua video dalam rilis ini sudah selesai diproses dan di-host di stream_drive.")
    else:
        print("\n==========================================================")
        print("🎬 MENJALANKAN GENERATOR HLS ABR & SUBTITLE PIPELINE")
        print("==========================================================")
        # Resume: lewati episode yang sudah ada di stream_drive
        skip_file = os.path.join(workspace_root, "drive_skip_list.txt")
        if os.path.exists(skip_file):
            os.remove(skip_file)
        dest_folders = sorted({
            os.path.dirname(v).replace(os.sep, "/").replace("RAW/", "", 1)
            for v in all_processed_videos
        })
        existing_total = sum(write_skip_list(workspace_root, d, skip_file) for d in dest_folders)
        if existing_total:
            print(f"[↩️] Resume: {existing_total} episode sudah ada di stream_drive dan akan dilewati.")

        generate_script = os.path.join(workspace_root, "generate_hls.py")
        cmd_gen = [sys.executable, "-u", generate_script] + all_processed_videos
        if os.path.exists(skip_file) and os.path.getsize(skip_file) > 0:
            cmd_gen += ["--skip-list", skip_file]
        res = subprocess.run(cmd_gen)
        if res.returncode not in (0, 2):
            print(f"[-] Generator HLS berhenti dengan kode {res.returncode}.")

    print("\n[🧹] Membersihkan file arsip dan berkas unduhan sementara...")
    shutil.rmtree(download_dir, ignore_errors=True)
    if release_body_file and os.path.exists(release_body_file):
        try:
            os.remove(release_body_file)
        except Exception:
            pass

    print("\n==========================================================")
    print("🎉 PIPELINE PROSES RELEASE SELESAI!")
    print("==========================================================")
    return 0

# ---------------------------------------------------------------------------
# MODE: --check-skip (pre-check cepat sebelum job lain berjalan)
# ---------------------------------------------------------------------------

def cmd_check_skip(release_tag, release_body_file):
    clean_tag_upper = release_tag.strip().upper()
    if clean_tag_upper in IGNORED_RELEASE_TAGS:
        print(f"[!] Tag release '{release_tag}' adalah tag khusus (misal password). Melewati.")
        return 100

    workspace_root = os.path.abspath(os.path.dirname(__file__))
    body_text = load_release_body(release_body_file, release_tag)
    file_mapping, aliases, source_info = parse_release_body_lines(body_text)

    target_folders = list(set(file_mapping.values()))
    if not target_folders:
        tag_slug = re.sub(r'[^a-zA-Z0-9_\-]', '_', release_tag).lower()
        target_folders = [f"anime/{tag_slug}"]

    all_already_in_drive = True
    for tf in target_folders:
        update_aliases_and_sources(workspace_root, tf, aliases, "", source_info)
        # Sama seperti plan: judul bermulti-musim diperiksa ketat per folder,
        # supaya musim yang kosong tidak ikut dianggap "sudah lengkap".
        loose = ancestor_fallback_allowed(tf, target_folders)
        done = check_if_already_processed_in_drive(tf, loose)
        if not done:
            all_already_in_drive = False
        print(f"[+] Folder '{tf}' -> {'SUDAH ada di stream_drive' if done else 'belum ada'} "
              f"(cek {'longgar' if loose else 'ketat'})")

    if all_already_in_drive:
        print(f"\n[⚡] PRE-CHECK SKIP: Seluruh konten untuk rilis '{release_tag}' SUDAH LENGKAP di stream_drive!")
        sync_metadata_from_stream_drive(workspace_root)
        return 100

    print("[+] Konten belum ada di stream_drive. Melanjutkan ke proses download & transcode...")
    return 0

def main():
    if len(sys.argv) < 2:
        print("Penggunaan:")
        print("  process_release_archive.py <download_dir> <release_tag> [release_body_file]")
        print("  process_release_archive.py --plan <release_body_file> <plan_out.json>")
        print("  process_release_archive.py --encode-chunk <release_body_file> <chunk.json> [download_dir]")
        print("  process_release_archive.py --check-skip <release_tag> [release_body_file]")
        print("  process_release_archive.py --sync-metadata")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "--plan":
        if len(sys.argv) < 4:
            print("Penggunaan: process_release_archive.py --plan <release_body_file> <plan_out.json>")
            sys.exit(1)
        sys.exit(cmd_plan(sys.argv[2], sys.argv[3]))

    if mode == "--encode-chunk":
        if len(sys.argv) < 4:
            print("Penggunaan: process_release_archive.py --encode-chunk <release_body_file> <chunk.json> [download_dir]")
            sys.exit(1)
        download_dir = sys.argv[4] if len(sys.argv) > 4 else "release_downloads"
        sys.exit(cmd_encode_chunk(sys.argv[2], sys.argv[3], download_dir))

    if mode == "--check-skip":
        if len(sys.argv) < 3:
            print("Penggunaan: process_release_archive.py --check-skip <release_tag> [release_body_file]")
            sys.exit(1)
        release_tag = sys.argv[2]
        release_body_file = sys.argv[3] if len(sys.argv) > 3 else "release_body.txt"
        sys.exit(cmd_check_skip(release_tag, release_body_file))

    if mode == "--sync-metadata":
        workspace_root = os.path.abspath(os.path.dirname(__file__))
        # Exit non-zero bila gagal supaya job publish jadi merah, bukan hijau diam-diam
        sys.exit(0 if sync_metadata_from_stream_drive(workspace_root) else 1)

    # Mode lama (semua rilis dalam satu proses)
    if len(sys.argv) < 3:
        print("Penggunaan: process_release_archive.py <download_dir> <release_tag> [release_body_file]")
        sys.exit(1)
    download_dir = sys.argv[1]
    release_tag = sys.argv[2]
    release_body_file = sys.argv[3] if len(sys.argv) > 3 else "release_body.txt"

    print("==========================================================")
    print(f"📦 PIPELINE RELEASE: {release_tag}")
    print("==========================================================")

    clean_tag_upper = release_tag.strip().upper()
    if clean_tag_upper in IGNORED_RELEASE_TAGS:
        print(f"[!] Tag release '{release_tag}' adalah tag khusus (misal password). Melewati.")
        sys.exit(0)

    sys.exit(cmd_legacy(download_dir, release_tag, release_body_file))

if __name__ == "__main__":
    main()