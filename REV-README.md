# PASUKAN SALES TAK TIDUR
### PT Ekspor Arang — AI Sales & Marketing Agent System (D:\BOT\pt-ekspor-arang-agent)

---

## Apa Ini?

Ini adalah "tim sales otomatis" berbasis AI untuk bisnis ekspor arang/briket (charcoal export) milik **PT Cahaya Woodchar International**. Bayangkan 7 karyawan AI yang bekerja 24 jam tanpa lelah: satu cari calon pembeli di internet, satu kirim email penawaran yang dipersonalisasi, satu baca dan klasifikasi balasan email, satu buat penawaran harga (PDF), dan satu lagi kirim laporan harian ke bos lewat Telegram — semuanya berjalan otomatis sesuai jadwal, tanpa biaya berlangganan bulanan (pakai API gratis/murah).

Proyek ini sudah cukup matang — punya dokumentasi lengkap (`DOCUMENTATION.md`/`.docx`, `GAP_ANALYSIS.md`/`.docx`, `README.docx`, changelog v2 & v3) dan sudah di versi 3. REV-README ini adalah rangkuman ulang dalam Bahasa Indonesia yang lebih santai, berdasarkan `README.md` asli yang sudah ada.

## Fitur Utama

- **Scraper Agent** (jalan jam 08:00) — cari calon pembeli dari Alibaba, TradeIndia, ExportHub, Kompass, dan Google
- **Enrichment Agent** — menilai skor tiap calon pembeli (0-100), hanya lanjutkan yang skornya ≥ 40
- **Outreach Agent** (jam 09:00) — AI (Gemini) menulis email penawaran personal, dikirim lewat Gmail SMTP
- **Monitor Agent** (tiap 2 jam) — baca inbox Gmail, AI klasifikasikan balasan (tertarik/tidak/butuh info lagi)
- **Follow-up Agent** (jam 14:00) — kirim susulan (touch ke-2/3/4) ke calon pembeli yang belum balas
- **Proposal Agent** — begitu ada yang berstatus TERTARIK, otomatis buat PDF penawaran harga dan kirim email
- **Reporter Agent** (jam 18:00) — kirim statistik harian ke Telegram
- Dashboard (folder `dashboard/`) berbasis Streamlit untuk pantau performa
- Vector memory (ChromaDB) untuk pencarian semantik riwayat percakapan
- Dukungan scraper media sosial tambahan (Instagram, Facebook, LinkedIn, TikTok, Twitter/X) — sebagian berisiko tinggi kena banned, harus hati-hati
- Endpoint health-check via Flask + pixel tracking email

## Teknologi yang Dipakai

- Python + CrewAI (framework multi-agent AI)
- APScheduler (penjadwalan otomatis harian)
- Google Gemini / Groq (LLM untuk generate email & klasifikasi)
- Gmail API + Gmail SMTP (kirim & baca email)
- Telegram Bot API (notifikasi & laporan)
- ReportLab (generate PDF penawaran)
- ChromaDB + sentence-transformers (vector memory / pencarian semantik)
- Streamlit + Plotly (dashboard)
- scikit-learn, pandas, numpy (analitik)
- Docker (`Dockerfile`, `docker-compose.yml`)

## Cara Instalasi

**Opsi A — Langsung dengan Python (tanpa Docker):**

1. Pastikan Python 3.11 terpasang (Dockerfile proyek ini pakai base image `python:3.11-slim`, jadi disarankan pakai Python 3.11 di lokal juga biar konsisten).
2. Buka folder proyek:
   ```powershell
   cd "D:\BOT\pt-ekspor-arang-agent"
   ```
3. Buat virtual environment:
   ```powershell
   python -m venv venv
   venv\Scripts\activate
   ```
4. Install semua dependency dari `requirements.txt`:
   ```powershell
   pip install -r requirements.txt
   ```
   Catatan: beberapa dependency scraper media sosial (`TikTokApi`, `playwright`) butuh langkah tambahan setelah install:
   ```powershell
   playwright install
   ```
5. Setup file `.env`: file `.env` di folder ini SUDAH TERISI nilai asli (lihat Catatan Penting di bawah — ini penting untuk diperhatikan). Kalau mau setup ulang dari nol, copy dari `.env.example`:
   ```powershell
   cd "D:\BOT\pt-ekspor-arang-agent"
   copy .env.example .env
   ```
   lalu isi tiap key (GEMINI_API_KEY, GROQ_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, dll — lihat `.env.example` untuk daftar lengkap 40+ variabel).
6. Kredensial Gmail API tersimpan di `credentials.json` (sudah ada di folder ini) dan token sesi tersimpan otomatis di `token.pickle` setelah login pertama kali.
7. Jalankan migrasi database dulu (sama seperti yang dilakukan Dockerfile sebelum start container):
   ```powershell
   cd "D:\BOT\pt-ekspor-arang-agent"
   python -m db.migrate_v31
   ```

**Opsi B — Pakai Docker (sesuai `Dockerfile` + `docker-compose.yml` proyek ini):**

1. Pastikan Docker Desktop sudah jalan.
2. Pastikan `.env`, `credentials.json`, dan `token.pickle` sudah ada di folder proyek (di-mount langsung ke container lewat `docker-compose.yml`).
3. Build image lalu jalankan container di background:
   ```powershell
   cd "D:\BOT\pt-ekspor-arang-agent"
   docker compose up -d --build
   ```
   Catatan: `Dockerfile` proyek ini SENGAJA cuma install subset dependency inti (`apscheduler`, `python-dotenv`, `google-genai`, `groq`, Google API, `requests`/`beautifulsoup4`/`lxml`/`fake-useragent`, `reportlab`, `scikit-learn`/`pandas`/`numpy`, `streamlit`, `plotly`, `chromadb`, `pytz`, `flask`, `pyshorteners`) — scraper media sosial berat (`instagrapi`, `facebook-scraper`, `linkedin-api`, `TikTokApi`, `snscrape`, `playwright`, `sentence-transformers`) TIDAK ikut ter-install di image Docker, jadi kalau butuh fitur social scraping penuh pakai Opsi A (install lokal) bukan Docker.
4. Cek log container:
   ```powershell
   docker compose logs -f agent
   ```

## Cara Menjalankan

**Tanpa Docker (venv aktif, dari folder proyek):**

Cek dulu apakah semua konfigurasi sudah lengkap (checklist onboarding):
```powershell
cd "D:\BOT\pt-ekspor-arang-agent"
python main.py --check
```

Menjalankan sistem penuh (`main.py` adalah satu-satunya entry point — di dalamnya otomatis meng-import `scheduler.py` lewat `build_scheduler()` dan menjalankan scheduler + semua 17 job agent + Telegram command listener; **`scheduler.py` sendiri BUKAN untuk dijalankan langsung**, itu cuma modul yang di-import oleh `main.py`):
```powershell
cd "D:\BOT\pt-ekspor-arang-agent"
python main.py
```

Menjalankan dashboard (opsional, terpisah, di terminal lain):
```powershell
cd "D:\BOT\pt-ekspor-arang-agent"
streamlit run dashboard/app.py
```
(sesuaikan nama file dashboard sesuai isi folder `dashboard/` kalau berbeda)

Log aktivitas otomatis tersimpan di `logs/agent.log` dan tampil juga di terminal.

**Dengan Docker:**

Container otomatis menjalankan urutan yang sama persis dengan yang didefinisikan di `Dockerfile` (`CMD ["sh", "-c", "python -m db.migrate_v31 && python main.py"]`) — migrasi DB dulu, lalu start `main.py` penuh (scheduler + semua agent + Telegram listener + health-check endpoint di port 8080):
```powershell
cd "D:\BOT\pt-ekspor-arang-agent"
docker compose up -d
```
Stop container:
```powershell
cd "D:\BOT\pt-ekspor-arang-agent"
docker compose down
```


## Catatan Penting

- **File `.env` di folder ini berisi 48 nilai konfigurasi yang SUDAH TERISI (bukan kosong)** — kemungkinan besar termasuk API key Gemini/Groq/NVIDIA, kredensial Gmail, token bot Telegram, dan data perusahaan. File ini WAJIB tetap di-gitignore dan tidak boleh pernah di-share/upload ke tempat publik. Segera cek isinya dan pertimbangkan rotate (ganti) key/token kalau folder ini pernah ter-share ke pihak luar atau ter-push ke repo publik.
- **File `credentials.json`** (OAuth client Gmail API) dan **`token.pickle`** (token sesi Gmail yang sudah login) juga ada di folder ini — dua file ini setara dengan "kunci masuk" ke akun Gmail yang dipakai bot. Jangan pernah ikut ter-share/upload ke publik.
- Beberapa scraper media sosial di `requirements.txt` (`linkedin-api`, `TikTokApi`) secara eksplisit ditandai developer aslinya sebagai "HIGH ban risk" — gunakan dengan hati-hati, akun bisa kena suspend kalau ketahuan otomatis scraping.
- Edit dulu `data/product_catalog.json` dengan data produk arang/briket asli (nama produk, grade, MOQ, harga) sebelum menjalankan bot — data ini dipakai langsung di email dan PDF penawaran ke calon pembeli.
- Sudah ada dokumentasi resmi lebih detail di `DOCUMENTATION.md`/`DOCUMENTATION.docx` dan `GAP_ANALYSIS.md`/`GAP_ANALYSIS.docx` — cek file itu juga kalau butuh detail arsitektur lebih dalam.

## Kebutuhan API LLM

- **Butuh API LLM?** Ya, mutlak — inti dari sistem ini adalah agent AI yang menulis email penawaran personal (Outreach Agent) dan mengklasifikasikan balasan email calon pembeli (Monitor Agent), keduanya murni tugas bahasa alami yang tidak bisa dikerjakan rule-based biasa. Saat ini pakai Google Gemini/Groq.
- **Bisa pakai API Claude (Anthropic)?** Ya, bisa — tinggal ganti provider LLM di Outreach Agent (penulisan email) dan Monitor Agent (klasifikasi balasan: tertarik/tidak/butuh info) dari Gemini/Groq ke Claude API. Untuk klasifikasi balasan email yang sifatnya cepat dan sederhana, Claude Haiku 4.5 sudah cukup dan murah; untuk penulisan email penawaran yang perlu nada persuasif dan personalisasi lebih baik, Claude Sonnet 5 lebih pas.

## Instalasi & Eksekusi Offline

- **Bisa instalasi offline?** Instalasi PERTAMA KALI wajib online — `pip install -r requirements.txt` narik ~30 package (CrewAI, ChromaDB, sentence-transformers, playwright, dll) dari PyPI, dan `docker compose up -d --build` juga narik base image `python:3.11-slim` + apt packages (`gcc g++ curl`) dari internet saat build. Setelah pernah diinstall sekali, reinstall/rebuild ulang di mesin yang sama bisa lebih cepat dan sebagian offline kalau pip/Docker masih punya cache lokal (`pip install` akan pakai wheel cache di `~/.cache/pip` atau `%LOCALAPPDATA%\pip\cache` kalau versi persis sama, dan Docker layer cache bikin rebuild `COPY . .` gak perlu install ulang dependency) — tapi ini gak dijamin 100% offline karena pip tetap coba cek index PyPI kecuali dipaksa pakai `--no-index`.
- **Bisa dijalankan offline (setelah terinstall)?** TIDAK BISA full offline — sistem ini secara desain butuh koneksi internet terus-menerus saat runtime karena: (1) Scraper Agent narik data calon pembeli dari Alibaba/TradeIndia/ExportHub/Kompass/Google via HTTP request, (2) Outreach & Monitor Agent manggil LLM API (Gemini/Groq/NVIDIA) tiap kali generate email atau klasifikasi balasan, (3) kirim/baca email lewat Gmail SMTP & Gmail API, (4) notifikasi & command listener lewat Telegram Bot API, (5) fitur tambahan seperti exchange rate API, YouTube API, Bit.ly, dan competitor price monitoring juga semuanya network call. Kalau internet putus, job-job yang butuh network akan gagal/exception (ditangkap oleh `_run_safe` di `scheduler.py` supaya scheduler-nya sendiri gak crash), tapi hasil kerja (scraping baru, email terkirim, balasan diklasifikasi) tidak akan terjadi sampai koneksi kembali. Satu-satunya bagian yang bisa jalan tanpa internet adalah baca/tulis database lokal (`db/crm.db`) dan generate PDF penawaran dari data yang sudah ada di cache/DB (ReportLab tidak butuh network) — tapi itu bukan alur kerja normal sistem.
