# Laporan status proyek SIDIK

Rekap pekerjaan PR #1 – #12, per 4 Oktober 2026 (UTC), main di commit
`5267c48`. Laporan ini adalah potret pada tanggal tersebut. Perbarui atau
tandai usang setelah ada perubahan besar, terutama setelah ada data board.

> **Belum ada satu pun hasil dari hardware.** Semua angka PUF di laporan ini
> adalah keluaran model dengan parameter asumsi, bukan pengukuran.
>
> **Proyek Quartus DE10-Nano belum pernah dikompilasi**, karena Quartus tidak
> tersedia di lingkungan pengembangan. Pengujiannya baru offline. Hal yang
> sama berlaku untuk generator kunci lengkap (`rtl/sidik_avmm.v`): ia baru
> berjalan di simulasi.
>
> **Yang sudah terbukti:** perilaku RTL di simulasi (cocotb/Icarus),
> kesetaraan RTL dengan model Python, dan sintesis generik (yosys). Semua CI
> di main hijau.

## 1. Ringkasan

SIDIK adalah kumpulan blok keamanan hardware untuk Tiny Tapeout / FPGA:
- core SHA-256 (Shaman, pihak ketiga, GPL-3.0);
- generator kunci berbasis RO-PUF dengan secure sketch SECDED.

Dalam dua belas PR yang semuanya sudah di-merge ke main, repositori ini
sekarang berisi:

- **Model perilaku dan studi Monte Carlo** generator kunci RO-PUF, termasuk
  enrollment dua suhu dan key-check value (KCV).
- **RTL tersimulasi:**
  - ring oscillator dan array 1.024 RO;
  - pengukur pasangan dengan prescaler dan sinkronisasi 50 MHz;
  - core Avalon-MM untuk karakterisasi;
  - dekoder SECDED (72,64) yang bit-exact dengan model;
  - fuzzy extractor (`fuzzy_ext.v`): enrollment dan rekonstruksi yang
    identik dengan model pada 200 chip virtual;
  - derivasi K, HMAC-SHA256, ID dan KCV di atas core Shaman
    (`sidik_crypto.v`), dengan latensi tetap;
  - generator kunci lengkap di balik register Avalon-MM (`sidik_avmm.v`):
    RO → fuzzy extractor → crypto, tanpa jalur baca ke K atau data mentah di
    build rilis, dengan tamper dan clear asinkron.
- **Jalur karakterisasi FPGA:**
  - proyek Quartus DE10-Nano dengan JTAG-to-Avalon Master;
  - skrip System Console;
  - analisis data (`sw/analyze.py`, `fpga/char/analyze.py`);
  - panduan tim ([`char_howto.md`](char_howto.md)).

Langkah paling menentukan berikutnya adalah **build pertama di Quartus dan
pengukuran di board**. Tanpa itu, parameter model (σ_process, σ_jitter,
σ_tempco) tetap asumsi, dan begitu juga semua kesimpulan tentang τ, tingkat
kegagalan dan mode enrollment.

## 2. Status verifikasi

| Komponen | Sudah terbukti | Belum terbukti |
|---|---|---|
| Model PUF (`model/`) | 25 unit test; Monte Carlo 100 chip × 1.000 rekonstruksi per konfigurasi | Parameter adalah asumsi; belum dicocokkan ke data nyata |
| SECDED (`rtl/secded72.v`) | <ul><li>72 galat 1 bit terkoreksi</li><li>2.556 galat 2 bit terdeteksi</li><li>10.000 vektor acak identik dengan model</li><li>2 mutan terbunuh</li><li>sintesis yosys bersih</li></ul> | Belum disintesis untuk FPGA/ASIC target |
| RO + pengukur (`rtl/ro_cell.v`, `ro_array.v`, `puf_meas.v`) | cocotb dengan periode RO acak: <ul><li>count persis sesuai prediksi</li><li>tanda dan \|Δ\| benar</li><li>urutan pasangan benar</li><li>hanya satu pasangan aktif</li><li>2 mutan manual tertangkap</li></ul> | Perilaku RO di silikon (frekuensi, jitter, tempco); fmax T-FF pertama |
| Core Avalon-MM (`rtl/ropuf/`) | 5 test lewat register; yosys: 1.024 NAND + 4.096 inverter tetap utuh | Integrasi nyata di Platform Designer |
| Proyek Quartus (`fpga/char/quartus/`) | <ul><li>nama di QSF/SDC ada di RTL</li><li>QSF/SDC lolos parse Tcl</li><li>top level terkompilasi (iverilog + stub)</li></ul> | Kompilasi Quartus, Ignored Assignments, timing, region LogicLock, pin |
| Skrip System Console | Protokol register dan format CSV diuji terhadap mock di tclsh | Akses JTAG nyata; cara System Console meneruskan argumen |
| Analisis (`sw/analyze.py`) | <ul><li>20 test</li><li>keputusan rekonstruksi identik dengan model pada vote yang sama (300 percobaan)</li><li>4 mutan tertangkap</li></ul> | Belum pernah dijalankan pada data board |
| Fuzzy extractor (`rtl/fuzzy_ext.v`) | <ul><li>200 chip virtual, 768 rekonstruksi: helper, kunci, `fail`, `attempts`, `kcv_caught` identik dengan model</li><li>mencakup enrollment gagal, dua suhu, ukur ulang, gagal, KCV menangkap miskoreksi</li><li>penghapusan buffer diperiksa; 8 mutan terbunuh</li></ul> | Dengan race dari RO nyata; ukuran (~24 ribu sel generik) belum dioptimalkan |
| Crypto (`rtl/sidik_crypto.v`) | <ul><li>1.000 kunci/challenge acak identik dengan `hashlib`/`hmac` dan model</li><li>latensi tetap: 777 siklus (derive), 2.833 siklus (HMAC)</li><li>0 pelanggaran protokol Shaman; state core terhapus setelah tiap operasi</li><li>4 mutan terbunuh</li></ul> | Kanal samping (daya/EM); sintesis untuk target |
| Generator kunci (`rtl/sidik_avmm.v`) | <ul><li>alur ENROLL → RECONSTRUCT → AUTH benar (ID, KCV, RESP = `hmac` Python)</li><li>pemindaian 64 alamat: tidak ada K, bit kunci atau counter mentah di build rilis; `CHAR_BUILD` memang memperlihatkan counter mentah</li><li>tamper pada siklus acak: semua state nol dalam ≤ 3 tepi clock; TAMPERED bertahan sampai `rst`</li><li>state ilegal → CLEAR; 3 mutan terbunuh</li><li>yosys: ~64 ribu sel generik, 0 masalah</li></ul> | Di FPGA; dengan `LOG2N = 14` dan `N_ENROLL = 16` (simulasi memakai 8 dan 4); bukti struktural tidak adanya jalur baca; timing reset asinkron `zeroize` |

## 3. Riwayat pekerjaan (PR #1 – #12)

Semua PR di-merge setelah CI hijau. Durasi adalah run CI di main setelah
merge. Tanggal dalam UTC.

| # | Merge | Isi | CI main |
|---|---|---|---|
| 1 | 3 Okt | <ul><li>struktur repo</li><li>core Shaman (GPL-3.0) di `rtl/third_party/`</li><li>[`baselines.md`](baselines.md): RO-PUF litneet64, ECC_test1, lisensi Apache-2.0</li><li>requirements, Makefile, CI GitHub Actions (Node 24)</li><li>LICENSE GPL-3.0, header SPDX, pemegang hak cipta Universitas Sriwijaya</li></ul> | hijau, 38 dtk |
| 2 | 3 Okt | Model generator kunci RO-PUF + Monte Carlo (σ 0,5/1/2 %, τ 0–128); KCV 32 bit di helper data | hijau, 27 dtk |
| 3 | 3 Okt | Enrollment dua suhu (25 + 85 °C, −40 + 85 °C) di model | hijau, 36 dtk |
| 4 | 3 Okt | Skrip karakterisasi `fpga/char/` (akuisisi simulasi, fitting parameter, uji linearitas tempco) | hijau, 36 dtk |
| 5 | 3 Okt | RTL RO-PUF pertama di `rtl/ropuf/` dengan register Avalon-MM; `synth_check.py` | hijau, 61 dtk |
| 6 | 3 Okt | `rtl/secded72.v` (Hamming diperluas); model dipindah dari Hsiao ke Hamming diperluas; uji mutasi | hijau, 88 dtk |
| 7 | 3 Okt | `rtl/ro_cell.v`, `ro_array.v`, `puf_meas.v` (prescaler, sinkronisasi 50 MHz); `rtl/ropuf` dibangun ulang di atasnya | hijau, 90 dtk |
| 8 | 3 Okt | Proyek Quartus DE10-Nano, skrip System Console, `sw/analyze.py`, [`char_howto.md`](char_howto.md) | hijau, 151 dtk |
| 9 | 3 Okt | Laporan status ini (`docs/status.md`) | hijau, 91 dtk |
| 10 | 4 Okt | `rtl/fuzzy_ext.v`: enrollment/rekonstruksi sesuai model, penghapusan buffer, uji 200 chip virtual, uji mutasi | hijau, 4 mnt 47 dtk |
| 11 | 4 Okt | `rtl/sidik_crypto.v`: derivasi K, HMAC, ID, KCV di atas Shaman, latensi tetap, uji 1.000 kasus | hijau, 7 mnt 23 dtk |
| 12 | 4 Okt | <ul><li>`rtl/sidik_avmm.v`: register Avalon-MM, build rilis/`CHAR_BUILD`, tamper dan clear asinkron</li><li>port `zeroize` di `fuzzy_ext`/`sidik_crypto`</li><li>simulasi `ro_array` ~4× lebih cepat</li><li>batas waktu CI 30 menit</li></ul> | hijau, 6 mnt 57 dtk |

## 4. Temuan model (label: model)

> **Semua angka di bagian ini adalah keluaran model.** Parameternya
> asumsi (σ_jitter 3·10⁻⁴, σ_tempco 2,5·10⁻⁵ /°C). Angka-angka ini menunjukkan
> arah desain, bukan sifat chip nyata. Sumber:
> [`puf-model/results.md`](puf-model/results.md) dan
> [`puf-model.md`](puf-model.md).

### 4.1 Respons mentah 512 pasangan (model)

| σ_process | σ_Δ (count) | Reliability 25 °C | Reliability 85 °C | Uniformity / uniqueness |
|---|---|---|---|---|
| 0,5 % | 116 | 98,05 % | 90,26 % | ≈ 50 % / ≈ 50 % |
| 1,0 % | 229 | 98,99 % | 94,75 % | ≈ 50 % / ≈ 50 % |
| 2,0 % | 454 | 99,50 % | 97,43 % | ≈ 50 % / ≈ 50 % |

Uniformity dan uniqueness ≈ 50 % adalah bawaan model (RO i.i.d. Gaussian).
Model tidak memuat gradien spasial atau bias layout, jadi kedua angka ini
bukan hasil tentang hardware.

### 4.2 Tingkat kegagalan rekonstruksi, σ_process 1 % (model)

| τ (count) | Pasangan lolos (rata-rata) | Enrollment 25 °C | 25 + 85 °C | −40 + 85 °C |
|---|---|---|---|---|
| 0 | 512 | 6,6·10⁻¹ | 3,5·10⁻¹ | 8·10⁻⁵ |
| 32 | 457 | 9,1·10⁻² | 4,3·10⁻² | 0 (< 3·10⁻⁵) |
| 64 | 402 | 3,2·10⁻⁴ | 1,1·10⁻⁴ | 0 (< 3·10⁻⁵) |
| 128 | 298 | 0 (< 3·10⁻⁵) | 0 (< 3·10⁻⁵) | 0 (< 3·10⁻⁵) |

Rekonstruksi dilakukan pada T acak −40…85 °C dengan KCV aktif. Dengan KCV,
tidak ada kunci salah yang lolos diam-diam di konfigurasi mana pun
(< 3·10⁻⁵).

### 4.3 Kesimpulan utama (model)

1. **Hanya mask yang mengurangi galat akibat suhu.** Mayoritas-3 dan ukur
   ulang hampir tidak membantu, karena semuanya terjadi pada suhu yang sama.
2. **Tanpa KCV, SECDED menghasilkan kunci salah diam-diam.** KCV 32 bit
   mengubah semuanya menjadi kegagalan terdeteksi, tetapi tidak mengurangi
   jumlah kegagalan.
3. **Kegagalan terkonsentrasi per chip:** pada σ 1 %, τ 64, semua
   kegagalan berasal dari 7 dari 100 chip.
4. **τ dibatasi oleh σ_Δ:** minimal 216 dari 512 pasangan harus lolos,
   sehingga τ ≲ 0,8·σ_Δ. Karena itu τ harus ditetapkan dari σ_Δ hasil ukur,
   bukan dipatok di awal.
5. **Enrollment di dua sudut suhu (−40 + 85 °C) menghilangkan kegagalan
   suhu di model.** Hasil ini optimistis karena tempco dimodelkan linier.
   Biayanya: perlu chamber suhu saat provisioning, dan pasangan yang lolos
   lebih sedikit.
6. **Entropi kunci:** sindrom membocorkan 24 bit, sehingga K (256 bit) memuat
   ≥ 160 dan ≤ 192 bit entropi.
7. **Monte Carlo ini tidak bisa membuktikan tingkat kegagalan rendah.**
   Nol kejadian dari 10⁵ rekonstruksi hanya berarti batas atas < 3·10⁻⁵.

## 5. RTL

| Modul | Isi dan catatan |
|---|---|
| `ro_cell.v` | NAND enable + 4 inverter. Tiga jalur: <ul><li>`SIM`: perilaku, periode dari parameter/seed</li><li>`CYCLONEV`: LUT + primitif `lcell`</li><li>generik: instance `ro_stage` yang dipertahankan</li></ul> |
| `ro_array.v` | 1.024 RO dalam pasangan saling lepas (2i, 2i+1); hanya pasangan terpilih yang di-enable. Di simulasi, pasangan dipilih lewat pohon OR (fungsi sama, ~4× lebih cepat). |
| `puf_meas.v` | Dua counter balapan sampai 2¹⁴ siklus RO di belakang prescaler ripple /2; sinkronizer 2-FF; keluaran tanda dan \|Δ\| di domain 50 MHz. Catatan: <ul><li>resolusi Δ = 2 siklus RO</li><li>zona mati: \|Δ ideal\| ≤ 4 count terbaca Δ = 0</li><li>bias \|Δ\| rata-rata −3 count</li></ul> |
| `ropuf/ropuf_core.v`, `ropuf_avmm.v` | Core + slave Avalon-MM (ID, PARAMS, CTRL, PAIR, COUNT_A/B, DELTA, TIMEOUT). Catatan keamanan: `ropuf_avmm` membuka respons mentah dan hanya untuk karakterisasi. |
| `secded72.v` | Sindrom Hamming (72,64) diperluas: koreksi 1 bit, deteksi 2 bit; bit-exact dengan `model/secded.py`. |
| `fuzzy_ext.v` | Enrollment (mask 512 bit + sindrom sebagai helper data, satu fase per suhu) dan rekonstruksi (mayoritas 3, SECDED, ukur ulang, maks. 3 ronde, `fail`). KCV diperiksa oleh konsumen lewat `key_valid`/`key_good`. Buffer dihapus setelah dipakai; `zeroize` asinkron. |
| `sidik_crypto.v` | Di atas Shaman tanpa modifikasi: K = SHA-256(216 bit ‖ "SIDIK-K"), HMAC(K, challenge), ID, KCV. K tidak keluar dari modul. Latensi tetap; core di-reset setelah tiap hash; `zeroize` asinkron. |
| `sidik_avmm.v` | Generator kunci lengkap di balik slave Avalon-MM: CTRL (ENROLL, RECONSTRUCT, AUTH, CLEAR), STATUS, TAU, HELPER, CHAL, RESP, ID. Build rilis tanpa jalur baca ke K/data mentah; `CHAR_BUILD` menambah register race mentah. `tamper_n` (sinkronizer 2-FF) → clear asinkron, TAMPERED bertahan sampai `rst`. |
| `third_party/shaman/` | Core SHA-256 Pat Deegan (GPL-3.0), tidak dimodifikasi. |

## 6. Jalur karakterisasi FPGA

Alur yang sudah tersedia (langkah lengkap ada di
[`char_howto.md`](char_howto.md)):

1. `fpga/char/quartus/build.sh`: Platform Designer (JTAG-to-Avalon Master →
   RO-PUF), kompilasi, hash bitstream.
2. Pemeriksaan wajib:
   - jumlah loop RO (1.024);
   - Ignored Assignments;
   - Ignored Constraints;
   - Unconstrained Paths;
   - region dan pin.
3. `fpga/char/syscon/measure_pairs.tcl`: ukur semua pasangan N kali dan
   simpan count mentah ke CSV. `mode=freq` dipakai untuk mengisi
   `RO_PERIOD_NS` di SDC.
4. `sw/analyze.py`: menghitung metrik yang sama dengan model. Opsi
   `--export-run` meneruskan data ke `fpga/char/analyze.py` untuk fitting
   σ_process, σ_jitter, σ_tempco dan uji linearitas tempco.
5. `model/puf_montecarlo.py --params fit.json`: ulangi studi Monte Carlo
   dengan parameter hasil ukur.

**Kebutuhan data minimum:**
- ≥ 2 board;
- ≥ 3 suhu, termasuk kedua sudut;
- ≥ 16 + 3k race per pasangan pada 25 °C.

Dengan n rekonstruksi tanpa kegagalan, klaim yang sah hanya "tingkat
kegagalan < 3/n".

## 7. Risiko dan keterbatasan

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Penempatan pasangan RO tidak simetris | Routing, bukan variasi proses, yang menentukan bit; uniformity dan uniqueness menyimpang | Location assignment per RO setelah floorplan pertama |
| QSF/SDC belum diterima Quartus | Region atau constraint diam-diam tidak berlaku | Langkah 2 di [`char_howto.md`](char_howto.md) |
| Parameter model hanya asumsi | Pilihan τ, mode enrollment dan klaim kegagalan bisa salah | Karakterisasi, lalu ulangi Monte Carlo |
| Tempco non-linier, tegangan, aging tidak dimodelkan | Hasil enrollment −40 + 85 °C terlalu optimistis | Ukur ≥ 3 suhu; uji linearitas |
| Register membuka respons mentah | Kunci bisa dihitung ulang dari bus | `ropuf_avmm` dan `CHAR_BUILD` hanya untuk karakterisasi. Build rilis `sidik_avmm` tidak punya jalur baca (dibuktikan perilaku lewat pemindaian dan mutan, belum struktural) |
| `zeroize` = OR tiga flip-flop sebagai reset asinkron | Glitch hanya menambah clear, tetapi timing recovery/removal belum dicek | Constraint dan analisis timing di Quartus |
| Parameter simulasi berbeda dari rilis | `sidik_avmm` diuji dengan `LOG2N = 8`, `N_ENROLL = 4` (rilis 14 dan 16) | Uji di FPGA dengan parameter rilis |
| Waktu CI | ~7 menit per run, batas 30 menit | Kurangi kasus di PR, jalankan penuh di main bila perlu |
| Lisensi GPL-3.0 (Shaman) | Rilis yang memuat Shaman wajib GPL-3.0 | Lihat [`baselines.md`](baselines.md) |

## 8. Langkah berikutnya (urut prioritas)

1. Build pertama di Quartus. Jalankan semua pemeriksaan langkah 2, lalu
   kunci region LogicLock.
2. Estimasi frekuensi RO (`mode=freq`), isi `RO_PERIOD_NS`, build ulang, dan
   periksa timing.
3. Rancang penempatan simetris per pasangan RO berdasarkan floorplan pertama.
4. Ukur ≥ 2 board pada ≥ 3 suhu, lalu commit CSV mentah beserta `meta.json`
   (seri board, hash bitstream, versi Quartus, kondisi).
5. Fit parameter dan ulangi Monte Carlo. Tetapkan τ dan mode enrollment dari
   data, bukan dari asumsi.
6. Build `sidik_avmm` (rilis) di Quartus dengan parameter rilis, lalu uji
   ENROLL/RECONSTRUCT/AUTH dan tamper di board.
7. Tambahkan pemeriksaan struktural bahwa `k_q` dan data mentah tidak punya
   jalur ke `avs_readdata` (misalnya analisis cone di yosys).
8. Ekspos enrollment dua suhu di register API, karena hasil model
   menunjukkan manfaatnya terbesar.

## Lampiran: reproduksi

| Perintah | Fungsi |
|---|---|
| `make install` | Pasang cocotb 1.8.1, numpy, matplotlib |
| `make test` | Model (25), karakterisasi (19), sw (20), RTL cocotb (shaman 3, ropuf 5, puf_meas 5, secded72 4, fuzzy_ext 4, sidik_crypto 3, sidik_avmm 4 + 1 di `CHAR_BUILD`), mutan (secded72 2, fuzzy_ext 8, sidik_crypto 4, sidik_avmm 3) |
| `make synth-check` | yosys: RO utuh; jalur CYCLONEV terkompilasi; secded72, fuzzy_ext dan sidik_crypto bersih |
| `make synth-check-full` | yosys: `sidik_avmm` utuh (~3 menit, tidak di CI) |
| `make puf-model` | Ulangi Monte Carlo model (~2,5 menit) ke `docs/puf-model/` |
