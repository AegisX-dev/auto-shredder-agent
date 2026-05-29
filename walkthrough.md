# Manual Verification Walkthrough: Auto-Shredder Agent

This guide outlines step-by-step instructions to manually verify all aspects of the **Auto-Shredder Agent**—from the background daemon to the Web UI dashboard.

---

## 1. Verify Background Daemon Service Status

The agent runs as a rootless systemd user service. Check its status and stream live logs:

```bash
# Check if the service is active and running
systemctl --user status auto-shredder.service

# View and follow real-time logs of the running agent
journalctl --user -u auto-shredder.service -f
```

---

## 2. Open the Premium Glassmorphic Dashboard

1. Locate **Auto-Shredder Agent** in your Ubuntu Application Menu, or click the desktop shortcut.
2. Alternatively, open your default web browser and navigate directly to:
   👉 **[http://127.0.0.1:5050](http://127.0.0.1:5050)**
3. Verify that the **All systems go** green pulse health indicator is visible in the top-right header, showing that the watcher and scheduler are running.

---

## 3. Test File Detection & Hybrid Classification

Let's test classification of individual files by generating them in your watched `~/Downloads` folder. Keep the dashboard open on one side of the screen, or monitor your `journalctl` stream.

Run these commands to simulate single file downloads:

```bash
# Test 1: Installer classification (Should schedule for 24 Hours)
touch ~/Downloads/installer_test_pack.deb

# Test 2: Temporary file classification (Should schedule for 6 Hours)
echo "temporary log session" > ~/Downloads/debug_output.log

# Test 3: Document classification (Should schedule for "Keep Forever" / -1h)
touch ~/Downloads/quarterly_report_Q2.pdf
```

### Expected Results:
* **Desktop Notifications:** For each file, a desktop pop-up alert (via Ubuntu's `notify-send`) should appear with a customized emoji (e.g., 📦 for installer, 🗑️ for temp).
* **Dashboard Update:** The files will immediately slide into the **Active** files list with accurate countdown timers.
* **Database Persistence:** The statistics counters at the top of your dashboard will dynamically update.

---

## 4. Test Intelligent Notification Batching (Anti-Spam)

Simulate a bulk arrival of files (like extracting an archive or batch-saving images) to verify the 3-second anti-spam window:

```bash
# Rapidly create 5 files within 1 second
for i in {1..5}; do touch ~/Downloads/bulk_photo_export_${i}.png; done
```

### Expected Results:
* Instead of 5 individual alerts popping up and spamming your desktop, **exactly one** combined desktop notification will trigger:
  `🔔 Auto-Shredder: 5 new files detected in Downloads.`
* All 5 files will be categorized individually as `media` and will appear in your active files list, grouped by a matching `batch_id` in the database.

---

## 5. Test Manual Control Actions

Directly on the dashboard UI card for any active file:

1. **Keep Forever:** Click the **🔒 Keep** button on the `installer_test_pack.deb` card.
   * *Expected Result:* The countdown changes to `∞ Keep`, the card border glows green, and it is exempt from autodelete.
2. **Snooze:** Click the **⏸️ Snooze** button on `debug_output.log`.
   * *Expected Result:* A modal overlays. Select `48 Hours` and click **Snooze**. The countdown timer instantly updates to reflex the new delayed deadline.
3. **Trash Now:** Click the **🗑️ Trash** button on any card.
   * *Expected Result:* The file is immediately moved to your OS system Trash (`~/.local/share/Trash`). The card disappears from the **Active** tab, slides into the **History** tab (marked as *Trashed*), and the **Reclaimed** space counter in the stats bar increments by the file's size!

---

## 6. Verify Physical Safety Deletion

Check that the files are truly in the standard OS Trash and not permanently deleted:

```bash
# List files currently in your system Trash directory
ls -la ~/.local/share/Trash/files/
```
You can restore them at any point using the standard Ubuntu Files manager.
