#!/usr/bin/env python3
"""
Release Manager (TUNDA 4) — UI lokal untuk menyusun / membuat / memperbarui
GitHub Release RAW yang diproses pipeline HLS.

Fitur utama:
  - Daftar & muat release yang sudah ada.
  - Susun body release sesuai format pipeline (cover, resolusi, episode,
    aliases, path arsip, sumber/icon provider).
  - Judul/alias otomatis dari input judul; toggle [RAW] / [RAW HARDSUB].
  - Kategori/tipe/season dengan nilai tambahan yang disimpan di
    `release_ui.ini` lokal.
  - Tag `password` punya editor variasi khusus; Metadata-Guide read-only.
  - Probe resolusi backend disiapkan untuk masa depan, tetapi UI saat ini
    TIDAK memanggil atau menampilkan tombol deteksi.
  - Cari kandidat ikon provider dari halaman sumber; user memilih sendiri.
  - Pratinjau (dry-run) sebelum membuat/mengubah release.
  - Buat release baru lewat draft -> upload aset -> publish, supaya pipeline
    tidak terpicu saat aset masih setengah terunggah.
  - Simpan judul/cover/sumber ke aliases.json (commit/push tidak lagi
    diekspos sebagai opsi UI).
  - Tidak menyediakan tombol/mekanisme unduh media.

Jalankan:
    export MAL_CLIENT_ID="..."       # opsional, untuk fitur MAL
    gh auth login                     # atau set GH_TOKEN
    python3 release_ui.py
    # buka http://127.0.0.1:8765

Keamanan:
  - Server default hanya bind ke 127.0.0.1.
  - Setiap POST wajib menyertakan token CSRF dari halaman UI.
  - Token/API key hanya dibaca dari environment (atau .env lokal), tidak
    pernah ditulis ke repo.
"""

from __future__ import annotations

import argparse
import configparser
import copy
import html
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE_FILE = ROOT / "tools" / "release_ui.html"
ALIASES_FILE = ROOT / "aliases.json"
MAL_CACHE_FILE = ROOT / "mal_cache.json"
METADATA_FILE = ROOT / "metadata.json"
CONFIG_FILE = ROOT / "release_ui.ini"
DEFAULT_EPISODE_TYPES = ("Episodes", "Compilation")
SPECIAL_METADATA_TAG = "metadata-guide"
SPECIAL_PASSWORD_TAG = "password"

GH_REPO = ""
UI_TOKEN = secrets.token_urlsafe(24)
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
APPLY_LOCK = threading.Lock()
MAL_LOCK = threading.Lock()
MAL_LAST_REQUEST = 0.0
MAX_ASSET_BYTES = 2 * 1024**3  # batas aman aset GitHub
MAX_BODY_BYTES = 4 * 1024**2
RESOLUTION_PROBE_CACHE: dict[tuple, dict] = {}
RESOLUTION_PROBE_LOCK = threading.Lock()

# Fungsi parsing/grouping dari pipeline dipakai ulang supaya format body dan
# pengelompokan split archive tidak berbeda dengan proses encode.
from process_release_archive import (  # noqa: E402
    ARCHIVE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    declared_episode_count,
    extract_release_cover,
    get_split_archive_info,
    group_release_archives,
    parse_release_body_lines,
    pick_title_for_folder,
)


# ---------------------------------------------------------------------------
# Environment / helper umum
# ---------------------------------------------------------------------------

def load_dotenv() -> None:
    """Baca kredensial/opsi dari .env lokal tanpa menimpa environment asli."""
    path = ROOT / ".env"
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key not in {"MAL_CLIENT_ID", "GH_TOKEN", "GITHUB_TOKEN", "RAW_ARCHIVE_PASSWORD"}:
                continue
            if key in os.environ:
                continue
            value = value.strip().strip("\"'")
            if value:
                os.environ[key] = value
    except OSError:
        pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_error(message: str, status: int = 400):
    return {"error": message}, status


def gh_cmd(args: list[str]) -> list[str]:
    cmd = ["gh", *args]
    if GH_REPO:
        cmd.extend(["--repo", GH_REPO])
    return cmd


def gh_run(args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        gh_cmd(args),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def gh_json(args: list[str]):
    proc = gh_run(args, timeout=60)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(msg or f"gh {' '.join(args)} gagal")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Respons gh bukan JSON valid: {exc}") from exc


def resolve_gh_repo() -> str:
    global GH_REPO
    if GH_REPO:
        return GH_REPO
    try:
        data = gh_json(["repo", "view", "--json", "nameWithOwner"])
        GH_REPO = data.get("nameWithOwner", "")
    except Exception:
        GH_REPO = ""
    return GH_REPO


def valid_tag(tag: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", tag or ""))


def valid_dest(dest: str) -> bool:
    dest = (dest or "").strip().strip("/")
    if not dest or ".." in dest.split("/"):
        return False
    parts = dest.split("/")
    if not (2 <= len(parts) <= 3):
        return False
    return all(re.fullmatch(r"[^\s/\\]+", p) and p not in {".", ".."} for p in parts)


def valid_http_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def safe_asset_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and not re.search(r"\s", name)


def resolve_local_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    return p.resolve()


def read_json_file(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return copy.deepcopy(default)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Gagal membaca {path.name}: {exc}") from exc


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _dedupe_strings(items) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        value = str(item).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def metadata_categories() -> list[str]:
    try:
        data = read_json_file(METADATA_FILE, {})
    except RuntimeError:
        data = {}
    values = data.get("categories") if isinstance(data, dict) else []
    return _dedupe_strings(values or [])


def load_ui_config() -> dict:
    parser = configparser.ConfigParser()
    try:
        parser.read(CONFIG_FILE, encoding="utf-8")
    except (OSError, configparser.Error):
        pass
    raw_types = parser.get("ui", "types", fallback="").split(",")
    raw_categories = parser.get("ui", "categories", fallback="").split(",")
    types = _dedupe_strings([*DEFAULT_EPISODE_TYPES, *raw_types])
    categories = _dedupe_strings([*metadata_categories(), *raw_categories])
    return {"types": types, "categories": categories}


def save_ui_config(types, categories) -> dict:
    clean_types = _dedupe_strings(types)
    clean_categories = _dedupe_strings(categories)
    for value in clean_types:
        if "\n" in value or "\r" in value or "," in value or "/" in value or "\\" in value:
            raise RuntimeError(f"Nilai tipe tidak valid: {value!r}")
    for value in clean_categories:
        if re.search(r"[\s/\\,]", value):
            raise RuntimeError(f"Nilai kategori tidak valid: {value!r}")
    parser = configparser.ConfigParser()
    parser["ui"] = {
        "types": ", ".join(clean_types),
        "categories": ", ".join(clean_categories),
    }
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".release_ui.ini.", dir=str(CONFIG_FILE.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            parser.write(fh)
        os.replace(tmp_name, CONFIG_FILE)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return load_ui_config()


# ---------------------------------------------------------------------------
# GitHub Release
# ---------------------------------------------------------------------------

def list_releases() -> list[dict]:
    data = gh_json([
        "release", "list", "--limit", "200",
        "--json", "tagName,name,isDraft,isPrerelease,publishedAt",
    ])
    items = data if isinstance(data, list) else []
    return sorted(
        items,
        key=lambda r: str(r.get("publishedAt") or ""),
        reverse=True,
    )


def get_release(tag: str) -> dict | None:
    if not valid_tag(tag):
        raise RuntimeError("Tag release tidak valid.")
    proc = gh_run([
        "release", "view", tag,
        "--json", "tagName,name,body,isDraft,isPrerelease,createdAt,publishedAt,url,assets",
    ], timeout=60)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        if re.search(r"not found|release not found", msg, re.IGNORECASE):
            return None
        raise RuntimeError(msg or "Gagal mengambil release.")
    data = json.loads(proc.stdout or "{}")
    assets = data.get("assets") or []
    data["assets"] = [
        {
            "name": a.get("name", ""),
            "size": a.get("size", 0),
            "state": a.get("state", ""),
        }
        for a in assets
    ]
    return data


def split_raw_title(name: str) -> tuple[str, str]:
    raw = str(name or "").strip()
    match = re.match(r"^\[(RAW(?:\s+HARDSUB)?)\]\s*(.*)$", raw, re.IGNORECASE)
    if not match:
        return "raw", raw
    mode = "hardsub" if "HARDSUB" in match.group(1).upper() else "raw"
    return mode, match.group(2).strip()


def parse_password_variants(body: str) -> list[str]:
    variants: list[str] = []
    for line in (body or "").splitlines():
        raw = line.strip()
        if raw.startswith("```"):
            continue
        clean = raw.strip("\"`").strip()
        if clean and clean not in variants:
            variants.append(clean)
    return variants


def parse_episode_header(body: str) -> tuple[int, str]:
    parts = re.split(r"\r?\n\s*={3,}\s*\r?\n", body or "", maxsplit=1)
    header = parts[0] if len(parts) > 1 else ""
    count = declared_episode_count(body) or 0
    episode_type = "Episodes"
    for line in header.splitlines():
        match = re.match(
            r"^\s*(\d+)(?:\s*[–—-]\s*(\d+))?\s+([A-Za-z][A-Za-z0-9._ -]*)\s*$",
            line,
        )
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if not count and end >= start:
            count = end - start + 1
        word = match.group(3).strip()
        low = word.lower()
        if low.startswith("episode"):
            episode_type = "Episodes"
        elif low.startswith("compilation"):
            episode_type = "Compilation"
        else:
            episode_type = word
        break
    return count, episode_type


def parse_existing_release(release: dict) -> dict:
    body = release.get("body") or ""
    tag = release.get("tagName", "")
    common = {
        "tag": tag,
        "title": release.get("name", ""),
        "body": body,
        "url": release.get("url", ""),
        "isDraft": bool(release.get("isDraft")),
        "isPrerelease": bool(release.get("isPrerelease")),
        "special": None,
    }
    if tag.lower() == SPECIAL_PASSWORD_TAG:
        return {
            **common,
            "special": "password",
            "passwordVariants": parse_password_variants(body),
            "aliases": [],
            "mappings": [],
            "remoteAssets": release.get("assets") or [],
            "cover": "",
            "resolution": "",
            "episodeCount": 0,
            "episodeType": "Episodes",
            "dest": "",
            "provider": {},
            "rawMode": "raw",
        }
    if tag.lower() == SPECIAL_METADATA_TAG:
        return {
            **common,
            "special": "metadata-guide",
            "aliases": [],
            "mappings": [],
            "remoteAssets": release.get("assets") or [],
            "cover": "",
            "resolution": "",
            "episodeCount": 0,
            "episodeType": "Episodes",
            "dest": "",
            "provider": {},
            "rawMode": "raw",
        }

    file_mapping, aliases, source_info = parse_release_body_lines(body)
    # parse_release_body_lines menyimpan key dalam lowercase agar pencocokan
    # case-insensitive. Untuk ditampilkan/ditulis ulang, pulihkan nama aset asli
    # dari daftar asset release supaya body tidak mengubah kapitalisasi file.
    actual_names = {
        str(a.get("name", "")).lower(): str(a.get("name", ""))
        for a in (release.get("assets") or [])
        if a.get("name")
    }
    mappings = [
        {"name": actual_names.get(name.lower(), name), "dest": dest}
        for name, dest in sorted(file_mapping.items(), key=lambda kv: kv[0].lower())
    ]
    cover = extract_release_cover(body)
    resolution = ""
    m = re.search(r"(?m)^\s*(\d{3,4}p)\s*$", body)
    if m:
        resolution = m.group(1)
    count, episode_type = parse_episode_header(body)
    raw_mode, base_title = split_raw_title(release.get("name", ""))

    default_dest = mappings[0]["dest"] if mappings else ""
    aliases_data = read_json_file(ALIASES_FILE, {"aliases": {}, "folder_info": {}, "sources": {}})
    sources = aliases_data.get("sources") or {}
    source_key = find_source_key(sources, default_dest)
    saved_source = sources.get(source_key, {}) if source_key else {}
    provider = dict(saved_source)
    if source_info.get("url"):
        provider["url"] = source_info["url"]
    if source_info.get("icon"):
        provider["icon"] = source_info["icon"]
    if source_info.get("provider") and not provider.get("provider"):
        provider["provider"] = source_info["provider"]

    folder_info = aliases_data.get("folder_info") or {}
    info = folder_info.get(default_dest) or {}
    if not cover and info.get("cover"):
        cover = info["cover"]
    return {
        **common,
        "title": base_title,
        "rawMode": raw_mode,
        "aliases": aliases,
        "mappings": mappings,
        "remoteAssets": release.get("assets") or [],
        "cover": cover,
        "resolution": resolution,
        "episodeCount": count,
        "episodeType": episode_type,
        "dest": default_dest,
        "provider": provider,
        "malId": info.get("mal_id"),
        "malTitle": info.get("mal_title"),
        "malCachedAt": info.get("mal_cached_at"),
    }


def find_source_key(sources: dict, dest: str) -> str:
    if not dest:
        return ""
    norm = re.sub(r"[^a-z0-9]", "", dest.lower())
    for key in sources:
        if re.sub(r"[^a-z0-9]", "", key.lower()) == norm:
            return key
    parts = dest.strip("/").split("/")
    if len(parts) >= 2:
        root = "/".join(parts[:2])
        norm_root = re.sub(r"[^a-z0-9]", "", root.lower())
        for key in sources:
            if re.sub(r"[^a-z0-9]", "", key.lower()) == norm_root:
                return key
    return ""


# ---------------------------------------------------------------------------
# Form / body / metadata
# ---------------------------------------------------------------------------

def normalize_mappings(raw) -> list[dict]:
    out = []
    seen = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        dest = str(item.get("dest") or "").strip().strip("/")
        if not name and not dest:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "dest": dest})
    return out


def normalize_assets(raw) -> list[dict]:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        dest = str(item.get("dest") or "").strip().strip("/")
        if path or dest:
            out.append({"path": path, "dest": dest})
    return out


# ---------------------------------------------------------------------------
# Deteksi resolusi dari video lokal (termasuk di dalam arsip RAW)
# ---------------------------------------------------------------------------

RESOLUTION_STANDARDS = (
    (3840, 2160, "2160p"),
    (2560, 1440, "1440p"),
    (1920, 1080, "1080p"),
    (1280, 720, "720p"),
    (854, 480, "480p"),
    (640, 360, "360p"),
)


def _is_video_name(name: str) -> bool:
    return name.lower().endswith(VIDEO_EXTENSIONS)


def _is_archive_name(name: str) -> bool:
    lower = name.lower()
    if any(lower.endswith(ext) for ext in ARCHIVE_EXTENSIONS):
        return True
    return bool(get_split_archive_info(name)[3])


def _probe_passwords() -> list[str]:
    """Password arsip yang lazim dipakai pipeline; tidak pernah dicetak ke log."""
    values = [
        os.environ.get("RAW_ARCHIVE_PASSWORD", "").strip(),
        "kurniawan",
        "",
    ]
    return list(dict.fromkeys(values))


def _probe_error_line(proc: subprocess.CompletedProcess, fallback: str) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else fallback


def _archive_error_text(errors: list[str], fallback: str) -> str:
    text = "; ".join(dict.fromkeys(errors))
    lower = text.lower()
    if "headers error" in lower or "wrong password" in lower:
        return "arsip terenkripsi dan password tidak cocok; set RAW_ARCHIVE_PASSWORD."
    return text or fallback


def _probe_media_dimensions(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe tidak ditemukan. Install ffmpeg/ffprobe terlebih dahulu.")
    try:
        proc = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_streams", "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffprobe melewati batas waktu 45 detik.") from exc
    if proc.returncode != 0:
        raise RuntimeError(_probe_error_line(proc, "ffprobe gagal membaca video."))
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Output ffprobe bukan JSON valid.") from exc
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError("Video tidak memiliki stream video yang bisa dibaca.")
    stream = streams[0]
    try:
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    if width <= 0 or height <= 0:
        raise RuntimeError("ffprobe tidak menemukan lebar/tinggi video.")

    # Video ponsel kadang menyimpan rotasi 90/270 derajat.
    rotation = (stream.get("tags") or {}).get("rotate")
    if rotation in (None, ""):
        for side_data in stream.get("side_data_list") or []:
            if isinstance(side_data, dict) and side_data.get("rotation") is not None:
                rotation = side_data.get("rotation")
                break
    try:
        if abs(int(float(rotation))) % 180 == 90:
            width, height = height, width
    except (TypeError, ValueError):
        pass

    return {
        "width": width,
        "height": height,
        "codec": str(stream.get("codec_name") or ""),
    }


def _archive_video_entries(archive: Path, passwords: list[str]) -> list[tuple[str, int]]:
    seven = shutil.which("7z")
    if not seven:
        raise RuntimeError("7z tidak ditemukan. Install p7zip untuk membaca arsip RAW.")
    errors: list[str] = []
    for password in passwords:
        cmd = [seven, "l", "-slt", "-ba", "-y"]
        if password:
            cmd.append("-p" + password)
        cmd.append(str(archive))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=75,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            errors.append("daftar arsip melewati batas waktu 75 detik")
            continue
        if proc.returncode != 0:
            errors.append(_probe_error_line(proc, "7z gagal membaca arsip"))
            continue

        entries: list[tuple[str, int]] = []
        current: dict = {}

        def flush() -> None:
            raw_path = str(current.get("path") or "")
            if not raw_path or not _is_video_name(raw_path):
                return
            try:
                size = int(current.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            entries.append((raw_path, size))

        for line in proc.stdout.splitlines():
            if not line.strip():
                flush()
                current = {}
                continue
            key, sep, value = line.partition(" = ")
            if not sep:
                continue
            if key == "Path":
                current["path"] = value
            elif key == "Size":
                current["size"] = value
        flush()

        if entries:
            return entries
        errors.append(f"tidak ada file video di dalam {archive.name}")

    raise RuntimeError(_archive_error_text(errors, "arsip tidak bisa dibaca"))


def _extract_archive_video(
    archive: Path,
    entry: str,
    output_dir: Path,
    passwords: list[str],
) -> Path:
    seven = shutil.which("7z")
    if not seven:
        raise RuntimeError("7z tidak ditemukan. Install p7zip untuk membaca arsip RAW.")
    target = output_dir / Path(entry).name
    errors: list[str] = []
    for password in passwords:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        cmd = [seven, "e", "-y", f"-o{output_dir}"]
        if password:
            cmd.append("-p" + password)
        cmd.extend([str(archive), entry])
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            errors.append("ekstraksi video melewati batas waktu 300 detik")
            continue
        if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            return target
        errors.append(_probe_error_line(proc, "7z gagal mengekstrak video"))
    raise RuntimeError(_archive_error_text(errors, "gagal mengekstrak video dari arsip"))


def _resolution_label(width: int, height: int) -> str:
    def score(item: tuple[int, int, str]) -> float:
        std_width, std_height, _ = item
        return abs(width - std_width) / std_width + abs(height - std_height) / std_height

    return min(RESOLUTION_STANDARDS, key=score)[2]


def _probe_local_asset(path: Path) -> dict:
    try:
        stat = path.stat()
    except OSError as exc:
        raise RuntimeError(f"tidak bisa membaca file: {exc}") from exc
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    with RESOLUTION_PROBE_LOCK:
        cached = RESOLUTION_PROBE_CACHE.get(cache_key)
    if cached:
        return {**cached, "cached": True}

    if _is_video_name(path.name):
        info = _probe_media_dimensions(path)
        result = {
            "width": info["width"],
            "height": info["height"],
            "codec": info["codec"],
            "source": path.name,
            "entry": "",
            "cached": False,
        }
    elif _is_archive_name(path.name):
        lower = path.name.lower()
        if lower.endswith(".zpaq"):
            raise RuntimeError("arsip .zpaq belum didukung untuk deteksi cepat; gunakan video langsung.")
        passwords = _probe_passwords()
        entries = _archive_video_entries(path, passwords)
        entry = entries[0][0]
        with tempfile.TemporaryDirectory(prefix="release_ui_probe_") as tmp:
            extracted = _extract_archive_video(path, entry, Path(tmp), passwords)
            info = _probe_media_dimensions(extracted)
        result = {
            "width": info["width"],
            "height": info["height"],
            "codec": info["codec"],
            "source": path.name,
            "entry": entry,
            "cached": False,
        }
    else:
        raise RuntimeError("bukan file video atau arsip yang didukung")

    with RESOLUTION_PROBE_LOCK:
        RESOLUTION_PROBE_CACHE[cache_key] = dict(result)
    return result


def probe_resolution(raw_assets) -> dict:
    """Cari video pertama yang bisa dibaca, lalu petakan dimensinya ke label resolusi."""
    assets = normalize_assets(raw_assets)
    if not assets:
        raise RuntimeError("Isi minimal satu path aset lokal untuk deteksi resolusi.")

    by_name: dict[str, Path] = {}
    order: dict[str, int] = {}
    errors: list[str] = []
    for idx, asset in enumerate(assets):
        raw_path = asset["path"]
        if not raw_path:
            continue
        path = resolve_local_path(raw_path)
        if not path.is_file():
            errors.append(f"{path.name or raw_path}: file tidak ditemukan")
            continue
        key = path.name.lower()
        if key in by_name:
            continue
        by_name[key] = path
        order[key] = idx

    if not by_name:
        detail = "; ".join(errors[:3])
        raise RuntimeError("Tidak ada aset lokal yang bisa diperiksa." + (f" {detail}" if detail else ""))

    candidates: list[Path] = []
    for group in group_release_archives(list(by_name)):
        path = by_name.get(group["primary_file"].lower())
        if path and (_is_video_name(path.name) or _is_archive_name(path.name)):
            candidates.append(path)
    candidates.sort(key=lambda p: order.get(p.name.lower(), 9999))

    if not candidates:
        raise RuntimeError("Tidak ada file video/arsip yang bisa diperiksa dari aset lokal.")

    errors = []
    for path in candidates:
        try:
            info = _probe_local_asset(path)
            return {
                "ok": True,
                "resolution": _resolution_label(info["width"], info["height"]),
                "width": info["width"],
                "height": info["height"],
                "codec": info["codec"],
                "source": info["source"],
                "entry": info["entry"],
                "cached": info["cached"],
            }
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    raise RuntimeError("Gagal mendeteksi resolusi. " + " | ".join(errors[:3]))


def build_password_plan(data: dict) -> dict:
    """Rencana khusus release tag `password` (kunci arsip untuk runner)."""
    tag = SPECIAL_PASSWORD_TAG
    existing = get_release(tag)
    errors: list[str] = []
    warnings: list[str] = []
    if not existing:
        errors.append("Release tag 'password' tidak ditemukan; UI ini hanya memperbarui release yang sudah ada.")
        return {"ok": False, "errors": errors, "warnings": warnings, "tag": tag, "title": ""}

    title = str(data.get("title") or existing.get("name") or "Key for RAW Archive").strip()
    if not title:
        errors.append("Judul release password wajib diisi.")
    variants: list[str] = []
    for item in data.get("password_variants") or []:
        value = str(item).strip().strip("\"`").strip()
        if not value:
            continue
        if "`" in value or "\n" in value or "\r" in value:
            errors.append(f"Variasi password tidak valid: {value!r}")
            continue
        if value not in variants:
            variants.append(value)
    if not variants:
        errors.append("Minimal satu variasi password wajib diisi.")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "tag": tag, "title": title}

    body = "\n".join(f"`{value}`" for value in variants) + "\n"
    commands = [
        "gh release edit "
        + tag
        + " --title "
        + shlex.quote(title)
        + " --notes-file <body.md>"
    ]
    warnings.append(
        "Runner akan mencoba semua variasi password. Pastikan urutan/isi kunci benar; "
        "jangan menambahkan kunci yang tidak dipakai arsip."
    )
    return {
        "ok": True,
        "special": "password",
        "action": "edit",
        "existing": True,
        "publishDraft": False,
        "tag": tag,
        "title": title,
        "body": body,
        "aliases": [],
        "mappings": [],
        "assets": [],
        "groups": [],
        "remoteAssets": existing.get("assets") or [],
        "commands": commands,
        "warnings": warnings,
        "errors": errors,
        "metadataChanges": [],
        "saveMetadata": False,
        "defaultDest": "",
        "passwordVariants": variants,
    }


def build_plan(data: dict) -> dict:
    """Validasi form, susun body, dan hasilkan rencana aksi (tanpa mutasi)."""
    errors: list[str] = []
    warnings: list[str] = []

    tag = str(data.get("tag") or "").strip()
    if not valid_tag(tag):
        errors.append("Tag wajib diisi dan hanya boleh berisi huruf, angka, titik, garis bawah, atau tanda hubung.")
    elif tag.lower() == SPECIAL_METADATA_TAG:
        return {
            "ok": False,
            "errors": [
                "Tag 'Metadata-Guide' adalah dokumen hukum/format khusus. "
                "UI ini tidak boleh mengubah body-nya; sesuaikan UI jika format panduan berubah."
            ],
            "warnings": [],
            "tag": tag,
            "title": "Metadata-Guide",
        }
    elif tag.lower() == SPECIAL_PASSWORD_TAG:
        return build_password_plan(data)

    parsed_mode, base_title = split_raw_title(str(data.get("title") or ""))
    raw_mode = str(data.get("raw_mode") or parsed_mode).strip().lower()
    if raw_mode not in {"raw", "hardsub"}:
        errors.append("Tipe RAW harus [RAW] atau [RAW HARDSUB].")
        raw_mode = "raw"
    if not base_title:
        errors.append("Judul release wajib diisi.")
    title = f"[RAW HARDSUB] {base_title}" if raw_mode == "hardsub" else f"[RAW] {base_title}"

    resolution = str(data.get("resolution") or "").strip()
    if not re.fullmatch(r"\d{3,4}p", resolution, re.IGNORECASE):
        errors.append("Resolusi harus berbentuk seperti 480p, 720p, atau 1080p.")

    try:
        episode_count = int(data.get("episode_count") or 0)
    except (TypeError, ValueError):
        episode_count = 0
    if episode_count < 1:
        errors.append("Jumlah episode minimal 1.")

    episode_type = str(data.get("episode_type") or "Episodes").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 ._-]{0,39}", episode_type):
        errors.append("Tipe episode tidak valid (huruf/angka, maksimal 40 karakter).")

    cover = str(data.get("cover_url") or "").strip()
    if cover and not valid_http_url(cover):
        errors.append("URL sampul harus http/https yang valid.")
    if cover and any(ch in cover for ch in '"<>'):
        errors.append("URL sampul mengandung karakter yang tidak aman untuk body HTML.")

    aliases = [
        str(a).strip()
        for a in (data.get("aliases") or [])
        if str(a).strip()
    ]
    # Alias pertama selalu judul dari input; alias lama tetap dipertahankan bila ada.
    aliases = list(dict.fromkeys(aliases))
    if base_title:
        aliases = [base_title, *[a for a in aliases if a != base_title]]
    if not aliases:
        errors.append("Minimal satu alias/judul wajib diisi.")

    assets = normalize_assets(data.get("assets"))
    prepared_assets = []
    local_names = set()
    for idx, asset in enumerate(assets, start=1):
        raw_path = asset["path"]
        if not raw_path:
            continue
        path = resolve_local_path(raw_path)
        if not path.is_file():
            errors.append(f"Aset #{idx} tidak ditemukan: {raw_path}")
            continue
        name = path.name
        if not safe_asset_name(name):
            errors.append(
                f"Nama aset '{name}' mengandung spasi/slash. Pipeline menggunakan format path "
                "tanpa spasi; ganti nama file terlebih dahulu."
            )
            continue
        if name.lower() in local_names:
            errors.append(f"Aset '{name}' dipilih lebih dari sekali.")
            continue
        local_names.add(name.lower())
        try:
            size = path.stat().st_size
        except OSError as exc:
            errors.append(f"Gagal membaca ukuran '{name}': {exc}")
            continue
        if size > MAX_ASSET_BYTES:
            errors.append(
                f"Aset '{name}' berukuran {size / (1024**3):.2f} GB; batas aman aset GitHub "
                "adalah 2 GB per file."
            )
        if size == 0:
            errors.append(f"Aset '{name}' berukuran 0 byte.")
        prepared_assets.append({"path": str(path), "name": name, "size": size, "dest": asset["dest"]})

    # Kelompokkan aset lokal (split archive) dan tentukan folder tujuan tiap grup.
    groups = []
    if prepared_assets:
        by_name = {a["name"]: a for a in prepared_assets}
        for group in group_release_archives(list(by_name)):
            group_assets = [by_name[n] for n in group["all_parts"] if n in by_name]
            if not group_assets:
                continue
            dests = {a["dest"] for a in group_assets if a["dest"]}
            if len(dests) > 1:
                errors.append(
                    f"Bagian arsip '{group['base_name']}' punya folder tujuan berbeda: "
                    + ", ".join(sorted(dests))
                )
                dest = sorted(dests)[0]
            else:
                dest = next(iter(dests), "")
            groups.append({
                "base": group["base_name"],
                "primary": group["primary_file"],
                "parts": group["all_parts"],
                "assets": group_assets,
                "dest": dest,
            })
        # Deteksi nomor bagian yang bolong untuk arsip split .001/.002/...
        for g in groups:
            if len(g["parts"]) <= 1:
                continue
            indices = []
            for n in g["parts"]:
                _, idx, _, is_split = get_split_archive_info(n)
                if is_split and idx < 9999:
                    indices.append(idx)
            if indices and len(indices) == len(g["parts"]):
                expected = list(range(min(indices), max(indices) + 1))
                if sorted(indices) != expected:
                    warnings.append(
                        f"Bagian arsip '{g['base']}' tampaknya tidak lengkap: {sorted(indices)}"
                    )

    mappings = normalize_mappings(data.get("mappings"))
    mapping_by_name = {m["name"].lower(): m for m in mappings if m["name"]}
    for m in mappings:
        if not safe_asset_name(m["name"]):
            errors.append(f"Nama aset pada pemetaan body tidak valid: '{m['name']}'.")
        if not valid_dest(m["dest"]):
            errors.append(f"Folder tujuan '{m['dest']}' tidak valid (2-3 segmen, tanpa spasi).")

    # Tambahkan pemetaan otomatis untuk grup aset lokal yang belum punya mapping.
    for group in groups:
        primary = group["primary"]
        if primary.lower() not in mapping_by_name:
            dest = group["dest"]
            if not valid_dest(dest):
                errors.append(
                    f"Folder tujuan untuk '{primary}' belum diisi / tidak valid: '{dest or '-'}'."
                )
                continue
            mappings.append({"name": primary, "dest": dest})
            mapping_by_name[primary.lower()] = mappings[-1]

    if not mappings:
        errors.append("Tidak ada pemetaan path arsip untuk body release.")

    # Folder tujuan yang dipakai untuk metadata.
    dests = sorted({m["dest"] for m in mappings if m.get("dest")})

    # Bandingkan dengan release yang sudah ada.
    existing = None
    if tag:
        existing = get_release(tag)
    action = "edit" if existing else "create"
    remote_assets = (existing or {}).get("assets") or []
    remote_names = {a["name"].lower() for a in remote_assets}
    clobber = bool(data.get("clobber"))
    duplicate_remote = sorted(
        {a["name"] for a in prepared_assets if a["name"].lower() in remote_names}
    )
    if duplicate_remote and not clobber:
        errors.append(
            "Aset dengan nama yang sama sudah ada di release: "
            + ", ".join(duplicate_remote)
            + ". Centang 'Ganti aset yang namanya sama' jika memang ingin menimpanya."
        )
    if duplicate_remote and clobber:
        warnings.append(
            "Aset lama akan DIHAPUS sebelum upload ulang: " + ", ".join(duplicate_remote)
        )

    if action == "create" and not prepared_assets:
        errors.append("Release baru wajib memiliki minimal satu aset lokal untuk diunggah.")
    if action == "create" and bool(data.get("publish_draft")):
        warnings.append("Opsi 'publikasikan draft' hanya berlaku saat mengedit release yang sudah ada.")

    if action == "edit":
        if not remote_assets and not prepared_assets and not data.get("draft_only"):
            warnings.append("Release ini tidak punya aset; pipeline kemungkinan tidak bisa memprosesnya.")
        if existing and existing.get("isDraft") and data.get("publish_draft"):
            warnings.append("Release draft akan dipublikasikan setelah perubahan disimpan.")
        if not existing.get("isDraft") and not data.get("draft_only"):
            warnings.append(
                "Release sudah published. Perubahan judul/body dapat memicu workflow antrian; "
                "pastikan tidak ada proses lain yang sedang berjalan."
            )

    if raw_mode == "raw":
        warnings.append("[RAW] berarti pipeline harus menghasilkan .vtt; pastikan video sumber punya subtitle.")
    else:
        warnings.append("[RAW HARDSUB] berarti tidak boleh ada .vtt terpisah; pastikan subtitle sudah terpiksel.")

    provider_url = str(data.get("provider_url") or "").strip()
    provider_icon = str(data.get("provider_icon") or "").strip()
    provider_name = str(data.get("provider_name") or "").strip()
    if provider_url and not valid_http_url(provider_url):
        errors.append("URL provider harus http/https yang valid.")
    if provider_icon and not valid_http_url(provider_icon):
        errors.append("URL ikon provider harus http/https yang valid.")
    if provider_url and not provider_icon:
        warnings.append("URL provider diisi tetapi ikon provider masih kosong.")
    if provider_icon and not provider_url:
        warnings.append("Ikon provider diisi tetapi URL provider masih kosong.")

    mal_id = 0
    if data.get("mal_id") not in (None, ""):
        try:
            mal_id = int(data["mal_id"])
        except (TypeError, ValueError):
            errors.append("MAL ID tidak valid (harus angka).")

    known_names = local_names | {a["name"].lower() for a in remote_assets if a.get("name")}
    for m in mappings:
        if m.get("name") and m["name"].lower() not in known_names:
            warnings.append(
                f"Pemetaan '{m['name']}' tidak ditemukan di aset release/lokal. "
                "Pastikan nama file benar."
            )

    if existing and existing.get("isDraft") and not data.get("publish_draft"):
        warnings.append(
            "Release ini masih draft. Pipeline baru akan berjalan setelah draft dipublikasikan."
        )

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    # Body release
    episode_word = episode_type
    if episode_count == 1:
        if episode_type == "Episodes":
            episode_word = "Episode"
        elif episode_type == "Compilation":
            episode_word = "Compilation"
    elif episode_type == "Compilation":
        episode_word = "Compilations"

    lines: list[str] = []
    if cover:
        lines.append(f'<img width="225" src="{html.escape(cover, quote=True)}" alt="cover" />')
        lines.append("")
    lines.append(resolution)
    lines.append(f"{episode_count} {episode_word}")
    lines.append("")
    lines.append("=" * 44)
    lines.extend(aliases)
    lines.append("=" * 44)
    for m in mappings:
        lines.append(f"{m['dest']}/{m['name']}")
    if provider_url or provider_icon:
        lines.append("=" * 44)
        if provider_url:
            lines.append(provider_url)
        if provider_icon:
            lines.append(provider_icon)
    body = "\n".join(lines).rstrip() + "\n"

    # Metadata aliases.json
    aliases_before = read_json_file(
        ALIASES_FILE, {"aliases": {}, "folder_info": {}, "sources": {}}
    )
    aliases_after = copy.deepcopy(aliases_before)
    source_info = {
        "provider": provider_name or "",
        "url": provider_url,
        "icon": provider_icon,
    }
    aliases_after.setdefault("aliases", {})
    aliases_after.setdefault("folder_info", {})
    aliases_after.setdefault("sources", {})
    for dest in dests:
        parts = dest.strip("/").split("/")
        leaf = parts[1] if len(parts) > 1 else parts[0]
        bucket = aliases_after["aliases"].get(leaf) or []
        for alias in aliases:
            if alias not in bucket:
                bucket.append(alias)
        if bucket:
            aliases_after["aliases"][leaf] = bucket

        display_title = pick_title_for_folder(aliases, dest) or aliases[0]
        apply_folder_info_safe(aliases_after, dest, display_title, cover)
        info = aliases_after["folder_info"].get(dest) or {}
        if mal_id:
            info["mal_id"] = mal_id
            info["mal_title"] = str(data.get("mal_title") or "").strip()
            info["mal_cached_at"] = now_iso()
            aliases_after["folder_info"][dest] = info

        if provider_url or provider_icon or provider_name:
            entry = {
                "provider": provider_name or urllib.parse.urlparse(provider_url).hostname or "RAW Source",
                "url": provider_url,
                "icon": provider_icon,
                "note": f"Sumber mentah dari Release {mappings[0]['name']}" if mappings else "Sumber mentah",
            }
            existing_source = aliases_before.get("sources", {}).get(dest) or {}
            if existing_source.get("note"):
                entry["note"] = existing_source["note"]
            aliases_after["sources"][dest] = entry
            if len(parts) >= 3:
                aliases_after["sources"]["/".join(parts[:2])] = entry

    metadata_changes = diff_metadata(aliases_before, aliases_after)
    warnings = list(dict.fromkeys(warnings))
    errors = list(dict.fromkeys(errors))

    # Rencana perintah (hanya untuk pratinjau)
    commands = []
    if action == "create":
        commands.append(
            "gh release create "
            + tag
            + " --title "
            + shlex.quote(title)
            + " --notes-file <body.md> --draft"
        )
        for a in prepared_assets:
            commands.append("gh release upload " + tag + " " + shlex.quote(a["path"]))
        if not data.get("draft_only"):
            commands.append("gh release edit " + tag + " --draft=false")
    else:
        commands.append(
            "gh release edit "
            + tag
            + " --title "
            + shlex.quote(title)
            + " --notes-file <body.md>"
        )
        for a in prepared_assets:
            commands.append(
                "gh release upload "
                + tag
                + " "
                + shlex.quote(a["path"])
                + (" --clobber" if clobber else "")
            )
        if data.get("publish_draft") and existing and existing.get("isDraft"):
            commands.append("gh release edit " + tag + " --draft=false")
    if data.get("save_metadata") and metadata_changes:
        commands.append("# tulis aliases.json")
    if data.get("save_metadata") and data.get("commit_metadata") and metadata_changes:
        commands.append("# commit & push aliases.json")

    return {
        "ok": True,
        "action": action,
        "existing": bool(existing),
        "publishDraft": bool(action == "edit" and existing and existing.get("isDraft") and data.get("publish_draft")),
        "tag": tag,
        "title": title,
        "baseTitle": base_title,
        "rawMode": raw_mode,
        "body": body,
        "aliases": aliases,
        "mappings": mappings,
        "assets": prepared_assets,
        "groups": groups,
        "remoteAssets": remote_assets,
        "commands": commands,
        "warnings": warnings,
        "errors": errors,
        "metadataChanges": metadata_changes,
        "saveMetadata": bool(data.get("save_metadata")),
        "defaultDest": dests[0] if dests else "",
    }


def apply_folder_info_safe(data: dict, folder_rel_path: str, title: str, cover_url: str = "") -> None:
    """Setara apply_folder_info() dari pipeline, tetapi TIDAK memanggil MAL.

    apply_folder_info() punya fallback fetch_mal_cover() yang dapat memakai
    kuota saat pratinjau. Metadata preview/apply di UI ini harus bebas efek
    samping jaringan.
    """
    data.setdefault("folder_info", {})
    fi = data["folder_info"]
    parts = [p for p in (folder_rel_path or "").strip("/").split("/") if p]
    if not parts:
        return
    full_key = "/".join(parts)
    parent_key = "/".join(parts[:2])
    keys = [full_key]
    if parent_key and parent_key != full_key:
        keys.append(parent_key)
    resolved_cover = (
        cover_url
        or (fi.get(full_key) or {}).get("cover", "")
        or (fi.get(parent_key) or {}).get("cover", "")
    )
    for key in keys:
        info = fi.get(key) or {}
        if title and (key == full_key or not info.get("title")):
            info["title"] = title
        if resolved_cover and (key == full_key or not info.get("cover")):
            info["cover"] = resolved_cover
        if info:
            fi[key] = info


def diff_metadata(before: dict, after: dict) -> list[dict]:
    changes = []
    for section in ("aliases", "folder_info", "sources"):
        b = before.get(section) or {}
        a = after.get(section) or {}
        for key in sorted(set(b) | set(a)):
            if b.get(key) != a.get(key):
                changes.append({
                    "section": section,
                    "key": key,
                    "before": b.get(key),
                    "after": a.get(key),
                })
    return changes


# ---------------------------------------------------------------------------
# MAL resmi
# ---------------------------------------------------------------------------

def load_mal_cache() -> dict:
    return read_json_file(MAL_CACHE_FILE, {"queries": {}, "anime": {}})


def save_mal_cache(cache: dict) -> None:
    cache["updated_at"] = now_iso()
    write_json_atomic(MAL_CACHE_FILE, cache)


def mal_search(query: str) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    client_id = (os.environ.get("MAL_CLIENT_ID") or "").strip()
    if not client_id:
        raise RuntimeError(
            "MAL_CLIENT_ID belum diset. Export environment variable atau isi .env lokal."
        )

    cache = load_mal_cache()
    key = re.sub(r"\s+", " ", query.lower())
    cached = (cache.get("queries") or {}).get(key)
    if cached:
        return cached.get("results", [])

    global MAL_LAST_REQUEST
    with MAL_LOCK:
        wait = 0.65 - (time.time() - MAL_LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        url = (
            "https://api.myanimelist.net/v2/anime?limit=6&fields="
            "id,title,alternative_titles,main_picture,num_episodes,media_type,start_date&q="
            + urllib.parse.quote(query)
        )
        req = urllib.request.Request(url, headers={
            "X-MAL-CLIENT-ID": client_id,
            "User-Agent": "stream-release-ui/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")[:300]
            if exc.code == 429:
                time.sleep(2)
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))
                except Exception as exc2:
                    raise RuntimeError(f"MAL rate limit / gagal: {exc2}") from exc2
            else:
                raise RuntimeError(f"MAL HTTP {exc.code}: {body}") from exc
        except Exception as exc:
            raise RuntimeError(f"Gagal menghubungi MAL: {exc}") from exc
        finally:
            MAL_LAST_REQUEST = time.time()

    results = []
    for item in payload.get("data") or []:
        node = item.get("node") or {}
        alt = node.get("alternative_titles") or {}
        titles = [node.get("title") or ""]
        for key_name in ("english", "japanese", "synonyms"):
            value = alt.get(key_name)
            if isinstance(value, list):
                titles.extend(value)
            elif value:
                titles.append(value)
        titles = list(dict.fromkeys(t.strip() for t in titles if t and t.strip()))
        pic = node.get("main_picture") or {}
        results.append({
            "id": node.get("id"),
            "title": node.get("title") or "",
            "titles": titles,
            "cover": pic.get("large") or pic.get("medium") or "",
            "episodes": node.get("num_episodes") or 0,
            "mediaType": node.get("media_type") or "",
            "startDate": node.get("start_date") or "",
        })
        if node.get("id"):
            cache.setdefault("anime", {})[str(node["id"])] = {
                "fetched_at": now_iso(),
                "node": node,
            }

    cache.setdefault("queries", {})[key] = {"fetched_at": now_iso(), "results": results}
    save_mal_cache(cache)
    return results


# ---------------------------------------------------------------------------
# Provider / ikon
# ---------------------------------------------------------------------------

class IconHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base = ""
        self.icons: list[tuple[str, str]] = []
        self.og_image = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "base" and attrs_dict.get("href"):
            self.base = attrs_dict["href"]
        elif tag == "link":
            rel = attrs_dict.get("rel", "").lower()
            href = attrs_dict.get("href", "").strip()
            if href and ("icon" in rel.split() or "apple-touch-icon" in rel):
                self.icons.append((rel, href))
        elif tag == "meta":
            prop = (attrs_dict.get("property") or attrs_dict.get("name") or "").lower()
            if prop == "og:image" and attrs_dict.get("content"):
                self.og_image = attrs_dict["content"].strip()


def provider_candidates(url: str, dest: str = "") -> dict:
    url = (url or "").strip()
    if not valid_http_url(url):
        raise RuntimeError("URL provider harus http/https yang valid.")
    parsed = urllib.parse.urlparse(url)
    provider = parsed.hostname or ""
    candidates: list[dict] = []

    # Sumber yang sudah tercatat di aliases.json selalu didahulukan.
    aliases_data = read_json_file(ALIASES_FILE, {"sources": {}})
    key = find_source_key(aliases_data.get("sources") or {}, dest or provider)
    existing = (aliases_data.get("sources") or {}).get(key, {}) if key else {}
    if existing.get("icon"):
        candidates.append({
            "url": existing["icon"],
            "label": "Tersimpan di aliases.json",
            "source": "saved",
        })
        provider = existing.get("provider") or provider

    page_error = ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; stream-release-ui/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read(1_500_000)
            charset = resp.headers.get_content_charset() or "utf-8"
            page = raw.decode(charset, errors="ignore")
        parser = IconHTMLParser()
        parser.feed(page)
        base_url = urllib.parse.urljoin(url, parser.base) if parser.base else url
        for rel, href in parser.icons:
            absolute = urllib.parse.urljoin(base_url, href)
            if valid_http_url(absolute):
                candidates.append({
                    "url": absolute,
                    "label": rel or "icon",
                    "source": "page",
                })
        if parser.og_image:
            absolute = urllib.parse.urljoin(base_url, parser.og_image)
            if valid_http_url(absolute):
                candidates.append({
                    "url": absolute,
                    "label": "og:image",
                    "source": "page",
                })
    except Exception as exc:
        page_error = str(exc)

    root_favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    candidates.append({"url": root_favicon, "label": "favicon.ico", "source": "fallback"})
    # Dedupe berdasarkan URL
    deduped = []
    seen = set()
    for c in candidates:
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        deduped.append(c)

    return {
        "provider": provider,
        "url": url,
        "candidates": deduped,
        "error": page_error,
    }


# ---------------------------------------------------------------------------
# Apply / job
# ---------------------------------------------------------------------------

def add_job_log(job: dict, line: str) -> None:
    with JOBS_LOCK:
        log = job.setdefault("log", [])
        log.append(str(line).rstrip("\n"))
        if len(log) > 1200:
            del log[: len(log) - 1200]
        job["updated_at"] = now_iso()


def run_job_cmd(job: dict, args: list[str]) -> int:
    cmd = gh_cmd(args)
    add_job_log(job, "$ " + shlex.join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        add_job_log(job, line)
    rc = proc.wait()
    add_job_log(job, f"[exit {rc}]")
    return rc


def write_body_temp(job: dict, body: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="release_ui_body_", suffix=".md", dir=str(ROOT))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    job["temp_files"].append(name)
    return Path(name)


def commit_metadata(job: dict) -> None:
    add_job_log(job, "[metadata] commit & push aliases.json")
    # Jangan pernah reset --hard di sini: UI lokal mungkin sedang punya perubahan
    # lain yang belum di-commit. Kalau push gagal, biarkan commit lokal dan
    # minta user menyelesaikannya secara manual.
    pull = subprocess.run(
        ["git", "pull", "--rebase", "--autostash", "origin", "main"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if pull.returncode != 0:
        raise RuntimeError(
            "git pull --rebase gagal; selesaikan konflik dulu. "
            + (pull.stderr or pull.stdout).strip()
        )
    proc = subprocess.run(
        ["git", "add", "aliases.json"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("git add aliases.json gagal: " + (proc.stderr or proc.stdout))
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", "aliases.json"],
        cwd=str(ROOT),
    )
    if diff.returncode == 0:
        add_job_log(job, "[metadata] tidak ada perubahan aliases.json untuk di-commit.")
        return
    commit = subprocess.run(
        ["git", "commit", "-m", "chore(metadata): update release metadata from UI [skip ci]"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if commit.returncode != 0:
        raise RuntimeError("git commit gagal: " + (commit.stderr or commit.stdout))
    push = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if push.returncode != 0:
        raise RuntimeError(
            "Commit lokal berhasil tetapi push gagal. Jalankan 'git push' manual "
            "setelah menyelesaikan perubahan remote. " + (push.stderr or push.stdout).strip()
        )
    add_job_log(job, "[metadata] aliases.json tersimpan di remote.")


def run_job(job_id: str, plan: dict, data: dict) -> None:
    job = JOBS[job_id]
    body_path = None
    try:
        add_job_log(job, f"[start] {plan['action']} release {plan['tag']}")

        if plan.get("special") == "password":
            body_path = write_body_temp(job, plan["body"])
            rc = run_job_cmd(job, [
                "release", "edit", SPECIAL_PASSWORD_TAG,
                "--title", plan["title"],
                "--notes-file", str(body_path),
            ])
            if rc != 0:
                raise RuntimeError("Gagal memperbarui variasi password.")
            job["status"] = "success"
            add_job_log(job, "[✓] Variasi password diperbarui.")
            return

        if data.get("save_metadata"):
            before = read_json_file(ALIASES_FILE, {"aliases": {}, "folder_info": {}, "sources": {}})
            # build_plan sudah menghitung metadataChanges; terapkan ulang dengan helper
            updated = copy.deepcopy(before)
            aliases = plan["aliases"]
            mal_id = int(data.get("mal_id") or 0)
            cover = str(data.get("cover_url") or "").strip()
            provider_url = str(data.get("provider_url") or "").strip()
            provider_icon = str(data.get("provider_icon") or "").strip()
            provider_name = str(data.get("provider_name") or "").strip()
            dests = sorted({m["dest"] for m in plan["mappings"] if m.get("dest")})
            updated.setdefault("aliases", {})
            updated.setdefault("folder_info", {})
            updated.setdefault("sources", {})
            for dest in dests:
                parts = dest.strip("/").split("/")
                leaf = parts[1] if len(parts) > 1 else parts[0]
                bucket = updated["aliases"].get(leaf) or []
                for alias in aliases:
                    if alias not in bucket:
                        bucket.append(alias)
                if bucket:
                    updated["aliases"][leaf] = bucket
                display_title = pick_title_for_folder(aliases, dest) or aliases[0]
                apply_folder_info_safe(updated, dest, display_title, cover)
                if mal_id:
                    info = updated["folder_info"].get(dest) or {}
                    info["mal_id"] = mal_id
                    info["mal_title"] = str(data.get("mal_title") or "").strip()
                    info["mal_cached_at"] = now_iso()
                    updated["folder_info"][dest] = info
                if provider_url or provider_icon or provider_name:
                    entry = {
                        "provider": provider_name or urllib.parse.urlparse(provider_url).hostname or "RAW Source",
                        "url": provider_url,
                        "icon": provider_icon,
                        "note": (before.get("sources", {}).get(dest, {}) or {}).get("note")
                        or (f"Sumber mentah dari Release {plan['mappings'][0]['name']}" if plan["mappings"] else "Sumber mentah"),
                    }
                    updated["sources"][dest] = entry
                    if len(parts) >= 3:
                        updated["sources"]["/".join(parts[:2])] = entry
            if updated != before:
                write_json_atomic(ALIASES_FILE, updated)
                add_job_log(job, "[metadata] aliases.json diperbarui secara lokal.")
            else:
                add_job_log(job, "[metadata] aliases.json tidak berubah.")
            if data.get("commit_metadata"):
                commit_metadata(job)

        body_path = write_body_temp(job, plan["body"])
        tag = plan["tag"]
        title = plan["title"]
        assets = plan["assets"]
        clobber = bool(data.get("clobber"))

        if plan["action"] == "create":
            rc = run_job_cmd(job, [
                "release", "create", tag,
                "--title", title,
                "--notes-file", str(body_path),
                "--draft",
            ])
            if rc != 0:
                raise RuntimeError("Gagal membuat release draft.")
            for asset in assets:
                upload_args = ["release", "upload", tag, asset["path"]]
                if clobber:
                    upload_args.append("--clobber")
                rc = run_job_cmd(job, upload_args)
                if rc != 0:
                    raise RuntimeError(
                        f"Gagal mengunggah '{asset['name']}'. Release tetap draft; "
                        "perbaiki masalah lalu ulangi / unggah manual."
                    )
            if data.get("draft_only"):
                add_job_log(job, "[info] Release dibiarkan sebagai draft; pipeline belum terpicu.")
            else:
                rc = run_job_cmd(job, ["release", "edit", tag, "--draft=false"])
                if rc != 0:
                    raise RuntimeError("Aset sudah terunggah, tetapi gagal mempublikasikan draft.")
                add_job_log(job, "[✓] Release dipublikasikan; antrian pipeline akan memprosesnya.")
        else:
            # Upload aset dulu, baru ubah judul/body. Dengan urutan ini, saat
            # event `release edited` terpicu, aset sudah lengkap di GitHub.
            for asset in assets:
                upload_args = ["release", "upload", tag, asset["path"]]
                if clobber:
                    upload_args.append("--clobber")
                rc = run_job_cmd(job, upload_args)
                if rc != 0:
                    raise RuntimeError(f"Gagal mengunggah '{asset['name']}'.")
            rc = run_job_cmd(job, [
                "release", "edit", tag,
                "--title", title,
                "--notes-file", str(body_path),
            ])
            if rc != 0:
                raise RuntimeError("Gagal memperbarui judul/body release.")
            if plan.get("publishDraft"):
                rc = run_job_cmd(job, ["release", "edit", tag, "--draft=false"])
                if rc != 0:
                    raise RuntimeError("Gagal mempublikasikan release draft.")

        try:
            info = gh_json(["release", "view", tag, "--json", "url"])
            if info.get("url"):
                add_job_log(job, "[URL] " + info["url"])
        except Exception:
            pass
        job["status"] = "success"
        add_job_log(job, "[✓] Selesai.")
    except Exception as exc:
        job["status"] = "error"
        add_job_log(job, f"[✗] {exc}")
    finally:
        job["finished_at"] = now_iso()
        try:
            APPLY_LOCK.release()
        except RuntimeError:
            pass
        if body_path:
            try:
                body_path.unlink()
            except OSError:
                pass
        for name in job.get("temp_files", []):
            try:
                os.unlink(name)
            except OSError:
                pass


def start_job(plan: dict, data: dict) -> str:
    job_id = secrets.token_hex(8)
    job = {
        "id": job_id,
        "status": "running",
        "log": [],
        "started_at": now_iso(),
        "finished_at": None,
        "temp_files": [],
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    thread = threading.Thread(target=run_job, args=(job_id, plan, data), daemon=True)
    thread.start()
    return job_id


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "StreamReleaseUI/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[ui] " + (fmt % args) + "\n")

    def _send_json(self, obj, status: int = 200):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, text: str):
        payload = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise RuntimeError("Payload terlalu besar.")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"JSON tidak valid: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Payload harus berupa objek JSON.")
        return data

    def _check_token(self) -> bool:
        return self.headers.get("X-UI-Token") == UI_TOKEN

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/":
                template = TEMPLATE_FILE.read_text(encoding="utf-8")
                repo = resolve_gh_repo() or "(repo tidak terdeteksi)"
                page = (
                    template
                    .replace("__UI_TOKEN__", UI_TOKEN)
                    .replace("__REPO__", html.escape(repo))
                    .replace("__MAL_READY__", "true" if os.environ.get("MAL_CLIENT_ID") else "false")
                )
                self._send_html(page)
                return
            if parsed.path == "/api/state":
                self._send_json({
                    "repo": resolve_gh_repo(),
                    "mal": bool(os.environ.get("MAL_CLIENT_ID")),
                    "cwd": str(ROOT),
                })
                return
            if parsed.path == "/api/config":
                self._send_json({"config": load_ui_config()})
                return
            if parsed.path == "/api/releases":
                self._send_json({"releases": list_releases()})
                return
            if parsed.path == "/api/source":
                dest = (urllib.parse.parse_qs(parsed.query).get("dest") or [""])[0]
                data_aliases = read_json_file(
                    ALIASES_FILE, {"aliases": {}, "folder_info": {}, "sources": {}}
                )
                sources = data_aliases.get("sources") or {}
                key = find_source_key(sources, dest)
                self._send_json({"source": sources.get(key) if key else None})
                return
            if parsed.path == "/api/release":
                tag = (urllib.parse.parse_qs(parsed.query).get("tag") or [""])[0]
                release = get_release(tag)
                if not release:
                    self._send_json({"error": f"Release '{tag}' tidak ditemukan."}, 404)
                    return
                self._send_json({"release": release, "parsed": parse_existing_release(release)})
                return
            if parsed.path == "/api/job":
                job_id = (urllib.parse.parse_qs(parsed.query).get("id") or [""])[0]
                with JOBS_LOCK:
                    job = copy.deepcopy(JOBS.get(job_id))
                if not job:
                    self._send_json({"error": "Job tidak ditemukan."}, 404)
                    return
                self._send_json({"job": job})
                return
            self._send_json({"error": "Tidak ditemukan."}, 404)
        except Exception as exc:
            sys.stderr.write(traceback.format_exc())
            self._send_json({"error": str(exc)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._check_token():
            self._send_json({"error": "Token UI tidak valid. Muat ulang halaman."}, 403)
            return
        try:
            data = self._read_json()
            if parsed.path == "/api/config":
                types = data.get("types")
                categories = data.get("categories")
                if not isinstance(types, list) or not isinstance(categories, list):
                    raise RuntimeError("types dan categories harus berupa array.")
                self._send_json({"config": save_ui_config(types, categories)})
                return
            if parsed.path == "/api/preview":
                plan = build_plan(data)
                self._send_json({"plan": plan})
                return
            if parsed.path == "/api/apply":
                plan = build_plan(data)
                if not plan.get("ok"):
                    self._send_json({"error": "Validasi gagal.", "plan": plan}, 400)
                    return
                confirm = str(data.get("confirm") or "")
                if confirm != plan["tag"]:
                    self._send_json({"error": "Konfirmasi tag tidak cocok."}, 400)
                    return
                if data.get("clobber") and not data.get("clobber_ack"):
                    self._send_json({"error": "Centang konfirmasi clobber terlebih dahulu."}, 400)
                    return
                if not APPLY_LOCK.acquire(blocking=False):
                    self._send_json(
                        {"error": "Masih ada job lain yang berjalan. Tunggu sampai selesai."},
                        409,
                    )
                    return
                try:
                    job_id = start_job(plan, data)
                except Exception:
                    APPLY_LOCK.release()
                    raise
                self._send_json({"job_id": job_id})
                return
            if parsed.path == "/api/mal":
                query = str(data.get("q") or "").strip()
                self._send_json({"results": mal_search(query)})
                return
            if parsed.path == "/api/provider":
                self._send_json(provider_candidates(
                    str(data.get("url") or ""),
                    str(data.get("dest") or ""),
                ))
                return
            if parsed.path == "/api/probe-resolution":
                self._send_json(probe_resolution(data.get("assets")))
                return
            self._send_json({"error": "Tidak ditemukan."}, 404)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            sys.stderr.write(traceback.format_exc())
            self._send_json({"error": str(exc)}, 500)


def main() -> int:
    parser = argparse.ArgumentParser(description="Release Manager UI lokal (TUNDA 4)")
    parser.add_argument("--host", default="127.0.0.1", help="Alamat bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Jangan buka browser otomatis")
    parser.add_argument("--repo", default="", help="OWNER/REPO target (default: repo dari direktori ini)")
    args = parser.parse_args()

    global GH_REPO
    load_dotenv()
    if args.repo:
        GH_REPO = args.repo.strip()
    else:
        resolve_gh_repo()

    if not TEMPLATE_FILE.exists():
        print(f"[-] Template UI tidak ditemukan: {TEMPLATE_FILE}", file=sys.stderr)
        return 1
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("[!] Peringatan: UI ini hanya untuk lokal. Jangan buka ke jaringan publik.", file=sys.stderr)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print("=" * 64)
    print("STREAM RELEASE MANAGER (TUNDA 4)")
    print("=" * 64)
    print(f"Repo       : {GH_REPO or '(tidak terdeteksi)'}")
    print(f"MAL        : {'siap' if os.environ.get('MAL_CLIENT_ID') else 'MAL_CLIENT_ID belum diset'}")
    print(f"URL        : {url}")
    print("Tekan Ctrl+C untuk berhenti.")
    print("=" * 64)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[i] Berhenti.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
