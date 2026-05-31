#!/usr/bin/env python3
"""手動備份腳本：python scripts/backup_db.py"""
import shutil, os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'project_manager.db')
BACKUP_DIR = os.path.join(os.path.dirname(__file__), '..', 'backups')

def backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        print("DB not found, skipping")
        return
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(BACKUP_DIR, f'project_manager_{ts}.db')
    shutil.copy2(DB_PATH, dest)

    # 只保留最近 48 個備份
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith('.db'))
    for old in files[:-48]:
        os.remove(os.path.join(BACKUP_DIR, old))

    print(f"Backup saved: {dest}")

if __name__ == '__main__':
    backup()
