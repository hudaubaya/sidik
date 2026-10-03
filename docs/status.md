# Laporan status proyek SIDIK

Rekap pekerjaan PR #1 – #8, per 3 Oktober 2026 (UTC), main di commit
`fed3172`. Laporan ini adalah potret pada tanggal tersebut. Perbarui atau
tandai usang setelah ada perubahan besar, terutama setelah ada data board.

> **Belum ada satu pun hasil dari hardware.** Semua angka PUF di laporan ini
> adalah keluaran model dengan parameter asumsi, bukan pengukuran.
>
> **Proyek Quartus DE10-Nano belum pernah dikompilasi**, karena Quartus tidak
> tersedia di lingkungan pengembangan. Pengujiannya baru offline.
>
> **Yang sudah terbukti:** perilaku RTL di simulasi (cocotb/Icarus),
> kesetaraan RTL dengan model Python, dan sintesis generik (yosys). Semua CI
> di main hijau.

## 1. Ringkasan

SIDIK adalah kumpulan blok keamanan hardware untuk Tiny Tapeout / FPGA:
- core SHA-256 (Shaman, pihak ketiga, GPL-3.0);
- generator kunci berbasis RO-PUF dengan secure sketch SECDED.

Dalam delapan PR yang semuanya sudah di-merge ke main, repositori ini
sekarang berisi:

- **Model perilaku dan studi Monte Carlo** generator kunci RO-PUF, termasuk
  enrollment dua suhu dan key-check value (KCV).
- **RTL tersimulasi:**
  - ring oscillator dan array 1.024 RO;
  - pengukur pasangan dengan prescaler dan sinkronisasi 50 MHz;
  - core Avalon-MM;
  - dekoder SECDED (72,64) yang bit-exact dengan model.
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

## 3. Riwayat pekerjaan (PR #1 – #8)

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
| `ro_array.v` | 1.024 RO dalam pasangan saling lepas (2i, 2i+1); hanya pasangan terpilih yang di-enable. |
| `puf_meas.v` | Dua counter balapan sampai 2¹⁴ siklus RO di belakang prescaler ripple /2; sinkronizer 2-FF; keluaran tanda dan \|Δ\| di domain 50 MHz. Catatan: <ul><li>resolusi Δ = 2 siklus RO</li><li>zona mati: \|Δ ideal\| ≤ 4 count terbaca Δ = 0</li><li>bias \|Δ\| rata-rata −3 count</li></ul> |
| `ropuf/ropuf_core.v`, `ropuf_avmm.v` | Core + slave Avalon-MM (ID, PARAMS, CTRL, PAIR, COUNT_A/B, DELTA, TIMEOUT). Catatan keamanan: register ini membuka respons mentah dan hanya untuk karakterisasi. |
| `secded72.v` | Sindrom Hamming (72,64) diperluas: koreksi 1 bit, deteksi 2 bit; bit-exact dengan `model/secded.py`. |
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
| Register membuka respons mentah | Kunci bisa dihitung ulang dari bus | Hanya untuk build karakterisasi; build kunci harus menutupnya |
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
6. Rancang build kunci tanpa register respons mentah (enrollment dan
   rekonstruksi di hardware).

## Lampiran: reproduksi

| Perintah | Fungsi |
|---|---|
| `make install` | Pasang cocotb 1.8.1, numpy, matplotlib |
| `make test` | Model (25), karakterisasi (19), sw (20), RTL cocotb (shaman 3, ropuf 5, puf_meas 5, secded72 4), mutan secded72 |
| `make synth-check` | yosys: RO utuh; jalur CYCLONEV terkompilasi; secded72 bersih |
| `make puf-model` | Ulangi Monte Carlo model (~2,5 menit) ke `docs/puf-model/` |
