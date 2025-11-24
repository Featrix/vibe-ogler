# 🎵 Viber - File System Change Monitor

A powerful CLI tool that monitors your source tree for file system events, tracks changes, and maintains shadow copies for before/after comparisons.

## Features

- **Real-time file monitoring** using macOS fsevents (via watchdog)
- **Shadow copy system** - automatically maintains "before" snapshots of files
- **Diff calculation** - computes lines added/deleted for each change
- **Change magnitude detection** - alerts on significant changes (file zeroing, large deletions)
- **SQLite database** - stores all change events with timestamps
- **Console output** - real-time feedback on file modifications
- **Smart filtering** - ignores binary files, hidden files, and common directories (.git, node_modules, etc.)

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Basic usage - monitor current directory
python viber.py .

# Monitor a specific directory
python viber.py /path/to/your/project

# Customize database and shadow directory locations
python viber.py /path/to/project --db mychanges.db --shadow-dir .my_shadows
```

## How It Works

### The "Before Write" Problem

**Short answer:** We can't intercept filesystem writes at the user-space level on macOS.

**The solution:** Viber maintains a **shadow copy** of each tracked file:

1. When a file is first detected, Viber creates a shadow copy
2. On modification events, Viber:
   - Compares the current file against the shadow copy (the "before" state)
   - Computes the diff and change statistics
   - Updates the shadow copy for the next change
   - Records everything to the database

This gives you the same result as a pre-write backup without requiring kernel-level hooks!

## Output Format

```
[2025-11-24T10:30:45.123456] src/main.py [LARGE DELETION: -1523 bytes]
  Size: 2048 → 525 bytes (-1523)
  Lines: +2 -45
```

- **Timestamp**: ISO format timestamp of when the change was detected
- **File path**: Relative to the watched directory
- **Magnitude alerts**:
  - `[FILE ZEROED OUT!]` - File was completely emptied
  - `[LARGE DELETION]` - File lost >50% of its size
  - `[LARGE ADDITION]` - File more than doubled in size
- **Size change**: Before → After with delta
- **Line changes**: Lines added (+) and deleted (-)

## Database Schema

All changes are stored in SQLite with the following schema:

```sql
CREATE TABLE file_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    file_path TEXT NOT NULL,
    event_type TEXT NOT NULL,          -- 'created', 'modified'
    size_before INTEGER,
    size_after INTEGER,
    size_change INTEGER,
    lines_added INTEGER,
    lines_deleted INTEGER,
    lines_changed INTEGER              -- total lines changed (added + deleted)
);
```

You can query this database to analyze your editing patterns:

```bash
sqlite3 .viber.db "SELECT * FROM file_changes ORDER BY timestamp DESC LIMIT 10"
```

## Excluded Files/Directories

Viber automatically ignores:
- Hidden files (starting with `.`)
- `.git` directories
- `__pycache__`, `node_modules`, `venv`, `.env`
- The shadow directory (`.viber_shadow` by default)
- The database file (`.viber.db` by default)
- Binary files (non-UTF8 content)

## Use Cases

- **Protect against accidental file deletion** - Viber maintains shadow copies
- **Track editing patterns** - See what files change most frequently
- **Detect problematic edits** - Get alerted when files are zeroed or heavily modified
- **Audit code changes** - Database provides complete history
- **Recovery tool** - Shadow copies can be used to recover lost content

## Example Session

```bash
$ python viber.py ~/my-project

🎵 Viber - File System Change Monitor
📁 Watching: /Users/me/my-project
💾 Database: .viber.db
📦 Shadow copies: .viber_shadow

============================================================
Monitoring started. Press Ctrl+C to stop.

Tracking new file: src/app.py

[2025-11-24T10:15:23.456789] src/app.py
  Size: 0 → 142 bytes (+142)
  Lines: +8 -0

[2025-11-24T10:16:45.123456] src/app.py [LARGE ADDITION: +523 bytes]
  Size: 142 → 665 bytes (+523)
  Lines: +15 -0

^C
👋 Stopping Viber...
✅ Viber stopped successfully
```

## Technical Notes

- Uses `watchdog` library for cross-platform file system monitoring
- Shadow copies use MD5 hashing to avoid path length issues
- Thread-safe database operations
- Graceful handling of binary files and permission errors
- Small delay on file creation to ensure file is fully written

## Future Enhancements

Potential additions:
- Web UI for browsing change history
- Export to JSON/CSV
- Git integration (compare against commits)
- Restore from shadow copies
- Pattern-based file inclusion/exclusion
- Statistics and reporting
