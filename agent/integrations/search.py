"""网页搜索与网页读取。

- 搜索：默认 DuckDuckGo（无需 Key）；配置 TAVILY_API_KEY 后改用 Tavily，结果更稳定。
- 读取网页：只允许访问公网地址，禁止本机/内网（防止被网页内容诱导访问本地服务）。
"""

import gzip
import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import threading
import time
import urllib.request
import zlib
from typing import Any, Dict, List, Tuple

from . import IntegrationError, html_to_text, truncate

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_BYTES = 2 * 1024 * 1024


def web_search(cfg, query: str, max_results: int = 5) -> List[Dict[str, str]]:
    query = (query or "").strip()
    if not query:
        raise ValueError("搜索词不能为空")
    max_results = max(1, min(int(max_results), 10))
    if cfg is not None and cfg.tavily_key:
        return _tavily(cfg.tavily_key, query, max_results)
    return _duckduckgo(query, max_results)


def _tavily(key: str, query: str, n: int) -> List[Dict[str, str]]:
    payload = json.dumps({"api_key": key, "query": query, "max_results": n}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search", data=payload, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise IntegrationError("Tavily 搜索失败：{}".format(e))
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": truncate(i.get("content", ""), 300)} for i in data.get("results", [])]


def _clean_ddg(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    p = urllib.parse.urlparse(href)
    if p.netloc.endswith("duckduckgo.com"):
        if p.path.startswith("/l/"):
            target = urllib.parse.parse_qs(p.query).get("uddg")
            return target[0] if target else ""
        return ""  # 广告或站内链接
    return href


_ddg_lock = threading.Lock()
_ddg_last = [0.0]
_cache: Dict[Tuple[str, int], Tuple[float, List[Dict[str, str]]]] = {}
DDG_MIN_INTERVAL = 2.0   # DuckDuckGo 对连续请求会拦截，两次搜索至少间隔 2 秒
CACHE_SECONDS = 600


def _duckduckgo(query: str, n: int) -> List[Dict[str, str]]:
    key = (query.lower(), n)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    # 多个智能体同时搜索时排队，并保持间隔；被拦截时等一会儿重试一次
    with _ddg_lock:
        for attempt in range(2):
            wait = DDG_MIN_INTERVAL - (time.time() - _ddg_last[0])
            if wait > 0:
                time.sleep(wait)
            try:
                results = _duckduckgo_once(query, n)
            except _Blocked:
                _ddg_last[0] = time.time()
                if attempt == 0:
                    time.sleep(5)
                    continue
                raise IntegrationError("DuckDuckGo 暂时拦截了搜索，请稍后再试，"
                                       "或在 .env 配置 TAVILY_API_KEY")
            finally:
                _ddg_last[0] = time.time()
            _cache[key] = (time.time(), results)
            return results
    return []


class _Blocked(Exception):
    pass


def _duckduckgo_once(query: str, n: int) -> List[Dict[str, str]]:
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/",
        data=urllib.parse.urlencode({"q": query}).encode("utf-8"),
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            page = r.read(MAX_BYTES).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise IntegrationError("搜索失败：{}".format(e))

    starts = [m.start() for m in re.finditer(r'<a\b[^>]*class="result__a"', page)]
    results = []
    for i, s in enumerate(starts):
        chunk = page[s: starts[i + 1] if i + 1 < len(starts) else s + 6000]
        m = re.match(r'(<a\b[^>]*>)(.*?)</a>', chunk, re.S)
        if not m:
            continue
        href = re.search(r'href="([^"]+)"', m.group(1))
        url = _clean_ddg(href.group(1)) if href else ""
        if not url:
            continue
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div|td)>', chunk, re.S)
        results.append({"title": html_to_text(m.group(2)), "url": url,
                        "snippet": html_to_text(snip.group(1)) if snip else ""})
        if len(results) >= n:
            break
    if not results and ("anomaly" in page or "captcha" in page.lower()):
        raise _Blocked()
    return results


def _check_public(url: str) -> None:
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise IntegrationError("只支持 http/https 网址")
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise IntegrationError("无法解析域名：" + p.hostname)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise IntegrationError("出于安全考虑，不允许访问本机或内网地址")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


def fetch_url(url: str, max_chars: int = 6000) -> Dict[str, Any]:
    url = (url or "").strip()
    _check_public(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,text/plain,application/json;q=0.9",
        "Accept-Encoding": "gzip, deflate"})
    try:
        with _opener.open(req, timeout=20) as r:
            ctype = r.headers.get_content_type()
            if not (ctype.startswith("text/") or ctype in ("application/json", "application/xhtml+xml")):
                raise IntegrationError("不支持的内容类型：" + ctype)
            raw = r.read(MAX_BYTES)
            encoding = (r.headers.get("Content-Encoding") or "").lower()
            charset = r.headers.get_content_charset()
            final_url = r.geturl()
        if encoding in ("gzip", "x-gzip"):
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        elif encoding and encoding != "identity":
            raise IntegrationError("不支持的压缩格式：" + encoding)
    except IntegrationError:
        raise
    except urllib.error.HTTPError as e:
        raise IntegrationError("网页返回错误 {}".format(e.code))
    except (urllib.error.URLError, OSError, ValueError, EOFError, zlib.error) as e:
        raise IntegrationError("打开网页失败：{}".format(e))

    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
        charset = m.group(1).decode("ascii") if m else "utf-8"
    try:
        text = raw.decode(charset, "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    title = ""
    if "html" in ctype:
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        title = html_to_text(m.group(1)) if m else ""
        text = html_to_text(text)
    return {"url": final_url, "title": title, "content": truncate(text, max_chars)}
