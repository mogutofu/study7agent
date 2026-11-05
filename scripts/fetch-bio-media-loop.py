# -*- coding: utf-8 -*-
"""自动补插图（耐心版）：一轮轮跑批量下载，限流了就多等一会儿，直到再也补不出来为止。

- 每轮之间歇 5 分钟（限流窗口一般几分钟就过去）
- 连着 6 轮没有新增才收工（最多 60 轮，够跑 5 小时）
- 候选池落盘缓存，只增不减；每轮结果追加到 fetch-bio-progress.log，随时能看
"""
import io
import json
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "python", "python.exe")
BULK = os.path.join(ROOT, "scripts", "fetch-bio-media-bulk.py")
MANIFEST = os.path.join(ROOT, "webui", "ui", "vendor", "bio", "manifest.json")
LOG = os.path.join(ROOT, "fetch-bio-progress.log")


def count():
    try:
        with io.open(MANIFEST, encoding="utf-8") as handle:
            return len(json.load(handle))
    except Exception:
        return 0


def say(text):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main():
    idle = 0
    total = count()
    say("起始 %d 个过程有插图" % total)
    for round_no in range(1, 61):
        subprocess.call([PY, BULK], cwd=ROOT, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
        after = count()
        idle = idle + 1 if after == total else 0
        say("第 %d 轮：%d -> %d（连续无新增 %d 轮）" % (round_no, total, after, idle))
        total = after
        if idle >= 6:
            say("连着 6 轮没有新增，收工")
            break
        time.sleep(300)
    say("结束，共 %d 个过程有插图" % total)


if __name__ == "__main__":
    main()
