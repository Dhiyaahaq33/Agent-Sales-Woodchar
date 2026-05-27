# CHANGELOG — AI Sales Agent System v2.0
**PT Cahaya Woodchar International**
Lampung Utara, Lampung, Indonesia
Prepared by: Claude Code | Mei 2026

---

## Ringkasan Eksekutif

Sistem v1.0 adalah fondasi pipeline penjualan otomatis: scraping leads, enrichment, cold email, follow-up, klasifikasi reply, proposal PDF, dan daily report Telegram. Sistem ini sudah berjalan.

**v2.0 adalah upgrade besar** yang membawa sistem dari *tool internal* menjadi *mesin penjualan kelas enterprise* — lebih cerdas, lebih aman, lebih visible, dan beroperasi 24/7 tanpa bergantung pada laptop.

---

## Apa yang Berubah dari v1.0 ke v2.0

### 1. Ganti LLM Engine — Lebih Cepat, Tidak Pernah Mati

**v1.0:** Hanya Gemini. Jika Gemini rate limit (15 RPM), sistem error dan skip task.

**v2.0:** Smart routing otomatis.

```
Gemini 2.0 Flash (primary)
    ↓ jika 429 / rate limit
Groq llama-3.3-70b (fallback, 30 RPM)
    ↓ jika Groq juga gagal
Exponential backoff → retry
```

**File baru:** `tools/llm_router.py`
**Yang berubah:** `tools/gemini_tool.py` — migrasi ke SDK `google-genai` terbaru, model `gemini-2.0-flash`
**Setup tambahan:** Daftar gratis di [console.groq.com](https://console.groq.com), tambah `GROQ_API_KEY` di `.env`

---

### 2. Telegram Bot — Dari Reporter Pasif ke Command Center

**v1.0:** Bot hanya kirim laporan harian. Tidak bisa dibalas.

**v2.0:** Bot adalah command center dua arah. COO bisa kontrol sistem dari HP kapan saja.

| Command | Fungsi |
|---|---|
| `/stats` | Summary hari ini: leads, email, reply, interested |
| `/leads 20` | Tampilkan 20 leads terbaru dengan status dan score |
| `/interested` | List semua buyer yang INTERESTED + QUALIFIED |
| `/pipeline` | Funnel lengkap: NEW → ENRICHED → OUTREACH → DEAD |
| `/product halaban` | Info produk dari catalog untuk quick reference saat negosiasi |
| `/stop` | Pause SEMUA agent (emergency stop) |
| `/start` | Resume semua agent setelah pause |
| `/errors` | Error log 24 jam terakhir untuk debugging |
| `/weekly` | Report mingguan dengan perbandingan |
| `/approve 5` | Approve draft negosiasi id=5 → langsung kirim ke buyer |
| `/reject 5` | Tolak draft negosiasi id=5 → email tidak dikirim |

**File yang diupdate:** `tools/telegram_tool.py`
**Cara kerja:** Bot berjalan di background thread, polling setiap 30 detik, dispatch command ke handler yang sesuai.

---

### 3. Produk Catalog — Data Aktual PT Cahaya Woodchar

**v1.0:** Catalog generic placeholder "PT Ekspor Arang" dengan harga dummy.

**v2.0:** Catalog resmi 5 produk PT Cahaya Woodchar International.

| Produk | Fixed Carbon | Calorific Value | Target Pasar |
|---|---|---|---|
| Halaban Lump Wood Charcoal | 75–85% | 7.500–8.000 kcal/kg | Restoran, steakhouse, catering premium |
| Mix Hardwood Charcoal | 70–80% | 7.000–7.800 kcal/kg | Pasar massal, dapur komersial |
| BBQ Briquette Charcoal | 70–80% | 6.800–7.500 kcal/kg | Retail supermarket, ekspor BBQ |
| Sawdust Charcoal | 65–75% | — | Rumah tangga, industri, eco-friendly |
| Coconut Shell Charcoal | 75–85% | 7.000–7.500 kcal/kg | Shisha internasional, BBQ premium |

> ⚠️ **COO WAJIB mengisi field `price_usd_per_mt` dan `moq_mt`** di file `data/product_catalog.json` sebelum sistem mulai kirim proposal ke buyer. Saat ini field tersebut berisi `"FILL_BY_COO"`.

**File yang diupdate:** `data/product_catalog.json`

---

### 4. Trigger Engine — Email Dikirim di Waktu yang Tepat

**v1.0:** Semua leads di-outreach dengan template yang sama, tanpa mempertimbangkan musim atau event.

**v2.0:** Sistem mendeteksi konteks pasar dan menyesuaikan prioritas outreach secara otomatis.

**Trigger yang berjalan setiap hari pukul 06:30:**

| Trigger | Kondisi Aktif | Target | Produk Fokus |
|---|---|---|---|
| Ramadan Pre-Season | 30 hari sebelum Ramadan | UAE, Saudi, Qatar, Kuwait | Coconut Shell (shisha) |
| BBQ Season Europe | Maret–September | Jerman, Belanda, Prancis, UK | Briquette, Halaban |
| BBQ Season Australia | Oktober–Maret | Australia, NZ | Briquette, Halaban |
| Winter Europe | Oktober–Februari | Jerman, Belanda, Polandia | Sawdust, Mix Hardwood |
| Winter Japan/Korea | November–Februari | Jepang, Korea Selatan | Sawdust, Coconut Shell |
| Trade Show Blast | 21 hari sebelum event | Negara event | Semua produk |

**Trade shows yang dipantau:** Gulfood Dubai, SIAL Paris, FOODEX Japan, Seoul Food & Hotel

**File baru:** `tools/trigger_engine.py`

---

### 5. A/B Testing Otomatis — System Belajar Sendiri

**v1.0:** Template email statis. Tidak ada mekanisme improvement otomatis.

**v2.0:** 20% leads secara random mendapat email variant B. Setelah 7 hari, sistem otomatis membandingkan reply rate dan memilih pemenang.

**Contoh elemen yang ditest:**

| Elemen | Variant A | Variant B |
|---|---|---|
| Subject line | "Premium Indonesian Charcoal for Your Business" | "Quick question about your charcoal supply" |
| Opening | "I am reaching out regarding..." | "Noticed you import charcoal — we export 200MT/month" |
| CTA | "Reply to discuss pricing" | "Can I send you our spec sheet?" |
| Panjang email | 150 kata | 80 kata |

Jika Variant B unggul dengan confidence ≥ 80% → B otomatis jadi template baru. Loop ini berjalan terus, sistem semakin optimal.

**File baru:** `tools/ab_tester.py`

---

### 6. ML Lead Scoring — Ganti Rule-Based dengan AI

**v1.0:** Scoring dengan aturan manual: email valid +20, website live +15, dst. Akurasi terbatas.

**v2.0:** Random Forest Classifier dari scikit-learn. Model dilatih dari data historis leads yang pernah convert vs tidak convert.

**Fitur yang dipelajari model:**
- Negara (proxy market quality)
- Source platform (TradeIndia vs Google vs Kompass)
- Ada/tidak email valid
- Ada/tidak website aktif
- Keyword density (charcoal/import/wholesale)
- Historical reply rate per segmen

**Jadwal:** Retrain otomatis setiap Minggu pukul 03:00. Model disimpan di `db/scoring_model.pkl`. Jika data belum cukup (< 30 records), sistem tetap pakai rule-based scoring.

**File baru:** `tools/scoring_ml.py`
**Install:** `pip install scikit-learn`

---

### 7. Google Sheets CRM Sync — Lihat Pipeline dari HP

**v1.0:** Data hanya bisa dilihat di terminal atau query SQLite langsung.

**v2.0:** Data otomatis tersinkronisasi ke Google Sheets setiap 30 menit. COO dan CBO bisa lihat dan anotasi dari HP.

**Spreadsheet: "Cahaya Woodchar CRM"** dengan 4 sheet:

| Sheet | Isi | Color Coding |
|---|---|---|
| Leads Pipeline | Semua leads + status, score, country | Merah = DEAD, Hijau = INTERESTED |
| Interested Buyers | Hanya INTERESTED + QUALIFIED, kolom COO Notes bisa diisi | Hijau |
| Outreach Log | Semua email terkirim + reply status | — |
| Weekly Stats | Agregasi harian untuk chart presentasi | — |

**File baru:** `tools/sheets_sync.py`
**Frekuensi sync:** Setiap 30 menit (dapat diubah di `scheduler.py`)

---

### 8. Streamlit Dashboard — Web UI Lokal

**v1.0:** Tidak ada UI. Hanya terminal dan Telegram.

**v2.0:** Dashboard web lengkap yang bisa dibuka di browser.

```bash
streamlit run dashboard/app.py
# Akses: http://localhost:8501
```

**5 halaman:**

| Halaman | Konten |
|---|---|
| 🏠 Dashboard | KPI cards, funnel chart, leads terbaru, INTERESTED buyers |
| 📋 Leads | Filter by status/country/score, export CSV |
| 📧 Outreach | Log email, timeline activity 14 hari |
| 📊 Analytics | Country distribution, score histogram, A/B results, classification chart |
| 🚨 Errors | Log error 7 hari, filter by severity |

**File baru:** `dashboard/app.py`

---

### 9. Market Intelligence Agent — Morning Briefing Harian

**v1.0:** Tidak ada monitoring pasar.

**v2.0:** Setiap pukul 06:30, agent mengirim briefing pagi ke Telegram COO sebelum agen sales mulai bekerja.

**Format briefing:**
```
🌅 MORNING BRIEFING — 27 Mei 2026

💱 USD/IDR: 16.250
📈 Demand signals: UAE: 82 | Japan: 71 | Australia: 65
🏪 Gulfood Dubai — 14 hari lagi
🔥 Active seasonal: Ramadan Pre-Season, BBQ Season Australia
⚡ Recommended: Prioritaskan UAE & Saudi buyers hari ini
```

**Data yang dikumpulkan:**
- Kurs USD/IDR realtime (ExchangeRate-API, gratis 1.500 req/bulan)
- Google Trends demand signals per negara target
- Trade show countdown
- Alibaba competitor pricing (best-effort scraping)

**File baru:** `agents/market_agent.py`
**Jadwal:** Setiap hari 06:30 WIB

---

### 10. Negotiation Agent — Auto-Draft dengan Safety Gate

**v1.0:** Setelah buyer INTERESTED, COO harus balas manual.

**v2.0:** Sistem otomatis draft balasan negosiasi menggunakan Gemini/Groq — tapi **TIDAK PERNAH kirim sendiri**.

**Alur:**
```
Monitor Agent deteksi reply INTERESTED
    → Negotiation Agent draft jawaban
    → Simpan ke database (status: PENDING)
    → Kirim ke COO via Telegram untuk review
    → COO balas /approve 5  → email terkirim
    → COO balas /reject 5   → email dibatalkan
```

> ⚠️ **SAFETY GATE ini tidak boleh dinonaktifkan.** `NEGOTIATION_AUTO_APPROVE` harus tetap `false`. Satu email negosiasi yang salah bisa merusak deal bernilai puluhan juta rupiah.

**File baru:** `agents/negotiation_agent.py`

---

### 11. Deliverability Manager — Jaga Reputasi Gmail

**v1.0:** Tidak ada proteksi reputasi domain. Satu blacklist manual.

**v2.0:** Sistem otomatis menjaga agar Gmail tidak masuk spam.

**Fitur:**
- **Bounce handler:** Email undeliverable → auto-mark DEAD + blacklist
- **Unsubscribe handler:** Deteksi kata "unsubscribe", "remove me", "stop emailing" → DEAD + blacklist
- **Domain rate limit:** Maks 3 email per domain (@alibaba.com dll) per 7 hari
- **Bounce rate monitor:** Jika >5% bounce → pause otomatis + alert COO
- **Blacklist check:** Wajib diperiksa sebelum setiap pengiriman

**File baru:** `tools/deliverability.py`

---

### 12. Database — 7 Tabel Baru, 8 Kolom Baru

**v1.0:** 3 tabel: leads, outreach, proposals.

**v2.0:** 10 tabel total.

**Tabel baru:**

| Tabel | Fungsi |
|---|---|
| `buyer_memory` | Riwayat interaksi per buyer (pondasi Phase 4 vector memory) |
| `ab_tests` | Tracking A/B test variants dan results |
| `market_intel` | Snapshot harga, kurs, demand signal harian |
| `triggers` | Jadwal dan status seasonal/event triggers |
| `blacklist` | Email/domain yang di-exclude dari outreach |
| `negotiation_log` | Draft negosiasi + status approval COO |
| `deliverability_log` | Tracking bounce rate harian |

**Kolom baru di tabel existing:**

| Tabel | Kolom | Fungsi |
|---|---|---|
| leads | `ab_variant` | Variant A atau B yang diterima lead |
| leads | `trigger_source` | Trigger yang memicu outreach |
| leads | `last_news` | Berita terbaru tentang perusahaan buyer |
| leads | `preferred_product` | Produk yang disebut buyer di reply |
| leads | `price_sensitivity` | HIGH/MEDIUM/LOW dari analisis reply |
| outreach | `send_local_time` | Waktu kirim dalam timezone buyer |
| outreach | `channel` | email / whatsapp / linkedin |
| outreach | `open_tracked` | Flag email dibuka |

**File baru:** `db/migrate_v2.py` — jalankan SEKALI sebelum v2 aktif.

---

### 13. Jadwal Scheduler — 9 Jobs (dari 6 di v1.0)

| Waktu | Job | v1.0 |
|---|---|---|
| 06:30 | Market Intelligence Agent | ❌ Baru |
| 08:00 | Scraper + Enrichment | ✅ Ada |
| 09:00 | Outreach Agent | ✅ Ada |
| Tiap 2 jam | Monitor Agent | ✅ Ada |
| 14:00 | Follow-up Agent | ✅ Ada |
| 18:00 | Reporter Agent | ✅ Ada |
| Tiap 30 menit | Google Sheets Sync | ❌ Baru |
| Minggu 03:00 | ML Retrain + A/B Evaluation | ❌ Baru |
| 02:00 | Database Backup | ✅ Ada |

---

## File Baru — Summary Lengkap

```
tools/
├── llm_router.py        ← Gemini + Groq routing otomatis
├── trigger_engine.py    ← Seasonal & trade show triggers
├── ab_tester.py         ← A/B testing otomatis
├── scoring_ml.py        ← Random Forest lead scoring
├── sheets_sync.py       ← Google Sheets CRM sync
└── deliverability.py    ← Bounce/unsubscribe/domain rate limit

agents/
├── market_agent.py      ← Morning briefing 06:30
└── negotiation_agent.py ← Draft negosiasi + approval gate

db/
└── migrate_v2.py        ← Migration script v1 → v2

dashboard/
└── app.py               ← Streamlit 5-page dashboard
```

---

## File yang Diubah

| File | Perubahan |
|---|---|
| `config.py` | +15 env vars baru, feature flags, company name update |
| `tools/gemini_tool.py` | Migrasi ke `google-genai` SDK, model ke `gemini-2.0-flash` |
| `tools/telegram_tool.py` | Tambah 10 commands + long-polling listener thread |
| `data/product_catalog.json` | 5 produk aktual PT Cahaya Woodchar (harga perlu diisi COO) |
| `scheduler.py` | 9 jobs (dari 6), tambah pause check dari `/stop` |
| `main.py` | Auto-run migration, start Telegram listener, startup notification |
| `requirements.txt` | +10 packages baru |
| `.env.example` | +12 env vars baru dengan komentar lengkap |

---

## Cara Update ke v2.0

Jika sistem v1.0 sudah berjalan, ikuti langkah berikut:

```bash
# 1. Backup sudah otomatis dibuat di: D:\BOT\pt-ekspor-arang-agent-backup

# 2. Install library baru
cd D:\BOT\pt-ekspor-arang-agent
venv\Scripts\activate
pip install -r requirements.txt

# 3. Jalankan database migration (SEKALI SAJA)
python db/migrate_v2.py

# 4. Update file .env — tambah variabel baru
notepad .env
# Tambahkan:
#   GROQ_API_KEY=       ← dari console.groq.com (gratis)
#   EXCHANGERATE_API_KEY= ← dari exchangerate-api.com (gratis)
#   GEMINI_MODEL=gemini-2.0-flash
#   NEGOTIATION_AUTO_APPROVE=false
#   AB_TEST_PERCENTAGE=20
#   BOUNCE_RATE_THRESHOLD=5

# 5. Isi harga produk di catalog
notepad data\product_catalog.json
# Cari semua "FILL_BY_COO" dan ganti dengan harga/MOQ aktual

# 6. Jalankan sistem
python main.py
```

---

## Hal yang Perlu Dilakukan COO Sebelum Sistem Berjalan

- [ ] **Wajib:** Isi `price_usd_per_mt` dan `moq_mt` di `data/product_catalog.json`
- [ ] **Wajib:** Tambah `GROQ_API_KEY` di `.env` (daftar gratis di console.groq.com)
- [ ] **Wajib:** Tambah `NEGOTIATION_AUTO_APPROVE=false` di `.env`
- [ ] **Opsional:** Tambah `EXCHANGERATE_API_KEY` untuk kurs realtime di morning briefing
- [ ] **Opsional:** Jalankan Streamlit dashboard: `streamlit run dashboard/app.py`

---

## Safety Gates yang Tidak Boleh Diubah

> Ini adalah batas pengaman sistem. Melanggarnya bisa merusak reputasi Gmail atau merugikan negosiasi.

1. **Negotiation Agent WAJIB approval COO** — `NEGOTIATION_AUTO_APPROVE` harus `false`
2. **Bounce rate >5% = pause otomatis** — threshold tidak boleh dinaikan sembarangan
3. **Max 200 email/hari** — hard limit untuk mencegah Gmail ban
4. **Blacklist selalu dicek** sebelum setiap pengiriman
5. **Max 3 email per domain per 7 hari** — domain rate limiting

---

## Roadmap Berikutnya (Belum Diimplementasi)

| Fase | Fitur | Estimasi |
|---|---|---|
| Fase 4 lanjutan | ChromaDB vector memory untuk long-term buyer context | Bulan 3–4 |
| Fase 3 lanjutan | WhatsApp outreach (WA-JS) — opsional | Bulan 2–3 |
| Fase 3 lanjutan | LinkedIn automation (Selenium) — opsional | Bulan 2–3 |
| Deployment | Railway/Render 24/7 cloud deploy + Dockerfile | Bulan 2 |
| Database | Migrasi ke PostgreSQL jika >100K leads | Bulan 4–6 |

---

*CHANGELOG v2.0 — PT Cahaya Woodchar International*
*Confidential — Internal Use Only*
*© 2026*
