# -*- coding: utf-8 -*-
"""备忘录：老师随手记的条目，存在 ``config\\memo.json``。

为什么单独一个文件、而不是塞进会话或工作区：备忘录是"跨课程、跨会话"的东西
（下节课要带什么、哪个班作业没交），换一个对话也得还在。

存盘一律**先写临时文件再 os.replace**：老师点"保存"的那一刻如果正好断电，
宁可丢掉这一次修改，也不能把原来的清单写坏——``os.replace`` 在同一分区上是原子的。

文件坏了、被别的程序占了、JSON 被手改烂了：一律当成空清单，绝不让接口 500。
"""

import json
import os
import secrets
import time

FILENAME = "memo.json"
MAX_TEXT = 2000                 # 单条上限：够写一整段话，又不至于把备忘录撑爆
MAX_ITEMS = 500                 # 再多多不过老师手打的量；防止文件无限膨胀


def _path(base_dir):
    return os.path.join(base_dir, "config", FILENAME)


def _new_id():
    """8 位十六进制 id：够短（前端好显示）、够随机（不会撞）。"""
    return secrets.token_hex(4)


def load(base_dir):
    """读清单。文件不在 / 坏了 / 不是列表 -> 空清单。永远不抛异常。"""
    try:
        with open(_path(base_dir), "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    items = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str):
            continue
        items.append({
            "id": str(entry.get("id") or _new_id()),
            "text": text[:MAX_TEXT],
            "at": str(entry.get("at") or ""),
            "done": bool(entry.get("done")),
        })
    return items


def save(base_dir, items):
    """原子写盘。返回错误字符串（空串 = 成功）。"""
    folder = os.path.dirname(_path(base_dir))
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
    except OSError as exc:
        return "建不了目录 %s：%s" % (folder, exc)
    target = _path(base_dir)
    temporary = target + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(items, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, target)
    except OSError as exc:
        try:
            if os.path.isfile(temporary):
                os.remove(temporary)
        except OSError:
            pass
        return "备忘录写不进去：%s" % exc
    return ""


def apply(base_dir, body):
    """处理一次增删改。返回 ``(ok, error, 最新清单)``。

    无论成功失败都回**整份**清单：前端拿到就能直接重画，不用自己维护状态。
    """
    items = load(base_dir)
    op = str(body.get("op") or "").strip().lower()
    if op == "add":
        text = str(body.get("text") or "").strip()
        if not text:
            return False, "内容不能为空", items
        items.insert(0, {"id": _new_id(), "text": text[:MAX_TEXT],
                         "at": time.strftime("%Y-%m-%d %H:%M"), "done": False})
        del items[MAX_ITEMS:]
    elif op == "update":
        target = _find(items, body.get("id"))
        if target is None:
            return False, "这条备忘录已经不在清单里了", items
        if body.get("text") is not None:
            text = str(body.get("text") or "").strip()
            if not text:
                return False, "内容不能为空", items
            target["text"] = text[:MAX_TEXT]
        if body.get("done") is not None:
            target["done"] = bool(body.get("done"))
    elif op == "delete":
        target = _find(items, body.get("id"))
        if target is None:
            return False, "这条备忘录已经不在清单里了", items
        items.remove(target)
    elif op == "clear_done":
        items = [item for item in items if not item["done"]]
    elif op == "clear_all":
        items = []
    else:
        return False, "不认识的操作：%s" % (op or "(空)"), items
    error = save(base_dir, items)
    if error:
        return False, error, load(base_dir)
    return True, "", items


def _find(items, item_id):
    wanted = str(item_id or "")
    if not wanted:
        return None
    for item in items:
        if item["id"] == wanted:
            return item
    return None
