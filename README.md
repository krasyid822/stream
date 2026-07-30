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

# Aturan & Perbedaan Fungsi Berkas Metadata

Agar struktur data tetap rapi, tidak terjadi duplikasi manual, dan sistem berjalan secara otomatis:

### 1. 📁 `aliases.json` — *(Source File Utama / Konfigurasi Manual)*
- **Fungsi**: Tempat **manusia (pengembang)** mendaftarkan nama alternatif (judul Mandarin, Jepang, Indonesia), kata kunci pencarian, serta informasi situs penyedia media (*sources/provider*).
- **Aturan**: Segala pembaruan judul pencarian atau provider **HANYA dilakukan di berkas ini**.

### 2. 📄 `metadata.json` — *(Compiled Output File / Hasil Generasi Otomatis)*
- **Fungsi**: Berkas JSON kompilasi akhir yang dikonsumsi langsung oleh Web UI (`explorer.js`).
- **Aturan**: Berkas ini **DISUSUN OTOMATIS oleh skrip Python (`generate_hls.py`)**. Skrip akan membaca folder video fisik lalu me-merge informasi alias & source dari `aliases.json` secara otomatis. **Jangan mengedit berkas ini secara manual!**

---

# Catatan

