/**
 * Auto-Shredder Agent — Dashboard Controller
 *
 * Pure vanilla ES6. Handles API polling, countdown rendering,
 * tab navigation, file actions, settings, and toast notifications.
 */

"use strict";

// ─── Configuration ───
const API_BASE = window.location.origin;
const POLL_INTERVAL_MS = 5000;
const COUNTDOWN_TICK_MS = 1000;

const CATEGORY_EMOJI = {
    installer: "📦",
    screenshot: "📸",
    receipt_invoice: "🧾",
    temporary: "🗑️",
    document: "📄",
    media: "🎬",
    archive: "📁",
    unknown: "❓",
};

// ─── State ───
let currentTab = "active";
let pollTimer = null;
let countdownTimer = null;
let activeFiles = [];
let snoozeTargetId = null;
let snoozeHours = 24;

// ─── DOM References ───
const dom = {
    statActive: document.getElementById("stat-active-value"),
    statTrashed: document.getElementById("stat-trashed-value"),
    statReclaimed: document.getElementById("stat-reclaimed-value"),
    fileList: document.getElementById("file-list"),
    historyList: document.getElementById("history-list"),
    emptyActive: document.getElementById("empty-active"),
    emptyHistory: document.getElementById("empty-history"),
    healthIndicator: document.getElementById("health-indicator"),
    healthLabel: document.querySelector(".health__label"),
    tabSlider: document.getElementById("tab-slider"),
    notificationsCheckbox: document.getElementById("notifications-checkbox"),
    watchedDirs: document.getElementById("watched-dirs"),
    apiEndpoint: document.getElementById("api-endpoint-display"),
    snoozeModal: document.getElementById("snooze-modal"),
    snoozeFilename: document.getElementById("snooze-modal-filename"),
    snoozeCancel: document.getElementById("snooze-cancel"),
    snoozeConfirm: document.getElementById("snooze-confirm"),
    snoozeOptions: document.getElementById("snooze-options"),
    toastContainer: document.getElementById("toast-container"),
};

// ═══════════════════════════════════════════
// API Client
// ═══════════════════════════════════════════

async function apiFetch(path, options = {}) {
    const url = `${API_BASE}${path}`;
    try {
        const response = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        if (error instanceof TypeError && error.message.includes("fetch")) {
            throw new Error("Cannot connect to Auto-Shredder Agent server");
        }
        throw error;
    }
}

const api = {
    getActiveFiles: () => apiFetch("/api/files"),
    getHistory: (limit = 50) => apiFetch(`/api/files/history?limit=${limit}`),
    getStats: () => apiFetch("/api/stats"),
    getHealth: () => apiFetch("/api/health"),
    getSettings: () => apiFetch("/api/settings"),
    keepFile: (id) => apiFetch(`/api/files/${id}/keep`, { method: "POST" }),
    snoozeFile: (id, hours) => apiFetch(`/api/files/${id}/snooze?hours=${hours}`, { method: "POST" }),
    trashFile: (id) => apiFetch(`/api/files/${id}/trash`, { method: "POST" }),
    updateSettings: (data) => apiFetch("/api/settings", { method: "POST", body: JSON.stringify(data) }),
};

// ═══════════════════════════════════════════
// Formatting Utilities
// ═══════════════════════════════════════════

function formatBytes(bytes) {
    if (bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    const val = bytes / Math.pow(1024, i);
    return `${val < 10 ? val.toFixed(1) : Math.round(val)} ${units[i]}`;
}

function formatCountdown(expiresAt) {
    if (!expiresAt) return { text: "∞ Keep", urgent: false, kept: true };

    const now = Date.now();
    const expires = new Date(expiresAt + (expiresAt.endsWith("Z") ? "" : "Z")).getTime();
    const diff = expires - now;

    if (diff <= 0) return { text: "Expired", urgent: true, kept: false };

    const totalMinutes = Math.floor(diff / 60000);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;

    if (hours >= 24) {
        const days = Math.floor(hours / 24);
        const remHours = hours % 24;
        return { text: `${days}d ${remHours}h`, urgent: false, kept: false };
    }

    if (hours > 0) {
        return { text: `${hours}h ${minutes}m`, urgent: hours < 2, kept: false };
    }

    return { text: `${minutes}m`, urgent: true, kept: false };
}

function getEmoji(category) {
    return CATEGORY_EMOJI[category] || "❓";
}

// ═══════════════════════════════════════════
// Rendering — File Cards
// ═══════════════════════════════════════════

function renderFileCard(file, showActions = true) {
    const countdown = formatCountdown(file.expires_at);
    const statusClass = file.status === "kept" ? "file-card--kept"
        : file.status === "trashed" ? "file-card--trashed"
        : file.status === "snoozed" ? "file-card--snoozed"
        : "";

    const timerClass = countdown.kept ? "file-card__timer--kept"
        : countdown.urgent ? "file-card__timer--urgent"
        : "";

    const timerLabel = file.status === "kept" ? "Kept Forever"
        : file.status === "trashed" ? "Trashed"
        : file.status === "snoozed" ? "Snoozed"
        : "Remaining";

    const actionsHtml = showActions && file.status !== "kept" && file.status !== "trashed"
        ? `<div class="file-card__actions">
            <button class="btn btn--keep" data-action="keep" data-id="${file.id}" title="Keep forever">🔒 Keep</button>
            <button class="btn btn--snooze" data-action="snooze" data-id="${file.id}" title="Snooze">⏸️ Snooze</button>
            <button class="btn btn--trash" data-action="trash" data-id="${file.id}" title="Trash now">🗑️ Trash</button>
           </div>`
        : "";

    return `
    <div class="file-card ${statusClass}" data-file-id="${file.id}">
        <div class="file-card__emoji">${getEmoji(file.category)}</div>
        <div class="file-card__info">
            <span class="file-card__name" title="${escapeHtml(file.filepath)}">${escapeHtml(file.filename)}</span>
            <div class="file-card__meta">
                <span class="file-card__category">${escapeHtml(file.category)}</span>
                <span class="file-card__size">${formatBytes(file.file_size_bytes)}</span>
                <span class="file-card__source">${file.source}</span>
            </div>
        </div>
        <div class="file-card__countdown">
            <span class="file-card__timer ${timerClass}" data-expires="${file.expires_at || ""}">${countdown.text}</span>
            <span class="file-card__timer-label">${timerLabel}</span>
        </div>
        ${actionsHtml}
    </div>`;
}

function escapeHtml(str) {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return String(str).replace(/[&<>"']/g, (m) => map[m]);
}

// ═══════════════════════════════════════════
// Data Fetching & Panel Updates
// ═══════════════════════════════════════════

async function refreshActiveFiles() {
    try {
        activeFiles = await api.getActiveFiles();
        if (activeFiles.length === 0) {
            dom.fileList.innerHTML = "";
            dom.emptyActive.classList.add("empty-state--visible");
        } else {
            dom.emptyActive.classList.remove("empty-state--visible");
            dom.fileList.innerHTML = activeFiles.map((f) => renderFileCard(f, true)).join("");
        }
    } catch (err) {
        console.error("[Auto-Shredder] Failed to fetch active files:", err.message);
    }
}

async function refreshHistory() {
    try {
        const history = await api.getHistory();
        if (history.length === 0) {
            dom.historyList.innerHTML = "";
            dom.emptyHistory.classList.add("empty-state--visible");
        } else {
            dom.emptyHistory.classList.remove("empty-state--visible");
            dom.historyList.innerHTML = history.map((f) => renderFileCard(f, false)).join("");
        }
    } catch (err) {
        console.error("[Auto-Shredder] Failed to fetch history:", err.message);
    }
}

async function refreshStats() {
    try {
        const stats = await api.getStats();
        dom.statActive.textContent = stats.active_count ?? 0;
        dom.statTrashed.textContent = stats.trashed_count ?? 0;
        dom.statReclaimed.textContent = formatBytes(stats.bytes_reclaimed ?? 0);
    } catch (err) {
        console.error("[Auto-Shredder] Failed to fetch stats:", err.message);
    }
}

async function refreshHealth() {
    try {
        const health = await api.getHealth();
        const isHealthy = health.status === "healthy";
        dom.healthIndicator.className = `health health--${isHealthy ? "healthy" : "degraded"}`;
        dom.healthLabel.textContent = isHealthy ? "All systems go" : "Degraded";
        dom.healthIndicator.title = `Watcher: ${health.watcher} · Scheduler: ${health.scheduler}`;
    } catch {
        dom.healthIndicator.className = "health health--degraded";
        dom.healthLabel.textContent = "Offline";
        dom.healthIndicator.title = "Cannot reach server";
    }
}

async function refreshSettings() {
    try {
        const settings = await api.getSettings();
        dom.notificationsCheckbox.checked = settings.notifications_enabled;
        renderWatchedDirs(settings.watched_directories);
    } catch (err) {
        console.error("[Auto-Shredder] Failed to fetch settings:", err.message);
    }
}

function renderWatchedDirs(dirs) {
    if (!dirs || dirs.length === 0) {
        dom.watchedDirs.innerHTML = '<span class="setting-row__desc">No directories configured</span>';
        return;
    }
    dom.watchedDirs.innerHTML = dirs
        .map((d) => `<div class="watched-dir"><span class="watched-dir__icon">📁</span>${escapeHtml(d)}</div>`)
        .join("");
}

// ═══════════════════════════════════════════
// Polling & Countdown
// ═══════════════════════════════════════════

async function pollAll() {
    const tasks = [refreshStats(), refreshHealth()];
    if (currentTab === "active") tasks.push(refreshActiveFiles());
    if (currentTab === "history") tasks.push(refreshHistory());
    await Promise.allSettled(tasks);
}

function tickCountdowns() {
    const timers = document.querySelectorAll(".file-card__timer[data-expires]");
    timers.forEach((el) => {
        const expiresAt = el.getAttribute("data-expires");
        if (!expiresAt) return;
        const countdown = formatCountdown(expiresAt);
        el.textContent = countdown.text;
        el.classList.toggle("file-card__timer--urgent", countdown.urgent);
        el.classList.toggle("file-card__timer--kept", countdown.kept);
    });
}

function startPolling() {
    pollAll();
    pollTimer = setInterval(pollAll, POLL_INTERVAL_MS);
    countdownTimer = setInterval(tickCountdowns, COUNTDOWN_TICK_MS);
}

// ═══════════════════════════════════════════
// Tab Navigation
// ═══════════════════════════════════════════

function initTabs() {
    const tabs = document.querySelectorAll(".tab");
    tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => {
            const target = tab.dataset.tab;
            if (target === currentTab) return;

            currentTab = target;

            // Update active tab
            tabs.forEach((t) => t.classList.remove("tab--active"));
            tab.classList.add("tab--active");

            // Slide indicator
            dom.tabSlider.style.transform = `translateX(${index * 100}%)`;

            // Show panel
            document.querySelectorAll(".panel").forEach((p) => p.classList.remove("panel--visible"));
            document.getElementById(`panel-${target}`).classList.add("panel--visible");

            // Fetch data for the new tab
            if (target === "active") refreshActiveFiles();
            if (target === "history") refreshHistory();
            if (target === "settings") refreshSettings();
        });
    });
}

// ═══════════════════════════════════════════
// File Actions
// ═══════════════════════════════════════════

function initActions() {
    // Delegate click events on file-list and history-list
    document.getElementById("content").addEventListener("click", async (e) => {
        const btn = e.target.closest("[data-action]");
        if (!btn) return;

        const action = btn.dataset.action;
        const fileId = parseInt(btn.dataset.id, 10);

        if (action === "keep") {
            await handleKeep(fileId);
        } else if (action === "snooze") {
            openSnoozeModal(fileId);
        } else if (action === "trash") {
            await handleTrash(fileId);
        }
    });
}

async function handleKeep(fileId) {
    try {
        await api.keepFile(fileId);
        showToast("File marked as Keep Forever 🔒", "success");
        await refreshActiveFiles();
        await refreshStats();
    } catch (err) {
        showToast(`Failed to keep file: ${err.message}`, "error");
    }
}

async function handleTrash(fileId) {
    try {
        await api.trashFile(fileId);
        showToast("File sent to Trash 🗑️", "success");
        await refreshActiveFiles();
        await refreshStats();
    } catch (err) {
        showToast(`Failed to trash file: ${err.message}`, "error");
    }
}

// ═══════════════════════════════════════════
// Snooze Modal
// ═══════════════════════════════════════════

function openSnoozeModal(fileId) {
    snoozeTargetId = fileId;
    snoozeHours = 24;

    // Find filename
    const file = activeFiles.find((f) => f.id === fileId);
    dom.snoozeFilename.textContent = file ? file.filename : `File #${fileId}`;

    // Reset selection
    dom.snoozeOptions.querySelectorAll(".modal__btn").forEach((btn) => {
        btn.classList.toggle("modal__btn--selected", parseInt(btn.dataset.hours, 10) === 24);
    });

    dom.snoozeModal.classList.add("modal-overlay--visible");
}

function closeSnoozeModal() {
    dom.snoozeModal.classList.remove("modal-overlay--visible");
    snoozeTargetId = null;
}

function initSnoozeModal() {
    dom.snoozeCancel.addEventListener("click", closeSnoozeModal);

    dom.snoozeOptions.addEventListener("click", (e) => {
        const btn = e.target.closest(".modal__btn");
        if (!btn) return;
        snoozeHours = parseInt(btn.dataset.hours, 10);
        dom.snoozeOptions.querySelectorAll(".modal__btn").forEach((b) => b.classList.remove("modal__btn--selected"));
        btn.classList.add("modal__btn--selected");
    });

    dom.snoozeConfirm.addEventListener("click", async () => {
        if (snoozeTargetId === null) return;
        try {
            await api.snoozeFile(snoozeTargetId, snoozeHours);
            showToast(`Snoozed for ${snoozeHours}h ⏸️`, "success");
            closeSnoozeModal();
            await refreshActiveFiles();
            await refreshStats();
        } catch (err) {
            showToast(`Failed to snooze: ${err.message}`, "error");
        }
    });

    // Close on overlay click
    dom.snoozeModal.addEventListener("click", (e) => {
        if (e.target === dom.snoozeModal) closeSnoozeModal();
    });
}

// ═══════════════════════════════════════════
// Settings
// ═══════════════════════════════════════════

function initSettings() {
    dom.notificationsCheckbox.addEventListener("change", async () => {
        try {
            await api.updateSettings({ notifications_enabled: dom.notificationsCheckbox.checked });
            showToast(
                dom.notificationsCheckbox.checked ? "Notifications enabled 🔔" : "Notifications disabled 🔕",
                "info",
            );
        } catch (err) {
            showToast(`Failed to update: ${err.message}`, "error");
            // Revert
            dom.notificationsCheckbox.checked = !dom.notificationsCheckbox.checked;
        }
    });

    dom.apiEndpoint.textContent = API_BASE;
}

// ═══════════════════════════════════════════
// Toast Notifications
// ═══════════════════════════════════════════

function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast--${type}`;
    toast.textContent = message;

    dom.toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = `toast-out ${300}ms var(--ease-out) forwards`;
        toast.addEventListener("animationend", () => toast.remove());
    }, 3500);
}

// ═══════════════════════════════════════════
// Keyboard Shortcuts
// ═══════════════════════════════════════════

function initKeyboard() {
    document.addEventListener("keydown", (e) => {
        // Escape closes modal
        if (e.key === "Escape") {
            closeSnoozeModal();
        }

        // 1/2/3 switches tabs (only when modal is closed)
        if (!dom.snoozeModal.classList.contains("modal-overlay--visible")) {
            if (e.key === "1") document.getElementById("tab-btn-active").click();
            if (e.key === "2") document.getElementById("tab-btn-history").click();
            if (e.key === "3") document.getElementById("tab-btn-settings").click();
        }
    });
}

// ═══════════════════════════════════════════
// Init
// ═══════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initActions();
    initSnoozeModal();
    initSettings();
    initKeyboard();
    startPolling();
});
