# -*- coding: utf-8 -*-
"""农历（1900-2100），只用标准库。

用流传最广的**压缩表**：一年一个整数，201 个整数覆盖 1900-2100。编码：

  - 位 16      闰月是 30 天(1) 还是 29 天(0)
  - 位 15..4   正月..十二月 各是 30 天(1) 还是 29 天(0)（正月 = 位 15）
  - 位 3..0    闰月月号（0 = 不闰）

基准点：1900-01-31 是 1900 年正月初一。

**没有 24 节气**：节气要算到"交节时刻落在北京时间哪一天"，本机没有高精度
太阳理论，用低精度公式在 1900-2100 的 4824 个节气里会有 18 个（0.37%）落在
交界上差一天（都是交节时刻离午夜不到 7 分钟的情况）。宁可不给，也不给错的：
日历接口里的 ``term`` 一律是空串，前端不用为它写分支。

本文件不联网、不抛异常：超出 1900-2100 的日期返回空名字。
"""

__all__ = ["lunar_text", "lunar_parts", "MONTH_NAMES", "DAY_NAMES", "LEAP_MONTH_NAMES"]

# 1900-2100 的农历压缩表（每年的编码见模块说明）
LUNAR_INFO = (
    0x04BD8, 0x04AE0, 0x0A570, 0x054D5, 0x0D260, 0x0D950, 0x16554, 0x056A0,
    0x09AD0, 0x055D2, 0x04AE0, 0x0A5B6, 0x0A4D0, 0x0D250, 0x1D255, 0x0B540,
    0x0D6A0, 0x0ADA2, 0x095B0, 0x14977, 0x04970, 0x0A4B0, 0x0B4B5, 0x06A50,
    0x06D40, 0x1AB54, 0x02B60, 0x09570, 0x052F2, 0x04970, 0x06566, 0x0D4A0,
    0x0EA50, 0x06E95, 0x05AD0, 0x02B60, 0x186E3, 0x092E0, 0x1C8D7, 0x0C950,
    0x0D4A0, 0x1D8A6, 0x0B550, 0x056A0, 0x1A5B4, 0x025D0, 0x092D0, 0x0D2B2,
    0x0A950, 0x0B557, 0x06CA0, 0x0B550, 0x15355, 0x04DA0, 0x0A5B0, 0x14573,
    0x052B0, 0x0A9A8, 0x0E950, 0x06AA0, 0x0AEA6, 0x0AB50, 0x04B60, 0x0AAE4,
    0x0A570, 0x05260, 0x0F263, 0x0D950, 0x05B57, 0x056A0, 0x096D0, 0x04DD5,
    0x04AD0, 0x0A4D0, 0x0D4D4, 0x0D250, 0x0D558, 0x0B540, 0x0B6A0, 0x195A6,
    0x095B0, 0x049B0, 0x0A974, 0x0A4B0, 0x0B27A, 0x06A50, 0x06D40, 0x0AF46,
    0x0AB60, 0x09570, 0x04AF5, 0x04970, 0x064B0, 0x074A3, 0x0EA50, 0x06B58,
    0x05AC0, 0x0AB60, 0x096D5, 0x092E0, 0x0C960, 0x0D954, 0x0D4A0, 0x0DA50,
    0x07552, 0x056A0, 0x0ABB7, 0x025D0, 0x092D0, 0x0CAB5, 0x0A950, 0x0B4A0,
    0x0BAA4, 0x0AD50, 0x055D9, 0x04BA0, 0x0A5B0, 0x15176, 0x052B0, 0x0A930,
    0x07954, 0x06AA0, 0x0AD50, 0x05B52, 0x04B60, 0x0A6E6, 0x0A4E0, 0x0D260,
    0x0EA65, 0x0D530, 0x05AA0, 0x076A3, 0x096D0, 0x04AFB, 0x04AD0, 0x0A4D0,
    0x1D0B6, 0x0D250, 0x0D520, 0x0DD45, 0x0B5A0, 0x056D0, 0x055B2, 0x049B0,
    0x0A577, 0x0A4B0, 0x0AA50, 0x1B255, 0x06D20, 0x0ADA0, 0x14B63, 0x09370,
    0x049F8, 0x04970, 0x064B0, 0x168A6, 0x0EA50, 0x06B20, 0x1A6C4, 0x0AAE0,
    0x092E0, 0x0D2E3, 0x0C960, 0x0D557, 0x0D4A0, 0x0DA50, 0x05D55, 0x056A0,
    0x0A6D0, 0x055D4, 0x052D0, 0x0A9B8, 0x0A950, 0x0B4A0, 0x0B6A6, 0x0AD50,
    0x055A0, 0x0ABA4, 0x0A5B0, 0x052B0, 0x0B273, 0x06930, 0x07337, 0x06AA0,
    0x0AD50, 0x14B55, 0x04B60, 0x0A570, 0x054E4, 0x0D160, 0x0E968, 0x0D520,
    0x0DAA0, 0x16AA6, 0x056D0, 0x04AE0, 0x0A9D4, 0x0A4D0, 0x0D150, 0x0F252,
    0x0D520,
)

MIN_YEAR = 1900
MAX_YEAR = 2100
# 1900 年正月初一（这张表的基准点）
BASE_DAY = 2415051                        # = _julian_day(1900, 1, 31)

MONTH_NAMES = ("正月", "二月", "三月", "四月", "五月", "六月",
               "七月", "八月", "九月", "十月", "冬月", "腊月")
# 闰月一般写"闰十一月 / 闰十二月"，不写"闰冬月"
LEAP_MONTH_NAMES = ("正月", "二月", "三月", "四月", "五月", "六月",
                    "七月", "八月", "九月", "十月", "十一月", "十二月")
DAY_NAMES = ("初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
             "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
             "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十")
# 24 节气：从黄经 315°（立春）起，每 15° 一个；下标偶数的是"节"，奇数是"中气"
TERM_NAMES = ("立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
              "立夏", "小满", "芒种", "夏至", "小暑", "大暑",
              "立秋", "处暑", "白露", "秋分", "寒露", "霜降",
              "立冬", "小雪", "大雪", "冬至", "小寒", "大寒")


# -- 压缩表 ---------------------------------------------------------------
def _leap_month(year):
    """这一年闰几月；0 表示不闰。"""
    return LUNAR_INFO[year - MIN_YEAR] & 0xF


def _leap_days(year):
    """闰月有多少天（29/30）；不闰返回 0。"""
    if not _leap_month(year):
        return 0
    return 30 if LUNAR_INFO[year - MIN_YEAR] & 0x10000 else 29


def _month_days(year, month):
    """year 年 month 月有多少天（month 是 1..12，不管闰月）。"""
    return 30 if LUNAR_INFO[year - MIN_YEAR] & (0x10000 >> month) else 29


def _year_days(year):
    """这一年一共多少天（含闰月）。"""
    total = 348
    mask = 0x8000
    while mask > 0x8:
        if LUNAR_INFO[year - MIN_YEAR] & mask:
            total += 1
        mask >>= 1
    return total + _leap_days(year)


def _julian_day(year, month, day):
    """公历 -> 当天 0 时的儒略日（格里历）。"""
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + b - 1524.5


def _from_julian_day(jd):
    """儒略日 -> (年, 月, 日)。"""
    jd = jd + 0.5
    z = int(jd)
    f = jd - z
    alpha = int((z - 1867216.25) / 36524.25)
    a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d = int(365.25 * c)
    e = int((b - d) / 30.6001)
    day = b - d - int(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    whole = int(day)
    return (year, month, whole + 1) if day - whole >= 1.0 else (year, month, whole)


def _as_ymd(date):
    """把 date / datetime / (年,月,日) 统一成三个整数。"""
    if isinstance(date, (tuple, list)):
        year, month, day = date[0], date[1], date[2]
    else:
        year, month, day = date.year, date.month, date.day
    return int(year), int(month), int(day)


def _solar_day(date):
    """公历日期 -> 距离基准点（1900-01-31）多少天。"""
    year, month, day = _as_ymd(date)
    return int(_julian_day(year, month, day) + 0.5) - BASE_DAY


def lunar_parts(date):
    """公历日期 -> (农历年, 月, 日, 是否闰月, 月名, 日名)。

    ``date`` 可以是 ``datetime.date`` / ``datetime.datetime``，也可以是 ``(年, 月, 日)``。
    超出 1900-2100 返回 ``(年, 0, 0, False, "", "")``，不抛异常。
    """
    year = _as_ymd(date)[0]
    if not (MIN_YEAR <= year <= MAX_YEAR):
        return year, 0, 0, False, "", ""
    offset = _solar_day(date)
    if offset < 0:                       # 1900-01-31 之前：表里没有 1899 年
        return year, 0, 0, False, "", ""
    # 从 1900 年正月初一往后数，先跨年、再跨月
    lunar_year = MIN_YEAR
    while lunar_year <= MAX_YEAR and offset >= _year_days(lunar_year):
        offset -= _year_days(lunar_year)
        lunar_year += 1
    if lunar_year > MAX_YEAR:
        return year, 0, 0, False, "", ""
    leap = _leap_month(lunar_year)
    month = 1
    while month <= 12:
        if leap and month == leap + 1:
            # 先过闰月，再过"闰月之后的那个月"
            if offset < _leap_days(lunar_year):
                name = "闰" + LEAP_MONTH_NAMES[leap - 1]
                return lunar_year, leap, offset + 1, True, name, DAY_NAMES[offset]
            offset -= _leap_days(lunar_year)
        days = _month_days(lunar_year, month)
        if offset < days:
            return (lunar_year, month, offset + 1, False,
                    MONTH_NAMES[month - 1], DAY_NAMES[offset])
        offset -= days
        month += 1
    return year, 0, 0, False, "", ""


def lunar_text(date):
    """公历日期 -> 农历文字，例如 ``冬月十三`` / ``正月初一`` / ``闰四月初三``。"""
    _year, _month, _day, _leap, month_name, day_name = lunar_parts(date)
    if not month_name:
        return ""
    return month_name + day_name
