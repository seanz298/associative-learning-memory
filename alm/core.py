"""三个动作：抽象卡点、提炼资料、判断一条资料是否改变一个卡点。

提醒的硬门槛由代码执行，不靠模型自觉：
  - claim / conditions / quote / verify 四项缺一即丢弃；
  - quote 必须真的出现在资料原文里，否则丢弃（防止编造依据）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

from .llm import ChatFn, chat, parse_json
from .store import Store

MAX_SOURCE_CHARS = 60_000  # 超出的部分不送给模型，入库时会提示

ABSTRACT_SYSTEM = (
    "Restate the stuck problem below as a domain-neutral problem structure in "
    "one or two sentences: what kind of object, what constraint, what currently "
    "violates it. Remove product names, field jargon and project-specific terms, "
    "so that someone from an unrelated field could recognise the same structure. "
    "Reply with the restatement only, in the language of the input."
)
EXTRACT_SYSTEM = (
    "Extract the transferable mechanisms from the material below. A mechanism is "
    "a method stated without its home field's vocabulary: what it takes in, what "
    "it changes, under which conditions it works or fails. Ignore results, "
    "benchmarks and motivation. Return at most 5. Reply with a JSON array only:\n"
    '[{"mechanism": "...", "conditions": "..."}]\n'
    "If the material contains no method, reply []."
)
JUDGE_SYSTEM = (
    "You decide whether a piece of material changes the condition a problem is "
    "stuck on. Sharing a topic or keywords is NOT enough: answer match=true only "
    "if a mechanism in the material would remove or weaken the specific blocking "
    "condition, or make a previously failed approach viable. Most pairs do not "
    "match. Be strict.\n"
    'If not, reply exactly {"match": false}.\n'
    "If so, reply with one JSON object:\n"
    '{"match": true,\n'
    ' "claim": "method A may resolve blocker C under condition B",\n'
    ' "conditions": "when it applies, and what must still be checked after transfer",\n'
    ' "quote": "a passage copied VERBATIM from the material (under 200 characters) '
    'that supports the claim",\n'
    ' "verify": "the cheapest test, doable within one day, reusing the failed cases"}\n'
    "Write claim, conditions and verify in the language of the blocker."
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _blocker_text(b: sqlite3.Row | Dict[str, Any]) -> str:
    return (f"Goal: {b['goal']}\nBlocked on: {b['blocker']}\n"
            f"Already tried / known failure conditions: {b['tried'] or '(none recorded)'}\n"
            f"Domain-neutral structure: {b['abstract'] or '(not available)'}")


class ALM:
    def __init__(self, store: Store, chat_fn: ChatFn = chat):
        self.store, self.chat_fn = store, chat_fn

    def _ask(self, op: str, system: str, user: str) -> str:
        text, cost = self.chat_fn(system, user)
        self.store.log_call(op, cost)
        return text

    # -- 录入卡点（新目标 -> 已有资料）-------------------------------------
    def add_blocker(self, project: str, goal: str, blocker: str, tried: str = "",
                    evidence: str = "", level: str = "method", scan: bool = True) -> int:
        abstract = ""
        if level == "method":
            abstract = self._ask("abstract", ABSTRACT_SYSTEM,
                                 f"Goal: {goal}\nBlocked on: {blocker}\nTried: {tried}").strip()
        bid = self.store.add_blocker(project, goal, blocker, tried, evidence, level, abstract)
        if scan and level == "method":
            for s in self.store.sources():
                self.judge(bid, s["id"])
        return bid

    # -- 录入资料（新资料 -> 旧卡点）---------------------------------------
    def ingest(self, title: str, text: str, ref: str = "") -> Dict[str, Any]:
        raw = self._ask("extract", EXTRACT_SYSTEM, f"# {title}\n\n{text[:MAX_SOURCE_CHARS]}")
        data = parse_json(raw)
        mechanisms = [m for m in data if isinstance(m, dict) and m.get("mechanism")] \
            if isinstance(data, list) else []
        sid = self.store.add_source(title, text, ref, mechanisms)
        hits: List[int] = []
        if mechanisms:  # 提炼不出方法的资料不值得逐条对照
            for b in self.store.blockers(status="open", level="method"):
                mid = self.judge(b["id"], sid)
                if mid:
                    hits.append(mid)
        return {"source_id": sid, "mechanisms": len(mechanisms), "matches": hits,
                "truncated": len(text) > MAX_SOURCE_CHARS}

    # -- 判断 ------------------------------------------------------------
    def judge(self, blocker_id: int, source_id: int) -> Optional[int]:
        """返回新提醒的 id；不相关或没过门槛返回 None。同一对只判一次。"""
        if self.store.judged(blocker_id, source_id):
            return None
        b, s = self.store.blocker(blocker_id), self.store.source(source_id)
        body = s["text"][:MAX_SOURCE_CHARS]
        user = (f"## Blocker\n{_blocker_text(b)}\n\n"
                f"## Mechanisms extracted from the material\n{s['mechanisms']}\n\n"
                f"## Material: {s['title']}\n{body}")
        data = parse_json(self._ask("judge", JUDGE_SYSTEM, user))
        if not isinstance(data, dict) or data.get("match") is not True:
            return None
        fields = {k: str(data.get(k) or "").strip()
                  for k in ("claim", "conditions", "quote", "verify")}
        if not all(fields.values()):
            return None
        if _norm(fields["quote"]) not in _norm(body):
            return None
        return self.store.add_match(blocker_id, source_id, **fields)


def match_as_hint(m: sqlite3.Row) -> Dict[str, Any]:
    """一条提醒 -> Orkestr 的 Hint 字段（见 Orkestr 的 orchestrator/memory.py）。"""
    return {
        "id": f"alm-{m['id']}", "kind": "candidate", "claim": m["claim"],
        "conditions": m["conditions"],
        "evidence": f"{m['title']} {m['ref']}".strip() + f" — \"{m['quote']}\"",
        "verify": m["verify"], "confirmed": False,
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
