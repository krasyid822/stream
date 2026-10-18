# 🎮 Retro Pixel Stream Hub

Aplikasi streaming video lokal berbasis **HLS ABR (Adaptive Bitrate Streaming)** dengan estetika desain **Retro Pixel Art 8-bit**.

---

## 📁 Panduan Penataan Folder & File di `RAW/`

Semua file mentah video (seperti `.mkv`, `.mp4`) yang akan dikonversi diletakkan di dalam folder `RAW/` dengan hierarki berikut:

```text
RAW/
└── <kategori>/
    └── <judul_anime_atau_media>/
        └── <nomor_season>/
            ├── [Doronime.id] GSYOS 17 [480p] [h265].mkv
            ├── [Doronime.id] GSYOS 18 [480p] [h265].mkv
            └── ...
```

### Contoh Penataan:
- `RAW/anime/gsyos/1/[Doronime.id] GSYOS 01.mkv`
- `RAW/anime/gsyos/2/[Doronime.id] GSYOS 17 [480p] [h265].mkv`
- `RAW/donghua/btth/5/[Donghua] BTTH 01.mp4`

> 💡 **Catatan Penamaan**: Nama folder di bawah `RAW/` **tidak case-sensitive** (misal: `GSYOS` atau `gsyos` akan otomatis diarahkan ke folder produksi `anime/gsyos/`).

---

## 🚀 Cara Penggunaan Skrip Konversi (`./hls.sh`)

Skrip `./hls.sh` mendukung **Multi-Input Batching**, **Auto-Cleanup**, dan **Auto-Metadata Generator**.

### 1. Konversi Tunggal (Single File)
```bash
./hls.sh "RAW/anime/gsyos/2/[Doronime.id] GSYOS 17 [480p] [h265].mkv"
```

### 2. Konversi Banyak File (Multi-Input Batching)
```bash
./hls.sh "RAW/anime/gsyos/2/[Doronime.id] GSYOS 17 [480p] [h265].mkv" "RAW/anime/gsyos/2/[Doronime.id] GSYOS 18 [480p] [h265].mkv"
```

### 3. Konversi Seluruh Isi Folder Sekaligus (Wildcard `*.mkv`)
```bash
./hls.sh RAW/anime/gsyos/2/*.mkv
```

---

## ⚙️ Fitur Otomatisasi Skrip:

1. **Auto-Routing ke Folder Produksi**:
   - Skrip secara otomatis menempatkan hasil konversi HLS ke lokasi produksi utama (misal: `anime/gsyos/2/Episode 17_hls`) di luar folder `RAW/`.
2. **Auto-Cleanup (Penghapusan Otomatis File Mentah)**:
   - Setelah proses HLS ABR, ekstraksi subtitle `.vtt`, dan pendaftaran metadata berhasil 100%, file mentah `.mkv` / `.mp4` di folder `RAW/` **akan otomatis dihapus bersih** untuk menghemat ruang penyimpanan.
3. **Pangkas Nama Episode (Clean Naming)**:
   - Tag panjang seperti `[Doronime.id]` atau `[480p] [h265]` otomatis dibuang dan nama episode dibersihkan menjadi `Episode 17`, `Episode 18`, dst.
4. **Auto-Pembaruan Metadata (`metadata.json`)**:
   - Episode baru langsung terdaftar dan dapat langsung diputar di Web UI tanpa perlu konfigurasi manual.

---

## 🖥️ Cara Menjalankan Web Application

Jalankan perintah HTTP server lokal di direktori utama:

```bash
python3 -m http.server 8000
```

Buka browser dan akses alamat:
```text
http://localhost:8000
```

## 📂 Fleksibilitas Struktur Folder di `RAW/`

Skrip mendukung penataan folder **dengan folder season** maupun **tanpa folder season**:

### 1. Struktur Menggunakan Folder Season:
```text
RAW/
└── anime/
    └── gsyos/
        └── 2/
            └── [Doronime.id] GSYOS 17 [480p] [h265].mkv
```
📁 Hasil Output: `anime/gsyos/2/Episode 17_hls/`

### 2. Struktur Tanpa Folder Season (Langsung Judul):
```text
RAW/
└── anime/
    └── fulldive rpg/
        └── [doronime.id] fulldive rpg [480p] 04.mkv
```
📁 Hasil Output: `anime/fulldive rpg/Episode 04_hls/`

---

## ✨ Fitur-Fitur Baru yang Telah Diperbarui:

1. **Format Nama Folder HLS Konsisten (`Episode <nomor>_hls`)**:
   - Nama folder HLS yang dihasilkan di tingkat direktori otomatis dipangkas dari judul file yang panjang menjadi `Episode 04_hls`, `Episode 14_hls`, dst.
2. **Ekstraksi Otomatis Websumber dari Tag File/Folder**:
   - Jika nama file/folder mengandung tag domain seperti `[doronime.id]`, skrip otomatis mengekstrak provider (`Doronime.id`), URL (`https://doronime.id`), dan ikon favicon tanpa perlu pendaftaran manual.
3. **Kategorisasi & Sub-label Hierarki Direktori pada Hasil Pencarian**:
   - Saat pengguna menggunakan fitur pencarian global di Web UI, setiap kartu media menampilkan sub-label path direktori (misal: `📁 anime/fulldive rpg`) untuk memudahkan navigasi.

---

## 📤 Otomatisasi Git Push Media Segmen `.ts` (`./push.sh`)

Karena segmen video HLS (`.ts`) berukuran cukup besar, skrip **`./push.sh`** (berbasis `git_smart_push.py`) disediakan untuk mengunggah file media ke GitHub secara otomatis dan aman dari batas maksimal komit (2.0 GB).

### Cara Penggunaan:
```bash
./push.sh
```

### Cara Kerja Skrip:
- Menghitung ukuran total berkas media `.ts` yang baru atau berubah.
- Otomatis membagi komit menjadi beberapa batch aman (maksimal **~1.2 GB per push**).
- Melakukan `git add`, `git commit`, dan `git push` berurutan hingga seluruh berkas media terunggah 100% tanpa risiko error batas ukuran push GitHub.

---

## 📥 Otomatisasi Git Pull & Pembersihan Segmen Lokal (`./pull.sh`)

Skrip **`./pull.sh`** (berbasis `git_smart_pull.py`) digunakan untuk mengambil update kode/metadata terbaru dari GitHub sekaligus **menghemat ruang penyimpanan lokal** dengan menghapus segmen `.ts` lokal:

### Cara Penggunaan:
```bash
./pull.sh
```

### Cara Kerja Skrip:
1. **Konfigurasi & Pull Blobless (`--filter=blob:none`)**: Mengaktifkan konfigurasi blobless pada remote origin sehingga saat `git pull`, Git hanya mengambil histori teks, metadata, playlist `.m3u8`, subtitle `.vtt`, dan poster `.jpg` tanpa mengunduh riwayat blob biner `.ts`.
2. **Pembersihan `.ts` di Working Directory**: Menghapus seluruh file segmen `.ts` di folder kategori lokal (misal: `anime/`, `donghua/`) untuk membebaskan ruang disk PC.
3. **`git update-index --assume-unchanged`**: Menandai file `.ts` agar Git mengabaikan hilangnya file `.ts` di lokal.
   - File `.ts` di repositori remote GitHub **tetap utuh dan terjaga 100%**.
   - Git tidak akan menganggap file `.ts` terhapus / modified saat melakukan commit/push di masa mendatang.
   - Pemutar HLS tetap memutar video secara online melalui CDN GitHub RAW.
4. **Pembersihan Database Objek Lokal (`.git/objects/pack`)**: Menjalankan repack blobless dan garbage collection (`git repack -a -d --filter=blob:none` & `git gc --prune=now`) untuk menyusutkan ukuran direktori `.git/` lokal.

# Aturan & Perbedaan Fungsi Berkas Metadata

Agar struktur data tetap rapi, tidak terjadi duplikasi manual, dan sistem berjalan secara otomatis:

### 1. 📁 `aliases.json` — *(Source File Utama / Konfigurasi Manual)*
- **Fungsi**: Tempat **manusia (pengembang)** mendaftarkan nama alternatif (judul Mandarin, Jepang, Indonesia), kata kunci pencarian, serta informasi situs penyedia media (*sources/provider*).
- **Aturan**: Segala pembaruan judul pencarian atau provider **HANYA dilakukan di berkas ini**.

### 2. 📄 `metadata.json` — *(Compiled Output File / Hasil Generasi Otomatis)*
- **Fungsi**: Berkas JSON kompilasi akhir yang dikonsumsi langsung oleh Web UI (`explorer.js`).
- **Aturan**: Berkas ini **DISUSUN OTOMATIS oleh skrip Python (`generate_hls.py`)**. Skrip akan membaca folder video fisik lalu me-merge informasi alias & source dari `aliases.json` secara otomatis. **Jangan mengedit berkas ini secara manual!**

---

## Penggunaan Widget Komentar (`komen.site.je`)

Widget komentar retro terpasang tepat di bawah HLS Player Monitor. Widget ini mendukung **Multi-Room Otomatis**:
- **Berdasarkan Media Diputar**: Setiap episode di dalam sub-direktori (misal `donghua/am` -> `Episode 1`) secara otomatis memiliki ruang komentar tersendiri dengan nama room unik `donghua_am_Episode_1`.
- **Berdasarkan Folder / Sub-Dir**: Ketika menelusuri folder tanpa memilih media, room otomatis menyesuaikan nama folder tersebut (misal `?room=donghua_am`).

| Tipe Navigasi | URL Room Komentar (`https://komen.site.je`) |
|---------------|---------------------------------------------|
| Library Utama | `https://komen.site.je/index.php?room=default` |
| Sub-Directory | `https://komen.site.je/index.php?room=donghua_am` |
| Media Episode | `https://komen.site.je/index.php?room=donghua_am_Episode_1` |

---

# Catatan
- **Dukungan Softsub WebVTT**: Softsub yang diekstrak menjadi file `.vtt` (misal: `sub_0_ind.vtt`) dari rilis softsub secara otomatis terdeteksi, tercatat di `metadata.json`, dan tombol toggle **SUBTITLE** pada pemutar video otomatis aktif.

---

# ⚙️ Pipeline Otomatis Rilis → HLS (GitHub Actions)

Rilis baru otomatis diproses oleh workflow `Process Release Archive & Auto HLS Stream Pipeline`
(`.github/workflows/process-release-hls.yml`) menjadi HLS ABR, lalu di-push ke repo `stream_drive`.

## Arsitektur 3 Job ( agar tidak kena limit 6 jam GitHub Actions )

| Job | Tugas | Output |
|-----|-------|--------|
| **1. Plan** | Membaca release body, memetakan folder tujuan, mengecek mana yang sudah ada di `stream_drive`, lalu menyusun **manifest chunk encode**. Tidak mengunduh file apa pun. | `plan.json` + output job |
| **2. Encode** | **Matrix job**: tiap job mengunduh arsipnya sendiri → ekstrak → memproses episode miliknya → upload hasilnya sebagai *artifact*. `fail-fast: false`, `max-parallel: 4`. | Artifact `hls_<id>` |
| **3. Publish** | Merge semua artifact → smart push ke `stream_drive` → sinkron `metadata.json` → push ke repo `stream`. Jalan **walaupun** sebagian shard gagal. | Media di `stream_drive` |

### Pembagian kerja antar job encode
- Satu grup arsip dipecah menjadi beberapa *shard* berdasarkan ukuran arsip
  (`shards = ukuran_arsip / HLS_CHUNK_SIZE_GB`, dibatasi `HLS_MAX_SHARDS`).
- Episode di dalam arsip dibagi **berdesar (stride)**: shard 0 mengambil episode 1, 3, 5, …,
  shard 1 mengambil episode 2, 4, 6, … sehingga beban kerja terbagi rata.
- Tidak ada yang perlu diisi manual: form `workflow_dispatch` hanya meminta `release_tag`,
  jumlah shard **dihitung otomatis** dari ukuran arsip.
- Kalau ingin menajamkan tuning, buat **repository variable**
  (*Settings → Secrets and variables → Actions → Variables → New repository variable*):

  | Variable | Default | Arti |
  |---|---|---|
  | `HLS_CHUNK_SIZE_GB` | `0.9` | Makin kecil = makin banyak job paralel |
  | `HLS_MAX_SHARDS` | `6` | Plafon shard per grup arsip |
  | `HLS_NATIVE_MODE` | `auto` | `auto` / `copy` / `encode` (lihat tabel di bawah) |
  | `HLS_NATIVE_PRESET` | `medium` | Preset rendition resolusi asli |
  | `HLS_DERIVED_PRESET` | `veryfast` | Preset rendition turunan |

### ⏱️ Anggaran waktu per job encode

Job encode dibatasi `timeout-minutes: 350` (batas keras runner GitHub-hosted = 360).
Rincian waktu satu shard, diukur dari log run `AM` (arsip 3,44 GB, 14 episode 1080p HEVC):

| Fase | Durasi | Catatan |
|---|---|---|
| Unduh arsip | ~1,5 mnt | 6 bagian diunduh berurutan, sebenarnya sangat cepat |
| **Ekstrak `zpaq`** | **~72 mnt** | Bottleneck tersembunyi; `zpaq` sangat lambat, diulang tiap shard |
| Encode | ~42 mnt/episode | 4 rendition (1080/720/480/360) jalan bersamaan, 1 thread masing-masing |
| Upload artifact | ~3 mnt | |

Per_release, durasi tiap shard ≈ `72 mnt + 42 mnt × episode_per_shard`.
Dengan `HLS_CHUNK_SIZE_GB = 0.9`, arsip 3,44 GB terbagi **4 shard × 4 episode ≈ 4j10m** — masih
memiliki ~1j40m cadangan. Nilai `1.2` menghasilkan 3 shard × 5 episode = 5j02m, **melewati
batas 5 jam** dan job dibunuh tepat setelah 5 jam kerja selesai.

> ⚠️ Jangan menaikkan `HLS_CHUNK_SIZE_GB` tanpa memperhitungkan fase ekstrak `zpaq`.
> roughly: shard harus tetap di bawah ~350 mnt.

## 🔄 Resume / Anti-Mulai-Ulang dari Nol
Sebelum encode, tiap episode dicek terhadap isi `stream_drive`. Episode yang `master.m3u8`-nya
sudah ada **dilewati**, bukan di-encode ulang. Artinya:
- Menjalankan ulang workflow setelah job kena limit / gagal → **melanjutkan**, bukan mengulang.
- Rilis yang hanya terisi sebagian (folder sudah ada tapi episode kurang) tetap bisa dilanjuti.

### Hasil parsial tetap dipublish
Job `publish` **tidak** bergantung pada `needs.encode.result`. Kalau satu shard timeout tapi
shard lain berhasil, artifact shard yang sukses tetap diunduh & di-push ke `stream_drive`.
Dulu langkah download/push dilewati begitu ada satu shard yang `cancelled`, sehingga
puluhan jam encode ikut terbuang. Job `publish` baru ditandai gagal di akhir *setelah*
media parsial tersimpan, sebagai penanda ada episode yang belum lengkap.

## 🎚️ Konfigurasi Encoding

| Environment Variable / Input | Default | Keterangan |
|------------------------------|---------|------------|
| `HLS_NATIVE_MODE` | `auto` | `auto` = stream-copy bila aman, `copy` = paksa, `encode` = tanpa stream-copy |
| `HLS_NATIVE_PRESET` | `medium` | Preset x264 untuk rendition **resolusi asli** (tidak diturunkan kualitasnya) |
| `HLS_DERIVED_PRESET` | `veryfast` | Preset x264 untuk rendition **turunan** (720p/480p/360p dari sumber yang lebih besar) |
| `HLS_CORES` | jumlah core runner | Jumlah core yang dibagi ke seluruh encoder (mencegah thread berebut) |
| `HLS_COPY_MAX_KEY_INTERVAL` | `10` | Batas interval keyframe sumber agar segmen HLS tetap rapi |
| `HLS_CHUNK_SIZE_GB` | `1.2` | Ukaran arsip target per shard encode (sharding otomatis) |
| `HLS_MAX_SHARDS` | `6` | Plafon jumlah shard per grup arsip |

> Semuanya diatur lewat **repository variables** (`Settings → Secrets and variables → Actions →
> Variables`), bukan lewat form `workflow_dispatch` yang hanya meminta `release_tag`.

### Stream-Copy pada Rendition Resolusi Asli
Agar hemat waktu **tanpa menurunkan kualitas**, rendition yang resolusinya sama persis dengan
sumber akan dilewati proses encode (`-c copy`) sehingga **100% identik dengan file asli**.
Syaratnya (dicek otomatis tiap video):
1. Kodek sumber **H.264** — kalau sumber HEVC/H.265, hasil stream-copy tidak akan bisa diputar
   browser, sehingga otomatis fallback ke encode.
2. Resolusi sumber persis sama dengan profil pada ladder (misal sumber 1080p → stream 1080p di-copy,
   720p/480p/360p di-encode).
3. Keyframe sumber seragam dan cukup rapat (default ≤ 10 detik).

Tersedia juga mode manual per perintah:
```bash
# paksa stream-copy (otomatis fallback ke encode bila tidak aman)
./hls.sh "RAW/anime/series/1/video.mkv" --native-mode copy

# atur preset
./hls.sh "RAW/anime/series/1/video.mkv" --native-preset medium --derived-preset veryfast
```

## 🏷️ Penamaan Folder Episode (`clean_episode_name`)

Nama file sumber dinormalisasi menjadi nama folder HLS. **Token musim dibuang lebih dulu**,
baru nomor episode dicari — ini penting, bukan sekadar kosmetik:

| Nama file sumber | Nama folder HLS | Catatan |
|---|---|---|
| `01.mkv` | `Episode 01` | |
| `E04.mkv` | `Episode 04` | |
| `FullDive RPG 01.mkv` | `FullDive RPG 01` | >2 kata, nama dipertahankan |
| `S2 01.mkv` | `Episode 01` | token `S2` **bukan** nomor episode |
| `Season 2 03.mkv` | `Episode 03` | |

> ⚠️ **Jangan pernah membalik urutan ini.** Kalau nomor episode dicari *sebelum* token musim
> dibuang, `S2 01` akan terbaca sebagai "Episode 2". Seluruh 12 episode musim 2 lalu menabrak
> satu folder yang sama, dan 11 di antaranya **hilang tanpa error** karena folder itu sudah
> ada. Ini terjadi sungguhan di `zero_tskma` musim 2–4 (±33 episode).

Sebagai pengaman, `resolve_unique_output_dir()` mendeteksi tabrakan nama dalam satu proses
dan memberi sufiks dari nama file sumber, alih-alih menimpa diam-diam.

### Deteksi musim yang belum terproses

Plan/release memakai dua tingkat ketat:

- Judul yang merujuk **satu** folder musim saja → boleh mencari ke folder induk
  (mis. `GSYOS` menulis `anime/gsyos/2/`, tapi drive menyimpannya datar di `anime/gsyos/`).
- Judul yang merunjuk **beberapa** musim → diperiksa **ketat per folder**.

Tanpa aturan ini, `JCTK` musim 2 yang kosong akan terlihat "sudah lengkap" hanya karena
musim 1 berisi episode — dan musim itu tidak akan pernah diproses.

## 🗂️ Mode Sinkron Metadata Saja

Kalau media **sudah ada** di `stream_drive` tapi `metadata.json` (indeks Web UI) basi,
jalankan workflow yang sama dengan opsi **`sync_only = true`**:

*Actions → Process Release Archive & Auto HLS Stream Pipeline → Run workflow*

| Input | Nilai |
|---|---|
| `release_tag` | **kosongkan** (tidak dipakai pada mode ini) |
| `sync_only` | ✅ centang |

Yang terjadi: job `0) Sinkron Metadata (saja)` menjalankan `--sync-metadata` lalu commit &
push `metadata.json` ke repo `stream`. Job `Plan`, `Encode`, dan `Publish` **dilewati**.

Hasilnya tampil di **Job Summary**: total media, jumlah per kategori, dan daftar episode.

Mode ini juga berguna sebagai **pemeriksaan cepat** — `metadata.json` di repo masih boleh
di-`git checkout HEAD -- metadata.json` bila ingin mengembalikan ke versi lama.

## 🔎 Checkup Rilis Otomatis (`checkups.py` + workflow `checkups.yml`)

Mengecek apakah arsip RAW pada setiap GitHub Release **benar-benar sudah terkonversi** menjadi HLS
di `stream_drive`, lalu **memberi tanda pada judul release**:

| Tanda | Arti |
|---|---|
| `✅` | Sudah lengkap & tidak ada masalah integritas |
| `⚠️ 13/26` | Belum lengkap (13 episode jadi dari total 26) |
| `❌ 0/12` | Belum ada sama sekali di `stream_drive` |

Contoh: `⚠️ 16/49 [RAW HARDSUB] Zero no Tsukaima`

**Kenapa tanda ditaruh di *judul*, bukan body?**
Body release dibaca `parse_release_body_lines()` untuk memetakan folder tujuan. Menempelkan tabel
status ke body akan membuat setiap barisnya terbaca sebagai **alias baru** dan mencemari `aliases.json`.
Nama release sendiri tidak pernah diparse, jadi menandainya di situ 100% aman.

Tanda bersifat **idempotent**: prefix lama (✅/⚠️/❌ + angka) dilepas dulu sebelum yang baru
ditempelkan, dan prefix bawaan seperti `[RAW]` / `[RAW HARDSUB]` tidak tersentuh.

### Konvensi subtitle: `[RAW]` vs `[RAW HARDSUB]`

| Tag di judul | Arti | Yang diharapkan |
|---|---|---|
| `[RAW HARDSUB]` | subtitle sudah **terpiksel** di dalam video | **tidak** ada `.vtt` terpisah |
| `[RAW]` | video punya data subtitle sendiri | **harus** ada `.vtt` |

`analyze_folder()` memakai **tag sebagai sumber utama** (mengingat release itu sendiri yang
menandainya), lalu isi `stream_drive` sebagai **pemeriksa silang**:

- Tag `[RAW HARDSUB]` dan memang tanpa `.vtt` → **wajar, diam** (tidak jadi derau).
- Tag `[RAW]` dan `.vtt` hilang di sebagian episode → **masalah** (`✅` → `⚠️`).
- Tag `[RAW]` tapi **nol** `.vtt` sama sekali → **masalah**: subtitle belum pernah di-encode.
- Tag `[RAW HARDSUB]` tapi ada `.vtt` → **catatan**: ternyata subtitle terpisah tersedia.
- Tanpa tag sama sekali → ditebak dari isi drive, ditandai `[hardsub]?` / `[softsub]?`.

Tanda `✅`/`⚠️ a/b` yang menempel di depan tag selalu dilepas dulu saat membaca tag, supaya
deteksi tetap bekerja setelah judul.release ditandai pada run sebelumnya.

### Cara menjalankan
```bash
# pratinjau semua release: aman, dan menampilkan judul baru yang akan dipakai
python3 checkups.py --dry-run

# laporan murni: tidak menyentuh judul, tidak menampilkan pratinjau pun
python3 checkups.py --no-mark

# cek satu tag saja
python3 checkups.py --tag ZERO-TSKMA --dry-run

# benar-benar menandai judul release + simpan laporan JSON
python3 checkups.py --json checkup_report.json

# verifikasi berat: pastikan semua segmen yang dirujuk playlist benar-benar ada
python3 checkups.py --tag FULLDIVE-RPG --deep --dry-run
```

Workflow-nya berjalan otomatis **harian (03:17 UTC)** dan bisa dipicu manual lewat
*Actions → Checkup Rilis RAW -> HLS → Run workflow* dengan opsi:
`release_tag`, `deep_check`, `mark_releases`, `dry_run`, `fail_on_incomplete`.

### Apa saja yang dicek
- Folder tujuan tiap release ada atau belum di `stream_drive` (toleran `anime/gsyos/2` vs
  `anime/gsyos`, `fulldive_rpg` vs `fulldive-rpg`).
- Jumlah episode vs jumlah yang dideklarasikan di body (`12 Episodes`).
- Integritas: `master.m3u8`, `poster.jpg`, `playlist.m3u8` per rendition, file `.ts` 0-byte,
  dan keselarasan jumlah segmen antar rendition.
- Penamaan episode: nomor melompat / kembar / episode tunggal tapi bukan `01`
  (gejala file sumber bernama `S2 01` yang ikut membawa nomor season).
- Subtitle: apakah `.vtt` sesuai tag judul `[RAW]` / `[RAW HARDSUB]` (lihat di atas).
- `--deep`: setiap segmen yang dirujuk playlist benar-benar ada di repo.

Hasil lengkap ditampilkan di **Job Summary** GitHub dan disimpan sebagai artifact `checkup-report`.

---

## 🧹 Penghematan Disk
Arsip yang sudah diekstrak langsung dihapus, `RAW_TEMP` dibersihkan per grup arsip, dan folder
`RAW/` dibersihkan di akhir job — agar runner 14 GB tidak penuh.

## 📤 Cara Job Publish Mendorong ke `stream_drive`
Repo `stream_drive` sudah **~15 GB / 19.000+ file `.ts`**, sehingga job publish memakai cara khusus:

1. **Clone ringkas (blobless + sparse tanpa `.ts`)**
   ```
   git clone --filter=blob:none --no-checkout --depth 1 <url> stream_drive_target
   git sparse-checkout init --no-cone
   git sparse-checkout set "/*" "!*.ts"
   git read-tree -mu HEAD
   ```
   File `.ts` lama tidak pernah diubah (hanya ditambahkan), jadi tidak perlu diunduh/materiilisasi.
   Yang dimuat hanya playlist, subtitle, dan poster (ratusan file kecil).
   *Aman untuk `git add`*: entri `.ts` lama berstatus *skip-worktree*, sehingga `git status` tetap
   bersih dan tidak pernah meng-commit penghapusan media lama.
2. **Push per artifact** — tiap artifact di-merge → langsung di-push → lalu artifact-nya dihapus.
   Episode bisa ditonton sebelum semua shard selesai, dan disk runner tidak menumpuk.
   Push yang gagal tidak menghentikan artifact berikutnya: file tetap tertahan di working tree
   dan ikut ter-push pada batch berikutnya.
3. **Timeout dinamis** — durasi push dihitung dari ukuran total artifact
   (`menit = ukuran_MB / 400 + 30`, dibatasi 60–330 menit), bukan angka statis.
4. Pengelompokan commit tetap memakai `git_smart_push.py` (batch ~500 MB per push).


## Sesi opencode
  Session   Perbaiki algoritma runner ffmpeg agar konversi be…
  Continue  opencode -s ses_eccba703cffek4irHS0Dtm1wrc

## Sumber legal untuk metadata
https://myanimelist.net/

## Catatan
fungsikan operasi direktori maju-mundur melalui tombol maju-mundur bawaan browser, agar proyek tidak tiba tiba tertutup.
script run.sh sepertinya perlu diupgrade.
cara set environment variable di github repo.
supaya g boros api mal, otomatis simpan ke metadata