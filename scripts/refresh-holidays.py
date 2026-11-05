# -*- coding: utf-8 -*-
"""刷新随包离线节假日表（``webui\\holidays-bundled.json``）。

为什么要有这一步：老师那台机器可能根本连不出去，而"明年放不放假"是天天要看的。
每次**发版前**跑一次，就把当时能拿到的年份都烤进包里；之后即使整年断网，
日历上的放假日和调休也还是对的。

    .\\python\\python.exe scripts\\refresh-holidays.py            # 今年 +- 1 年
    .\\python\\python.exe scripts\\refresh-holidays.py 2026 2027  # 指定年份

联网拿不到（或那年还没公布）就保持原样，不会把已有的数据清掉。退出码 0 = 没出错。
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "webui"))

import holiday                                                   # noqa: E402

PATH = os.path.join(ROOT, "webui", "holidays-bundled.json")


def main(argv):
    years = []
    for raw in argv:
        try:
            years.append(int(raw))
        except ValueError:
            sys.stderr.write("不是年份：%s\n" % raw)
            return 2
    if not years:
        import time
        this_year = time.localtime().tm_year
        years = [this_year - 1, this_year, this_year + 1]

    try:
        with io.open(PATH, encoding="utf-8") as handle:
            bundled = json.load(handle)
    except (OSError, ValueError):
        bundled = {}
    if not isinstance(bundled, dict):
        bundled = {}
    before = sorted(bundled)

    changed = []
    for year in years:
        old = bundled.get(str(year)) or {}
        days = holiday._fetch(year)
        if not days:
            print("%d：还没公布 / 取不到，保持原样（现有 %d 天）" % (year, len(old)))
            continue
        if old != days:
            changed.append(year)
        bundled[str(year)] = days
        print("%d：%d 天" % (year, len(days)))

    ordered = {key: bundled[key] for key in sorted(bundled)}
    if not changed and before == sorted(ordered):
        print("没有变化，不写文件")
        return 0
    with io.open(PATH, "w", encoding="utf-8") as handle:
        json.dump(ordered, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print("已写入 %s（覆盖年份：%s，%d B）"
          % (os.path.relpath(PATH, ROOT), "、".join(sorted(ordered)),
             os.path.getsize(PATH)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
