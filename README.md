# termux-agent

CLI coding agent untuk **Termux (Android)**, mirip [opencode](https://opencode.ai): chat dengan LLM + tool-use loop untuk membaca/menulis file, mencari kode, dan menjalankan perintah — dengan dukungan **multi-provider**.

## Fitur

- **Langsung jalan**: setelah install cukup ketik `termux-agent` — default memakai OpenCode Zen model free (tanpa API key), seperti opencode yang langsung bisa dipakai.
- **Agent loop lengkap**: model bebas memilih jawab atau memanggil tool (`read_file`, `write_file`, `edit_file`, `list_dir`, `grep_file`, `glob_find`, `run_command`, `web_fetch`, `git_status`, `git_diff`, `git_commit`) sampai tugas selesai.
- **Rules proyek**: file `AGENTS.md`, `CLAUDE.md`, atau `.termux-agent/rules.md` di direktori kerja (dan parent sampai `$HOME`) otomatis dimuat ke instruksi agent — seperti opencode.
- **Git terintegrasi**: agent bisa cek status, diff, dan commit (commit butuh konfirmasi).
- **Resume sesi**: lanjutkan percakapan sebelumnya dengan `--resume` atau `/resume`.
- **Compact konteks**: `/compact` merangkum riwayat lama agar hemat token.
- **Mode otomatis**: `--yes` melewati semua konfirmasi (cocok untuk skrip).
- **Akses storage Android**: aktifkan `allow_storage: true` di config untuk akses file di `/storage/emulated/0` (jalankan `termux-setup-storage` dulu).
- **Multi-provider** via preset: OpenAI, Anthropic, OpenRouter, Ollama, Groq, DeepSeek, Gemini, dan **OpenCode Zen**.
- **Mode interaktif & one-shot**.
- **Sesi tersimpan** ke `~/.termux-agent/sessions/`.
- **Aman untuk mobile**: batas direktori kerja, konfirmasi perintah non-whitelist, timeout, dan pemotongan output agar hemat memori.

## Instalasi di Termux

```bash
# 1. siapkan storage (opsional, untuk akses file Android)
termux-setup-storage

# 2. pasang Python + git
pkg update && pkg install -y python git

# 3. clone & install (menciptakan perintah `termux-agent`)
cd ~
git clone https://github.com/ANDA/termux-agent.git
cd termux-agent
pip install .

# 4. pakai — langsung jalan, tanpa konfigurasi apa pun
termux-agent                       # mode interaktif
```

Pada run pertama `~/.termux-agent/config.yaml` dibuat otomatis; kamu bisa mengabaikannya karena default sudah memakai model free. Untuk provider berbayar, cukup set API key di env:

```bash
export OPENAI_API_KEY=sk-...        # atau ANTHROPIC_API_KEY, GROQ_API_KEY, OPENCODE_API_KEY, dst.
```

Skrip satu-perintah juga tersedia: `bash scripts/install-termux.sh`.

## Pemakaian

```bash
# mode interaktif (default provider dari config)
termux-agent

# mode one-shot
termux-agent "baca file main.py lalu perbaiki bug-nya"

# lanjutkan sesi terakhir
termux-agent --resume
termux-agent --resume 20260819-233713 "lanjutan: ..."

# lewati semua konfirmasi (berbahaya; cocok untuk skrip)
termux-agent --yes "git commit semua perubahan"

# pilih provider/model
termux-agent --provider zen --model nemotron-3-ultra-free "cek isi direktori ini"

# daftar preset provider
termux-agent --list-providers

# daftar sesi
termux-agent --sessions
```

### Rules proyek

Letakkan `AGENTS.md` (atau `CLAUDE.md`, `.termux-agent/rules.md`) di direktori kerja — isinya otomatis jadi instruksi untuk agent:

```bash
echo "Selalu tulis docstring di tiap fungsi." > AGENTS.md
termux-agent "tambah docstring ke app.py"   # mengikuti aturan itu
```

### Perintah di mode interaktif

```
/exit, /quit   keluar
/new           mulai sesi baru
/provider NAME ganti provider (mis. /provider ollama)
/model MODEL   ganti model
/help          bantuan
/cwd           tampilkan direktori kerja
/sessions      daftar sesi
/resume [ID]   lanjutkan sesi (ID opsional, default terbaru)
/compact       ringkas riwayat sesi agar hemat konteks
```

### Akses file Android (storage)

Agar agent bisa membaca/menulis file di penyimpanan Android (foto, dokumen, dll):

```bash
termux-setup-storage          # sekali: membuat ~/storage -> /storage/emulated/0
# lalu aktifkan di ~/.termux-agent/config.yaml:
#   allow_storage: true
termux-agent "baca file terbaru di folder Download"
```

## Provider & API key

| Provider   | Env var               | Catatan                                  |
|------------|-----------------------|------------------------------------------|
| openai     | `OPENAI_API_KEY`      | OpenAI GPT                                 |
| anthropic  | `ANTHROPIC_API_KEY`   | Claude (Messages API)                     |
| openrouter | `OPENROUTER_API_KEY`  | Banyak model via satu key                  |
| ollama     | (tanpa key)           | Model lokal di perangkat (`pkg install ollama`) |
| groq       | `GROQ_API_KEY`        | Model cepat & murah                        |
| deepseek   | `DEEPSEEK_API_KEY`    | DeepSeek chat/reasoner                     |
| gemini     | `GEMINI_API_KEY`      | Google Gemini (endpoint OpenAI-compat)     |
| zen        | `OPENCODE_API_KEY` (opsional) | OpenCode Zen — model free jalan tanpa key |

### Model free OpenCode Zen

Tanpa API key, gunakan model `*free`:

```bash
termux-agent --provider zen --model nemotron-3-ultra-free "berapa 2+2?"
```

Model free yang tersedia saat ini: `nemotron-3-ultra-free`, `deepseek-v4-flash-free`, `mimo-v2.5-free`, `big-pickle`. Model lain butuh `OPENCODE_API_KEY` dari <https://opencode.ai/auth>.

## Pengembangan

```bash
pip install -e ".[dev]"          # dependency dev (pytest)
python -m pytest tests/ -q        # 37 test: tool, provider, agent, git, rules, resume
python tests/mock_server.py &     # mock OpenAI/Anthropic server (port 8765)
termux-agent --model mock-model "..."   # tes tanpa API key
```

Struktur:

```
termux_agent/
├── cli.py          # CLI entry point
├── config.py       # konfigurasi + preset provider
├── agent.py        # loop agent + tool-call
├── session.py      # simpan sesi JSONL
├── providers/      # base, openai_compat, anthropic
├── tools/          # files, search, shell, web + registri
└── ui/             # renderer (rich) + REPL (prompt_toolkit)
```

## Lisensi

MIT