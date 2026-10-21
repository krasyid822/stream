#!/usr/bin/env python3
"""
Antrian HLS sederhana berbasis file (TUNDA 2).

File antrian disimpan di `.github/hls_queue.json` dan di-commit oleh workflow.
Satu dispatcher (workflow terpisah) memproses antrian satu per satu, jadi:
  - job yang sedang berjalan tidak dibatalkan;
  - berapa pun release yang diantrikan, tidak ada yang dibuang karena batas
    "pending" concurrency GitHub.

Perintah:
  enqueue <tag>   masukkan release ke antrian (idempotent)
  reconcile       selaraskan status dengan run GitHub + pilih tag berikutnya
  list            tampilkan isi antrian

`reconcile` menulis tag yang harus dijalankan ke `.github/hls_queue_next.txt`.
Workflow dispatcher yang melakukan commit + push, lalu memanggil
`gh workflow run process-release-hls.yml`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime

QUEUE_FILE = os.environ.get("HLS_QUEUE_FILE", ".github/hls_queue.json")
NEXT_FILE = os.environ.get("HLS_QUEUE_NEXT_FILE", ".github/hls_queue_next.txt")
MAIN_WORKFLOW = os.environ.get("HLS_MAIN_WORKFLOW", "process-release-hls.yml")

ACTIVE_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested"}
DISPATCH_GRACE_SECONDS = int(os.environ.get("HLS_QUEUE_DISPATCH_GRACE", "900"))
MAX_DISPATCH_ATTEMPTS = int(os.environ.get("HLS_QUEUE_MAX_ATTEMPTS", "2"))


def now() -> float:
    return time.time()


def parse_time(value) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def load_queue() -> dict:
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    data["items"] = [item for item in items if isinstance(item, dict) and item.get("tag")]
    return data


def save_queue(data: dict) -> None:
    parent = os.path.dirname(QUEUE_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    data["updated_at"] = now()
    with open(QUEUE_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def find_item(data: dict, tag: str):
    for item in data["items"]:
        if item.get("tag") == tag:
            return item
    return None


def gh_json(args: list[str]):
    try:
        proc = subprocess.run(
            ["gh", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("[-] gh CLI tidak ditemukan.", file=sys.stderr)
        return None
    except subprocess.CalledProcessError as exc:
        print(f"[-] gh {' '.join(args)} gagal (exit {exc.returncode}).", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return None
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        print(f"[-] Respons gh bukan JSON valid: {exc}", file=sys.stderr)
        return None


def fetch_runs():
    return gh_json([
        "run", "list",
        "--workflow", MAIN_WORKFLOW,
        "--limit", "100",
        "--json", "databaseId,status,conclusion,displayTitle,createdAt,url",
    ])


def run_tag(run: dict) -> str:
    title = str(run.get("displayTitle") or "").strip()
    prefix = "HLS: "
    if title.startswith(prefix):
        return title[len(prefix):].strip()
    return ""


def runs_for_tag(runs: list[dict], tag: str) -> list[dict]:
    return [run for run in runs if run_tag(run) == tag]


def latest_run(runs: list[dict]):
    if not runs:
        return None
    return max(runs, key=lambda run: parse_time(run.get("createdAt")))


def cmd_enqueue(tag: str) -> int:
    tag = (tag or "").strip()
    if not tag:
        print("[-] Tag release kosong.", file=sys.stderr)
        return 2

    data = load_queue()
    item = find_item(data, tag)
    if item:
        status = item.get("status")
        if status in {"queued", "dispatched"}:
            print(f"[i] {tag} sudah ada di antrian (status: {status}); tidak ditambahkan lagi.")
            return 0
        # Status failed/unknown: izinkan percobaan baru.
        item.update({
            "status": "queued",
            "requested_at": now(),
            "updated_at": now(),
            "attempts": 0,
            "dispatched_at": None,
            "last_error": None,
        })
        print(f"[✓] {tag} dimasukkan ulang ke antrian (percobaan baru).")
    else:
        data["items"].append({
            "tag": tag,
            "status": "queued",
            "requested_at": now(),
            "updated_at": now(),
            "attempts": 0,
            "dispatched_at": None,
            "last_error": None,
        })
        print(f"[✓] {tag} ditambahkan ke antrian.")

    save_queue(data)
    return 0


def cmd_reconcile() -> int:
    runs = fetch_runs()
    if runs is None:
        print("[-] Tidak bisa membaca daftar run GitHub; antrian tidak diubah.", file=sys.stderr)
        return 1

    data = load_queue()
    changed = False
    remove_tags: set[str] = set()

    for item in list(data["items"]):
        tag = item.get("tag")
        tag_runs = runs_for_tag(runs, tag)
        active = [run for run in tag_runs if run.get("status") in ACTIVE_STATUSES]
        requested_at = float(item.get("requested_at") or 0)
        recent = [
            run for run in tag_runs
            if parse_time(run.get("createdAt")) >= requested_at - 60
        ]
        completed = [
            run for run in recent
            if run.get("status") == "completed"
        ]
        latest_completed = latest_run(completed)

        if active:
            if item.get("status") != "dispatched":
                item["status"] = "dispatched"
                item["updated_at"] = now()
                changed = True
            continue

        if latest_completed:
            conclusion = latest_completed.get("conclusion")
            if conclusion == "success":
                remove_tags.add(tag)
                changed = True
                continue
            if item.get("status") in {"queued", "dispatched"}:
                item["status"] = "failed"
                item["updated_at"] = now()
                item["last_error"] = f"Run {latest_completed.get('databaseId')} selesai: {conclusion}"
                changed = True
                continue

        if item.get("status") == "dispatched":
            age = now() - float(item.get("dispatched_at") or requested_at or now())
            if age > DISPATCH_GRACE_SECONDS:
                attempts = int(item.get("attempts") or 0)
                if attempts < MAX_DISPATCH_ATTEMPTS:
                    item["status"] = "queued"
                    item["updated_at"] = now()
                    item["last_error"] = "Run tidak ditemukan setelah dispatch; dicoba ulang."
                else:
                    item["status"] = "failed"
                    item["updated_at"] = now()
                    item["last_error"] = "Run tidak pernah muncul setelah batas percobaan."
                changed = True

    if remove_tags:
        data["items"] = [item for item in data["items"] if item.get("tag") not in remove_tags]

    active_any = any(run.get("status") in ACTIVE_STATUSES for run in runs)
    next_tag = ""

    if not active_any:
        queued = sorted(
            [item for item in data["items"] if item.get("status") == "queued"],
            key=lambda item: float(item.get("requested_at") or 0),
        )
        if queued:
            item = queued[0]
            item["status"] = "dispatched"
            item["dispatched_at"] = now()
            item["updated_at"] = now()
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_error"] = None
            next_tag = item["tag"]
            changed = True

    if changed:
        save_queue(data)

    with open(NEXT_FILE, "w", encoding="utf-8") as fh:
        if next_tag:
            fh.write(next_tag + "\n")

    if active_any:
        print("[i] Ada run HLS yang masih aktif; dispatcher menunggu.")
    elif next_tag:
        print(f"[→] Berikutnya: {next_tag}")
    elif not data["items"]:
        print("[i] Antrian kosong.")
    else:
        print(f"[i] Tidak ada item yang siap dijalankan ({len(data['items'])} item tersimpan).")

    if remove_tags:
        print(f"[✓] Selesai & dihapus dari antrian: {', '.join(sorted(remove_tags))}")
    failed = [item for item in data["items"] if item.get("status") == "failed"]
    if failed:
        print("[!] Gagal (perlu enqueue ulang): " + ", ".join(item["tag"] for item in failed))

    return 0


def cmd_list() -> int:
    data = load_queue()
    if not data["items"]:
        print("[i] Antrian kosong.")
        return 0
    for item in sorted(data["items"], key=lambda i: float(i.get("requested_at") or 0)):
        print(
            f"{item.get('status', '?'):10} {item.get('tag')} "
            f"(attempts={item.get('attempts', 0)})"
        )
        if item.get("last_error"):
            print(f"           {item['last_error']}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 1

    command = sys.argv[1]
    if command == "enqueue":
        if len(sys.argv) < 3:
            print("Penggunaan: hls_queue.py enqueue <release_tag>", file=sys.stderr)
            return 2
        return cmd_enqueue(sys.argv[2])
    if command == "reconcile":
        return cmd_reconcile()
    if command == "list":
        return cmd_list()

    print(f"Perintah tidak dikenal: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
