#!/usr/bin/env python3
"""
Viber - A file system change monitoring tool that tracks modifications, 
computes diffs, and maintains a shadow copy for backup purposes.
"""

import os
import sys
import time
import sqlite3
import difflib
import hashlib
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple
import click
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent


class ShadowCopyManager:
    """Manages shadow copies of files for before/after comparison."""
    
    def __init__(self, shadow_dir: Path):
        self.shadow_dir = shadow_dir
        self.shadow_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_shadow_path(self, file_path: str) -> Path:
        """Convert a file path to its shadow copy path using hash."""
        # Use hash to avoid path length issues and special characters
        path_hash = hashlib.md5(file_path.encode()).hexdigest()
        return self.shadow_dir / path_hash
    
    def _get_metadata_path(self, file_path: str) -> Path:
        """Get the metadata file path for a shadow copy."""
        shadow_path = self._get_shadow_path(file_path)
        return shadow_path.with_suffix('.meta')
    
    def has_shadow(self, file_path: str) -> bool:
        """Check if a shadow copy exists for the file."""
        shadow_path = self._get_shadow_path(file_path)
        return shadow_path.exists()
    
    def create_shadow(self, file_path: str) -> bool:
        """Create a shadow copy of the file."""
        try:
            if not os.path.exists(file_path):
                return False
            
            shadow_path = self._get_shadow_path(file_path)
            metadata_path = self._get_metadata_path(file_path)
            
            # Copy the file
            shutil.copy2(file_path, shadow_path)
            
            # Store metadata (original path and size)
            with open(metadata_path, 'w') as f:
                f.write(f"{file_path}\n")
                f.write(f"{os.path.getsize(file_path)}\n")
            
            return True
        except Exception as e:
            print(f"Error creating shadow copy: {e}")
            return False
    
    def get_shadow_content(self, file_path: str) -> Optional[str]:
        """Get the content of the shadow copy."""
        try:
            shadow_path = self._get_shadow_path(file_path)
            if not shadow_path.exists():
                return None
            
            with open(shadow_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            print(f"Error reading shadow copy: {e}")
            return None
    
    def get_shadow_size(self, file_path: str) -> Optional[int]:
        """Get the size of the shadow copy."""
        try:
            shadow_path = self._get_shadow_path(file_path)
            if not shadow_path.exists():
                return None
            return os.path.getsize(shadow_path)
        except Exception:
            return None
    
    def update_shadow(self, file_path: str) -> bool:
        """Update the shadow copy with current file content."""
        return self.create_shadow(file_path)


class ChangeDatabase:
    """Manages SQLite database for storing file change events."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._create_tables()
    
    def _create_tables(self):
        """Create database tables if they don't exist."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                file_path TEXT NOT NULL,
                event_type TEXT NOT NULL,
                size_before INTEGER,
                size_after INTEGER,
                size_change INTEGER,
                lines_added INTEGER,
                lines_deleted INTEGER,
                lines_changed INTEGER
            )
        """)
        self.conn.commit()
    
    def record_change(self, file_path: str, event_type: str,
                     size_before: Optional[int], size_after: Optional[int],
                     lines_added: int, lines_deleted: int):
        """Record a file change event."""
        timestamp = datetime.now().isoformat()
        size_change = None
        if size_before is not None and size_after is not None:
            size_change = size_after - size_before
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO file_changes 
            (timestamp, file_path, event_type, size_before, size_after, 
             size_change, lines_added, lines_deleted, lines_changed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, file_path, event_type, size_before, size_after,
              size_change, lines_added, lines_deleted, lines_added + lines_deleted))
        self.conn.commit()
        
        return timestamp
    
    def close(self):
        """Close database connection."""
        self.conn.close()


class FileChangeHandler(FileSystemEventHandler):
    """Handles file system events and processes changes."""
    
    def __init__(self, watch_path: Path, shadow_manager: ShadowCopyManager,
                 database: ChangeDatabase):
        self.watch_path = watch_path.resolve()
        self.shadow_manager = shadow_manager
        self.database = database
        self.processing = set()  # Avoid duplicate processing
    
    def _should_process(self, file_path: str) -> bool:
        """Check if we should process this file."""
        # Skip directories
        if os.path.isdir(file_path):
            return False
        
        # Skip hidden files and common exclusions
        path_parts = Path(file_path).parts
        excluded = {'.git', '__pycache__', 'node_modules', '.viber_shadow', 
                   '.viber.db', 'venv', '.env'}
        if any(part in excluded or part.startswith('.') for part in path_parts):
            return False
        
        # Only process text-like files (you can expand this)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                f.read(1024)  # Try to read a bit
            return True
        except (UnicodeDecodeError, PermissionError, FileNotFoundError):
            return False
    
    def _compute_diff(self, old_content: str, new_content: str) -> Tuple[int, int]:
        """Compute lines added and deleted between two versions."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        
        diff = difflib.unified_diff(old_lines, new_lines, lineterm='')
        
        added = 0
        deleted = 0
        for line in diff:
            if line.startswith('+') and not line.startswith('+++'):
                added += 1
            elif line.startswith('-') and not line.startswith('---'):
                deleted += 1
        
        return added, deleted
    
    def _process_modification(self, file_path: str):
        """Process a file modification event."""
        if file_path in self.processing:
            return
        
        try:
            self.processing.add(file_path)
            
            if not self._should_process(file_path):
                return
            
            # Get current file info
            if not os.path.exists(file_path):
                return
            
            size_after = os.path.getsize(file_path)
            
            # Read current content
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    new_content = f.read()
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
                return
            
            # Check if we have a shadow copy
            if self.shadow_manager.has_shadow(file_path):
                # Get shadow content and size
                old_content = self.shadow_manager.get_shadow_content(file_path)
                size_before = self.shadow_manager.get_shadow_size(file_path)
                
                if old_content is not None:
                    # Compute diff
                    lines_added, lines_deleted = self._compute_diff(old_content, new_content)
                    
                    # Detect significant changes
                    magnitude = ""
                    if size_after == 0 and size_before > 0:
                        magnitude = " [FILE ZEROED OUT!]"
                    elif size_before and size_after < size_before * 0.5:
                        magnitude = f" [LARGE DELETION: -{size_before - size_after} bytes]"
                    elif size_before and size_after > size_before * 2:
                        magnitude = f" [LARGE ADDITION: +{size_after - size_before} bytes]"
                    
                    # Record to database
                    timestamp = self.database.record_change(
                        file_path, 'modified', size_before, size_after,
                        lines_added, lines_deleted
                    )
                    
                    # Print to console
                    rel_path = os.path.relpath(file_path, self.watch_path)
                    print(f"\n[{timestamp}] {rel_path}{magnitude}")
                    print(f"  Size: {size_before} → {size_after} bytes "
                          f"({size_after - size_before:+d})")
                    print(f"  Lines: +{lines_added} -{lines_deleted}")
            else:
                # First time seeing this file, just create shadow
                print(f"Tracking new file: {os.path.relpath(file_path, self.watch_path)}")
            
            # Update shadow copy for next comparison
            self.shadow_manager.update_shadow(file_path)
            
        finally:
            self.processing.discard(file_path)
    
    def _process_creation(self, file_path: str):
        """Process a file creation event."""
        if not self._should_process(file_path):
            return
        
        if os.path.exists(file_path):
            size_after = os.path.getsize(file_path)
            timestamp = self.database.record_change(
                file_path, 'created', None, size_after, 0, 0
            )
            
            rel_path = os.path.relpath(file_path, self.watch_path)
            print(f"\n[{timestamp}] {rel_path} [CREATED]")
            print(f"  Size: {size_after} bytes")
            
            # Create initial shadow
            self.shadow_manager.create_shadow(file_path)
    
    def on_modified(self, event: FileSystemEvent):
        """Handle file modification events."""
        if not event.is_directory:
            self._process_modification(event.src_path)
    
    def on_created(self, event: FileSystemEvent):
        """Handle file creation events."""
        if not event.is_directory:
            # Small delay to let file be fully written
            time.sleep(0.1)
            self._process_creation(event.src_path)


@click.command()
@click.argument('watch_path', type=click.Path(exists=True))
@click.option('--db', default='.viber.db', help='Database file path')
@click.option('--shadow-dir', default='.viber_shadow', help='Shadow copy directory')
def main(watch_path: str, db: str, shadow_dir: str):
    """
    Viber - Monitor file system changes and track modifications.
    
    WATCH_PATH: The directory to monitor for changes
    """
    watch_path = Path(watch_path).resolve()
    
    print(f"🎵 Viber - File System Change Monitor")
    print(f"📁 Watching: {watch_path}")
    print(f"💾 Database: {db}")
    print(f"📦 Shadow copies: {shadow_dir}")
    print(f"\n{'='*60}")
    print("Monitoring started. Press Ctrl+C to stop.\n")
    
    # Initialize components
    shadow_manager = ShadowCopyManager(Path(shadow_dir))
    database = ChangeDatabase(Path(db))
    event_handler = FileChangeHandler(watch_path, shadow_manager, database)
    
    # Set up observer
    observer = Observer()
    observer.schedule(event_handler, str(watch_path), recursive=True)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n👋 Stopping Viber...")
        observer.stop()
    
    observer.join()
    database.close()
    print("✅ Viber stopped successfully")


if __name__ == '__main__':
    main()

