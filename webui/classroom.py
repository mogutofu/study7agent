# -*- coding: utf-8 -*-
"""课堂互动与学习闭环能力。

  - pick_students   抽学号（默认 1-50，不重复）/ 随机分组，结果投大屏
  - class_timer     课堂倒计时（小组讨论、限时练习），大屏显示
  - show_board      全屏投屏：板书要点、题目、答案，字号按教室后排可读设计
  - mistake_book    错题本：积累错题 → 按知识点生成重练卷
  - score_stats     成绩统计：及格率、优秀率、分数段等用代码精算，不靠心算

工具通过 tools.emit 往界面推事件（大屏/计时器），通过 tools.session 保存会话状态（抽过哪些学号）。
只使用 Python 3.8 标准库。
"""

import csv
import io
import json
import os
import random
import re
import time

import office
import core as core_module

NUMBER = re.compile(r"^[-+]?\d+(\.\d+)?$")
# 成绩表里最常见的是「学号,姓名,分数」：学号那一列整列都是数字，
# 如果只按「哪一列数字最多」挑分数列，就会把学号当成分数（而且并列时还挑了最左边那列）。
# 所以自动选列时先看列名：排除学号/序号，优先「分数/成绩」。
ID_HEADER_WORDS = ("学号", "学籍号", "考号", "考生号", "座号", "编号", "序号",
                   "准考证号", "id", "no", "number")
NAME_HEADER_WORDS = ("姓名", "名字", "学生", "同学", "name")
SCORE_HEADER_WORDS = ("分数", "成绩", "得分", "总分", "总评", "分值", "score")
DEFAULT_CLASS_SIZE = 50
MAX_CLASS_SIZE = 200
MISTAKE_FILE = "错题本.json"


def emit(tools, event):
    """往界面推事件；没有事件出口时（例如单元测试）静默忽略。"""
    sender = getattr(tools, "emit", None)
    if callable(sender):
        sender(event)


def decode_text(data):
    """统一用 core 的解码规则（成绩表可能是 GBK 的 CSV，也可能是 UTF-16 的导出文件）。"""
    return core_module.decode_text(data)


# --------------------------------------------------------------------------
# 抽学号 / 分组（只按学号，不需要名单文件）
# --------------------------------------------------------------------------

def class_size(args, session):
    """班级人数（学号上限）：参数优先，其次记住上次设置，默认 50。"""
    raw = args.get("max_number") or (session or {}).get("class_size") or DEFAULT_CLASS_SIZE
    try:
        size = int(raw)
    except (TypeError, ValueError):
        size = DEFAULT_CLASS_SIZE
    return max(2, min(MAX_CLASS_SIZE, size))


def valid_picks(raw, size):
    """挑出仍在 1-size 范围内的历史学号（去重）。

    老师把班级人数从 50 改成 30 之后，旧记录里 31-50 号还在，但已经不该算数了：
    不筛掉的话 len(picked) 会大于 size，"本轮还剩几人"要报成负数。
    """
    out = []
    for item in raw or []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= size and number not in out:
            out.append(number)
    return out


def pick_students(tools, args):
    """抽学号：默认在 1-50 号里不重复地抽，抽完一轮自动重开；也可以随机分组。

    reset_only=true 只清空记录、不抽（大屏上「完全重置抽取记录」按钮走这条路）。
    """
    # 注意：空字典是 falsy，这里不能用 `or {}`，否则状态写不回会话
    session = getattr(tools, "session", None)
    if session is None:
        session = {}
    size = class_size(args, session)
    session["class_size"] = size
    picked = [] if args.get("reset") else valid_picks(session.get("picked_numbers"), size)
    rng = random.SystemRandom()

    if args.get("reset_only"):
        session["picked_numbers"] = []
        emit(tools, {"t": "board", "action": "show", "kind": "pick",
                     "title": "抽取记录已完全重置", "body": [],
                     "footer": "1-%d 号全部可抽，点「抽下一个」开始" % size})
        return ("已完全重置抽取记录：1-%d 号全部回到未抽取状态。" % size), {
            "kind": "classroom", "action": "reset", "class_size": size,
            "picked": 0, "remaining": size}

    mode = str(args.get("mode") or "pick").strip().lower()
    if mode in ("group", "groups", "分组"):
        try:
            groups = int(args.get("groups") or 0)
        except (TypeError, ValueError):
            groups = 0
        try:
            per_group = int(args.get("per_group") or 0)
        except (TypeError, ValueError):
            per_group = 0
        numbers = list(range(1, size + 1))
        rng.shuffle(numbers)
        if not groups and per_group:
            groups = max(1, (len(numbers) + per_group - 1) // per_group)
        if not groups:
            groups = 6
        groups = max(1, min(20, groups))
        buckets = [[] for _ in range(groups)]
        for index, number in enumerate(numbers):
            buckets[index % groups].append(number)
        body = ["第 %d 组（%d 人）：%s 号"
                % (index + 1, len(bucket),
                   "、".join(str(item) for item in sorted(bucket)))
                for index, bucket in enumerate(buckets)]
        emit(tools, {"t": "board", "action": "show", "kind": "group",
                     "title": "分组结果：1-%d 号分成 %d 组" % (size, groups), "body": body})
        return ("已把 1-%d 号随机分成 %d 组：\n%s" % (size, groups, "\n".join(body))), {
            "kind": "classroom", "action": "group", "groups": groups,
            "class_size": size, "detail": body}

    try:
        count = max(1, min(20, int(args.get("count") or 1)))
    except (TypeError, ValueError):
        count = 1
    remaining = [number for number in range(1, size + 1) if number not in picked]
    reset_note = ""
    if len(remaining) < count:
        picked = []
        remaining = list(range(1, size + 1))
        reset_note = "（1-%d 号已经抽完一轮，已自动重新开始）" % size
    chosen = sorted(rng.sample(remaining, min(count, len(remaining))))
    picked.extend(chosen)
    session["picked_numbers"] = picked
    left = size - len(picked)

    labels = "、".join("学号 %d" % number for number in chosen)
    emit(tools, {"t": "board", "action": "show", "kind": "pick",
                 "title": "请回答：%s" % labels, "body": [],
                 "footer": "本轮还剩 %d 人未抽到" % left})
    return ("本次抽到：%s\n（1-%d 号共 %d 人；本轮已抽 %d 人，还剩 %d 人未抽到%s）"
            % (labels, size, size, len(picked), left, reset_note)), {
        "kind": "classroom", "action": "pick", "numbers": chosen, "class_size": size,
        "picked": len(picked), "remaining": left}


# --------------------------------------------------------------------------
# 课堂计时 / 投屏
# --------------------------------------------------------------------------

def class_timer(tools, args):
    if args.get("stop"):
        emit(tools, {"t": "timer", "action": "stop"})
        return "已停止课堂计时。", {"kind": "classroom", "action": "timer", "stopped": True}
    try:
        seconds = int(args.get("seconds") or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        raise ValueError("请给出倒计时秒数（例如 300 表示 5 分钟）")
    seconds = min(seconds, 3600 * 2)
    label = str(args.get("label") or "课堂计时").strip() or "课堂计时"
    emit(tools, {"t": "timer", "action": "start", "seconds": seconds, "label": label})
    minutes, rest = divmod(seconds, 60)
    human = ("%d 分 %d 秒" % (minutes, rest)) if minutes else ("%d 秒" % rest)
    return ("已在大屏上开始 %s 倒计时（%s），结束时会有提示。"
            % (human, label)), {"kind": "classroom", "action": "timer",
                                "seconds": seconds, "label": label}


def show_board(tools, args):
    action = str(args.get("action") or "show").strip().lower()
    if action in ("hide", "close", "关闭"):
        emit(tools, {"t": "board", "action": "hide"})
        return "已关闭大屏展示。", {"kind": "classroom", "action": "board", "hidden": True}

    title = str(args.get("title") or "").strip()
    body = args.get("body")
    if isinstance(body, str):
        body = [line.strip() for line in body.split("\n") if line.strip()]
    body = [str(item).strip() for item in (body or []) if str(item).strip()]
    if not title and not body:
        raise ValueError("请给出要展示的标题或内容")
    kind = str(args.get("kind") or "points").strip().lower()
    footer = str(args.get("footer") or "").strip()
    emit(tools, {"t": "board", "action": "show", "kind": kind, "title": title,
                 "body": body, "footer": footer})
    return ("已把内容投到大屏（%s）。学生看完后可以关闭或继续下一张。"
            % (title or "无标题")), {"kind": "classroom", "action": "board",
                                     "title": title, "lines": len(body), "kind_name": kind}


# --------------------------------------------------------------------------
# 错题本
# --------------------------------------------------------------------------

def mistake_path(tools, args):
    if args.get("file"):
        return tools.resolve(args["file"], for_write=True)
    return os.path.join(tools.settings.workspace_root(), MISTAKE_FILE)


def load_mistakes(tools, args):
    path = mistake_path(tools, args)
    if not os.path.isfile(path):
        return path, {"items": []}
    try:
        with io.open(path, encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return path, {"items": []}
    if not isinstance(payload, dict):
        payload = {"items": []}
    payload.setdefault("items", [])
    return path, payload


def save_mistakes(path, payload):
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def mistake_book(tools, args):
    action = str(args.get("action") or "list").strip().lower()
    path, payload = load_mistakes(tools, args)
    items = payload["items"]

    if action in ("add", "record", "记录", "添加"):
        entries = args.get("items")
        if isinstance(entries, dict):
            entries = [entries]
        if not entries and args.get("question"):
            entries = [{"question": args.get("question"), "answer": args.get("answer"),
                        "topic": args.get("topic"), "reason": args.get("reason"),
                        "student": args.get("student"), "subject": args.get("subject")}]
        if not entries:
            raise ValueError("请给出要记录的错题（items 数组，或用 question/answer/topic 直接给一条）")
        added = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            question = str(entry.get("question") or "").strip()
            if not question:
                continue
            record = {
                "id": "m%d" % (len(items) + 1),
                "date": time.strftime("%Y-%m-%d"),
                "subject": str(entry.get("subject") or "").strip(),
                "topic": str(entry.get("topic") or "未分类").strip() or "未分类",
                "student": str(entry.get("student") or "").strip(),
                "question": question,
                "answer": str(entry.get("answer") or "").strip(),
                "reason": str(entry.get("reason") or "").strip(),
                "source": str(entry.get("source") or "").strip(),
                "practiced": 0,
            }
            items.append(record)
            added.append(record)
        save_mistakes(path, payload)
        topics = {}
        for record in added:
            topics[record["topic"]] = topics.get(record["topic"], 0) + 1
        return ("已记录 %d 道错题（涉及知识点：%s）。错题本共 %d 道，文件：%s"
                % (len(added), "、".join("%s×%d" % (key, value) for key, value in topics.items()),
                   len(items), path)), {"kind": "mistake", "action": "add",
                                        "added": len(added), "total": len(items), "path": path}

    if action in ("list", "列表", "查看"):
        topic = str(args.get("topic") or "").strip()
        student = str(args.get("student") or "").strip()
        try:
            limit = int(args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        chosen = [record for record in items
                  if (not topic or topic in record.get("topic", ""))
                  and (not student or student == record.get("student", ""))]
        chosen = chosen[-limit:]
        if not chosen:
            return "错题本里没有符合条件的记录。", {"kind": "mistake", "action": "list", "count": 0}
        lines = ["错题本共 %d 道，符合条件的 %d 道：" % (len(items), len(chosen))]
        for record in chosen:
            lines.append("- [%s] %s｜知识点：%s%s%s" % (
                record.get("id"), record.get("question", "")[:80], record.get("topic", ""),
                ("｜错因：" + record["reason"][:40]) if record.get("reason") else "",
                ("｜学生：" + record["student"]) if record.get("student") else ""))
        return "\n".join(lines), {"kind": "mistake", "action": "list", "count": len(chosen)}

    if action in ("stats", "统计", "分析"):
        by_topic = {}
        for record in items:
            topic = record.get("topic", "未分类")
            by_topic[topic] = by_topic.get(topic, 0) + 1
        if not by_topic:
            return "错题本还是空的，先记录几道错题吧。", {"kind": "mistake", "action": "stats"}
        ordered = sorted(by_topic.items(), key=lambda item: -item[1])
        lines = ["错题知识点分布（共 %d 道）：" % len(items)]
        for topic, count in ordered:
            lines.append("- %s：%d 道%s" % (topic, count, "（薄弱）" if count >= 3 else ""))
        lines.append("")
        lines.append("建议先针对“%s”出重练卷。" % ordered[0][0])
        return "\n".join(lines), {"kind": "mistake", "action": "stats",
                                  "topics": [{"topic": topic, "count": count}
                                             for topic, count in ordered]}

    if action in ("remove", "delete", "删除"):
        ids = args.get("ids") or ([args.get("id")] if args.get("id") else [])
        ids = {str(item) for item in ids if item}
        if not ids:
            raise ValueError("请给出要删除的 id（错题本列表里有）")
        kept = [record for record in items if record.get("id") not in ids]
        removed = len(items) - len(kept)
        payload["items"] = kept
        save_mistakes(path, payload)
        return "已删除 %d 道错题，剩余 %d 道。" % (removed, len(kept)), {
            "kind": "mistake", "action": "remove", "removed": removed, "total": len(kept)}

    if action in ("practice", "重练", "生成重练卷"):
        topic = str(args.get("topic") or "").strip()
        try:
            count = int(args.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        out = str(args.get("out") or "").strip()
        chosen = [record for record in items if not topic or topic in record.get("topic", "")]
        chosen.sort(key=lambda record: (record.get("practiced", 0), record.get("date", "")))
        if count:
            chosen = chosen[:count]
        if not chosen:
            raise ValueError("错题本里没有符合条件的题目，无法生成重练卷。")
        if not out:
            out = os.path.join(tools.settings.workspace_root(),
                               "错题重练-%s.docx" % time.strftime("%m%d"))
        target = tools.resolve(out, for_write=True)

        lines = ["# 错题重练（%s）" % time.strftime("%Y-%m-%d"), ""]
        lines.append("姓名：__________　班级：__________　得分：__________")
        lines.append("")
        current_topic = None
        for index, record in enumerate(chosen, start=1):
            if record.get("topic") != current_topic:
                current_topic = record.get("topic")
                lines.append("## %s" % current_topic)
                lines.append("")
            lines.append("%d. %s" % (index, record.get("question", "")))
            if record.get("source"):
                lines.append("")
                lines.append("   （来自：%s）" % record["source"])
            lines.append("")
        lines.append("---")
        lines.append("# 参考答案")
        lines.append("")
        for index, record in enumerate(chosen, start=1):
            lines.append("%d. %s" % (index, record.get("answer") or "（错题本里没有记录答案）"))
            if record.get("reason"):
                lines.append("   错因提示：%s" % record["reason"])
        with io.open(target, "wb") as handle:
            handle.write(office.build_docx("\n".join(lines)))
        for record in chosen:
            record["practiced"] = int(record.get("practiced") or 0) + 1
        save_mistakes(path, payload)
        topics = sorted({record.get("topic", "") for record in chosen})
        return ("已生成错题重练卷：%s（%d 道题，知识点：%s；题目在前、参考答案另起一页）"
                % (target, len(chosen), "、".join(topics))), {
            "kind": "mistake", "action": "practice", "path": target,
            "count": len(chosen), "topics": topics}

    raise ValueError("不支持的 action：%s（可用 add/list/stats/practice/remove）" % action)


# --------------------------------------------------------------------------
# 成绩统计（用代码算，避免模型心算出错）
# --------------------------------------------------------------------------

def _read_table(tools, args):
    path = tools.resolve(args.get("file"), must_exist=True)
    extension = os.path.splitext(path)[1].lower()
    if extension == ".xlsx":
        text = office.read_xlsx(path)
    else:
        with io.open(path, "rb") as handle:
            text = decode_text(handle.read())
    rows = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("##"):
            continue
        cells = next(csv.reader([line], delimiter="\t")) if "\t" in line else \
            next(csv.reader([line]))
        rows.append([cell.strip() for cell in cells])
    return path, [row for row in rows if any(cell for cell in row)]


def _header_at(header, index):
    return (header[index] or "").strip() if 0 <= index < len(header) else ""


def _header_has(text, words):
    lowered = (text or "").strip().lower()
    return any(word in lowered for word in words)


def _is_id_header(text):
    """列名看着像学号/序号：这种列常常整列都是数字，但绝不是分数。"""
    text = (text or "").strip().lower()
    if not text:
        return False
    if _header_has(text, ID_HEADER_WORDS):
        return True
    return text in ("号", "no.", "num", "number.", "序号")


def score_stats(tools, args):
    path, rows = _read_table(tools, args)
    if not rows:
        raise ValueError("表格里没有数据：%s" % path)

    full_marks = float(args.get("full_marks") or 100)
    pass_ratio = float(args.get("pass_ratio") or 0.6)
    excellent_ratio = float(args.get("excellent_ratio") or 0.85)

    header = rows[0]
    data = rows[1:] if any(not NUMBER.match(cell or "") for cell in header[:2]) else rows

    score_column = args.get("score_column")
    name_column = args.get("name_column")
    columns = max(len(row) for row in data)

    def numeric_column(index):
        found = []
        for row in data:
            if index < len(row) and NUMBER.match(row[index] or ""):
                found.append(float(row[index]))
        return found

    ambiguous_columns = []
    if score_column is not None:
        if isinstance(score_column, str) and not NUMBER.match(score_column):
            score_index = header.index(score_column) if score_column in header else 0
        else:
            score_index = max(0, int(score_column) - 1)
    else:
        has_header = data is not rows
        candidates = []
        for index in range(columns):
            found = len(numeric_column(index))
            if found:
                candidates.append((index, found))
        # ① 有表头时先排除「学号/序号」这类整列数字、但不是分数的列
        pool = [item for item in candidates
                if not (has_header and _is_id_header(_header_at(header, item[0])))] or candidates
        # ② 列名里就写着「分数/成绩」的优先
        named = [item for item in pool
                 if has_header and _header_has(_header_at(header, item[0]), SCORE_HEADER_WORDS)]
        if named:
            pool = named
        elif len(pool) > 1 and has_header:
            ambiguous_columns = [_header_at(header, index) for index, _found in pool]
        best, score_index = -1, 0
        for index, found in pool:
            if found > best:
                best, score_index = found, index
    scores = numeric_column(score_index)
    if not scores:
        raise ValueError("没有找到分数列，请用 score_column 指定（列名或第几列，从 1 开始）")

    if name_column is not None:
        if isinstance(name_column, str) and not NUMBER.match(name_column):
            name_index = header.index(name_column) if name_column in header else 0
        else:
            name_index = max(0, int(name_column) - 1)
    else:
        name_index = -1
        if data is not rows:
            for index in range(len(header)):
                if index != score_index and _header_has(_header_at(header, index), NAME_HEADER_WORDS):
                    name_index = index
                    break
            if name_index < 0:
                for index in range(columns):
                    if index != score_index and not _is_id_header(_header_at(header, index)):
                        name_index = index
                        break
        if name_index < 0:
            name_index = 0 if score_index != 0 else 1

    pairs = []
    for row in data:
        if score_index < len(row) and NUMBER.match(row[score_index] or ""):
            name = row[name_index] if name_index < len(row) else ""
            pairs.append((name, float(row[score_index])))

    count = len(scores)
    ordered = sorted(scores)
    mean = sum(scores) / count
    median = (ordered[count // 2] if count % 2
              else (ordered[count // 2 - 1] + ordered[count // 2]) / 2)
    variance = sum((value - mean) ** 2 for value in scores) / count
    std = variance ** 0.5
    pass_line = full_marks * pass_ratio
    excellent_line = full_marks * excellent_ratio
    passed = [value for value in scores if value >= pass_line]
    excellent = [value for value in scores if value >= excellent_line]

    bands = [("不及格", 0, pass_line), ("及格", pass_line, full_marks * 0.7),
             ("中等", full_marks * 0.7, full_marks * 0.8),
             ("良好", full_marks * 0.8, excellent_line), ("优秀", excellent_line, 1e9)]
    band_rows = []
    for label, low, high in bands:
        hits = [value for value in scores if low <= value < high]
        span = "%.0f-%s" % (low, "满分" if high > 1e8 else "%.0f" % high)
        band_rows.append((label, span, len(hits), len(hits) * 100.0 / count))

    lines = ["成绩统计（%d 人，满分 %.0f）：" % (count, full_marks),
             "- 平均分 %.2f ｜ 中位数 %.2f ｜ 标准差 %.2f" % (mean, median, std),
             "- 最高 %.1f ｜ 最低 %.1f ｜ 极差 %.1f"
             % (max(scores), min(scores), max(scores) - min(scores)),
             "- 及格率（≥%.0f）%.1f%%（%d 人）｜ 优秀率（≥%.0f）%.1f%%（%d 人）"
             % (pass_line, len(passed) * 100.0 / count, len(passed),
                excellent_line, len(excellent) * 100.0 / count, len(excellent)),
             "",
             "分数段分布：",
             "| 分数段 | 区间 | 人数 | 占比 |",
             "| --- | --- | --- | --- |"]
    if ambiguous_columns:
        lines.append("- 提示：表里有多个像分数的列（%s），本次统计的是「%s」；"
                     "要统计别的请传 score_column"
                     % ("、".join(name or "未命名列" for name in ambiguous_columns),
                        _header_at(header, score_index) or "未命名列"))
    for label, span, hits, ratio in band_rows:
        lines.append("| %s | %s | %d | %.1f%% |" % (label, span, hits, ratio))
    if args.get("show_names"):
        by_score = sorted(pairs, key=lambda item: -item[1])
        lines.append("")
        lines.append("前 3 名：%s" % "、".join("%s %.0f" % (name, value)
                                             for name, value in by_score[:3]))
        lines.append("后 3 名：%s" % "、".join("%s %.0f" % (name, value)
                                             for name, value in by_score[-3:][::-1]))
    else:
        lines.append("")
        lines.append("（默认不列学生个人分数；需要时用 show_names=true）")

    out = str(args.get("out") or "").strip()
    written = ""
    if out:
        target = tools.resolve(out, for_write=True)
        table_rows = [["统计项", "数值"],
                      ["人数", str(count)], ["平均分", "%.2f" % mean],
                      ["中位数", "%.2f" % median], ["标准差", "%.2f" % std],
                      ["最高分", "%.1f" % max(scores)], ["最低分", "%.1f" % min(scores)],
                      ["及格率", "%.1f%%" % (len(passed) * 100.0 / count)],
                      ["优秀率", "%.1f%%" % (len(excellent) * 100.0 / count)]]
        table_rows += [["%s人数" % label, str(hits)] for label, _span, hits, _ratio in band_rows]
        table_rows += [[], ["原始成绩明细"], ["姓名", "分数"]]
        table_rows += [[name or "-", "%.1f" % value] for name, value in pairs]
        with io.open(target, "wb") as handle:
            handle.write(office.build_xlsx(table_rows, sheet="成绩分析"))
        written = target
        lines.append("")
        lines.append("统计表已保存：%s（含统计汇总与原始明细）" % target)

    return "\n".join(lines), {
        "kind": "score", "count": count, "mean": round(mean, 2),
        "median": round(median, 2), "std": round(std, 2),
        "max": max(scores), "min": min(scores),
        "score_column": _header_at(header, score_index) or (score_index + 1),
        "pass_rate": round(len(passed) * 100.0 / count, 1),
        "excellent_rate": round(len(excellent) * 100.0 / count, 1),
        "bands": [{"label": label, "count": hits, "ratio": round(ratio, 1)}
                  for label, _span, hits, ratio in band_rows],
        "out": written}
