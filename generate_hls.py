#!/usr/bin/env python3
import json
import os
import subprocess
import sys

# Definisi profil target (resolusi & bitrate)
RESOLUTIONS = [
    {"name": "1080p", "width": 1920, "height": 1080, "bitrate": "5000k", "maxrate": "5350k", "bufsize": "7500k", "audio_bitrate": "192k"},
    {"name": "720p",  "width": 1280, "height": 720,  "bitrate": "2800k", "maxrate": "3000k", "bufsize": "4200k", "audio_bitrate": "128k"},
    {"name": "480p",  "width": 854,  "height": 480,  "bitrate": "1400k", "maxrate": "1500k", "bufsize": "2100k", "audio_bitrate": "128k"},
    {"name": "360p",  "width": 640,  "height": 360,  "bitrate": "800k",  "maxrate": "856k",  "bufsize": "1200k", "audio_bitrate": "96k"},
]

def get_video_info(video_path):
    """Mendapatkan resolusi video input menggunakan ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return stream["width"], stream["height"]
    except Exception as e:
        print(f"[-] Gagal mendapatkan info video dengan ffprobe: {e}")
        sys.exit(1)

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

def main():
    if len(sys.argv) > 1:
        video_path = sys.argv[1].strip()
    else:
        video_path = input("Masukkan path file video: ").strip()

    # Bersihkan kutipan (quotes) jika ada
    video_path = video_path.strip("'\"")

    if not os.path.exists(video_path):
        print(f"[-] File '{video_path}' tidak ditemukan!")
        sys.exit(1)

    # Dapatkan resolusi asli video & stream subtitle
    in_w, in_h = get_video_info(video_path)
    subtitles = get_subtitle_streams(video_path)
    print(f"[+] Deteksi resolusi input: {in_w}x{in_h}")
    if subtitles:
        print(f"[+] Ditemukan {len(subtitles)} stream subtitle.")

    # Filter profil yang tidak melebihi tinggi (height) video asli
    filtered_profiles = [res for res in RESOLUTIONS if res["height"] <= in_h]

    # Jika video input lebih kecil dari 360p (misal 240p), sertakan profil terdekat agar tetap bisa di-generate
    if not filtered_profiles:
        filtered_profiles = [RESOLUTIONS[-1]]

    print("[+] Profil kualitas yang akan dibuat:")
    for p in filtered_profiles:
        print(f"    - {p['name']} ({p['width']}x{p['height']})")

def generate_poster_thumbnail(output_dir, num_streams):
    """Mengekstrak frame gambar dari segmen .ts berukuran byte Paling Besar di varian resolusi tertinggi (stream_0)."""
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

def update_library_metadata(workspace_dir):
    """Memindai seluruh folder kategori (anime, donghua, dll) serta file media HLS."""
    media_library = []
    category_folders = []
    
    # Load Aliases Dictionary jika ada
    aliases_dict = {}
    aliases_path = os.path.join(workspace_dir, "aliases.json")
    if os.path.exists(aliases_path):
        try:
            with open(aliases_path, "r", encoding="utf-8") as f:
                aliases_dict = json.load(f).get("aliases", {})
        except Exception as e:
            print(f"[-] Gagal membaca aliases.json: {e}")

    # Folder yang akan diabaikan dari pemindaian
    IGNORED_DIRS = {"RAW", ".git", "assets", ".vscode", ".gemini", "__pycache__"}

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
            
            # Cari apakah ada alias yang cocok dengan kata di dalam path
            matched_aliases = []
            lower_rel_path = rel_path.lower()
            for key, alias_list in aliases_dict.items():
                if key.lower() in lower_rel_path:
                    matched_aliases.extend(alias_list)

            # Format path: kategori/id-judul/id-season/hls_folder
            item = {
                "id": rel_path.replace(os.sep, "_"),
                "name": os.path.basename(root).replace("_hls", ""),
                "folder": os.path.dirname(rel_path),
                "path": rel_path,
                "master_url": os.path.join(rel_path, "master.m3u8"),
                "poster_url": os.path.join(rel_path, poster_file) if poster_file else None,
                "subtitles": [os.path.join(rel_path, v) for v in vtt_files],
                "aliases": matched_aliases,
                "type": "hls_stream"
            }
            media_library.append(item)

    metadata_path = os.path.join(workspace_dir, "metadata.json")
    with open(metadata_path, "w") as f:
        json.dump({
            "categories": category_folders,
            "media": media_library, 
            "updated_at": str(os.path.getmtime(workspace_dir))
        }, f, indent=2)
    print(f"[+] Metadata perpustakaan diperbarui: {metadata_path}")

def main():
    if len(sys.argv) > 1:
        video_path = sys.argv[1].strip()
    else:
        video_path = input("Masukkan path file video: ").strip()

    # Bersihkan kutipan (quotes) jika ada
    video_path = video_path.strip("'\"")

    if not os.path.exists(video_path):
        print(f"[-] File '{video_path}' tidak ditemukan!")
        sys.exit(1)

    # Dapatkan resolusi asli video & stream subtitle
    in_w, in_h = get_video_info(video_path)
    subtitles = get_subtitle_streams(video_path)
    print(f"[+] Deteksi resolusi input: {in_w}x{in_h}")
    if subtitles:
        print(f"[+] Ditemukan {len(subtitles)} stream subtitle.")

    # Filter profil yang tidak melebihi tinggi (height) video asli
    filtered_profiles = [res for res in RESOLUTIONS if res["height"] <= in_h]

    # Jika video input lebih kecil dari 360p (misal 240p), sertakan profil terdekat agar tetap bisa di-generate
    if not filtered_profiles:
        filtered_profiles = [RESOLUTIONS[-1]]

    print("[+] Profil kualitas yang akan dibuat:")
    for p in filtered_profiles:
        print(f"    - {p['name']} ({p['width']}x{p['height']})")

    # Tentukan folder output
    abs_video_path = os.path.abspath(video_path)
    workspace_root = os.path.abspath(os.path.dirname(__file__))
    rel_video_path = os.path.relpath(abs_video_path, start=workspace_root)
    
    # Jika video berada di dalam folder RAW, arahkan hasil output ke folder produksi di luar RAW
    rel_parts = rel_video_path.split(os.sep)
    if rel_parts[0] == "RAW" and len(rel_parts) > 1:
        target_rel_dir = os.path.dirname(os.path.join(*rel_parts[1:]))
        prod_base_dir = os.path.join(workspace_root, target_rel_dir)
    else:
        prod_base_dir = os.path.dirname(abs_video_path)

    filename_without_ext = os.path.splitext(os.path.basename(video_path))[0]
    output_dir = os.path.join(prod_base_dir, filename_without_ext + "_hls")
    os.makedirs(output_dir, exist_ok=True)

    print(f"[+] Direktori output: {output_dir}")

    master_playlist = os.path.join(output_dir, "master.m3u8")
    stream_playlist = os.path.join(output_dir, "stream_%v", "playlist.m3u8")
    segment_filename = os.path.join(output_dir, "stream_%v", "segment_%03d.ts")

    # Cek apakah master playlist sudah ada (video HLS sudah pernah di-generate sebelumnya)
    if os.path.exists(master_playlist):
        print("\n[!] Master playlist HLS sudah ada di direktori output.")
        print("[+] Melewati proses encoding video/audio (segment .ts tidak akan di-overwrite).")
        if subtitles:
            extract_subtitles(video_path, subtitles, output_dir)
            update_master_playlist_with_subtitles(master_playlist, subtitles)
        generate_poster_thumbnail(output_dir, num_streams)
        update_library_metadata(workspace_root)
        print(f"\n[✓] Selesai! Master playlist HLS: {master_playlist}")
        return

    # Ekstrak subtitle ke format WebVTT (.vtt)
    if subtitles:
        extract_subtitles(video_path, subtitles, output_dir)

    # Bangun argumen perintah FFmpeg
    num_streams = len(filtered_profiles)
    filter_complex_split = f"[0:v]split={num_streams}" + "".join([f"[v{i+1}]" for i in range(num_streams)])
    filter_complex_scales = []

    for i, p in enumerate(filtered_profiles):
        filter_complex_scales.append(f"[v{i+1}]scale=w={p['width']}:h={p['height']}:force_original_aspect_ratio=decrease,pad={p['width']}:{p['height']}:(ow-iw)/2:(oh-ih)/2[v{i+1}out]")

    filter_complex = filter_complex_split + ";" + ";".join(filter_complex_scales)

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-filter_complex", filter_complex
    ]

    var_stream_maps = []

    for i, p in enumerate(filtered_profiles):
        # Map video
        ffmpeg_cmd.extend([
            "-map", f"[v{i+1}out]",
            f"-c:v:{i}", "libx264",
            f"-b:v:{i}", p["bitrate"],
            f"-maxrate:v:{i}", p["maxrate"],
            f"-bufsize:v:{i}", p["bufsize"],
            f"-preset:{i}", "medium",
            f"-g:{i}", "48",
            f"-keyint_min:{i}", "48",
            f"-sc_threshold:{i}", "0"
        ])
        
        # Map audio (gunakan audio stream pertama untuk setiap varian video)
        ffmpeg_cmd.extend([
            "-map", "0:a:0",
            f"-c:a:{i}", "aac",
            f"-b:a:{i}", p["audio_bitrate"]
        ])

        var_stream_maps.append(f"v:{i},a:{i}")

    ffmpeg_cmd.extend([
        "-var_stream_map", " ".join(var_stream_maps),
        "-master_pl_name", "master.m3u8",
        "-f", "hls",
        "-hls_time", "6",
        "-hls_playlist_type", "vod",
        "-hls_segment_filename", segment_filename,
        stream_playlist
    ])

    print("\n[+] Menjalankan FFmpeg...")
    try:
        subprocess.run(ffmpeg_cmd, check=True)
        # Tambahkan subtitle ke master playlist
        if subtitles:
            update_master_playlist_with_subtitles(master_playlist, subtitles)
        update_library_metadata(workspace_root)
        print(f"\n[✓] Berhasil! Master playlist HLS tersimpan di: {master_playlist}")
    except subprocess.CalledProcessError as e:
        print(f"\n[-] Proses HLS gagal diproses oleh FFmpeg. Error code: {e.returncode}")

if __name__ == "__main__":
    main()
