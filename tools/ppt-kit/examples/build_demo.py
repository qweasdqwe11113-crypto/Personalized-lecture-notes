# -*- coding: utf-8 -*-
"""最小可跑示例：examples/deck.json → examples/课程知识库问答系统.pptx

    python examples/build_demo.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pptkit import build, check  # noqa: E402

CONTRACT = os.path.join(HERE, "deck.json")


def main() -> int:
    with open(CONTRACT, "r", encoding="utf-8") as f:
        deck = json.load(f)
    out = os.path.join(HERE, deck["meta"]["title"] + ".pptx")
    build(deck, out)

    print()
    errs = 0
    for lv, msg in check(out):
        print(f"  [{lv}] {msg}")
        errs += lv == "error"
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
