# Laporan status proyek SIDIK

Rekap pekerjaan PR #1 – #13, per 4 Oktober 2026 (UTC), main di commit
`17fd151`. Laporan ini adalah potret pada tanggal tersebut. Perbarui atau
tandai usang setelah ada perubahan besar, terutama setelah ada data board.

> **Belum ada satu pun hasil dari hardware.** Semua angka PUF di laporan ini
> adalah keluaran model dengan parameter asumsi, bukan pengukuran.
>
> **Proyek Quartus DE10-Nano (karakterisasi dan rilis) belum pernah
> dikompilasi**, karena Quartus tidak tersedia di lingkungan pengembangan.
> Pengujiannya baru offline. Hal yang sama berlaku untuk generator kunci
> lengkap (`rtl/sidik_avmm.v`), sistem dua instans dan verifier: semuanya
> baru berjalan di simulasi. Angka sumber daya FPGA di bagian 6.3 adalah
> **estimasi yosys**, bukan laporan fitter Quartus.
>
> **Yang sudah terbukti:** perilaku RTL di simulasi (cocotb/Icarus),
> kesetaraan RTL dengan model Python, dan sintesis generik (yosys). Semua CI
> di main hijau.

## 1. Ringkasan

SIDIK adalah kumpulan blok keamanan hardware untuk Tiny Tapeout / FPGA:
- core SHA-256 (Shaman, pihak ketiga, GPL-3.0);
- generator kunci berbasis RO-PUF dengan secure sketch SECDED.

Dalam tiga belas PR yang semuanya sudah di-merge ke main, repositori ini
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
- **Rilis FPGA ([`fpga/release/`](../fpga/release/README.md)):**
  - komponen Platform Designer `sidik` dan dua instans, `sidik_a` (larik A)
    dan `sidik_b` (larik B), masing-masing di region LogicLock sendiri;
  - akses lewat lightweight HPS bridge (GHRD) dan JTAG-to-Avalon Master;
  - KEY0 sebagai tamper kedua instans.
- **Verifier ([`sw/`](../sw/README.md)):** `verifier.py` dan padanannya
  dalam C (`sidik_verifier.c`):
  - enrollment menyimpan helper data dan N pasangan challenge-respons;
  - autentikasi dengan challenge sekali pakai;
  - demo kloning (helper data A diterapkan ke B).
- **Simulasi sistem gabungan (`tb/sidik_system`):** dua `sidik_avmm` di
  belakang model dekoder alamat, dijalankan oleh kode verifier yang sama.

Langkah paling menentukan berikutnya adalah **build pertama di Quartus dan
pengukuran di board**. Estimasi yosys menunjukkan dua instans bisa sempit
di 5CSEBA6 (bagian 6.3), jadi build pertama juga menjawab apakah desain ini
muat. Tanpa itu, parameter model (σ_process, σ_jitter,
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
| Sistem dua instans (`tb/sidik_system`) | <ul><li>dekoder: MAGIC hanya di 0x000 dan 0x100; tulis ke satu instans tidak sampai ke yang lain; base sama dengan skrip `fpga/release`</li><li>`verifier.py` tanpa modifikasi di atas RTL: enrollment, challenge sekali pakai, penolakan saat CRP habis</li><li>demo kloning: B dengan helper A ditolak (rekonstruksi gagal), tidak ada challenge sampai ke B, A tetap diterima; juga lewat program C</li><li>KEY0: state kedua instans nol dalam ≤ 3 tepi clock, verifier menolak, A diterima lagi setelah `rst`</li></ul> | Interconnect Platform Designer yang sebenarnya (dekoder di simulasi adalah model); B memakai RO perilaku lain (`RO_INDEX_BASE`), bukan chip lain |
| Verifier (`sw/verifier.py`, `sidik_verifier.c`) | <ul><li>17 test terhadap model perangkat (`sw/sidik_sim.py`)</li><li>C dan Python berbagi satu database</li><li>CRP ditandai terpakai dan disimpan sebelum challenge dikirim</li><li>konstanta register dicocokkan dengan RTL, header C dan `fpga/release`</li></ul> | Transport `/dev/mem` dan JTAG di board |
| Rilis FPGA (`fpga/release/`) | <ul><li>11 test offline: skrip Platform Designer, assignment Quartus, QSF dan SDC dijalankan di tclsh dengan stub perekam</li><li>anggota LogicLock dan target synchronizer cocok dengan hierarki RTL</li><li>top level dan sumber komponen terkompilasi (iverilog)</li><li>bridge System Console diuji terhadap mock</li></ul> | Kompilasi Quartus; nama instans GHRD (asumsi); muat atau tidaknya dua instans; timing |

## 3. Riwayat pekerjaan (PR #1 – #13)

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
| 13 | 4 Okt | <ul><li>laporan status sampai PR #12</li><li>`fpga/release/`: komponen `sidik`, dua instans di dua region LogicLock, LW HPS bridge + JTAG, KEY0 tamper</li><li>`sw/verifier.py`, `sidik_verifier.c`, `sidik_sim.py`</li><li>`tb/sidik_system`: simulasi sistem gabungan</li><li>parameter simulasi `RO_INDEX_BASE`</li></ul> | hijau, 14 mnt 53 dtk |

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
| `ro_array.v` | 1.024 RO dalam pasangan saling lepas (2i, 2i+1); hanya pasangan terpilih yang di-enable. Di simulasi, pasangan dipilih lewat pohon OR (fungsi sama, ~4× lebih cepat). Parameter `INDEX_BASE` (lewat `RO_INDEX_BASE` di `ropuf_core`/`sidik_avmm`, hanya berpengaruh di simulasi) membuat instans kedua memakai RO perilaku lain, sehingga dua instans berlaku sebagai dua chip. |
| `puf_meas.v` | Dua counter balapan sampai 2¹⁴ siklus RO di belakang prescaler ripple /2; sinkronizer 2-FF; keluaran tanda dan \|Δ\| di domain 50 MHz. Catatan: <ul><li>resolusi Δ = 2 siklus RO</li><li>zona mati: \|Δ ideal\| ≤ 4 count terbaca Δ = 0</li><li>bias \|Δ\| rata-rata −3 count</li></ul> |
| `ropuf/ropuf_core.v`, `ropuf_avmm.v` | Core + slave Avalon-MM (ID, PARAMS, CTRL, PAIR, COUNT_A/B, DELTA, TIMEOUT). Catatan keamanan: `ropuf_avmm` membuka respons mentah dan hanya untuk karakterisasi. |
| `secded72.v` | Sindrom Hamming (72,64) diperluas: koreksi 1 bit, deteksi 2 bit; bit-exact dengan `model/secded.py`. |
| `fuzzy_ext.v` | Enrollment (mask 512 bit + sindrom sebagai helper data, satu fase per suhu) dan rekonstruksi (mayoritas 3, SECDED, ukur ulang, maks. 3 ronde, `fail`). KCV diperiksa oleh konsumen lewat `key_valid`/`key_good`. Buffer dihapus setelah dipakai; `zeroize` asinkron. |
| `sidik_crypto.v` | Di atas Shaman tanpa modifikasi: K = SHA-256(216 bit ‖ "SIDIK-K"), HMAC(K, challenge), ID, KCV. K tidak keluar dari modul. Latensi tetap; core di-reset setelah tiap hash; `zeroize` asinkron. |
| `sidik_avmm.v` | Generator kunci lengkap di balik slave Avalon-MM: CTRL (ENROLL, RECONSTRUCT, AUTH, CLEAR), STATUS, TAU, HELPER, CHAL, RESP, ID. Build rilis tanpa jalur baca ke K/data mentah; `CHAR_BUILD` menambah register race mentah. `tamper_n` (sinkronizer 2-FF) → clear asinkron, TAMPERED bertahan sampai `rst`. |
| `third_party/shaman/` | Core SHA-256 Pat Deegan (GPL-3.0), tidak dimodifikasi. |

## 6. Rilis FPGA, verifier dan estimasi sumber daya

### 6.1 Sistem rilis (DE10-Nano)

| Instans | Larik PUF | Region LogicLock | JTAG master | HPS (Linux) |
|---|---|---|---|---|
| `sidik_a` | larik A | `ro_region_a` | 0x000 | 0xFF240000 |
| `sidik_b` | larik B | `ro_region_b` | 0x100 | 0xFF240100 |

- **Dua varian sistem:**
  - `release_sys.tcl`: mandiri, hanya JTAG, dengan proyek Quartus lengkap (`build.sh`);
  - `add_to_ghrd.tcl`: menambahkan kedua instans ke GHRD Terasic di lightweight HPS bridge (window 0x40000) plus JTAG master. Nama instans GHRD adalah asumsi dan bisa di-override.
- **Assignment** (`sidik_assignments.tcl`, dipakai untuk kedua proyek): makro `CYCLONEV`, LCELL dipertahankan, physical synthesis mati, synchronizer, dan dua region LogicLock (floating dan auto-size sampai kompilasi pertama).
- **Constraint** (`sidik_ro.sdc`): clock RO untuk kedua instans, ring dikeluarkan dari analisis timing, false path ke flop synchronizer tamper pertama.
- **KEY0** dipakai bersama sebagai `tamper_n` kedua instans; KEY1 sebagai reset (top mandiri).
- **`syscon/sidik_bridge.tcl`**: server TCP System Console (hanya 127.0.0.1) dengan protokol yang sama seperti simulator, sehingga verifier bisa berjalan dari PC lewat JTAG.

### 6.2 Verifier

- **Enrollment** (sekali per perangkat, di lingkungan tepercaya): ENROLL pada TAU, simpan helper data (18 word) dan ID, jalankan AUTH untuk N challenge acak 32 byte, simpan pasangan challenge-respons (CRP), lalu CLEAR. Verifier tidak pernah melihat K.
- **Autentikasi:** tulis helper data, RECONSTRUCT, cek ID, ambil satu CRP yang belum terpakai, tandai terpakai dan simpan database secara atomik **sebelum** challenge dikirim, AUTH, bandingkan secara constant-time, CLEAR. Jika CRP habis, verifier menolak.
- **Demo kloning:** helper data A diterapkan ke B. B gagal merekonstruksi dan ditolak; tidak ada challenge yang sampai ke B.
- **Transport:** `/dev/mem` (LW bridge), `--jtag` / `-j` (bridge System Console), `sw/sidik_sim.py` (model). Python dan C memakai format database dan kode keluar yang sama.

### 6.3 Estimasi sumber daya (yosys, label: estimasi)

> **Semua angka di bagian ini adalah estimasi** dari yosys 0.33
> `synth_intel_alm -family cyclonev` pada build rilis `sidik_avmm` dengan
> parameter rilis (LOG2N = 14, N_ENROLL = 16). Cincin RO di-blackbox di
> level `ro_cell`. ABC9 crash pada langkah area-recovery terakhir (`&mfs`,
> bug ABC di yosys 0.33), jadi angka ABC9 berasal dari netlist sebelum
> langkah itu; run dengan ABC klasik dipakai sebagai batas atas. Angka yang
> berlaku adalah laporan fitter Quartus.

| Satu instans, logika sinkron | LUT (ALUT2–6) | Sel aritmetika | FF |
|---|---|---|---|
| ABC9, rata | 13.195 | 825 | 6.719 |
| ABC9, hierarki | 13.354 | 817 | 6.755 |
| ABC klasik, rata | 15.511 | 980 | 6.719 |

Rincian per modul (run hierarki):

| Bagian | LUT | Aritmetika | FF |
|---|---|---|---|
| `sidik_avmm` (register bus, HELPER/CHAL/RESP/ID, FSM) | 2.135 | 9 | 1.437 |
| `fuzzy_ext` + `secded72` | 6.106 | 178 | 1.876 |
| `sidik_crypto` tanpa Shaman | 1.511 | 99 | 1.074 |
| Core Shaman | 2.633 | 389 | 2.235 |
| `ropuf_core` + `puf_meas` + 2 counter | 90 | 142 | 133 |
| **Subtotal tanpa larik RO** | **12.475** | **817** | **6.755** |
| Dekoder enable + mux 1024:1 di `ro_array` | 879 | 0 | 0 |

**Larik RO** (dari jumlah LUT per RO): jalur `CYCLONEV` memakai 5 tahap LUT
+ `lcell`, ditambah AND keluaran yang mungkin terserap ke mux, yaitu 5–6 ALUT
per RO. Untuk 1.024 RO: 5.120–6.144 ALUT per larik, tanpa FF.

| Total (estimasi) | ALUT | Aritmetika | FF |
|---|---|---|---|
| 1 instans | ~18.500–21.700 | 817–980 | ~6.750 |
| 2 instans (larik A + B) | ~37.000–43.300 | ~1.600–2.000 | ~13.500 |

5CSEBA6 punya 41.910 ALM. Bergantung pada seberapa banyak pasangan ALUT bisa
dikemas dalam satu ALM, perkiraan kasarnya 9–20 ribu ALM per instans. Pada
ujung atas, dua instans ditambah GHRD mendekati kapasitas perangkat.
Tahap RO yang dipertahankan di region reserved cenderung tidak berbagi ALM.
`fuzzy_ext` adalah blok terbesar, lebih besar dari core SHA-256.

## 7. Jalur karakterisasi FPGA

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

## 8. Risiko dan keterbatasan

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Penempatan pasangan RO tidak simetris | Routing, bukan variasi proses, yang menentukan bit; uniformity dan uniqueness menyimpang | Location assignment per RO setelah floorplan pertama |
| QSF/SDC belum diterima Quartus | Region atau constraint diam-diam tidak berlaku | Langkah 2 di [`char_howto.md`](char_howto.md) |
| Parameter model hanya asumsi | Pilihan τ, mode enrollment dan klaim kegagalan bisa salah | Karakterisasi, lalu ulangi Monte Carlo |
| Tempco non-linier, tegangan, aging tidak dimodelkan | Hasil enrollment −40 + 85 °C terlalu optimistis | Ukur ≥ 3 suhu; uji linearitas |
| Register membuka respons mentah | Kunci bisa dihitung ulang dari bus | `ropuf_avmm` dan `CHAR_BUILD` hanya untuk karakterisasi. Build rilis `sidik_avmm` tidak punya jalur baca (dibuktikan perilaku lewat pemindaian dan mutan, belum struktural) |
| `zeroize` = OR tiga flip-flop sebagai reset asinkron | Glitch hanya menambah clear, tetapi timing recovery/removal belum dicek | Constraint dan analisis timing di Quartus |
| Parameter simulasi berbeda dari rilis | `sidik_avmm` diuji dengan `LOG2N = 8`, `N_ENROLL = 4` (rilis 14 dan 16) | Uji di FPGA dengan parameter rilis |
| Dua instans tidak muat di 5CSEBA6 | Estimasi yosys ~37–43 ribu ALUT untuk dua instans; pada pengemasan buruk, bersama GHRD mendekati 41.910 ALM | Fitter Quartus pada build pertama; bila perlu, simpan mask/sign/pass `fuzzy_ext` di MLAB/M10K atau pakai satu `sidik_crypto` bersama |
| Nama instans GHRD diasumsikan | `add_to_ghrd.tcl` gagal atau terhubung ke master yang salah | Override lewat `--cmd`; periksa peta alamat (window 0x40000–0x401FF harus kosong) |
| Database verifier menyimpan respons | Siapa pun yang membaca atau mengubahnya (misalnya mengembalikan flag `used`) bisa memutar ulang respons | Simpan seperti kunci rahasia; batasi akses dan cadangkan dengan integritas |
| Demo kloning di simulasi memakai RO perilaku lain | Membuktikan alur verifier, bukan keunikan chip nyata | Ulangi demo di board dengan larik A dan B nyata |
| Waktu CI | ~15 menit per run setelah PR #13 (dua kali lipat PR #12), batas 30 menit | Kurangi kasus di PR, jalankan penuh di main bila perlu |
| Lisensi GPL-3.0 (Shaman) | Rilis yang memuat Shaman wajib GPL-3.0 | Lihat [`baselines.md`](baselines.md) |

## 9. Langkah berikutnya (urut prioritas)

1. Build pertama di Quartus. Jalankan semua pemeriksaan langkah 2, lalu
   kunci region LogicLock.
2. Estimasi frekuensi RO (`mode=freq`), isi `RO_PERIOD_NS`, build ulang, dan
   periksa timing.
3. Rancang penempatan simetris per pasangan RO berdasarkan floorplan pertama.
4. Ukur ≥ 2 board pada ≥ 3 suhu, lalu commit CSV mentah beserta `meta.json`
   (seri board, hash bitstream, versi Quartus, kondisi).
5. Fit parameter dan ulangi Monte Carlo. Tetapkan τ dan mode enrollment dari
   data, bukan dari asumsi.
6. Build `fpga/release` di Quartus (mandiri dulu, lalu GHRD). Bandingkan
   laporan fitter dengan estimasi di bagian 6.3, lalu jalankan verifier di
   board: enrollment, autentikasi, demo kloning larik A/B dan tamper KEY0.
7. Tambahkan pemeriksaan struktural bahwa `k_q` dan data mentah tidak punya
   jalur ke `avs_readdata` (misalnya analisis cone di yosys).
8. Ekspos enrollment dua suhu di register API, karena hasil model
   menunjukkan manfaatnya terbesar.

## Lampiran: reproduksi

| Perintah | Fungsi |
|---|---|
| `make install` | Pasang cocotb 1.8.1, numpy, matplotlib |
| `make test` | Model (25), karakterisasi (19), rilis (11), sw (37: analyze 20, verifier 17), RTL cocotb (shaman 3, ropuf 5, puf_meas 5, secded72 4, fuzzy_ext 4, sidik_crypto 3, sidik_avmm 4 + 1 di `CHAR_BUILD`, sidik_system 5), mutan (secded72 2, fuzzy_ext 8, sidik_crypto 4, sidik_avmm 3) |
| `make synth-check` | yosys: RO utuh; jalur CYCLONEV terkompilasi; secded72, fuzzy_ext dan sidik_crypto bersih |
| `make synth-check-full` | yosys: `sidik_avmm` utuh (~3 menit, tidak di CI) |
| `yosys -p "read_verilog <ro_cell blackbox> <sumber sidik_avmm tanpa ro_cell.v>; synth_intel_alm -family cyclonev -top sidik_avmm -noiopad -noclkbuf"` | Estimasi sumber daya di bagian 6.3 (~3 menit; belum jadi target `make`) |
| `make puf-model` | Ulangi Monte Carlo model (~2,5 menit) ke `docs/puf-model/` |
