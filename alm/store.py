"""SQLite 存储。四张表：卡点、资料、提醒、调用日志。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS blockers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL,          -- 属于哪个项目
  goal TEXT NOT NULL,             -- 想实现什么
  blocker TEXT NOT NULL,          -- 卡在哪个条件上（一句话）
  tried TEXT DEFAULT '',          -- 试过什么、在什么条件下失败
  evidence TEXT DEFAULT '',       -- 失败的证据：测试输出、任务 id、链接
  level TEXT DEFAULT 'method',    -- method=方案层 | implementation=实现层
  abstract TEXT DEFAULT '',       -- 去掉领域词汇后的问题结构（模型生成）
  status TEXT DEFAULT 'open',     -- open | resolved
  created REAL);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  ref TEXT DEFAULT '',            -- 链接或文件路径
  text TEXT NOT NULL,
  mechanisms TEXT DEFAULT '[]',   -- JSON：从资料里提炼出的领域无关机制
  created REAL);
CREATE TABLE IF NOT EXISTS matches(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  blocker_id INTEGER NOT NULL REFERENCES blockers(id),
  source_id INTEGER NOT NULL REFERENCES sources(id),
  claim TEXT NOT NULL,            -- "方法 A 可能在条件 B 下解除障碍 C"
  conditions TEXT NOT NULL,       -- 适用条件，以及迁移后还要检查什么
  quote TEXT NOT NULL,            -- 资料原文中的依据（已核对确实出现在原文里）
  verify TEXT NOT NULL,           -- 一天内能做完的验证
  status TEXT DEFAULT 'new',      -- new | tried | useful | rejected
  created REAL,
  UNIQUE(blocker_id, source_id));
CREATE TABLE IF NOT EXISTS calls(
  n INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, op TEXT, cost_usd REAL);
"""

LEVELS = ("method", "implementation")
MATCH_STATES = ("new", "tried", "useful", "rejected")


class Store:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -- 卡点 ------------------------------------------------------------
    def add_blocker(self, project: str, goal: str, blocker: str, tried: str = "",
                    evidence: str = "", level: str = "method", abstract: str = "") -> int:
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        cur = self.db.execute(
            "INSERT INTO blockers(project,goal,blocker,tried,evidence,level,abstract,created)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (project, goal, blocker, tried, evidence, level, abstract, time.time()))
        return cur.lastrowid

    def blocker(self, bid: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM blockers WHERE id=?", (bid,)).fetchone()
        if row is None:
            raise KeyError(f"no such blocker: {bid}")
        return row

    def blockers(self, status: Optional[str] = "open",
                 level: Optional[str] = None) -> List[sqlite3.Row]:
        sql, args = "SELECT * FROM blockers WHERE 1=1", []
        if status:
            sql, args = sql + " AND status=?", args + [status]
        if level:
            sql, args = sql + " AND level=?", args + [level]
        return self.db.execute(sql + " ORDER BY id", args).fetchall()

    def resolve(self, bid: int) -> None:
        self.blocker(bid)
        self.db.execute("UPDATE blockers SET status='resolved' WHERE id=?", (bid,))

    # -- 资料 ------------------------------------------------------------
    def add_source(self, title: str, text: str, ref: str = "",
                   mechanisms: Optional[List[Dict[str, Any]]] = None) -> int:
        cur = self.db.execute(
            "INSERT INTO sources(title,ref,text,mechanisms,created) VALUES(?,?,?,?,?)",
            (title, ref, text, json.dumps(mechanisms or [], ensure_ascii=False), time.time()))
        return cur.lastrowid

    def source(self, sid: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError(f"no such source: {sid}")
        return row

    def sources(self) -> List[sqlite3.Row]:
        return self.db.execute("SELECT * FROM sources ORDER BY id").fetchall()

    # -- 提醒 ------------------------------------------------------------
    def judged(self, bid: int, sid: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM matches WHERE blocker_id=? AND source_id=?", (bid, sid)
        ).fetchone() is not None

    def add_match(self, bid: int, sid: int, claim: str, conditions: str,
                  quote: str, verify: str) -> int:
        cur = self.db.execute(
            "INSERT INTO matches(blocker_id,source_id,claim,conditions,quote,verify,created)"
            " VALUES(?,?,?,?,?,?,?)",
            (bid, sid, claim, conditions, quote, verify, time.time()))
        return cur.lastrowid

    def matches(self, status: Optional[str] = None,
                blocker_id: Optional[int] = None) -> List[sqlite3.Row]:
        sql = ("SELECT m.*, b.project, b.blocker, s.title, s.ref FROM matches m"
               " JOIN blockers b ON b.id=m.blocker_id JOIN sources s ON s.id=m.source_id"
               " WHERE 1=1")
        args: list = []
        if status:
            sql, args = sql + " AND m.status=?", args + [status]
        if blocker_id:
            sql, args = sql + " AND m.blocker_id=?", args + [blocker_id]
        return self.db.execute(sql + " ORDER BY m.id", args).fetchall()

    def mark(self, mid: int, status: str) -> None:
        if status not in MATCH_STATES:
            raise ValueError(f"status must be one of {MATCH_STATES}")
        if self.db.execute("UPDATE matches SET status=? WHERE id=?", (status, mid)).rowcount == 0:
            raise KeyError(f"no such match: {mid}")

    # -- 日志 ------------------------------------------------------------
    def log_call(self, op: str, cost_usd: float) -> None:
        self.db.execute("INSERT INTO calls(ts,op,cost_usd) VALUES(?,?,?)",
                        (time.time(), op, cost_usd))

    def stats(self) -> Dict[str, Any]:
        one = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        by = {r["status"]: r["c"] for r in self.db.execute(
            "SELECT status, COUNT(*) c FROM matches GROUP BY status")}
        return {
            "open_blockers": one("SELECT COUNT(*) FROM blockers WHERE status='open'"),
            "sources": one("SELECT COUNT(*) FROM sources"),
            "judgements": one("SELECT COUNT(*) FROM calls WHERE op='judge'"),
            "matches": {s: by.get(s, 0) for s in MATCH_STATES},
            "cost_usd": round(one("SELECT COALESCE(SUM(cost_usd),0) FROM calls"), 4),
        }
