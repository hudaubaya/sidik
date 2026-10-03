# Karakterisasi RO-PUF di DE10-Nano: langkah untuk tim

Panduan ini membawa tim dari kode di repo ke CSV hasil ukur dan laporan
metrik. Proyek Quartus, skrip System Console dan `sw/analyze.py`
dibahas berurutan.

> **Status: belum pernah dijalankan di board.** Proyek Quartus di
> `fpga/char/quartus/` belum pernah dikompilasi, karena Quartus tidak
> tersedia di lingkungan pengembangan repo ini. Skrip System Console baru
> diuji terhadap *mock* register di `tclsh`. Belum ada satu pun angka hasil
> board di repo ini. Setiap langkah di bawah yang menyebut "periksa" memang
> harus diperiksa, bukan diasumsikan lulus.

## Isi

| Path | Isi |
|---|---|
| `fpga/char/quartus/sidik_char.qpf`, `.qsf` | Proyek Quartus untuk DE10-Nano (5CSEBA6U23I7). Mengatur makro `CYCLONEV`, setelan agar ring RO tidak dirombak, region LogicLock `ro_region` (`ro_array` + `puf_meas`), dan pin. |
| `fpga/char/quartus/sidik_char.sdc` | Clock 50 MHz, JTAG, clock RO pada prescaler, jaringan RO dikecualikan dari analisis timing, dan clock group asinkron. |
| `fpga/char/quartus/sidik_char_top.v` | Top level: reset KEY[0], LED heartbeat, sistem `char_sys`. |
| `fpga/char/quartus/char_sys.tcl` | Membuat sistem Platform Designer `char_sys.qsys`: clock 50 MHz → JTAG to Avalon Master Bridge → RO-PUF di alamat 0x0. |
| `fpga/char/quartus/sidik_ropuf_hw.tcl` | Komponen Platform Designer `sidik_ropuf`. Memakai RTL langsung dari `rtl/` (`ropuf_avmm.v`, `ropuf_core.v`, `ro_array.v`, `ro_cell.v`, `puf_meas.v`). |
| `fpga/char/quartus/build.sh` | Generate `char_sys`, kompilasi, SHA-256 bitstream, dan (opsional) program. |
| `fpga/char/syscon/measure_pairs.tcl` | Skrip System Console: ukur semua pasangan N kali dan simpan nilai counter mentah ke CSV. |
| `sw/analyze.py` | Metrik 1A dari CSV, dengan definisi yang sama dengan `model/puf_montecarlo.py`. |

## 0. Prasyarat

- **Board:** DE10-Nano dengan kabel USB ke port USB-Blaster II (mini-USB di
  dekat konektor HDMI) dan catu 5 V.
- **Quartus Prime Lite atau Standard** dengan dukungan Cyclone V, termasuk
  Platform Designer dan System Console. Versi yang dipakai belum ditentukan:
  catat versinya di `meta.json` setiap run.
  [Menebak] Region LogicLock tersedia di edisi Lite; kalau ternyata tidak,
  hapus blok `LL_*` di QSF dan catat bahwa build tanpa region.
- **Linux:** aturan udev untuk USB-Blaster II, agar `jtagconfig` melihat
  board tanpa root.
- **Python 3** dengan `requirements.txt` (`make install`).

Cek JTAG: `jtagconfig` harus menampilkan dua perangkat, `SOCVHPS` (HPS) dan
`5CSEBA6...` (FPGA). FPGA adalah perangkat ke-2 di rantai.

## 1. Build

```sh
cd fpga/char/quartus
./build.sh            # char_sys.qsys -> char_sys/ -> output_files/sidik_char.sof
```

`build.sh` menjalankan:
1. `qsys-script` untuk membuat `char_sys.qsys`;
2. `qsys-generate`;
3. `quartus_sh --flow compile`.

Semua hasil generate di-*ignore* oleh git. Lewat GUI juga bisa: buka
`char_sys.tcl` di Platform Designer (File → Execute Script), generate HDL,
lalu buka `sidik_char.qpf` dan compile.

## 2. Periksa hasil kompilasi (wajib, sebelum mengukur)

Bitstream yang lolos kompilasi belum tentu berisi RO yang benar. Periksa
lima hal ini dan catat hasilnya:

1. **Ring utuh.**
   - `build.sh` menghitung peringatan "Found combinational loop". Harapannya
     satu per RO (1024), atau Quartus melaporkannya dalam bentuk lain yang
     menunjuk 1024 loop masing-masing 5 node.
   - Di Chip Planner atau Technology Map Viewer, ambil beberapa RO
     (`...|u_array|g_ro[i].u_ro`). Setiap RO harus berisi 5 sel `lcell`.
   - Jika ring hilang atau tergabung, bitstream tidak boleh dipakai.
2. **Ignored assignments.** Laporan Fitter → "Ignored Assignments" harus
   kosong. Isinya menandakan nama hierarki di QSF tidak cocok, sehingga
   region atau synchronizer tidak berlaku.
3. **Timing.** Di Timing Analyzer:
   - "Report Ignored Constraints" kosong;
   - "Report Unconstrained Paths" kosong;
   - clock `ro_a`, `ro_a_div`, `ro_b`, `ro_b_div` muncul.

   Slack di domain RO hanya bermakna setelah `RO_PERIOD_NS` di SDC diisi
   frekuensi RO yang terukur (langkah 5). Nilai awal 2,5 ns adalah tebakan.
4. **Region.** Di Chip Planner, `ro_region` berisi `ro_array` dan
   `puf_meas`. Setelah build pertama yang baik, kunci region: set
   `LL_AUTO_SIZE OFF` dan `LL_STATE LOCKED`, lalu salin `LL_ORIGIN`,
   `LL_WIDTH` dan `LL_HEIGHT` hasil fitter ke QSF. Dengan begitu semua
   bitstream berikutnya memakai area yang sama.
5. **Pin.** Bandingkan pin di QSF dengan QSF golden top DE10-Nano dari
   Terasic (CD board). Hanya clock, KEY dan LED yang dipakai.

Simpan `output_files/sidik_char.sof.sha256`. Hash ini masuk `meta.json`
setiap run, sehingga data selalu bisa dilacak ke bitstream-nya.

## 3. Program FPGA

```sh
./build.sh program
# atau: quartus_pgm -m jtag -o "p;output_files/sidik_char.sof@2"
```

LED0 berkedip (~1,5 Hz) dan LED1 menyala jika clock dan reset normal. KEY0
me-reset sistem. Konfigurasi lewat JTAG hilang saat board dimatikan.

## 4. Uji koneksi

```sh
system-console -cli
% get_service_paths master
```

Akan muncul path JTAG master (`.../phy_0/master`). Path HPS juga bisa
muncul; skrip melewatinya dan hanya memakai master yang membaca ID
`0x50554631` di alamat 0x0.

## 5. Estimasi frekuensi RO (untuk SDC)

```sh
cd fpga/char/syscon
system-console -cli --script=measure_pairs.tcl \
    out=freq_b1.csv board=1 temp_c=25 reps=1 mode=freq
```

Setiap race dihentikan setelah `timeout` = 50 siklus (1 µs). Frekuensinya
f ≈ count / window_ns. Hasil ini kasar, karena count yang ditangkap saat
timeout tidak dijamin eksak. Pakai RO tercepat ditambah margin untuk
`RO_PERIOD_NS` di `sidik_char.sdc`, lalu build ulang dan periksa timing
lagi (langkah 2.3).

[Menebak] Cara System Console meneruskan argumen setelah `--script` bisa
berbeda antarversi. Jika `out=...` tidak terbaca, jalankan dari konsol
interaktif:

```tcl
set argv {out=freq_b1.csv board=1 temp_c=25 reps=1 mode=freq}
source measure_pairs.tcl
```

## 6. Pengukuran

```sh
system-console -cli --script=measure_pairs.tcl \
    out=runs/2026-10-xx-b1/b1_25C.csv board=1 temp_c=25 vdd_v=1.10 reps=40
```

- **Isi CSV.** Satu baris per race: `board,temp_c,vdd_v,rep,pair,count_a,count_b,status`.
  - `count_a` dan `count_b` adalah nilai mentah register COUNT_A dan COUNT_B.
  - `status` adalah register CTRL setelah race: 2 = selesai, 6 = timeout.
  - Baris berawalan `#` berisi metadata: tanggal, path service, parameter
    inti.
- **Pemeriksaan per race.** Skrip memeriksa:
  - BUSY sudah turun;
  - DONE menyala;
  - PAIR terbaca balik sama;
  - DELTA = COUNT_A − COUNT_B.

  Jika salah satu gagal, skrip berhenti. Data yang sudah tersimpan tetap
  aman, karena file di-*flush* setiap sapuan.
- **Urutan.** Setiap repetisi menyapu pasangan 0..511 berurutan, sehingga
  drift waktu tersebar rata ke semua pasangan.
- **Durasi.** Belum diukur. Per race ada 2 tulis + 1 baca burst lewat JTAG;
  jika satu transaksi ~1 ms, satu sapuan 512 pasangan butuh beberapa
  detik. [Menebak] Catat durasi sebenarnya di log run pertama.

**Rencana minimum agar metrik 1A bisa dihitung:**

| Kebutuhan | Alasan |
|---|---|
| ≥ 16 + 3·k repetisi per pasangan pada 25 °C | 16 untuk enrollment, sisanya diputar ulang sebagai k rekonstruksi |
| ≥ 2 board | uniqueness dan "distinct IDs" butuh lebih dari satu chip |
| ≥ 3 suhu (mis. −40/0, 25, 85 °C) | reliability vs suhu; linearitas tempco di `fpga/char/analyze.py` |
| Catat `vdd_v` | hanya catu nominal yang dianalisis |

Tunggu suhu die stabil di setiap setpoint sebelum mengukur. Jumlah
rekonstruksi menentukan seberapa kuat klaim "0 kegagalan". Dengan n
rekonstruksi tanpa kegagalan, batas atas 95 % hanya 3/n. Model 1A memakai
1000 rekonstruksi per chip.

## 7. Analisis

```sh
python3 sw/analyze.py runs/2026-10-xx-*/*.csv --out runs/2026-10-xx-report
```

Keluarannya `results.json`, `results.md` dan gambar. Metriknya sama dengan 1A:
- uniformity, uniqueness, reliability per suhu, σ Δ;
- pasangan lolos mask per τ;
- key BER (1 race dan mayoritas 3);
- tingkat kegagalan terdeteksi dan kunci salah diam-diam;
- kebutuhan ukur ulang, KCV, dan distinct ID.

Perbedaan dari 1A:
- Rekonstruksi memutar ulang race yang terekam, pada suhu yang diukur
  (bukan acak −40..85 °C).
- Jumlah rekonstruksi dibatasi oleh data yang terekam.
- Race dengan Δ = 0 (zona mati, `rtl/ropuf/README.md`) dihitung sebagai bit 0.
- Pasangan yang pernah timeout dikeluarkan dari semua metrik.

Laporan selalu diberi label sumber data dari metadata CSV (`source=`).
Data simulasi atau mock tidak akan berlabel "hardware".

Untuk mencocokkan parameter model (σ_process, σ_jitter, σ_tempco), ekspor
ke format `fpga/char/`:

```sh
python3 sw/analyze.py runs/2026-10-xx-*/*.csv --export-run fpga/char/runs/2026-10-xx-de10nano
python3 fpga/char/analyze.py fpga/char/runs/2026-10-xx-de10nano
python3 model/puf_montecarlo.py --params fpga/char/runs/2026-10-xx-de10nano/analysis/fit.json \
    --out fpga/char/runs/2026-10-xx-de10nano/montecarlo
```

## 8. Yang wajib dicatat dan di-commit

Untuk setiap run, simpan di `fpga/char/runs/<tanggal>-<board>/`:
- CSV mentah;
- `meta.json`, berisi: nomor seri board, SHA-256 bitstream, versi Quartus,
  commit repo, suhu (chamber dan die jika ada), catu, operator, dan
  kejadian selama run.

Laporan hasil analisis boleh di-commit, tetapi angka di dalamnya baru boleh
dikutip setelah langkah 2 terbukti lulus untuk bitstream tersebut.

## Keterbatasan yang sudah diketahui

- **Simetri pasangan.** Region LogicLock hanya menjaga semua RO di satu
  area; dua RO dalam satu pasangan belum tentu ditempatkan identik.
  Perbedaan routing bisa mendominasi bit, sehingga uniformity atau
  uniqueness menyimpang dan menyesatkan. Penempatan per RO
  (`set_location_assignment` per `lcell`, pasangan di LAB bersebelahan
  dengan pola sama) baru bisa dibuat setelah ada floorplan hasil fitter
  pertama.
- **Mux 512:1 di jalur clock.** `ro_a`/`ro_b` dipilih lewat mux dari 512 RO.
  Jalur mux ikut menentukan delay, tetapi tidak frekuensi. Pemilih hanya
  berubah saat semua RO mati, jadi glitch tidak terhitung.
- **Nama hierarki di QSF/SDC.** Nama di QSF dan SDC memakai wildcard
  berdasarkan hierarki RTL (`fpga/char/test_quartus.py` memastikan nama itu
  ada di RTL). Bentuk hierarki hasil Platform Designer belum dilihat;
  langkah 2.2 dan 2.3 yang membuktikannya.
- **Pin DE10-Nano.** Pin diambil dari manual dan belum dicek ke board.
