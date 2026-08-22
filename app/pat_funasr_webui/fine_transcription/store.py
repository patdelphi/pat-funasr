# -*- coding: utf-8 -*-
"""
精细转录 SQLite 存储模块
WAL 模式 + 事务操作，存储任务元数据、转写片段、LLM 产物
"""
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# 数据库默认路径
_DB_PATH = Path(__file__).parent / "fine_transcription.db"


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    """获取数据库连接，设置 WAL 模式"""
    path = db_path or str(_DB_PATH)
    conn = sqlite3.connect(path)
    # WAL 模式减少 IO 开销
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[str] = None):
    """初始化数据库表结构"""
    conn = _get_conn(db_path)
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_id TEXT NOT NULL,
            scene_name TEXT NOT NULL,
            audio_file TEXT NOT NULL,
            audio_duration REAL DEFAULT 0,
            asr_model TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS transcript_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            seg_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            speaker TEXT DEFAULT '',
            start_time REAL DEFAULT 0,
            end_time REAL DEFAULT 0,
            words_json TEXT DEFAULT '',
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS llm_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            output_type TEXT NOT NULL,
            content TEXT NOT NULL,
            model TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        """)
        conn.commit()
    finally:
        conn.close()


def create_task(scene_id: str, scene_name: str, audio_file: str,
                audio_duration: float = 0, asr_model: str = "",
                db_path: Optional[str] = None) -> int:
    """创建转录任务，返回 task_id"""
    conn = _get_conn(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """INSERT INTO tasks (scene_id, scene_name, audio_file, audio_duration, asr_model, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (scene_id, scene_name, audio_file, audio_duration, asr_model)
            )
            return cursor.lastrowid
    finally:
        conn.close()


def update_task_status(task_id: int, status: str, db_path: Optional[str] = None):
    """更新任务状态"""
    conn = _get_conn(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, datetime.now().isoformat(), task_id)
            )
    finally:
        conn.close()


def save_segments(task_id: int, segments: list, db_path: Optional[str] = None):
    """保存 ASR 转写片段"""
    conn = _get_conn(db_path)
    try:
        with conn:
            # 先删除旧片段
            conn.execute("DELETE FROM transcript_segments WHERE task_id=?", (task_id,))
            # 批量插入
            for i, seg in enumerate(segments):
                conn.execute(
                    """INSERT INTO transcript_segments
                       (task_id, seg_index, text, speaker, start_time, end_time, words_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task_id, i,
                        seg.get("text", ""),
                        seg.get("speaker", ""),
                        seg.get("start", 0),
                        seg.get("end", 0),
                        json.dumps(seg.get("words", []), ensure_ascii=False),
                    )
                )
    finally:
        conn.close()


def save_llm_output(task_id: int, output_type: str, content: str,
                    model: str = "", db_path: Optional[str] = None):
    """保存 LLM 产出（transcript/summary/mindmap）"""
    conn = _get_conn(db_path)
    try:
        with conn:
            conn.execute(
                """INSERT INTO llm_outputs (task_id, output_type, content, model)
                   VALUES (?, ?, ?, ?)""",
                (task_id, output_type, content, model)
            )
    finally:
        conn.close()


def get_task(task_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """获取任务详情"""
    conn = _get_conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_segments(task_id: int, db_path: Optional[str] = None) -> list:
    """获取转写片段"""
    conn = _get_conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM transcript_segments WHERE task_id=? ORDER BY seg_index",
            (task_id,)
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                "text": r["text"],
                "speaker": r["speaker"],
                "start": r["start_time"],
                "end": r["end_time"],
                "words": json.loads(r["words_json"]) if r["words_json"] else [],
            })
        return result
    finally:
        conn.close()


def get_llm_outputs(task_id: int, db_path: Optional[str] = None) -> dict:
    """获取 LLM 产出"""
    conn = _get_conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM llm_outputs WHERE task_id=? ORDER BY id",
            (task_id,)
        ).fetchall()
        result = {}
        for r in rows:
            result[r["output_type"]] = {
                "content": r["content"],
                "model": r["model"],
            }
        return result
    finally:
        conn.close()


def list_tasks(limit: int = 20, db_path: Optional[str] = None) -> list:
    """列出最近的任务"""
    conn = _get_conn(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
