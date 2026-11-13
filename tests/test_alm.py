import json

import pytest

from alm.core import ALM, match_as_hint
from alm.llm import parse_json
from alm.store import Store

PAPER = ("We study trajectory planning for manipulators. Our projection step moves any "
         "waypoint inside a forbidden region to the nearest feasible point, then "
         "re-checks continuity of the path.")
GOOD = {"match": True, "claim": "投影修复可能解除穿越禁区的问题",
        "conditions": "修复后需检查连通和间距",
        "quote": "moves any waypoint inside a forbidden region to the nearest feasible point",
        "verify": "用之前失败的样例跑一遍"}


class FakeLLM:
    """按 system prompt 的开头分辨是哪一步，返回预设回答。"""

    def __init__(self, judge):
        self.judge, self.calls = judge, []

    def __call__(self, system, user):
        if system.startswith("Restate"):
            op, out = "abstract", "在约束区域之外生成一条连通路径"
        elif system.startswith("Extract"):
            op, out = "extract", '```json\n[{"mechanism": "把越界点投影回可行域", "conditions": "凸区域"}]\n```'
        else:
            op, out = "judge", self.judge if isinstance(self.judge, str) else json.dumps(self.judge)
        self.calls.append(op)
        return out, 0.001


def make(tmp_path, judge):
    llm = FakeLLM(judge)
    return ALM(Store(tmp_path / "t.db"), llm), llm


def add(alm, **kw):
    return alm.add_blocker("pcb", "自动布线", "候选路径会穿过禁布区", "生成模型直接出路径", **kw)


def test_new_source_reopens_old_blocker(tmp_path):
    alm, llm = make(tmp_path, GOOD)
    bid = add(alm)
    assert alm.store.blocker(bid)["abstract"]
    res = alm.ingest("robot paper", PAPER, "https://example.org/p")
    assert res["mechanisms"] == 1 and len(res["matches"]) == 1
    m = alm.store.matches()[0]
    assert m["blocker_id"] == bid and m["status"] == "new"
    hint = match_as_hint(m)
    assert hint["id"] == "alm-1" and not hint["confirmed"] and GOOD["quote"] in hint["evidence"]


def test_new_blocker_scans_existing_sources(tmp_path):
    alm, _ = make(tmp_path, GOOD)
    alm.ingest("robot paper", PAPER)
    bid = add(alm)
    assert [m["blocker_id"] for m in alm.store.matches()] == [bid]


@pytest.mark.parametrize("judge", [
    {"match": False},
    "not json at all",
    {**GOOD, "verify": ""},                       # 缺一项
    {**GOOD, "quote": "a sentence the paper never said"},  # 编造的依据
    {**GOOD, "match": "true"},                    # 不是布尔值
])
def test_gate_rejects(tmp_path, judge):
    alm, _ = make(tmp_path, judge)
    add(alm)
    assert alm.ingest("robot paper", PAPER)["matches"] == []
    assert alm.store.matches() == []


def test_quote_check_ignores_whitespace_and_case(tmp_path):
    alm, _ = make(tmp_path, {**GOOD, "quote": "Moves any waypoint\n inside a  forbidden region"})
    add(alm)
    assert len(alm.ingest("robot paper", PAPER)["matches"]) == 1


def test_only_open_method_blockers_are_judged(tmp_path):
    alm, llm = make(tmp_path, GOOD)
    add(alm, level="implementation")
    done = add(alm)
    alm.store.resolve(done)
    alm.ingest("robot paper", PAPER)
    assert "judge" not in llm.calls and llm.calls.count("abstract") == 1


def test_pair_is_judged_once(tmp_path):
    alm, llm = make(tmp_path, GOOD)
    bid = add(alm)
    sid = alm.ingest("robot paper", PAPER)["source_id"]
    assert alm.judge(bid, sid) is None and llm.calls.count("judge") == 1


def test_source_without_method_skips_judging(tmp_path):
    alm, llm = make(tmp_path, GOOD)
    add(alm)
    llm_extract = llm.__call__
    alm.chat_fn = lambda s, u: ("[]", 0.0) if s.startswith("Extract") else llm_extract(s, u)
    assert alm.ingest("news", "nothing methodological")["mechanisms"] == 0
    assert "judge" not in llm.calls


def test_feedback_and_stats(tmp_path):
    alm, _ = make(tmp_path, GOOD)
    add(alm)
    mid = alm.ingest("robot paper", PAPER)["matches"][0]
    alm.store.mark(mid, "useful")
    st = alm.store.stats()
    assert st["matches"]["useful"] == 1 and st["judgements"] == 1 and st["cost_usd"] == 0.003
    with pytest.raises(ValueError):
        alm.store.mark(mid, "great")
    with pytest.raises(KeyError):
        alm.store.mark(999, "tried")


def test_parse_json():
    assert parse_json('sure:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json("[1, 2]") == [1, 2]
    assert parse_json("no") is None and parse_json("{broken") is None
