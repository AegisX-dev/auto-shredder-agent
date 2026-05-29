# 🧹 Auto-Shredder Agent

Auto-Shredder Agent is a lightweight, fully local, AI-powered file retention manager for Linux desktops. It silently monitors your specified folders (like `~/Downloads` and `~/Desktop`), automatically classifies incoming files, and schedules them for automatic cleanup. 

To ensure safety, **files are never permanently shredded via `rm`**; instead, they are safely moved to the **System Trash**, giving you a 30-day safety net to restore any file.

Built with an ultra-lightweight, pure-Python hybrid classifier (no heavy LLMs, no Ollama, < 50MB RAM usage), it features a premium glassmorphic local web dashboard to track, snooze, or keep your files forever.

---

## ✨ Features

- **📂 Live Folder Watcher:** Monitors folders using highly efficient, event-driven OS APIs (no CPU-heavy polling).
- **🧠 Zero-Dependency Local AI:** Uses a hybrid rules + pure-Python micro-NLP engine (TF-IDF + Naive Bayes) to categorize files in < 1ms.
- **🔔 Intelligent Notification Grouping:** Silences notification spam by grouping 3+ rapid file arrivals into a single aggregated alert.
- **🔒 Safety-First Retention:** Automatically moves expired files to your OS System Trash.
- **💻 Premium Glassmorphic Web UI:** A beautiful dark-themed local web dashboard (`http://localhost:5050`) to view countdowns, override recommendations, snooze files, and customize settings.
- **⚙️ Desktop Autostart:** Automatically launches the background daemon when you log into your Ubuntu session (zero `sudo` required).

---

## 📅 Default Retention Policies

When a new file is detected, the AI categorizes it and applies a tailored timer:

| Category | Default Timer | Rationale |
|---|---|---|
| **Installer** (`.deb`, `.iso`) | 24 Hours | Typically deleted immediately after installation. |
| **Screenshot** (`Screenshot*`) | 48 Hours | Ephemeral captures used for sharing or referencing. |
| **Temporary** (`.tmp`, `.log`) | 6 Hours | Highly short-lived data. |
| **Media** (`.mp4`, `.mp3`, `.png`) | 7 Days | Usually consumed and no longer needed. |
| **Archive** (`.zip`, `.tar.gz`) | 48 Hours | Retained briefly for extraction. |
| **Document** (`.pdf`, `.docx`) | Keep Forever | Important files likely requiring long-term archiving. |
| **Receipt / Invoice** | Keep Forever | Financial or commercial transactions. |
| **Unknown** | 7 Days | Safe default duration allowing manual override. |

---

## 🛠️ Tech Stack

- **Backend:** Python 3 (FastAPI, Uvicorn, SQLite, Watchdog, Send2Trash).
- **Frontend:** Vanilla HTML5, Premium CSS3 (Glassmorphism, Dark-mode, HSL, CSS Keyframes), Vanilla ES6 JavaScript.
- **AI Engine:** Pure-Python custom Vectorizer & Naive Bayes Classifier (~50KB payload, <1MB RAM).
- **Service Integration:** Systemd User Services (`systemctl --user`).

---

## 🚀 Step-by-Step Installation

Once the codebase is built, installing is simple:

```bash
# Clone the repository (or navigate to your local folder)
cd ~/dev-code/auto-shredder-agent

# Run the installer
chmod +x install.sh
./install.sh
```

The installer will automatically set up a Python virtual environment, install the minimal required dependencies, register a user-level autostart systemd service, and open the web dashboard in your browser.

---

## 📄 License

MIT License. Designed with care for local, private desktop automation.
