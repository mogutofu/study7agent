# -*- coding: utf-8 -*-
"""联网搜索：内置引擎解析 + 可配置的自定义搜索接口。

设计取舍：
  - 离线内网机器上的"联网搜索"通常指向单位自建的搜索网关（SearXNG、ES 检索、门户搜索），
    所以支持自定义 endpoint（模板里用 {query} 占位，返回 JSON 或 HTML 都能解析）。
  - 没有自定义接口时退回公开搜索引擎的 HTML/RSS 结果页，**多引擎依次兜底**：
    Bing（RSS 优先，结构最稳）→ DuckDuckGo → SearXNG 公共实例。
    前一个报错或一条都没解析出来就换下一个；整条链共用同一份时间预算，
    离线机器上不会一个接一个地干等到超时。
  - 解析出来的结果按"是否命中查询词"加权排序、按归一化 URL 去重；
    **只加权不过滤**（中文长句查询常常一个整词都对不上标题，过滤就把好结果全丢了）。
  - 只返回搜索结果的元信息，正文交给 read_url 去取，避免把整页塞进模型上下文。

只使用 Python 3.8 标准库。
"""

import html as html_module
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = ("Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36")

ENGINES = ("auto", "bing", "duckduckgo", "searxng", "custom")

# SearXNG 是元搜索（一个实例背后聚合多个引擎），这几个公开实例不需要 key。
# 公共实例随时可能限流或宕机，所以每个都短超时、失败就换下一个，全挂了如实报错。
SEARXNG_INSTANCES = ("https://searx.be", "https://search.inetol.net", "https://priv.au")
# 每个 SearXNG 实例最多等这么久（整条链的总预算另算，见 search()）
SEARXNG_PER_INSTANCE = 8.0
# 单个结果页最多读这么多字节：正常搜索结果页 1MB 以内，防止自定义接口把内存灌满
MAX_PAGE_BYTES = 4 * 1024 * 1024
# 反爬验证页的特征：命中说明是被限流了，换引擎比重试有用
CHALLENGE_RE = re.compile(r"anomaly|captcha|unusual traffic|robot check", re.I)
# 去重时忽略的跟踪参数前缀（同一个页面常常带一串 utm_/spm_ 变成好几个 URL）
TRACKING_PREFIXES = ("utm_", "spm", "from=", "ref=", "share_", "fr=", "src=")


class SearchError(Exception):
    pass


def search(query, engine="auto", endpoint="", api_key="", max_results=5,
           timeout=20, insecure=False, opener=None):
    """执行搜索，返回 [{'title','url','snippet'}]。

    多引擎兜底 + 单一时间预算：timeout（默认 20 秒）按引擎数平分，每个引擎
    6-7 秒，不会出现"每个引擎各干等 20 秒"。注意 socket 超时管不住 DNS 解析，
    所以离线时总耗时可能略超这个预算（实测约 +50%）。
    """
    query = (query or "").strip()
    if not query:
        raise SearchError("搜索关键词不能为空")
    try:
        limit = max(1, min(20, int(max_results or 5)))
    except (TypeError, ValueError):
        limit = 5
    try:
        budget = max(3.0, float(timeout or 20))
    except (TypeError, ValueError):
        budget = 20.0

    engine = (engine or "auto").strip().lower()
    if engine == "custom" or (engine == "auto" and endpoint):
        if not endpoint:
            raise SearchError("选择了自定义搜索，但没有配置 search.endpoint")
        return _custom(query, endpoint, api_key, limit, budget, insecure, opener)

    order = _engine_order(engine)
    # 把总预算平分给要试的引擎：Bing 通的时候一样是零点几秒返回，全都不通时总耗时≈timeout，
    # 而不是"每个引擎各干等 20 秒"（教室机器多半是离线的，这一点很要紧）。
    # 注意 socket 超时管不住 DNS 解析，所以每一段至少留 4 秒。
    slice_seconds = max(4.0, budget / len(order))
    errors = []
    for name in order:
        try:
            results = _run_engine(name, query, limit, slice_seconds, insecure, opener)
        except SearchError as exc:
            errors.append("%s：%s" % (name, exc))
            continue
        if results:
            return _rank(results, query, limit)
        errors.append("%s：没有解析到结果" % name)
    raise SearchError("搜索失败。%s。若在离线内网，请在设置里配置单位自建的搜索接口"
                      "（search.endpoint）。" % "；".join(errors))


def _engine_order(engine):
    """要依次尝试的引擎。

    顺序有讲究：Bing 的 RSS 输出结构固定（最稳）→ DuckDuckGo 常被限流 →
    SearXNG 是公共实例、最慢也最容易挂，放最后。
    """
    aliases = {"ddg": "duckduckgo", "ddg-lite": "duckduckgo", "searx": "searxng"}
    name = aliases.get(engine, engine)
    if name in ("bing", "duckduckgo", "searxng"):
        return [name]
    return ["bing", "duckduckgo", "searxng"]


def _run_engine(name, query, limit, timeout, insecure, opener):
    if name == "bing":
        return _bing(query, limit, timeout, insecure, opener)
    if name == "duckduckgo":
        return _duckduckgo(query, limit, timeout, insecure, opener)
    if name == "searxng":
        return _searxng(query, limit, timeout, insecure, opener)
    raise SearchError("不认识的搜索引擎：%s" % name)


# --------------------------------------------------------------------------
# 通用请求
# --------------------------------------------------------------------------

def _decode_body(raw, content_type=""):
    """先按响应头里的 charset 解码，再试 UTF-8 / GB18030，最后容错解。

    只按 UTF-8 解中文站点（很多还是 GBK）会把整页变成乱码，解析出来的标题摘要也没法看。
    """
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        try:
            return raw.decode(match.group(1))
        except (UnicodeDecodeError, LookupError):
            pass
    for name in ("utf-8", "gb18030"):
        try:
            return raw.decode(name)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def _fetch(url, timeout, insecure, opener=None, headers=None):
    request = urllib.request.Request(url, headers=dict({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }, **(headers or {})))
    context = ssl.create_default_context()
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        if opener is not None:
            with opener(request, timeout) as response:
                return _decode_body(response.read(MAX_PAGE_BYTES),
                                    getattr(response, "headers", None) and
                                    response.headers.get("Content-Type", "") or "")
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return _decode_body(response.read(MAX_PAGE_BYTES),
                                response.headers.get("Content-Type", "") or "")
    except urllib.error.HTTPError as exc:
        raise SearchError("HTTP %s" % exc.code)
    except urllib.error.URLError as exc:
        raise SearchError("网络不可达（%s）" % exc.reason)
    except Exception as exc:  # noqa: BLE001
        raise SearchError(str(exc))


def _strip_tags(text):
    text = re.sub(r"<!--[\s\S]*?-->", " ", text)
    text = re.sub(r"<(script|style|noscript)\b[\s\S]*?</\1>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html_module.unescape(text)).strip()


def _clean_url(url):
    url = html_module.unescape((url or "").strip())
    if url.startswith("//"):
        url = "https:" + url
    # DuckDuckGo 的跳转链接：//duckduckgo.com/l/?uddg=<encoded>
    if "duckduckgo.com/l/" in url:
        query = urllib.parse.urlparse(url).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            url = urllib.parse.unquote(target[0])
    if url.startswith("/"):
        return ""
    return url


def _url_key(url):
    """归一化 URL 用于去重：http/https、带不带 www、带一堆跟踪参数都是同一个页面。"""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = "&".join(sorted(pair for pair in parts.query.split("&")
                            if pair and not pair.lower().startswith(TRACKING_PREFIXES)))
    return "%s%s?%s" % (host, parts.path.rstrip("/") or "/", query)


def _collect(limit):
    """解析阶段多留一些候选：排序、去重都发生在后面，先砍到 limit 会把好东西砍掉。"""
    return max(10, limit * 2)


def _dedupe(results, limit):
    seen = set()
    out = []
    for item in results:
        url = item.get("url") or ""
        key = _url_key(url)
        if not url or key in seen:
            continue
        seen.add(key)
        out.append({"title": (item.get("title") or "").strip()[:200],
                    "url": url,
                    "snippet": re.sub(r"\s+", " ", item.get("snippet") or "").strip()[:400]})
        if len(out) >= limit:
            break
    return out


def _terms(query):
    """把查询拆成用于"是否命中"判断的片段。

    中文没有空格，所以长词再拆出二字片段："分数的意义" → 分数/数的/的意/意义。
    这样标题里出现"分数"也算命中（整词匹配对中文太苛刻了）。
    """
    terms = set()
    for token in re.split(r"[\s,，、。；;：:？?！!（）()\[\]\"'“”]+", query):
        token = token.strip().lower()
        if len(token) < 2:
            continue
        terms.add(token)
        if len(token) > 2 and re.search(r"[\u4e00-\u9fff]", token):
            terms.update(token[index:index + 2] for index in range(len(token) - 1))
    return terms


def _rank(results, query, limit):
    """命中查询词的结果往前排，然后截断。

    只加权、**不过滤**：中文长句查询（"分数的意义是什么"）的完整词往往在标题里对不上，
    过滤等于把搜索引擎给的好结果全丢掉。同分保持引擎原来的顺序（稳定排序）。
    """
    terms = _terms(query)
    if not terms:
        return results[:limit]
    scored = []
    for index, item in enumerate(results):
        title = (item.get("title") or "").lower()
        snippet = (item.get("snippet") or "").lower()
        score = sum((2 if term in title else 0) + (1 if term in snippet else 0)
                    for term in terms)
        scored.append((-score, index, item))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [item for _score, _index, item in scored[:limit]]


# --------------------------------------------------------------------------
# 内置引擎
# --------------------------------------------------------------------------

def _bing(query, limit, timeout, insecure, opener=None):
    """Bing：优先用 RSS 输出（结构稳定、不易被改版破坏），失败再退回 HTML 解析。

    两次请求（RSS 一次、HTML 兜底一次）平分这个引擎的时间预算。
    """
    half = max(2.0, float(timeout) / 2)
    params = {"q": query, "count": max(10, limit), "mkt": "zh-CN", "setlang": "zh-CN"}
    rss_url = "https://cn.bing.com/search?" + urllib.parse.urlencode(
        dict(params, format="rss"))
    try:
        page = _fetch(rss_url, half, insecure, opener)
        results = _from_rss(page)
        if results:
            return _dedupe(results, _collect(limit))
    except SearchError:
        pass
    html_url = "https://cn.bing.com/search?" + urllib.parse.urlencode(
        dict(params, ensearch="0"))
    page = _fetch(html_url, half, insecure, opener)
    if CHALLENGE_RE.search(page[:4000]):
        raise SearchError("被限流（返回了验证页）")
    results = []
    for block in re.findall(r'<li class="b_algo"[\s\S]*?(?=<li class="b_algo"|</ol>)', page):
        link = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', block)
        if not link:
            continue
        snippet = re.search(r'<p[^>]*>([\s\S]*?)</p>', block)
        results.append({"url": _clean_url(link.group(1)),
                        "title": _strip_tags(link.group(2)),
                        "snippet": _strip_tags(snippet.group(1)) if snippet else ""})
    if not results:
        # 页面结构又改了：退一步把所有外链当结果，总比"一条都没有"强
        results = _from_html(page)
    return _dedupe(results, _collect(limit))


def _from_rss(page):
    results = []
    for item in re.findall(r"<item>([\s\S]*?)</item>", page):
        title = re.search(r"<title>([\s\S]*?)</title>", item)
        link = re.search(r"<link>([\s\S]*?)</link>", item)
        description = re.search(r"<description>([\s\S]*?)</description>", item)
        if not title or not link:
            continue
        results.append({"url": _clean_url(_strip_tags(link.group(1))),
                        "title": _strip_tags(title.group(1)),
                        "snippet": _strip_tags(description.group(1)) if description else ""})
    return results


def _duckduckgo(query, limit, timeout, insecure, opener=None):
    """DuckDuckGo：先 HTML 版，被限流或解析不到再试 Lite 版。

    两个域名的限流是分开算的，所以 Lite 常常还能用；公共搜索里 DDG 的验证页很常见，
    不换端点的话就是"搜一次失败一次"。
    """
    params = urllib.parse.urlencode({"q": query, "kl": "cn-zh"})
    half = max(2.0, float(timeout) / 2)
    errors = []
    for url, parse in (("https://html.duckduckgo.com/html/?" + params, _ddg_html),
                       ("https://lite.duckduckgo.com/lite/?" + params, _ddg_lite)):
        try:
            page = _fetch(url, half, insecure, opener)
        except SearchError as exc:
            errors.append(str(exc))
            continue
        if CHALLENGE_RE.search(page[:4000]):
            errors.append("被限流（返回了验证页）")
            continue
        results = _dedupe(parse(page), _collect(limit))
        if results:
            return results
        errors.append("没有解析到结果")
    raise SearchError("；".join(errors) or "没有解析到结果")


def _ddg_html(page):
    results = []
    for block in re.split(r'class="result__body"', page)[1:]:
        link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', block)
        if not link:
            continue
        snippet = re.search(r'class="result__snippet"[^>]*>([\s\S]*?)</a>', block)
        results.append({"url": _clean_url(link.group(1)),
                        "title": _strip_tags(link.group(2)),
                        "snippet": _strip_tags(snippet.group(1)) if snippet else ""})
    return results


def _ddg_lite(page):
    """Lite 版是一张表：链接在 result-link 单元格，摘要在 result-snippet 单元格。"""
    links = re.findall(r'<a[^>]*class=["\']result-link["\'][^>]*>([\s\S]*?)</a>', page)
    hrefs = re.findall(r'<a[^>]*class=["\']result-link["\'][^>]*href=["\']([^"\']+)["\']', page)
    snippets = re.findall(r'class=["\']result-snippet["\'][^>]*>([\s\S]*?)</td>', page)
    results = []
    for index, title in enumerate(links):
        if index >= len(hrefs):
            break
        results.append({"url": _clean_url(hrefs[index]),
                        "title": _strip_tags(title),
                        "snippet": _strip_tags(snippets[index]) if index < len(snippets) else ""})
    return results


def _searxng(query, limit, timeout, insecure, opener=None):
    """SearXNG 公共实例：先要 JSON（好解析），不行再抓 HTML 结果页。

    实例很多、全挂是常态，所以**只在这个引擎自己的时间预算内**挨个试：
    预算用完就如实说明"实例没试完"，不再一个接一个地干等。
    """
    errors = []
    started = time.time()
    per = max(2.0, min(float(timeout) / 2, SEARXNG_PER_INSTANCE))
    for base in SEARXNG_INSTANCES:
        if errors and time.time() - started >= timeout:
            errors.append("实例没试完（这个引擎的 %.0f 秒预算用完了）" % timeout)
            break
        params = urllib.parse.urlencode({"q": query, "format": "json"})
        try:
            body = _fetch("%s/search?%s" % (base, params), per, insecure, opener,
                          {"Accept": "application/json"})
            payload = json.loads(body)
            results = _dedupe(_from_json(payload), _collect(limit))
            if results:
                return results
            errors.append("%s：JSON 里没有结果" % base)
        except (SearchError, ValueError) as exc:
            errors.append("%s：%s" % (base, exc))
        try:
            html_url = "%s/search?%s" % (base, urllib.parse.urlencode({"q": query}))
            page = _fetch(html_url, per, insecure, opener)
        except SearchError as exc:
            errors.append("%s：%s" % (base, exc))
            continue
        results = _dedupe(_searxng_html(page), _collect(limit))
        if results:
            return results
        errors.append("%s：HTML 结果页也没解析到结果" % base)
    raise SearchError("所有公共 SearXNG 实例都不可用（%s）" % "；".join(errors)[:300])


def _searxng_html(page):
    results = []
    for block in re.split(r'<article[^>]*class="[^"]*result', page)[1:]:
        link = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([\s\S]*?)</a>', block)
        if not link:
            continue
        snippet = re.search(r'<p[^>]*class="[^"]*content[^"]*"[^>]*>([\s\S]*?)</p>', block)
        results.append({"url": _clean_url(link.group(1)),
                        "title": _strip_tags(link.group(2)),
                        "snippet": _strip_tags(snippet.group(1)) if snippet else ""})
    return results or _from_html(page)


# --------------------------------------------------------------------------
# 自定义搜索接口
# --------------------------------------------------------------------------

def _custom(query, endpoint, api_key, limit, timeout, insecure, opener=None):
    if "{query}" in endpoint:
        url = endpoint.replace("{query}", urllib.parse.quote(query))
    else:
        joiner = "&" if "?" in endpoint else "?"
        url = "%s%sq=%s" % (endpoint, joiner, urllib.parse.quote(query))
    if "{limit}" in url:
        url = url.replace("{limit}", str(limit))
    headers = {"Accept": "application/json, text/html;q=0.9"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    body = _fetch(url, timeout, insecure, opener, headers)

    text = body.lstrip()
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise SearchError("自定义搜索返回的不是合法 JSON：%s" % exc)
        return _rank(_dedupe(_from_json(payload), _collect(limit)), query, limit)
    return _rank(_dedupe(_from_html(body), _collect(limit)), query, limit)


def _from_json(payload):
    items = []
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "hits", "list", "organic"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        if not items and isinstance(payload.get("data"), dict):
            for key in ("results", "items", "hits"):
                if isinstance(payload["data"].get(key), list):
                    items = payload["data"][key]
                    break
    elif isinstance(payload, list):
        items = payload
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("link") or item.get("href") or ""
        title = item.get("title") or item.get("name") or ""
        snippet = (item.get("snippet") or item.get("description")
                   or item.get("content") or item.get("summary") or "")
        if isinstance(item.get("_source"), dict):
            source = item["_source"]
            url = url or source.get("url") or source.get("link") or ""
            title = title or source.get("title") or ""
            snippet = snippet or source.get("content") or source.get("description") or ""
        out.append({"url": _clean_url(url), "title": str(title), "snippet": str(snippet)})
    return out


def _from_html(page):
    results = []
    for match in re.finditer(r"""<a[^>]+href=["'](https?://[^"']+)["'][^>]*>([\s\S]{2,200}?)</a>""",
                             page):
        title = _strip_tags(match.group(2))
        if len(title) < 4:
            continue
        results.append({"url": _clean_url(match.group(1)), "title": title, "snippet": ""})
    return results
