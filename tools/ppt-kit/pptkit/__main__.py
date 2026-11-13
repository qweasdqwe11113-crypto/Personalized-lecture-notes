# -*- coding: utf-8 -*-
"""命令行入口：python -m pptkit <check|preview> ...

用 `python -m pptkit.check` 也能跑，但会触发 runpy 的重复导入警告，
所以统一走这里。
"""
import sys

USAGE = """用法:
  python -m pptkit check   <file.pptx>          几何自检
  python -m pptkit preview <file.pptx> [-o dir] 渲染逐页 png
"""


def main() -> int:
    if len(sys.argv) < 3:
        print(USAGE)
        return 2
    cmd, target = sys.argv[1], sys.argv[2]
    if cmd == "check":
        from .check import report
        return report(target)
    if cmd == "preview":
        from .preview import render
        out = "shots"
        if "-o" in sys.argv:
            out = sys.argv[sys.argv.index("-o") + 1]
        render(target, out)
        return 0
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
