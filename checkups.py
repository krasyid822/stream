#!/usr/bin/env python3
"""
Checkup Rilis RAW -> HLS

Memverifikasi apakah arsip video pada setiap GitHub Release sudah terkonversi menjadi
HLS di repo stream_drive, lalu memberi TANDA pada judul release tersebut.

Tanda pada judul (prefix, idempotent & tidak merusak prefix bawaan seperti "[RAW]"):
    ✅                 -> sudah lengkap & tidak ada masalah integritas
    ⚠️ <a>/<b>         -> belum lengkap (a episode jadi dari total b)
    ❌ 0/<b>           -> belum ada sama sekali

Judul release TIDAK dibaca oleh parser pipeline, jadi menanda di sini aman 100%.
(Isi BODY release tetap dipertahankan karena parser pipeline bergantung pada itu.)

Pengecekan integritas (murah, dari Git Tree API):
    - episode punya master.m3u8
    - ada poster.jpg
    - setiap rendition (stream_N) punya playlist.m3u8 + minimal 1 segmen
    - jumlah segmen antar rendition tidak jauh berbeda (indikasi encode tidak sinkron)
    - tidak ada file 0-byte

Pengejaan tambahan (opsional, --deep): semua segmen yang dirujuk playlist benar-benar ada.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from process_release_archive import gh_api, parse_release_body_lines  # noqa: E402

DRIVE_REPO = "krasyid822/stream_drive"
RAW_BASE = f"https://raw.githubusercontent.com/{DRIVE_REPO}/main/"
IGNORED_RELEASE_TAGS = {"PASSWORD"}

STATUS_DONE = "DONE"
STATUS_PARTIAL = "PARTIAL"
STATUS_MISSING = "MISSING"

BADGE = {STATUS_DONE: "✅", STATUS_PARTIAL: "⚠️", STATUS_MISSING: "❌"}
# Menghapus tanda lama (✅/⚠️/❌ + angka opsional) tanpa menyentuh prefix lain seperti [RAW]
BADGE_RE = re.compile(r"^(?:✅|⚠️|❌)(?:\s+\d+\/\d+)?\s*")

TREE_CACHE = {}


# ---------------------------------------------------------------------------
# Repo stream_drive
# ---------------------------------------------------------------------------

def load_drive_tree():
    """Mengambil seluruh pohon file stream_drive sekali saja."""
    if "all" in TREE_CACHE:
        return TREE_CACHE["all"]
    # Responsnya ~7 MB dan GitHub kadang membalas HTTP 500 acak -> percobakan lebih banyak
    out, err = gh_api("git/trees/main", "?recursive=1", retries=8)
    if out is None:
        print("[-] Tidak bisa membaca stream_drive. Pastikan DRIVE_PAT tersedia.")
        sys.exit(2)
    data = json.loads(out)
    if data.get("truncated"):
        print("[-] Peringatan: pohon stream_drive terpotong, hasil bisa kurang lengkap.")
    TREE_CACHE["all"] = data["tree"]
    return TREE_CACHE["all"]


def norm_segments(path):
    """Normalisasi segmen path untuk pencocokan longgar (case, '-'/'_')."""
    return [re.sub(r"[^a-z0-9]", "", s) for s in path.strip("/").split("/") if s]


def resolve_drive_folders(dest_folder, tree):
    """
    Mencari folder stream_drive yang mewakili folder tujuan dari release body.
    Toleran: 'anime/gsyos/2' vs drive 'anime/gsyos', 'fulldive_rpg' vs 'fulldive-rpg'.
    """
    paths = [t["path"] for t in tree]
    target = norm_segments(dest_folder)
    # Cari kandidat dari paling spesifik (panjang penuh) ke lebih pendek
    for length in range(len(target), 1, -1):
        prefix = target[:length]
        for p in paths:
            seg = norm_segments(p)
            if len(seg) >= length and seg[:length] == prefix:
                return "/".join(p.split("/")[:length])
    return None


# ---------------------------------------------------------------------------
# Analisis isi folder
# ---------------------------------------------------------------------------

def check_episode_numbering(episodes):
    """
    Mendeteksi nomor episode yang tidak wajar (melompat, kembar, atau tunggal tapi bukan 01).
    Gejala khas penamaan file sumber yang salah, mis. 'S2 01' ikut terbaca season-nya.
    """
    nums = []
    for ep in episodes:
        m = re.match(r"^(?:episode|ep|e)[\s_.-]*0*(\d+)", ep.strip(), re.IGNORECASE)
        if m:
            nums.append((int(m.group(1)), ep))
    if not nums:
        return None

    values = [n for n, _ in nums]
    if len(set(values)) < len(values):
        dup = sorted({n for n in values if values.count(n) > 1})
        return f"nomor episode kembar {dup} (kemungkinan beberapa season tertumpuk di satu folder)"
    ordered = sorted(values)
    if len(ordered) == 1 and ordered[0] != 1:
        return f"hanya 1 episode tapi bernomor {ordered[0]:02d} (kemungkinan penamaan file salah)"
    if ordered != list(range(ordered[0], ordered[0] + len(ordered))):
        return f"nomor episode tidak berurutan: {ordered[:12]}"
    if ordered[0] != 1:
        return f"episode mulai dari nomor {ordered[0]:02d}, bukan 01"
    return None


def declared_sub_mode(title):
    """
    Baca konvensi penandaan pada judul release:

      "[RAW HARDSUB]" -> subtitle sudah terpiksel di dalam video, jadi TIDAK diharapkan
                         ada file .vtt terpisah.
      "[RAW]"         -> video ini diharapkan punya data subtitle (.vtt terpisah).

    Mengembalikan "hardsub", "softsub", atau None bila tidak ada tag sama sekali.
    """
    # Judul bisa sudah diberi tanda (✅ / ⚠️ 13/26) oleh run sebelumnya, dan tanda itu
    # menempel di depan tag [RAW]. Kalau tidak dilepas dulu, tag tidak akan pernah
    # terdeteksi lagi setelah penandaan pertama.
    teks = strip_badge(title or "").upper()
    m = re.match(r"^\s*\[([^\]]{1,40})\]", teks)
    if not m:
        return None
    tag = m.group(1).strip()
    # Urutan penting: "HARDSUB" sendiri mengandung kata "SUB".
    if "HARDSUB" in tag or "HARD SUB" in tag or "BAKED" in tag:
        return "hardsub"
    if "SOFTSUB" in tag or "SOFT SUB" in tag or "SUB" in tag or "RAW" in tag:
        return "softsub"
    return None


def analyze_folder(drive_path, tree, declared_subs=None):
    """
    Memeriksa kelengkapan & integritas satu folder episode di stream_drive.
    Dipisah menjadi:
      issues : masalah nyata (menurunkan status menjadi ⚠️)
      notes  : catatan informatif (tidak menurunkan status)
    """
    prefix = drive_path + "/"
    # Hanya isi folder ini & anak langsungnya. Episode punya bentuk <folder>/<episode>_hls/*,
    # sehingga file yang terlalu dalam (mis. season di dalam season) tidak ikut terhitung
    # dan mencegah penghitungan ganda antar folder tujuan.
    sizes = {t["path"]: t.get("size", 0) for t in tree if t["path"].startswith(prefix)}
    types = {t["path"]: t["type"] for t in tree if t["path"].startswith(prefix)}

    episodes = sorted({
        "/".join(p[len(prefix):].split("/")[:-1])
        for p in sizes
        if p.endswith("master.m3u8")
        and types.get(p) == "blob"
        and p[len(prefix):].count("/") == 1
    })

    issues = []
    notes = []
    detail = []

    # Kumpulkan file per episode lebih dulu: untuk menilai subtitle kita perlu
    # melihat kondisi SELURUH folder, bukan tiap episode secara terpisah.
    ep_files_map = {}
    for ep in episodes:
        ep_prefix = prefix + ep + "/"
        ep_files_map[ep] = {p[len(ep_prefix):] for p in sizes if p.startswith(ep_prefix)}

    # Sumber utama: konvensi tag di judul release ("[RAW]" = ada subtitle terpisah,
    # "[RAW HARDSUB]" = subtitle sudah terpiksel di video). Itu yang paling bisa
    # diandalkan, karena memang release itu sendiri yang menandainya.
    # Isi drive dipakai sebagai PEMERIKSA SILANG, bukan sebagai penentu.
    ep_punya_sub = {ep for ep, files in ep_files_map.items()
                    if any(f.endswith(".vtt") for f in files)}
    dari_data = "softsub" if ep_punya_sub else "hardsub"
    subs = declared_subs or dari_data
    softsub = (subs == "softsub")

    # Kalau tag dan isi drive bertentangan, itu masalah nyata yang harus terlihat.
    if episodes:
        if declared_subs == "softsub" and not ep_punya_sub:
            issues.append(f"{drive_path}: ditandai [RAW] (diharapkan ada subtitle) "
                          f"tapi tidak ada .vtt sama sekali")
        elif declared_subs == "hardsub" and ep_punya_sub:
            notes.append(f"{drive_path}: ditandai hardsub tapi {len(ep_punya_sub)} "
                         f"episode punya .vtt (subtitle terpisah tersedia)")

    for ep in episodes:
        ep_files = ep_files_map[ep]

        if "poster.jpg" not in ep_files:
            issues.append(f"{ep}: poster.jpg tidak ada")
        # Hardsub sengaja TIDAK dicatat: tidak ada subtitle terpisah yang diharapkan,
        # jadi catatan itu cuma derau. Hilangnya subtitle baru jadi masalah bila softsub.
        if softsub and ep not in ep_punya_sub:
            issues.append(f"{ep}: subtitle .vtt hilang, padahal release ini punya subtitle")

        renditions = sorted({f.split("/")[0] for f in ep_files
                             if re.fullmatch(r"stream_\d+/.+\.ts", f)})
        if not renditions:
            issues.append(f"{ep}: tidak ada rendition/segmen .ts")
            continue

        seg_counts = {}
        for r in renditions:
            n = len([f for f in ep_files if f.startswith(r + "/") and f.endswith(".ts")])
            seg_counts[r] = n
            if n == 0:
                issues.append(f"{ep}: {r} tidak punya segmen .ts")
            if f"{r}/playlist.m3u8" not in ep_files:
                issues.append(f"{ep}: {r}/playlist.m3u8 hilang")

        # Rendition ber-bitrate lebih rendah boleh menggabung segmen; yang penting durasinya sama.
        counts = [c for c in seg_counts.values() if c > 0]
        if counts:
            delta = max(counts) - min(counts)
            if delta > max(5, 0.15 * max(counts)):
                notes.append(f"{ep}: jumlah segmen berbeda antar rendition {seg_counts}")

        zero = [f for f in ep_files if f.endswith(".ts") and sizes.get(prefix + ep + "/" + f) == 0]
        if zero:
            issues.append(f"{ep}: {len(zero)} file .ts 0-byte")

        detail.append({"episode": ep, "segmen": seg_counts})

    numbering = check_episode_numbering(episodes)
    if numbering:
        notes.append(f"{drive_path}: {numbering}")

    return {"folder": drive_path, "episode": episodes,
            "issues": issues, "notes": notes, "detail": detail,
            "subs": subs, "subs_source": "tag" if declared_subs else "data"}


def deep_check_folder(drive_path, tree):
    """
    Verifikasi berat: pastikan setiap segmen yang dirujuk playlist benar-benar ada.
    Hanya dipakai bila --deep (memakai banyak permintaan HTTP).
    """
    prefix = drive_path + "/"
    existing = {t["path"] for t in tree if t["path"].startswith(prefix)}
    problems = []

    ep_paths = sorted({p[: -len("/master.m3u8")] for p in existing if p.endswith("/master.m3u8")})
    for ep_full in ep_paths:
        ep_name = ep_full.rsplit("/", 1)[-1]
        try:
            master = fetch_text(f"{ep_full}/master.m3u8")
        except Exception as e:
            problems.append(f"{ep_name}: gagal ambil master.m3u8 ({e})")
            continue
        for ref in re.findall(r"^(?!#)(\S*playlist\.m3u8)$", master, re.M):
            pl_full = f"{ep_full}/{ref}"
            if pl_full not in existing:
                problems.append(f"{ep_name}: playlist tidak ada -> {ref}")
                continue
            try:
                playlist = fetch_text(pl_full)
            except Exception as e:
                problems.append(f"{ep_name}/{ref}: gagal ambil playlist ({e})")
                continue
            pl_dir = pl_full.rsplit("/", 1)[0]
            for seg in re.findall(r"^(?!#)(\S+\.ts)$", playlist, re.M):
                seg_full = f"{pl_dir}/{seg}"
                if seg_full not in existing:
                    problems.append(f"{ep_name}/{ref}: segmen hilang -> {seg}")
    return problems


def fetch_text(path):
    url = RAW_BASE + urllib.parse.quote(path)
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


# ---------------------------------------------------------------------------
# GitHub Release
# ---------------------------------------------------------------------------

def list_release_tags(repo):
    out = subprocess.run(["gh", "release", "list", "-R", repo, "--limit", "100",
                          "--json", "tagName"], capture_output=True, text=True)
    if out.returncode != 0:
        print(f"[-] Gagal mengambil daftar release: {out.stderr.strip()[:200]}")
        sys.exit(2)
    return [r["tagName"] for r in json.loads(out.stdout or "[]")]


def get_release(repo, tag):
    """
    Mengambil body & judul release.
    Field judul berbeda antar versi gh ('name' di versi lama, 'title' di versi baru),
    jadi dicoba dua-duanya.
    """
    for field in ("name", "title"):
        res = subprocess.run(["gh", "release", "view", tag, "-R", repo, "--json", f"body,{field}"],
                             capture_output=True, text=True)
        if res.returncode == 0:
            data = json.loads(res.stdout or "{}")
            return {"body": data.get("body") or "", "title": data.get(field) or tag}
    return None


def declared_episode_count(body):
    m = re.search(r"(\d+)\s*episodes?\b", body or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


def compose_badge(status, found, expected):
    if status == STATUS_DONE:
        return BADGE[STATUS_DONE]
    if expected:
        return f"{BADGE[STATUS_MISSING if found == 0 else STATUS_PARTIAL]} {found}/{expected}"
    return BADGE[STATUS_MISSING if found == 0 else STATUS_PARTIAL]


def strip_badge(title):
    return BADGE_RE.sub("", title or "").strip()


def apply_badge(title, badge):
    """Menempelkan tanda di depan judul tanpa merusak prefix yang sudah ada."""
    return f"{badge} {strip_badge(title)}".strip()


def mark_release(repo, tag, new_title, dry_run=False):
    if dry_run:
        print(f"    [dry-run] judul akan jadi: {new_title}")
        return True
    res = subprocess.run(["gh", "release", "edit", tag, "-R", repo, "--title", new_title],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(f"    [-] gagal menandai: {res.stderr.strip()[:200]}")
        return False
    return True


# ---------------------------------------------------------------------------
# Cek per rilis
# ---------------------------------------------------------------------------

def check_release(repo, tag, tree, deep=False):
    info = get_release(repo, tag)
    if info is None:
        print(f"[?] {tag}: tidak bisa membaca release, dilewati.")
        return None

    body = info.get("body") or ""
    title = info.get("title") or tag
    file_mapping, aliases, source_info = parse_release_body_lines(body)

    dests = sorted(set(file_mapping.values()))
    if not dests:
        slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", tag).lower()
        dests = [f"anime/{slug}"]

    rows = []
    found_total = 0
    issues = []
    notes = []
    seen_drive_folders = {}

    for dest in dests:
        drive_path = resolve_drive_folders(dest, tree)
        if drive_path is None:
            rows.append({"folder": dest, "found": 0, "issues": ["folder belum ada di stream_drive"]})
            issues.append(f"{dest}: belum ada di stream_drive")
            continue

        # Dua folder tujuan (mis. season 1 & 2) bisa menunjuk ke folder drive yang sama
        # karena di drive disimpan datar -> jangan dihitung dua kali.
        if drive_path in seen_drive_folders:
            note = (f"{dest} menunjuk ke folder yang sama dengan "
                    f"{seen_drive_folders[drive_path]} ({drive_path}) -> tidak dihitung dua kali")
            rows.append({"folder": dest, "drive_folder": drive_path, "found": 0,
                         "issues": [], "notes": [note], "duplicate_of": seen_drive_folders[drive_path]})
            notes.append(note)
            continue
        seen_drive_folders[drive_path] = dest

        res = analyze_folder(drive_path, tree, declared_subs=declared_sub_mode(title))
        found_total += len(res["episode"])
        issues.extend(res["issues"])
        notes.extend(res["notes"])
        if deep:
            deep_problems = deep_check_folder(drive_path, tree)
            if deep_problems:
                issues.extend(deep_problems)
                res["issues"] = res["issues"] + deep_problems
        rows.append({"folder": dest, "drive_folder": drive_path,
                     "found": len(res["episode"]), "issues": res["issues"],
                     "notes": res["notes"], "episodes": res["episode"],
                     "subs": res.get("subs", ""),
                     "subs_source": res.get("subs_source", "data")})

    expected = declared_episode_count(body)
    if found_total == 0:
        status = STATUS_MISSING
    elif expected and found_total < expected:
        status = STATUS_PARTIAL
    else:
        status = STATUS_DONE

    # Masalah integritas menurunkan status walau jumlah episode sudah cukup
    if issues and status == STATUS_DONE:
        status = STATUS_PARTIAL

    badge = compose_badge(status, found_total, expected)
    new_title = apply_badge(title, badge)

    return {
        "tag": tag,
        "title": title,
        "new_title": new_title,
        "status": status,
        "badge": badge,
        "found": found_total,
        "expected": expected,
        "rows": rows,
        "issues": issues,
        "notes": notes,
        "changed": new_title != title,
    }


def main():
    ap = argparse.ArgumentParser(description="Checkup rilis RAW -> HLS & penandaan judul release")
    ap.add_argument("--repo", default="krasyid822/stream")
    ap.add_argument("--tag", default="", help="Hanya cek 1 tag (kosong = semua)")
    ap.add_argument("--deep", action="store_true", help="Verifikasi semua segmen yang dirujuk playlist")
    ap.add_argument("--no-mark", action="store_true", help="Jangan mengubah judul release")
    ap.add_argument("--dry-run", action="store_true", help="Tampilkan hasil tanpa mengubah apa pun")
    ap.add_argument("--fail-on-incomplete", action="store_true", help="Exit 1 bila ada rilis belum lengkap")
    ap.add_argument("--json", default="", help="Simpan laporan JSON ke file")
    args = ap.parse_args()

    print("==========================================================")
    print("🔎 CHECKUP RILIS RAW -> HLS")
    print("==========================================================")

    tree = load_drive_tree()
    print(f"[+] stream_drive: {len(tree)} entri diperiksa\n")

    tags = [args.tag] if args.tag else list_release_tags(args.repo)
    tags = [t for t in tags if t.upper() not in IGNORED_RELEASE_TAGS]

    results = []
    for tag in tags:
        res = check_release(args.repo, tag, tree, deep=args.deep)
        if not res:
            continue
        results.append(res)

        exp = f"{res['found']}/{res['expected']}" if res["expected"] else str(res["found"])
        mark = "  " if res["changed"] else "= "
        print(f"{mark}{BADGE[res['status']]} {tag:14} episode={exp:7} | "
              f"{len(res['issues'])} masalah, {len(res['notes'])} catatan")
        print(f"     {strip_badge(res['title'])}")
        for row in res["rows"]:
            dup = " (duplikat, tidak dihitung)" if row.get("duplicate_of") else ""
            tag_sub = f"  [{row['subs']}]" if row.get("subs") else ""
            if row.get("subs_source") == "data":
                tag_sub += "?"  # tidak ada tag di judul, jadi ditebak dari isi drive
            print(f"     - {row['folder']} -> {row.get('drive_folder', '(tidak ada)')} : "
                  f"{row['found']} episode{dup}{tag_sub}")
            for iss in row.get("issues", [])[:4]:
                print(f"         ⛔ {iss}")
            for nts in row.get("notes", [])[:2]:
                print(f"         ·  {nts}")
        if not args.no_mark and res["changed"]:
            mark_release(args.repo, tag, res["new_title"], dry_run=args.dry_run)
        elif not args.no_mark:
            print("     (tanda sudah sesuai, tidak diubah)")
        print()

    done = sum(1 for r in results if r["status"] == STATUS_DONE)
    partial = sum(1 for r in results if r["status"] == STATUS_PARTIAL)
    missing = sum(1 for r in results if r["status"] == STATUS_MISSING)
    total_eps = sum(r["found"] for r in results)
    total_missing = sum(max(0, (r["expected"] or r["found"]) - r["found"]) for r in results)

    print("==========================================================")
    print(f"📊 RINGKASAN: {len(results)} rilis | ✅ {done} selesai | ⚠️ {partial} sebagian | ❌ {missing} belum")
    print(f"   Total episode di stream_drive : {total_eps}")
    print(f"   Total episode belum ada       : {total_missing}")
    print("==========================================================")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"checked_at": datetime.now(timezone.utc).isoformat(),
                       "summary": {"total": len(results), "done": done, "partial": partial,
                                   "missing": missing, "episodes": total_eps,
                                   "episodes_missing": total_missing},
                       "releases": results}, f, indent=2, ensure_ascii=False)
        print(f"[✓] Laporan JSON: {args.json}")

    if args.fail_on_incomplete and (partial or missing):
        sys.exit(1)


if __name__ == "__main__":
    main()