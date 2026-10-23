#!/usr/bin/env python3
"""Relokasi folder di stream_drive tanpa re-encode (Git Data API).

Penggunaan:
  python3 relocate_drive_folders.py --map "anime/tskb:anime/tskb/1" [--dry-run]
  python3 relocate_drive_folders.py --map-file mapping.txt [--dry-run]

Format mapping.txt (satu per baris): folder_lama -> folder_baru
"""
import argparse, json, subprocess, sys, time

DRIVE_REPO = "krasyid822/stream_drive"
BRANCH = "main"

def gh_api(method, path, data=None, params=""):
    cmd = ["gh", "api", f"repos/{DRIVE_REPO}/{path}{params}", "-X", method]
    if data is not None:
        cmd += ["--input", "-"]
    res = subprocess.run(cmd, input=(json.dumps(data) if data is not None else None),
                        capture_output=True, text=True)
    if res.returncode != 0:
        return None, (res.stderr or res.stdout).strip()
    try:
        return json.loads(res.stdout), None
    except Exception:
        return res.stdout, None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", action="append", default=[])
    ap.add_argument("--map-file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mappings = []
    for m in args.map:
        if ":" in m:
            a, b = m.split(":", 1)
            mappings.append((a.strip("/"), b.strip("/")))
    if args.map_file:
        with open(args.map_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "->" in line:
                    a, b = line.split("->", 1)
                elif ":" in line:
                    a, b = line.split(":", 1)
                else:
                    continue
                mappings.append((a.strip().strip("/"), b.strip().strip("/")))

    if not mappings:
        print("Tidak ada mapping. Gunakan --map atau --map-file.")
        sys.exit(1)

    # Ambil ref + commit + tree
    ref, err = gh_api("GET", "git/ref/heads/" + BRANCH)
    if not ref:
        print("Gagal ambil ref:", err); sys.exit(1)
    base_commit = ref["object"]["sha"]
    commit, err = gh_api("GET", f"git/commits/{base_commit}")
    if not commit:
        print("Gagal ambil commit:", err); sys.exit(1)
    base_tree = commit["tree"]["sha"]

    tree, err = gh_api("GET", f"git/trees/{base_tree}", params="?recursive=1")
    if not tree:
        print("Gagal ambil tree:", err); sys.exit(1)
    entries = tree.get("tree", [])

    # Transform paths
    new_entries = []
    moved = 0
    for e in entries:
        path = e["path"]
        new_path = path
        for old, new in mappings:
            if path == old or path.startswith(old + "/"):
                new_path = new + path[len(old):]
                moved += 1
                break
        if new_path != path:
            ne = dict(e)
            ne["path"] = new_path
            new_entries.append(ne)
        else:
            new_entries.append(e)

    # Hapus path lama: buat entri dengan sha null
    old_paths = set()
    for old, new in mappings:
        for e in entries:
            if e["path"] == old or e["path"].startswith(old + "/"):
                old_paths.add(e["path"])
    for p in old_paths:
        new_entries.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    print(f"Mapping: {mappings}")
    print(f"Entri dipindah: {moved}")
    print(f"Entri dihapus (lama): {len(old_paths)}")

    if args.dry_run:
        print("[DRY-RUN] Tidak ada perubahan yang dikirim.")
        return

    # Buat tree baru
    new_tree, err = gh_api("POST", "git/trees", {"base_tree": base_tree, "tree": new_entries})
    if not new_tree:
        print("Gagal buat tree:", err); sys.exit(1)
    new_tree_sha = new_tree["sha"]

    # Buat commit
    new_commit, err = gh_api("POST", "git/commits", {
        "message": "relocate: " + ", ".join(f"{a}->{b}" for a, b in mappings),
        "tree": new_tree_sha,
        "parents": [base_commit],
    })
    if not new_commit:
        print("Gagal buat commit:", err); sys.exit(1)

    # Update ref
    upd, err = gh_api("PATCH", "git/refs/heads/" + BRANCH, {"sha": new_commit["sha"], "force": False})
    if not upd:
        print("Gagal update ref:", err); sys.exit(1)
    print("Relokasi selesai. Commit:", new_commit["sha"])

if __name__ == "__main__":
    main()
