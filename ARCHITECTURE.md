# Prinsip Arsitektur Bersih & Satu File Satu Fitur (Clean Architecture & One File One Feature)

Dokumen ini mendefinisikan standar arsitektur dan struktur kode yang wajib diikuti dalam proyek **stream**. Aturan ini dibuat untuk memastikan kode tetap bersih, mudah diuji, modular, dan memiliki skalabilitas yang tinggi.

---

## 1. Clean Architecture (Arsitektur Bersih)

Arsitektur Bersih memisahkan kode berdasarkan perhatiannya (*Separation of Concerns*). Secara umum, kode dibagi menjadi beberapa lapisan melingkar dengan aturan ketergantungan yang ketat: **Lapisan dalam tidak boleh mengetahui apa pun tentang lapisan luar.**

### Lapisan-Lapisan Arsitektur:

1. **Entities / Domain (Lapisan Terdalam)**
   - Berisi model bisnis inti dan logika bisnis dasar yang tidak bergantung pada database, framework, atau UI.
   - Tidak boleh mengimpor modul dari lapisan luar.

2. **Use Cases / Application Business Rules**
   - Berisi alur kerja spesifik dari aplikasi (misalnya: `GetMoviesUseCase`, `UploadVideoUseCase`).
   - Lapisan ini mengoordinasikan aliran data dari dan ke Entities.
   - Menggunakan abstraksi (interface/port) untuk berinteraksi dengan database atau layanan eksternal.

3. **Interface Adapters (Controllers, Gateways, Presenters)**
   - Mengubah data dari format yang nyaman untuk Use Cases ke format yang nyaman untuk database atau web framework (dan sebaliknya).
   - Berisi implementasi repository, controller API, handler, dll.

4. **Frameworks & Drivers (Lapisan Terluar)**
   - Tempat framework web (seperti Express, Fastify, Next.js), database (PostgreSQL, MongoDB), router, dan alat eksternal lainnya berada.
   - Lapisan ini bersifat dinamis dan mudah diganti tanpa mengganggu logika bisnis inti.

---

## 2. Satu File Satu Fitur (One File, One Feature / Single Responsibility)

Untuk menghindari file raksasa (*god files*) yang sulit dipelihara dan rentan terhadap konflik saat kolaborasi, kita menerapkan prinsip **Satu File Satu Fitur**:

### Aturan Utama:
- **Satu Kelas/Fungsi Utama per File:** Setiap file hanya boleh mengekspor satu komponen utama, satu Use Case, satu Controller, atau satu Model.
- **Nama File Mencerminkan Isinya:** Nama file harus mencerminkan fungsi tunggal tersebut secara spesifik (contoh: `get-movie-detail.usecase.ts` bukan `movies.ts` yang berisi semua fungsi terkait movie).
- **Struktur Folder Berbasis Fitur (Feature-Based Folder Structure):** Kelompokkan file berdasarkan fitur atau domain bisnisnya, bukan jenis kodenya saja.

### Contoh Struktur Folder yang Diterapkan:

```text
src/
├── domain/                  # Entitas bisnis inti (Shared/Core)
│   └── movie.entity.ts
│
├── features/                # Fitur-fitur aplikasi (Modular & Terisolasi)
│   ├── movies/              # Domain Movie
│   │   ├── usecases/        # Setiap use case memiliki file sendiri
│   │   │   ├── list-movies.usecase.ts
│   │   │   └── get-movie-detail.usecase.ts
│   │   │
│   │   ├── controllers/     # Setiap endpoint/handler memiliki file sendiri
│   │   │   ├── list-movies.controller.ts
│   │   │   └── get-movie-detail.controller.ts
│   │   │
│   │   └── repositories/
│   │       ├── movie.repository.interface.ts # Port (Domain/UseCase)
│   │       └── pg-movie.repository.ts        # Adapter (Implementasi DB)
│   │
│   └── users/               # Domain User
│       ├── usecases/
│       │   └── register-user.usecase.ts
│       └── ...
```

---

## 3. Mengapa Prinsip Ini Penting?

1. **Skalabilitas Mudah (Scalability):** Menambahkan fitur baru sesederhana menambahkan file/folder baru tanpa mengubah kode lama secara agresif.
2. **Kemudahan Pengujian (Testability):** Logika bisnis dapat diuji secara terisolasi tanpa memerlukan koneksi database nyata atau server web.
3. **Mengurangi Konflik Merge:** Git conflict sangat jarang terjadi karena developer bekerja pada file-file fitur yang berbeda secara terpisah.
4. **Keterbacaan Tinggi:** Developer baru dapat dengan mudah menemukan di mana logika tertentu berada hanya dengan melihat struktur folder.
