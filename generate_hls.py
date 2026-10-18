#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys

# Pastikan semua output print langsung tampil real-time di GitHub Actions
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(line_buffering=True)

# Definisi profil target (resolusi & bitrate)
RESOLUTIONS = [
    {"name": "1080p", "width": 1920, "height": 1080, "bitrate": "5000k", "maxrate": "5350k", "bufsize": "7500k", "audio_bitrate": "192k"},
    {"name": "720p",  "width": 1280, "height": 720,  "bitrate": "2800k", "maxrate": "3000k", "bufsize": "4200k", "audio_bitrate": "128k"},
    {"name": "480p",  "width": 854,  "height": 480,  "bitrate": "1400k", "maxrate": "1500k", "bufsize": "2100k", "audio_bitrate": "128k"},
    {"name": "360p",  "width": 640,  "height": 360,  "bitrate": "800k",  "maxrate": "856k",  "bufsize": "1200k", "audio_bitrate": "96k"},
]

# ---------------------------------------------------------------------------
# Konfigurasi encoding (dapat di-override lewat environment variable)
# ---------------------------------------------------------------------------
# HLS_NATIVE_MODE   : auto | copy | encode  (perilaku rendition resolusi asli)
# HLS_NATIVE_PRESET : preset x264 untuk rendition resolusi asli
# HLS_DERIVED_PRESET: preset x264 untuk rendition turunan (resolusi lebih kecil)
# HLS_CORES          : jumlah core yang boleh dipakai encoder (default: os.cpu_count())
DEFAULT_NATIVE_MODE = os.environ.get("HLS_NATIVE_MODE", "auto")
DEFAULT_NATIVE_PRESET = os.environ.get("HLS_NATIVE_PRESET", "medium")
DEFAULT_DERIVED_PRESET = os.environ.get("HLS_DERIVED_PRESET", "veryfast")
DEFAULT_CORES = int(os.environ.get("HLS_CORES") or (os.cpu_count() or 4))

# Syarat minimal agar rendition resolusi asli boleh di-stream-copy (tanpa re-encode)
COPY_MAX_KEY_INTERVAL = float(os.environ.get("HLS_COPY_MAX_KEY_INTERVAL", "10"))
COPY_MIN_KEYFRAMES = int(os.environ.get("HLS_COPY_MIN_KEYFRAMES", "4"))
COPY_SUPPORTED_CODECS = ("h264", "avc", "avc1")


def probe_media(video_path):
    """Mengambil metadata media (resolusi, kodek video, keberadaan audio, durasi) via ffprobe."""
    info = {"width": 1280, "height": 720, "vcodec": "", "has_audio": False, "duration": 0.0}
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
        "-of", "json",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        for st in data.get("streams", []):
            ctype = st.get("codec_type")
            if ctype == "video" and not info["vcodec"]:
                info["vcodec"] = (st.get("codec_name") or "").lower()
                info["width"] = st.get("width") or info["width"]
                info["height"] = st.get("height") or info["height"]
            elif ctype == "audio" and not info["has_audio"]:
                info["has_audio"] = True
        try:
            info["duration"] = float(data.get("format", {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            info["duration"] = 0.0
    except Exception as e:
        print(f"[-] Gagal mendapatkan info video dengan ffprobe: {e}")
    return info


def get_video_info(video_path):
    """Wrapper kompatibilitas: mengembalikan (lebar, tinggi, punya_audio)."""
    info = probe_media(video_path)
    return info["width"], info["height"], info["has_audio"]


def analyze_keyframes(video_path, max_samples=120):
    """
    Mengambil daftar waktu keyframe (hanya frame key) lalu assessing apakah intervalnya
    seragam & pendek. Hasil: (uniform, daftar_waktu_keyframe).
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-skip_frame", "nokey",
        "-show_entries", "frame=pts_time",
        "-of", "csv=p=0",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except Exception as e:
        print(f"[-] Gagal menganalisis keyframe sumber: {e}")
        return False, []

    times = []
    for line in result.stdout.splitlines():
        raw = line.strip().strip(",")
        if not raw or raw.upper() == "N/A":
            continue
        try:
            times.append(float(raw.split(",")[0]))
        except ValueError:
            continue
        if len(times) >= max_samples:
            break

    if len(times) < COPY_MIN_KEYFRAMES:
        return False, times

    intervals = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not intervals:
        return False, times

    min_i, max_i = min(intervals), max(intervals)
    uniform = max_i <= COPY_MAX_KEY_INTERVAL and max_i <= (min_i * 1.6 + 0.5)
    return uniform, times


def allocate_threads(encoded_indices, cores, native_index):
    """
    Membagi jumlah core yang tersedia ke tiap encoder (per rendition yang di-encode)
    supaya tidak terjadi oversubscribe (libx264 default --> ~1.5x core per encoder).
    """
    if not encoded_indices:
        return {}
    threads = {i: 1 for i in encoded_indices}
    spare = max(0, cores - len(encoded_indices))
    # Prioritaskan rendition resolusi asli (paling berat), lalu resolusi tertinggi berikutnya
    order = sorted(encoded_indices, key=lambda i: (0 if i == native_index else 1, i))
    pos = 0
    while spare > 0 and order:
        threads[order[pos % len(order)]] += 1
        spare -= 1
        pos += 1
    return threads


def should_copy_native(native_mode, info, native_profile, keyframe_uniform):
    """
    Stream-copy hanya aman bila:
      1. Kodek video sumber H.264 (HLS/TS di browser hanya bisa H.264 + AAC),
      2. Resolusi sumber persis sama dengan profil resolusi asli,
      3. Keyframe sumber seragam & cukup rapat agar segmen HLS tetap rapi.
    Selain itu fallback ke encode (kualitas turun, tapi tetap kompatibel).
    """
    codec_ok = info["vcodec"] in COPY_SUPPORTED_CODECS
    res_ok = (
        native_profile is not None
        and native_profile["width"] == info["width"]
        and native_profile["height"] == info["height"]
    )

    if native_mode == "copy":
        reason = "kodek sumber bukan H.264" if not codec_ok else ("resolusi sumber tidak persis" if not res_ok else "keyframe sumber tidak seragam")
        if codec_ok and res_ok and keyframe_uniform:
            return True
        print(f"[!] Mode copy dipaksa tapi tidak aman ({reason}). Fallback ke encode.")
        return False

    if native_mode == "encode":
        return False

    if codec_ok and res_ok and keyframe_uniform:
        return True

    if not codec_ok:
        print(f"[!] Sumber memakai kodek '{info['vcodec'] or 'tidak diketahui'}' (bukan H.264) "
              f"-> rendition asli di-encode ulang ke H.264 demi kompatibilitas browser.")
    return False


def get_subtitle_streams(video_path):
    """Mendapatkan daftar stream subtitle dari video input."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "s",
        "-show_entries", "stream=index:stream_tags=language,title",
        "-of", "json",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        subtitles = []
        for i, st in enumerate(streams):
            tags = st.get("tags", {})
            lang = tags.get("language", f"sub{i+1}")
            title = tags.get("title", f"Subtitle {i+1} ({lang})")
            subtitles.append({
                "index": st["index"],
                "lang": lang,
                "title": title,
                "filename": f"sub_{i}_{lang}.vtt"
            })
        return subtitles
    except Exception:
        return []

def extract_subtitles(video_path, subtitles, output_dir):
    """Mengekstrak setiap stream subtitle menjadi file .vtt (WebVTT)."""
    for sub in subtitles:
        out_vtt = os.path.join(output_dir, sub["filename"])
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-map", f"0:{sub['index']}",
            out_vtt
        ]
        print(f"[+] Ekstrak subtitle: {sub['title']} -> {sub['filename']}")
        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[-] Gagal mengekstrak subtitle stream 0:{sub['index']}: {e.stderr.decode('utf-8', errors='ignore')}")

def update_master_playlist_with_subtitles(master_playlist_path, subtitles):
    """Memastikan master.m3u8 bersih dari tag HLS subtitle m3u8 yang tidak kompatibel."""
    if not os.path.exists(master_playlist_path):
        return

    with open(master_playlist_path, "r") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]

    clean_lines = []
    for line in lines:
        if line.startswith("#EXT-X-MEDIA:TYPE=SUBTITLES"):
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            parts = [p for p in line.split(",") if not p.startswith('SUBTITLES=')]
            clean_lines.append(",".join(parts))
        else:
            clean_lines.append(line)

    with open(master_playlist_path, "w") as f:
        f.write("\n".join(clean_lines) + "\n")

def generate_poster_thumbnail(output_dir, num_streams):
    """Mengekstrak frame gambar dari segmen .ts berukuran byte paling Besar di varian resolusi tertinggi (stream_0)."""
    # Resolusi tertinggi berada pada varian stream pertama (stream_0)
    largest_stream_dir = os.path.join(output_dir, "stream_0")
    poster_path = os.path.join(output_dir, "poster.jpg")

    if os.path.exists(largest_stream_dir):
        # Cari semua file segmen .ts
        ts_files = [
            os.path.join(largest_stream_dir, f)
            for f in os.listdir(largest_stream_dir)
            if f.endswith(".ts")
        ]

        if ts_files:
            # Urutkan berdasarkan ukuran file (st_size) TERBESAR
            largest_ts = max(ts_files, key=lambda f: os.path.getsize(f))
            largest_size_kb = round(os.path.getsize(largest_ts) / 1024, 2)
            print(f"[+] Segmen .ts terbesar ditemukan: {os.path.basename(largest_ts)} ({largest_size_kb} KB)")

            cmd = [
                "ffmpeg", "-y",
                "-i", largest_ts,
                "-vframes", "1",
                "-q:v", "2",
                poster_path
            ]
            try:
                subprocess.run(cmd, capture_output=True, check=True)
                print(f"[+] Sampul poster berhasil diekstrak dari segmen terbesar ({os.path.basename(largest_ts)}): poster.jpg")
                return "poster.jpg"
            except subprocess.CalledProcessError as e:
                print(f"[-] Gagal membuat sampul poster: {e.stderr.decode('utf-8', errors='ignore')}")
    return None

import re

def extract_web_source_from_filename(filename):
    """Mendeteksi domain websumber otomatis dari nama file atau folder (misal: [doronime.id] -> Doronime.id)."""
    match = re.search(r'\[([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\]', filename)
    if match:
        domain = match.group(1).strip()
        provider_name = domain.capitalize() if not domain.startswith("http") else domain
        # Format nama provider rapi (misal: doronime.id -> Doronime.id)
        if "." in provider_name:
            parts = provider_name.split(".")
            provider_name = parts[0].capitalize() + "." + ".".join(parts[1:])
        return {
            "provider": provider_name,
            "url": f"https://{domain}" if not domain.startswith("http") else domain,
            "icon": f"https://{domain}/favicon.ico",
            "note": "Sumber mentah otomatis diekstrak dari nama file"
        }
    return None

# Token musim/part yang HARUS dibuang sebelum mencari nomor episode.
# Contoh: 'S2', 'S02', 'Season 3', 'Part 2', 'Cour 1', 'Vol 2'.
# Lookbehind '[^a-z0-9]' mencegah nama seperti '5toubun' ikut terpotong.
# Tanpa ini, nama 'S2 01' akan terbaca sebagai "Episode 2" (dari angka musim),
# sehingga SELURUH episode satu musim menabrak folder HLS yang sama lalu saling menimpa.
_SEASON_TOKEN_RE = re.compile(
    r'(?<![a-z0-9])(?:season|ss|s|part|cour|vol)\s*[-_.]?\s*(\d{1,2})(?!\d)',
    re.IGNORECASE
)


def clean_episode_name(raw_name):
    """Memangkas nama folder/file episode yang panjang menjadi ringkas (misal: 'Episode 04' atau 'Episode 14')."""
    clean_name = raw_name.replace("_hls", "").strip()

    # 1. Jika ada tag pembuka seperti [Doronime.id] atau [480p] [h265], bersihkan tag tanda kurung siku/bisa
    no_brackets = re.sub(r'\[.*?\]|\(.*?\)', '', clean_name).strip()
    no_brackets = re.sub(r'\s+', ' ', no_brackets).strip()

    # 2. Buang token musim/part lebih dulu, baru cari nomor episode.
    #    'S2 01' -> '01' -> 'Episode 01' (bukan 'Episode 2')
    seasonless = re.sub(r'\s+', ' ', _SEASON_TOKEN_RE.sub(' ', no_brackets)).strip()
    basis = seasonless or no_brackets

    # 3. Cek apakah ada pola nomor episode seperti 'Episode 04', 'Ep 14', 'E04', atau angka '04' di dalam nama
    ep_match = re.search(r'(?:episode|ep|e)?\s*(\d+)', basis, re.IGNORECASE)

    if ep_match:
        ep_num = ep_match.group(1)
        if not basis or basis.isdigit() or len(basis.split()) <= 2:
            return f"Episode {ep_num}"

    if basis:
        return basis

    return clean_name


# Folder output HLS yang sudah dipakai VIDEO LAIN dalam satu proses berjalan.
# Kunci = path output huruf kecil, nilai = nama file sumber yang memakainya.
_CLAIMED_OUTPUT_DIRS = {}


def _slug_for_output_suffix(raw_filename):
    """Mengubah nama file sumber menjadi sufiks folder yang aman & ringkas."""
    slug = re.sub(r'[^A-Za-z0-9]+', '_', raw_filename).strip('_')
    return slug[:40] or "part"


def resolve_unique_output_dir(prod_base_dir, clean_ep_folder, raw_filename):
    """
    Menentukan folder output HLS, dan mencegah TABRAKAN NAMA.

    Dua file sumber berbeda bisa dipetakan ke nama episode yang sama (misal
    'S2 01' dan 'S2 02' sama-sama jadi 'Episode 02'). Tanpa guard, video kedua
    akan melihat folder milik video pertama lalu dilewati sebagai "sudah ada",
    sehingga episode hilang tanpa error sama sekali.

    Di sini tabrakan diperjelas: folder diberi sufiks dari nama file sumber.
    """
    output_dir = os.path.join(prod_base_dir, clean_ep_folder + "_hls")
    claim_key = os.path.normcase(output_dir)
    prior = _CLAIMED_OUTPUT_DIRS.get(claim_key)

    if prior is not None and prior != raw_filename:
        suffix = _slug_for_output_suffix(raw_filename)
        base = clean_ep_folder + "_" + suffix
        candidate = os.path.join(prod_base_dir, base + "_hls")
        counter = 2
        while (os.path.normcase(candidate) in _CLAIMED_OUTPUT_DIRS
               and _CLAIMED_OUTPUT_DIRS[os.path.normcase(candidate)] != raw_filename):
            candidate = os.path.join(prod_base_dir, f"{base}_{counter}_hls")
            counter += 1
        print(f"[⚠️] TABRAKAN NAMA: '{raw_filename}' menghasilkan nama episode '{clean_ep_folder}' "
              f"yang sudah dipakai oleh '{prior}'.")
        print(f"[⚠️] Folder output diubah menjadi '{os.path.basename(candidate)}' "
              f"agar episode '{raw_filename}' tidak menimpa.")
        output_dir = candidate
        claim_key = os.path.normcase(output_dir)

    _CLAIMED_OUTPUT_DIRS[claim_key] = raw_filename
    return output_dir


def update_library_metadata(workspace_dir):
    """Memindai seluruh folder kategori (anime, donghua, dll) serta file media HLS."""
    media_library = []
    category_folders = []

    # Load Aliases & Sources Dictionary jika ada
    aliases_dict = {}
    sources_dict = {}
    aliases_path = os.path.join(workspace_dir, "aliases.json")
    if os.path.exists(aliases_path):
        try:
            with open(aliases_path, "r", encoding="utf-8") as f:
                data_json = json.load(f)
                aliases_dict = data_json.get("aliases", {})
                sources_dict = data_json.get("sources", {})
        except Exception as e:
            print(f"[-] Gagal membaca aliases.json: {e}")

    # Folder yang akan diabaikan dari pemindaian
    IGNORED_DIRS = {"RAW", "RAW_TEMP", "RAW_TEMP_EXTRACT", "release_downloads", ".git", "assets", ".vscode", ".gemini", "__pycache__"}

    # Ambil semua folder fisik di root workspace sebagai folder kategori awal
    for entry in os.listdir(workspace_dir):
        full_p = os.path.join(workspace_dir, entry)
        if os.path.isdir(full_p) and entry not in IGNORED_DIRS and not entry.startswith("."):
            category_folders.append(entry)

    for root, dirs, files in os.walk(workspace_dir):
        # Filter folder agar tidak masuk ke folder yang diabaikan
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]

        if "master.m3u8" in files:
            rel_path = os.path.relpath(root, start=workspace_dir)
            vtt_files = [f for f in files if f.endswith(".vtt")]
            poster_file = "poster.jpg" if "poster.jpg" in files else None

            # Cari apakah ada alias & source yang cocok dengan kata di dalam path
            matched_aliases = []
            matched_source = None
            lower_rel_path = rel_path.lower()
            for key, alias_list in aliases_dict.items():
                if key.lower() in lower_rel_path:
                    matched_aliases.extend(alias_list)

            for src_key, src_val in sources_dict.items():
                if src_key.lower() in lower_rel_path:
                    matched_source = src_val

            raw_folder_name = os.path.basename(root)

            # Jika belum ada di sources_dict, ekstrak websumber otomatis dari tag kurung siku nama folder/file
            if not matched_source:
                matched_source = extract_web_source_from_filename(raw_folder_name)

            display_name = clean_episode_name(raw_folder_name)

            # Format path murni tanpa duplikasi data alias & sources
            item = {
                "id": rel_path.replace(os.sep, "_"),
                "name": display_name,
                "folder": os.path.dirname(rel_path),
                "path": rel_path,
                "master_url": os.path.join(rel_path, "master.m3u8"),
                "poster_url": os.path.join(rel_path, poster_file) if poster_file else None,
                "subtitles": [os.path.join(rel_path, v) for v in vtt_files],
                "type": "hls_stream"
            }
            media_library.append(item)

    # Muat metadata.json yang sudah ada agar data episode lama tidak tertimpa/hilang
    metadata_path = os.path.join(workspace_dir, "metadata.json")
    existing_media_map = {}
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                old_meta = json.load(f)
                for m in old_meta.get("media", []):
                    existing_media_map[m["id"]] = m
                for c in old_meta.get("categories", []):
                    if c not in category_folders:
                        category_folders.append(c)
        except Exception:
            pass

    # Masukkan media yang baru dipindai (akan menimpa versi lama jika id-nya sama)
    for item in media_library:
        existing_media_map[item["id"]] = item
        cat_root = item["folder"].split("/")[0] if "/" in item["folder"] else item["folder"]
        if cat_root and cat_root not in category_folders:
            category_folders.append(cat_root)

    # Urutkan berdasarkan folder dan nama
    final_media_list = list(existing_media_map.values())
    final_media_list.sort(key=lambda x: (x.get("folder", ""), x.get("name", "")))

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump({
            "categories": sorted(category_folders),
            "media": final_media_list,
            "updated_at": str(os.path.getmtime(workspace_dir))
        }, f, indent=2, ensure_ascii=False)
    print(f"[+] Metadata perpustakaan diperbarui ({len(final_media_list)} total media): {metadata_path}")

def find_existing_case_insensitive_dir(base_dir, relative_path):
    """Mencari direktori fisik secara case-insensitive agar tidak membuat folder ganda (misal: GSYOS vs gsyos)."""
    parts = [p for p in relative_path.split(os.sep) if p]
    current = base_dir

    for part in parts:
        found_match = False
        if os.path.exists(current) and os.path.isdir(current):
            try:
                for existing in os.listdir(current):
                    if existing.lower() == part.lower() and os.path.isdir(os.path.join(current, existing)):
                        current = os.path.join(current, existing)
                        found_match = True
                        break
            except Exception:
                pass
        if not found_match:
            current = os.path.join(current, part.lower())
    return current


def normalize_rel_path(path):
    """Normalisasi path relatif (slash '/', huruf kecil) untuk perbandingan lintas platform."""
    return str(path).replace(os.sep, "/").strip("/").lower()


def load_skip_list(skip_list_file):
    """Membaca daftar master.m3u8 yang sudah ada di stream_drive (untuk resume)."""
    existing = set()
    if not skip_list_file or not os.path.exists(skip_list_file):
        return existing
    with open(skip_list_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                existing.add(normalize_rel_path(line))
    return existing


def stage_completed_output(workspace_root, output_dir, staging_dir):
    """
    Memindahkan folder HLS yang SUDAH SELESAI ke folder staging (HLS_OUT) segera setelah
    satu episode beres — bukan menunggu seluruh shard selesai.

    Tujuannya: kalau job dibunuh di tengah (mis. timeout), episode yang sudah jadi tetap
    tersimpan di HLS_OUT dan bisa ter-upload sebagai artifact, bukan hilang semua.
    """
    if not staging_dir or not output_dir or not os.path.isdir(output_dir):
        return False
    try:
        abs_staging = staging_dir if os.path.isabs(staging_dir) else os.path.join(workspace_root, staging_dir)
        abs_out = output_dir if os.path.isabs(output_dir) else os.path.join(workspace_root, output_dir)
        rel = os.path.relpath(abs_out, start=workspace_root)
        if rel.startswith(".."):
            # di luar workspace -> tidak bisa direferensikan relatif, salin apa adanya
            target = os.path.join(abs_staging, os.path.basename(abs_out))
        else:
            target = os.path.join(abs_staging, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(abs_out, target)
        print(f"[📦] Di-stage: {rel} -> {os.path.relpath(target, start=workspace_root)}")
        return True
    except Exception as e:
        print(f"[-] Gagal men-stage '{output_dir}': {e}")
        return False


def process_single_video(video_path, opts):
    """Memproses 1 file video ke format HLS ABR dan menghapus file sumber jika berhasil 100%."""
    video_path = video_path.strip("'\"")
    if not os.path.exists(video_path):
        print(f"\n[-] Error: File video '{video_path}' tidak ditemukan.")
        return "failed"

    print(f"\n========================================================")
    print(f"[+] Memproses File: {os.path.basename(video_path)}")
    print(f"========================================================")

    # Dapatkan metadata media (resolusi, kodek, audio, durasi)
    info = probe_media(video_path)
    in_w, in_h, has_audio = info["width"], info["height"], info["has_audio"]
    subtitles = get_subtitle_streams(video_path)
    print(f"[+] Deteksi input: {in_w}x{in_h} | Kodek: {info['vcodec']} | Audio: {'Ada' if has_audio else 'Tidak ada/Bisu'} | Durasi: {info['duration']:.1f}s")
    if subtitles:
        print(f"[+] Ditemukan {len(subtitles)} stream subtitle.")

    # Filter profil yang tidak melebihi tinggi (height) video asli
    filtered_profiles = [res for res in RESOLUTIONS if res["height"] <= in_h]

    # Jika video input lebih kecil dari 360p (misal 240p), sertakan profil terdekat agar tetap bisa di-generate
    if not filtered_profiles:
        filtered_profiles = [RESOLUTIONS[-1]]

    # Tentukan folder output
    abs_video_path = os.path.abspath(video_path)
    workspace_root = os.path.abspath(os.path.dirname(__file__))
    rel_video_path = os.path.relpath(abs_video_path, start=workspace_root)

    # Jika video berada di dalam folder RAW, arahkan hasil output ke folder produksi di luar RAW
    rel_parts = rel_video_path.split(os.sep)
    if rel_parts[0] == "RAW" and len(rel_parts) > 1:
        target_rel_dir = os.path.dirname(os.path.join(*rel_parts[1:]))
        prod_base_dir = find_existing_case_insensitive_dir(workspace_root, target_rel_dir)
    else:
        prod_base_dir = os.path.dirname(abs_video_path)

    raw_filename = os.path.splitext(os.path.basename(video_path))[0]
    clean_ep_name = clean_episode_name(raw_filename)

    # Jika clean_ep_name menghasilkan nama ringkas (misal 'Episode 04' atau 'Episode 14'), gunakan sebagai nama folder HLS
    clean_ep_folder = clean_ep_name if clean_ep_name else raw_filename
    # resolve_unique_output_dir mencegah dua file sumber berbeda menabrak folder HLS yang sama
    output_dir = resolve_unique_output_dir(prod_base_dir, clean_ep_folder, raw_filename)

    master_playlist = os.path.join(output_dir, "master.m3u8")
    stream_playlist = os.path.join(output_dir, "stream_%v", "playlist.m3u8")
    segment_filename = os.path.join(output_dir, "stream_%v", "segment_%03d.ts")

    num_streams = len(filtered_profiles)
    rel_master = os.path.relpath(master_playlist, start=workspace_root)

    # Cek apakah episode ini sudah pernah selesai di stream_drive (resume / idempotent).
    # Kunci = folder tujuan (dari release body) + folder episode, huruf kecil.
    if normalize_rel_path(rel_master) in opts["skip_list"]:
        print(f"[⏭️] Lewati: HLS untuk '{rel_master}' sudah ada di stream_drive.")
        return "skipped"

    # Cek apakah master playlist sudah ada di workspace (video HLS sudah pernah di-generate di run sebelumnya)
    if os.path.exists(master_playlist):
        print("\n[!] Master playlist HLS sudah ada di direktori output.")
        print("[+] Melewati proses encoding video/audio (segment .ts tidak akan di-overwrite).")
        if subtitles:
            extract_subtitles(video_path, subtitles, output_dir)
            update_master_playlist_with_subtitles(master_playlist, subtitles)
        generate_poster_thumbnail(output_dir, num_streams)
        update_library_metadata(workspace_root)
        print(f"[✓] Selesai! Master playlist HLS: {master_playlist}")

        # Hapus file sumber asli jika HLS master sudah ada & berhasil
        if os.path.exists(abs_video_path):
            try:
                os.remove(abs_video_path)
                print(f"[🗑️] File video sumber berhasil dihapus: {os.path.basename(abs_video_path)}")
            except Exception as e:
                print(f"[-] Gagal menghapus file sumber: {e}")
        return "success"

    # Putuskan apakah rendition resolusi asli boleh di-stream-copy (tanpa re-encode)
    native_index = 0 if filtered_profiles[0]["height"] == in_h else None
    keyframe_uniform = False
    if native_index is not None and opts["native_mode"] != "encode":
        keyframe_uniform, kf_times = analyze_keyframes(video_path)
        if kf_times:
            span = (kf_times[-1] - kf_times[0]) / max(1, len(kf_times) - 1)
            print(f"[+] Keyframe sumber: {len(kf_times)} sampel, interval rata-rata ~{span:.2f}s "
                  f"({'seragam' if keyframe_uniform else 'tidak seragam/terlalu jarang'}).")
    copy_native = should_copy_native(
        opts["native_mode"], info,
        filtered_profiles[native_index] if native_index is not None else None,
        keyframe_uniform
    )
    # copy_index = rendition yang benar-benar di-stream-copy (None = semua di-encode)
    copy_index = 0 if copy_native else None

    print("[+] Profil kualitas yang akan dibuat:")
    for i, p in enumerate(filtered_profiles):
        if i == copy_index:
            badge = "STREAM-COPY (tanpa re-encode, kualitas identik sumber)"
        else:
            badge = f"x264 preset {opts['native_preset'] if i == native_index else opts['derived_preset']}"
        print(f"    - {p['name']} ({p['width']}x{p['height']}) -> {badge}")

    os.makedirs(output_dir, exist_ok=True)
    print(f"[+] Direktori output: {output_dir}")

    # Bangun argumen perintah FFmpeg
    encode_indices = [i for i in range(num_streams) if i != copy_index]
    threads_map = allocate_threads(encode_indices, opts["cores"], native_index)

    filter_parts = []
    if encode_indices:
        split_labels = "".join([f"[vs{i}]" for i in encode_indices])
        filter_parts.append(f"[0:v]split={len(encode_indices)}{split_labels}")
        for i in encode_indices:
            p = filtered_profiles[i]
            preset = opts["native_preset"] if i == native_index else opts["derived_preset"]
            filter_parts.append(
                f"[vs{i}]scale=w={p['width']}:h={p['height']}:force_original_aspect_ratio=decrease,"
                f"pad={p['width']}:{p['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}out]"
            )
            print(f"[+] Encoder {p['name']}: preset={preset}, threads={threads_map.get(i, 1)}")

    ffmpeg_cmd = ["ffmpeg", "-y", "-i", video_path]
    if filter_parts:
        ffmpeg_cmd.extend(["-filter_complex", ";".join(filter_parts)])

    var_stream_maps = []

    for i, p in enumerate(filtered_profiles):
        preset = opts["native_preset"] if i == native_index else opts["derived_preset"]

        # Map video
        if i == copy_index:
            # Stream-copy: kualitas 100% identik dengan sumber, tanpa biaya CPU encode
            ffmpeg_cmd.extend(["-map", "0:v:0", f"-c:v:{i}", "copy"])
        else:
            ffmpeg_cmd.extend([
                "-map", f"[v{i}out]",
                f"-c:v:{i}", "libx264",
                f"-b:v:{i}", p["bitrate"],
                f"-maxrate:v:{i}", p["maxrate"],
                f"-bufsize:v:{i}", p["bufsize"],
                f"-preset:{i}", preset,
                f"-threads:{i}", str(threads_map.get(i, 1)),
                f"-g:{i}", "48",
                f"-keyint_min:{i}", "48",
                f"-sc_threshold:{i}", "0"
            ])

        # Map audio jika video memiliki stream audio
        if has_audio:
            ffmpeg_cmd.extend([
                "-map", "0:a:0?",
                f"-c:a:{i}", "aac",
                f"-b:a:{i}", p["audio_bitrate"]
            ])
            var_stream_maps.append(f"v:{i},a:{i}")
        else:
            var_stream_maps.append(f"v:{i}")

    ffmpeg_cmd.extend([
        "-var_stream_map", " ".join(var_stream_maps),
        "-master_pl_name", "master.m3u8",
        "-f", "hls",
        "-hls_time", "6",
        "-hls_playlist_type", "vod",
        "-hls_segment_filename", segment_filename,
        stream_playlist
    ])

    # Ekstrak subtitle ke format WebVTT (.vtt)
    if subtitles:
        extract_subtitles(video_path, subtitles, output_dir)

    print("\n[+] Menjalankan FFmpeg...")
    try:
        subprocess.run(ffmpeg_cmd, check=True)
        # Tambahkan subtitle ke master playlist
        if subtitles:
            update_master_playlist_with_subtitles(master_playlist, subtitles)
        generate_poster_thumbnail(output_dir, num_streams)
        update_library_metadata(workspace_root)
        print(f"\n[✓] Berhasil! Master playlist HLS tersimpan di: {master_playlist}")

        # HAPUS OTOMATIS FILE VIDEO SUMBER HANYA JIKA PROSES BERHASIL 100%
        if os.path.exists(abs_video_path):
            try:
                os.remove(abs_video_path)
                print(f"[🗑️] File video sumber berhasil dihapus otomatis: {os.path.basename(abs_video_path)}")
            except Exception as e:
                print(f"[-] Gagal menghapus file video sumber: {e}")

        # Segera amankan hasilnya ke staging supaya tetap ter-upload walau job dibunuh nanti
        stage_completed_output(workspace_root, output_dir, opts.get("stage_dir"))
        return "success"
    except subprocess.CalledProcessError as e:
        print(f"\n[-] Proses HLS gagal diproses oleh FFmpeg. Error code: {e.returncode}")
        print("[!] File video sumber TIDAK dihapus karena terjadi kesalahan.")
        return "failed"


def main():
    parser = argparse.ArgumentParser(description="Script otomatis generator HLS ABR & Subtitle Extractor untuk Video Streaming (Multi-input & Auto-cleanup)")
    parser.add_argument("video_paths", nargs="+", help="Satu atau banyak path ke file video input (misal: video1.mkv video2.mkv *.mkv)")
    parser.add_argument("--native-mode", choices=["auto", "copy", "encode"], default=DEFAULT_NATIVE_MODE,
                        help="auto=stream-copy bila aman (default), copy=paksa, encode=tanpa stream-copy")
    parser.add_argument("--native-preset", default=DEFAULT_NATIVE_PRESET,
                        help=f"Preset x264 untuk rendition resolusi asli (default: {DEFAULT_NATIVE_PRESET})")
    parser.add_argument("--derived-preset", default=DEFAULT_DERIVED_PRESET,
                        help=f"Preset x264 untuk rendition turunan (default: {DEFAULT_DERIVED_PRESET})")
    parser.add_argument("--cores", type=int, default=DEFAULT_CORES,
                        help=f"Jumlah core yang boleh dipakai untuk encoding (default: {DEFAULT_CORES})")
    parser.add_argument("--skip-list", default=os.environ.get("HLS_SKIP_LIST", ""),
                        help="File berisi daftar master.m3u8 yang sudah ada di stream_drive (resume)")
    parser.add_argument("--stage-dir", default=os.environ.get("HLS_STAGE_DIR", ""),
                        help="Folder staging (mis. HLS_OUT). Kalau diisi, tiap episode yang selesai "
                             "langsung dipindahkan ke sini supaya tetap ter-upload walau job dihentikan.")
    args = parser.parse_args()

    opts = {
        "native_mode": args.native_mode,
        "native_preset": args.native_preset,
        "derived_preset": args.derived_preset,
        "cores": max(1, args.cores),
        "skip_list": load_skip_list(args.skip_list),
        "stage_dir": args.stage_dir,
    }

    total_files = len(args.video_paths)
    if opts["skip_list"]:
        print(f"[+] Resume aktif: {len(opts['skip_list'])} episode sudah ada di stream_drive.")
    print(f"[+] Mode rendition asli: {opts['native_mode']} | preset asli={opts['native_preset']} "
          f"turunan={opts['derived_preset']} | cores={opts['cores']}")

    print(f"\n========================================================")
    print(f"[🚀] Menerima {total_files} file video untuk diproses ke HLS ABR")
    print(f"========================================================")

    successful_files = 0
    skipped_files = 0
    failed_files = 0

    for idx, path in enumerate(args.video_paths, 1):
        print(f"\n>>> Progres: [{idx}/{total_files}] <<<")
        try:
            result = process_single_video(path, opts)
        except Exception as e:
            # Fail-soft: satu episode bermasalah tidak boleh menghentikan seluruh batch
            print(f"\n[-] Error tak terduga saat memproses '{path}': {e}")
            result = "failed"
        if result == "success":
            successful_files += 1
        elif result == "skipped":
            skipped_files += 1
        else:
            failed_files += 1

    print(f"\n========================================================")
    print(f"[🏁] SELESAI! {successful_files} berhasil, {skipped_files} dilewati (sudah ada), "
          f"{failed_files} gagal dari {total_files} file video.")
    print(f"========================================================")

    # Exit code 2 = ada episode gagal (dipakai pipeline agar tetap lanjut push hasil parsial)
    sys.exit(2 if failed_files else 0)


if __name__ == "__main__":
    main()