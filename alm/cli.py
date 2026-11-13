"""命令行。数据库位置：环境变量 ALM_DB，默认 ./alm.db。"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .core import ALM, dumps, match_as_hint
from .store import LEVELS, MATCH_STATES, Store


def _show_match(m) -> str:
    return (f"[{m['id']}] ({m['status']}) 卡点 #{m['blocker_id']} {m['project']}: {m['blocker']}\n"
            f"  主张: {m['claim']}\n  条件: {m['conditions']}\n"
            f"  依据: {m['title']} {m['ref']}\n        \"{m['quote']}\"\n"
            f"  验证: {m['verify']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="alm", description="让新资料回头去找旧卡点")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add-blocker", help="录入一个卡点")
    a.add_argument("--project", required=True)
    a.add_argument("--goal", required=True, help="想实现什么")
    a.add_argument("--blocker", required=True, help="卡在哪个条件上")
    a.add_argument("--tried", default="", help="试过什么、在什么条件下失败")
    a.add_argument("--evidence", default="", help="失败的证据")
    a.add_argument("--level", default="method", choices=LEVELS)
    a.add_argument("--no-scan", action="store_true", help="不对照已有资料")

    i = sub.add_parser("ingest", help="录入一篇资料并对照所有未解决的方案层卡点")
    i.add_argument("file", help="文本文件路径，- 表示标准输入")
    i.add_argument("--title", default="")
    i.add_argument("--ref", default="", help="链接")

    sub.add_parser("blockers", help="列出未解决的卡点")
    m = sub.add_parser("matches", help="列出提醒")
    m.add_argument("--status", choices=MATCH_STATES)
    m.add_argument("--json", action="store_true", help="按 Orkestr Hint 的字段输出")
    k = sub.add_parser("mark", help="记录一条提醒的结果")
    k.add_argument("id", type=int)
    k.add_argument("status", choices=MATCH_STATES)
    r = sub.add_parser("resolve", help="卡点已解决")
    r.add_argument("id", type=int)
    sub.add_parser("stats", help="统计：提醒数、采纳数、花费")

    args = p.parse_args(argv)
    try:
        return _run(args)
    except (RuntimeError, KeyError, ValueError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def _run(args) -> int:
    store = Store(os.environ.get("ALM_DB", "alm.db"))
    alm = ALM(store)

    if args.cmd == "add-blocker":
        bid = alm.add_blocker(args.project, args.goal, args.blocker, args.tried,
                              args.evidence, args.level, scan=not args.no_scan)
        print(f"卡点 #{bid} 已录入")
        if store.blocker(bid)["abstract"]:
            print(f"  问题结构: {store.blocker(bid)['abstract']}")
        for row in store.matches(blocker_id=bid):
            print(_show_match(row))
    elif args.cmd == "ingest":
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text("utf-8")
        title = args.title or (Path(args.file).stem if args.file != "-" else "untitled")
        res = alm.ingest(title, text, args.ref or (args.file if args.file != "-" else ""))
        print(f"资料 #{res['source_id']} 已录入，提炼出 {res['mechanisms']} 个机制，"
              f"产生 {len(res['matches'])} 条提醒")
        if res["truncated"]:
            print("  注意：资料过长，只有开头部分参与了对照")
        ids = set(res["matches"])
        for row in store.matches():
            if row["id"] in ids:
                print(_show_match(row))
    elif args.cmd == "blockers":
        for b in store.blockers():
            print(f"#{b['id']} [{b['level']}] {b['project']}: {b['blocker']}")
    elif args.cmd == "matches":
        rows = store.matches(status=args.status)
        if args.json:
            print(dumps([match_as_hint(x) for x in rows]))
        else:
            print("\n".join(_show_match(x) for x in rows) or "（没有提醒）")
    elif args.cmd == "mark":
        store.mark(args.id, args.status)
    elif args.cmd == "resolve":
        store.resolve(args.id)
    elif args.cmd == "stats":
        print(dumps(store.stats()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
