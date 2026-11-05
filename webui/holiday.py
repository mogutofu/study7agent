# -*- coding: utf-8 -*-
"""节假日 / 调休：多个源里挑最先答得上的，缓存到 ``config\\holiday-<年>.json``。

为什么要这么绕：这台机器可能连不上外网（内网、断网、被墙），而"明天放不放假"是老师
天天要看的。所以规矩是**永远先给能给的**：

  1. 内存里这次进程已经拿到的 -> 直接给（翻月份一秒都不能等）；
  2. 磁盘缓存（``config\\holiday-<年>.json``）：7 天内算新鲜，直接用，不联网；
     过期了也先给（旧的总比没有强），**不在这里等网**；
  3. 随包带一份离线表（``webui\\holidays-bundled.json``）：连缓存都没有时用它，
     同样是"先给"，然后由 ``refresh_soon`` 在后台去刷新；
  4. 只有"连内置表里都没有这一年"时，才同步问一次网（有总预算，见 ``DEADLINE``）。

数据源按顺序试（2026-09 实测：五个源都通，但学校网对 github/jsdelivr 常常不友好，
所以国内源排前面；单源不可靠 —— 老师遇到过一次"经常下载不下来"）：

    timor.tech（https -> http）  https://timor.tech/api/holiday/year/2026
        {"code":0,"holiday":{"01-01":{"holiday":true,"name":"元旦","date":"2026-01-01"}}}
        ``holiday: true`` = 放假；``false`` = 调休上班。
    holiday-cn（jsDelivr -> raw GitHub）  https://…/NateScarlet/holiday-cn/master/2026.json
        {"days":[{"name":"元旦","date":"2026-01-01","isOffDay":true}, …]}
        这份是从国务院公告整理的公开数据集，字段含义和上面一一对应。

超时和总预算都写死在这里、全部包在 try 里：**任何**失败都只是"这次拿不到"，
绝不影响页面和对话。
"""

import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

YEAR_API = "https://timor.tech/api/holiday/year/%d"
CDN_API = "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/%d.json"
RAW_API = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/%d.json"
# (名字, 地址模板, 解析用的形状)。顺序 = 试的顺序，国内源在前。
SOURCES = (
    ("timor https", YEAR_API, "timor"),
    ("timor http", YEAR_API.replace("https://", "http://", 1), "timor"),
    ("holiday-cn cdn", CDN_API, "holiday-cn"),
    ("holiday-cn raw", RAW_API, "holiday-cn"),
)
TIMEOUT = 3.5                   # 单个源最多等多久（秒）
DEADLINE = 4.5                  # 一次 _fetch 总共最多花多久（秒）
CACHE_DAYS = 7.0                # 缓存有效期（天）
# 进程内缓存：老师翻月份时**一秒都不能等**。磁盘缓存已经很快了，但它每次都要读文件 +
# json 解析；学校那台机器上翻 12 个月就是 12 次。这里直接按年留在内存里。
_MEMORY = {}
_LOCK = threading.Lock()
_REFRESHING = set()             # 正在后台刷新的年份，避免同一年被反复触发
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _path(base_dir, year):
    return os.path.join(base_dir, "config", "holiday-%d.json" % year)


def _parse(payload):
    """timor.tech 的形状 -> ``{日期: {"name":..., "holiday": True/False}}``。"""
    if not isinstance(payload, dict) or payload.get("code") != 0:
        return {}
    days = payload.get("holiday")
    if not isinstance(days, dict):
        return {}
    out = {}
    for key, entry in days.items():
        if not isinstance(entry, dict):
            continue
        date = str(entry.get("date") or "")
        if not _DATE.match(date):                 # 接口偶尔给 "01-01"，用它自带的 date
            continue
        out[date] = {"name": str(entry.get("name") or ""),
                     "holiday": bool(entry.get("holiday"))}
    return out


def _parse_cn(payload):
    """holiday-cn 的形状（``days`` 是列表，``isOffDay`` = 放假）-> 同一张表。"""
    if not isinstance(payload, dict):
        return {}
    days = payload.get("days")
    if not isinstance(days, list):
        return {}
    out = {}
    for entry in days:
        if not isinstance(entry, dict):
            continue
        date = str(entry.get("date") or "")
        if not _DATE.match(date):
            continue
        out[date] = {"name": str(entry.get("name") or ""),
                     "holiday": bool(entry.get("isOffDay"))}
    return out


def _read_cache(base_dir, year):
    """读缓存。返回 ``(天数表, 写入时间戳)``；没有/坏了 -> ({}, 0)。"""
    try:
        with open(_path(base_dir, year), "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}, 0.0
    if not isinstance(payload, dict):
        return {}, 0.0
    days = payload.get("days")
    if not isinstance(days, dict):
        return {}, 0.0
    clean = {}
    for date, entry in days.items():
        if isinstance(entry, dict):
            clean[str(date)] = {"name": str(entry.get("name") or ""),
                               "holiday": bool(entry.get("holiday"))}
    try:
        stamp = float(payload.get("fetchedAt") or 0)
    except (TypeError, ValueError):
        stamp = 0.0
    return clean, stamp


def _write_cache(base_dir, year, days, stamp):
    """写缓存（临时文件 + os.replace）。失败就当没写，不抛。"""
    folder = os.path.join(base_dir, "config")
    target = _path(base_dir, year)
    temporary = target + ".tmp"
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"year": year, "fetchedAt": stamp, "days": days},
                      handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, target)
    except OSError:
        try:
            if os.path.isfile(temporary):
                os.remove(temporary)
        except OSError:
            pass


def _says_nothing(payload, shape):
    """源**明确回答**"这一年一条都没有"（还没公布）——不是"取不到"。

    timor：``{"code":0,"holiday":{}}``；holiday-cn：``{"days":[]}``。
    这种情况要立刻收手：四家挨个问一遍就是 4.8 秒（2027 年的实测），
    老师那边看到的就是"日历一直在转圈"。
    """
    if not isinstance(payload, dict):
        return False
    if shape == "timor":
        return payload.get("code") == 0 and isinstance(payload.get("holiday"), dict) \
            and not payload["holiday"]
    return isinstance(payload.get("days"), list) and not payload["days"]


def _fetch(year):
    """去网上问一次：多个源按顺序试，谁先答对就用谁的。

    返回天数表；任何问题都返回 None（不是空表——要区分"没数据"）。
    每个源最多等 ``TIMEOUT``，整趟最多花 ``DEADLINE`` —— 学校网经常是"连得上但不通"，
    在一个源上死等，老师那边就是"日历一直转圈"。某一家明确答"还没有"就直接收手。
    """
    deadline = time.time() + DEADLINE
    for name, template, shape in SOURCES:
        left = deadline - time.time()
        if left <= 0.3:
            break
        url = template % year
        request = urllib.request.Request(
            url, headers={"User-Agent": "Study7Agent/0.6"})
        try:
            with urllib.request.urlopen(request, timeout=min(TIMEOUT, left)) as response:
                raw = response.read()
        except (urllib.error.URLError, OSError, ValueError):
            continue
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        days = _parse(payload) if shape == "timor" else _parse_cn(payload)
        if days:
            return days
        if _says_nothing(payload, shape):
            return None
    return None


def _bundled(year):
    """随包带一份离线节假日表（webui\\holidays-bundled.json）。

    学校内网经常根本连不出去。联网一次都拿不到时，用它兜底：至少在放假日和调休上不会
    给出错的信息（没有的年份返回空表，页面照常画农历）。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "holidays-bundled.json")
    try:
        with io.open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    year_map = payload.get(str(year)) or payload.get(year)
    return year_map if isinstance(year_map, dict) else {}


def refresh_soon(base_dir, year):
    """后台刷一次这一年的数据（联网成功就写缓存 + 更新内存）。

    为什么不让请求路径自己等：老师点开日历必须**立刻**看到东西。缓存/内置表先顶上，
    真数据在后台慢慢拿；拿回来之后下一次翻月份就是新的（前端见 ``pending`` 会再问一次）。
    返回 True 表示"已经在刷了"，调用方据此告诉页面"稍后会更新"。
    """
    key = (os.path.abspath(base_dir), int(year))
    with _LOCK:
        if key in _REFRESHING:
            return True
        _REFRESHING.add(key)

    def work():
        try:
            days = _fetch(int(year))
            if days:
                stamp = time.time()
                _write_cache(base_dir, int(year), days, stamp)
                _MEMORY[key] = {"source": "online", "fetchedAt": stamp,
                                "days": days, "stale": False}
        except Exception:                                     # noqa: BLE001
            pass
        finally:
            with _LOCK:
                _REFRESHING.discard(key)

    thread = threading.Thread(target=work, name="holiday-refresh-%d" % int(year))
    thread.daemon = True
    thread.start()
    return True


def year_map(base_dir, year, force=False):
    """拿一年的放假/调休。

    返回 ``{"source": "memory"|"online"|"cache"|"bundled"|"none", "fetchedAt", "days", "stale"}``。

    ★ 老师翻月份时必须**立刻**返回：只在"内存/缓存/内置表都没有这一年"时才同步联网。
      ``stale=True`` 表示"这份数据不是刚联网拿的"，调用方可以顺手安排一次后台刷新
      （见 ``refresh_soon``）。
    """
    key = (os.path.abspath(base_dir), int(year))
    if not force and key in _MEMORY:
        # 内存命中的语义：**这一次没有联网**，那就是"缓存"。
        # 不能在内存里把第一次的 online 一直报下去——页面上那句"数据来源"必须是真话。
        # bundled / none 例外：那两种要如实说"内置离线表" / "没拿到"，页面提示不一样。
        hit = _MEMORY[key]
        if hit["source"] in ("online", "cache"):
            return {"source": "cache", "fetchedAt": hit["fetchedAt"], "days": hit["days"],
                    "stale": bool(hit.get("stale"))}
        return hit

    cached, stamp = _read_cache(base_dir, year)
    fresh = bool(stamp) and (time.time() - stamp) < CACHE_DAYS * 86400
    if cached and fresh and not force:
        result = {"source": "cache", "fetchedAt": stamp, "days": cached, "stale": False}
        _MEMORY[key] = result
        return result
    if cached and not force:
        # 有过期缓存：先给旧的（秒回），刷新交给 refresh_soon —— 不在这里等网。
        result = {"source": "cache", "fetchedAt": stamp, "days": cached, "stale": True}
        _MEMORY[key] = result
        return result

    offline = _bundled(year)
    if offline and not force:
        # 内置表里有这一年：先给，联网放到后台去 —— 断网的教室也一样能看放假安排
        result = {"source": "bundled", "fetchedAt": 0.0, "days": offline, "stale": True}
        _MEMORY[key] = result
        return result

    days = _fetch(year)
    if days is not None:
        stamp = time.time()
        _write_cache(base_dir, year, days, stamp)
        result = {"source": "online", "fetchedAt": stamp, "days": days, "stale": False}
    elif cached:
        result = {"source": "cache", "fetchedAt": stamp, "days": cached, "stale": True}
    else:
        result = {"source": "bundled" if offline else "none", "fetchedAt": 0.0,
                  "days": offline, "stale": True}
    _MEMORY[key] = result
    return result


def view(base_dir, year, force=False):
    """给接口用的整理结果：``days`` 换成 ``{"2026-01-01": {...}}``，另带可读时间。"""
    result = year_map(base_dir, year, force=force)
    stamp = result["fetchedAt"]
    return {
        "source": result["source"],
        "fetchedAt": time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp)) if stamp else "",
        "stale": bool(result.get("stale")),
        "days": result["days"],
    }
