# Session State: Auto-Shredder Agent

This file tracks the current state of our pair programming session, architectural decisions, completed tasks, and the exact roadmap to resume development seamlessly.

---

## 1. Project Overview

*   **Project Name:** Auto-Shredder Agent
*   **Project Directory:** `/home/aegis-dev/dev-code/auto-shredder-agent`
*   **Target Machine Specs:** Lenovo ThinkPad L13 Gen 2, Intel Core i5-1145G7, 16GB RAM, Intel Iris Xe iGPU, Ubuntu 26.04 LTS.
*   **Design Philosophy:** Zero-dependency, ultra-lightweight (< 50MB RAM idle), local-only privacy, safety-first deletion (System Trash), and high-fidelity custom glassmorphism web dashboard.

---

## 2. Competitive Landscape & Differentiation

Our recent competitive analysis against standard utilities like `organize` (by Thomas Feldmann), `DropIt`, and `Incron/Bash scripts` has solidified our core product boundaries:
*   **AI vs. Complex YAML Rules:** Competitors require manual YAML setup, regular expressions, and configuration files. Auto-Shredder uses a local micro-ML model to classify files automatically—**zero user coding required**.
*   **Visual Control vs. Silent Operations:** Competitors delete files instantly and silently. Auto-Shredder uses **interactive desktop notifications** and a **premium glassmorphic web dashboard** to show countdowns and allow manual overrides.
*   **Delayed Expiry vs. Instant Deletion:** Instead of immediate actions, Auto-Shredder schedules files on a countdown (e.g., 24h), allowing users to click **Snooze (+24h)** or **Keep Forever** before any action occurs.
*   **Recoverable Trash vs. Destructive `rm`:** Auto-Shredder integrates directly with the OS System Trash (`send2trash`), making all auto-cleaned files 100% recoverable for 30 days.

---

## 3. Key Architectural Decisions Finalized

1.  **Zero-Ollama Custom ML:** Ditched heavy local LLMs/Ollama runtimes (saving 2GB+ disk and 2GB+ background RAM). Instead, we are using a **pure-Python custom ML engine** (TF-IDF Vectorizer + Naive Bayes Classifier) loaded via a ~50KB pre-trained vocabulary weights JSON file.
2.  **Safety Trashing:** Deletion events will *never* use permanent `rm` or secure shredding. They will utilize the `send2trash` Python library to move files directly to the standard OS System Trash (`~/.local/share/Trash`), preserving a 30-day user recovery safety net.
3.  **Opt-Out Notifications:** Desktop notifications (via standard Linux `notify-send`) will have a global toggle switch within the web dashboard settings.
4.  **Intelligent Notification Batching:** To prevent notification spam when multiple files arrive (e.g., zip extraction, bulk screenshot uploads), a watcher buffer groups $\ge 3$ files detected in a 3-second window into a single combined notification: *"🔔 Auto-Shredder: X new files detected in Downloads. [Open Dashboard]"*.
5.  **User-Level Desktop Autostart:** The daemon autostarts on user login via a user-space systemd service (`systemctl --user`), maintaining a completely rootless/zero-`sudo` runtime environment.

---

## 4. Completed Tasks

### Phase 1: Project Setup & Environment
- [x] Create project directories (`src/`, `src/web/`, `data/`, `tests/`)
- [x] Write `requirements.txt` (fastapi, uvicorn, watchdog, send2trash)
- [x] Initialize Python virtual environment & install dependencies
- [x] Create `__init__.py` files for packages
- [x] Create `pyproject.toml` with Pyrefly settings

### Phase 2: Hybrid Classification Engine
- [x] Create pre-trained weights JSON (`src/model_data.json`) — 141-term vocabulary, 8 class Naive Bayes
- [x] Implement `RuleEngine` — extension map (70+ extensions) + filename pattern matching (14 regex patterns)
- [x] Implement `MicroMLClassifier` — pure-Python TF-IDF weighted Naive Bayes (zero sklearn/numpy)
- [x] Implement `HybridClassifier` — rules-first, ML-fallback pipeline with confidence scores
- [x] Fix rule priority: filename patterns fire before extension matching
- [x] Write unit tests (`tests/test_classifier.py`) — 66 tests covering rules, ML, retention, performance, edge cases
- [x] Verify all tests pass (66/66 in 4ms)
- [x] Verify performance NFR: rule classification < 1ms, ML classification < 50ms, bulk 100 files < 100ms

### Phase 3: Database & Core Daemon
- [x] Create SQLite database layer (`src/database.py`) — schema, CRUD, WAL mode, settings store
- [x] Create desktop notification engine (`src/notifier.py`) — notify-send wrapper with batch support
- [x] Implement file system watcher (`src/watcher.py`) with watchdog + batch detection (3s window, ≥3 file threshold)
- [x] Implement timer scheduler & safety trashing (`src/scheduler.py`) — 60s polling, send2trash only
- [x] Smoke test: all modules import, DB schema initializes, insert/query/update lifecycle works

---

## 5. Current Blockers & Unresolved Bugs

*   **None.** Core classification engine and daemon components are fully functional and ready to be integrated.

---

## 6. Exact Next Steps

We are ready to commit the daemon and DB components (Phase 3) and move to Phase 4 & Phase 5:

### Phase 4: FastAPI Web Server & API (`src/main.py`)
1.  Implement FastAPI & Uvicorn entry point.
2.  Design endpoints:
    - `GET /api/files` — Retrieve tracked files list.
    - `POST /api/files/{id}/action` — Keep, Trash, or Snooze actions.
    - `GET /api/settings` and `POST /api/settings` — Read/Write system configurations.
3.  Coordinate Watcher & Scheduler startup/shutdown with the FastAPI app lifecycle events.

### Phase 5: Premium Web UI Dashboard (`src/web/`)
1.  Create glassmorphic HTML/CSS/JS dashboard.
2.  Enable real-time countdown display and override controls.
