"""网页抓取：通用 Crawler + Fandom/MediaWiki 专用 FandomWikiCrawler。"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Mapping, Optional
from urllib.parse import unquote, urljoin, urlparse

import requests
from requests import Response
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import config
from libs.task_queue import crawler_queue

logger = logging.getLogger(__name__)

_WIKI_PATH_RE = re.compile(r"^(?:/[a-zA-Z0-9_-]+)?/wiki/(.+)$")


class CrawlerError(RuntimeError):
    """爬虫相关错误。"""


class RetryableCrawlerError(CrawlerError):
    """值得重试的爬虫错误（超时、5xx、429 等）。"""


class Crawler:
    """简单可靠的 HTTP 抓取客户端。"""

    def __init__(
        self,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff: float | None = None,
        user_agent: str | None = None,
        headers: Optional[Mapping[str, str]] = None,
        proxies: Optional[Mapping[str, str]] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.timeout = timeout if timeout is not None else config.HTTP_TIMEOUT
        self.max_retries = (
            max_retries if max_retries is not None else config.HTTP_MAX_RETRIES
        )
        self.backoff = (
            backoff if backoff is not None else config.HTTP_RETRY_BACKOFF
        )
        self.session = session or requests.Session()
        resolved_proxies = (
            dict(proxies) if proxies is not None else config.http_proxies()
        )
        if resolved_proxies:
            self.session.proxies.update(resolved_proxies)

        default_headers = {
            "User-Agent": user_agent or config.DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if headers:
            default_headers.update(headers)
        self.session.headers.update(default_headers)
        self._session_lock = threading.Lock()

    def get(
        self,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        allow_redirects: bool = True,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> Response:
        """GET 请求，失败按指数退避重试。"""
        return self._request_with_retry(
            "GET",
            url,
            params=params,
            headers=headers,
            allow_redirects=allow_redirects,
            expected_statuses=expected_statuses,
        )

    def fetch_text(
        self,
        url: str,
        *,
        encoding: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """抓取页面并返回解码后的文本。"""
        response = self.get(url, **kwargs)
        if encoding:
            response.encoding = encoding
        elif not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    def fetch_html(self, url: str, **kwargs: Any) -> str:
        """抓取 HTML 页面内容。"""
        return self.fetch_text(url, **kwargs)

    def fetch_bytes(self, url: str, **kwargs: Any) -> bytes:
        """抓取原始字节。"""
        response = self.get(url, **kwargs)
        return response.content

    def resolve_url(self, base: str, href: str) -> str:
        """相对链接转绝对 URL。"""
        return urljoin(base, href)

    def same_host(self, url_a: str, url_b: str) -> bool:
        """两个 URL 是否同 host。"""
        return urlparse(url_a).netloc == urlparse(url_b).netloc

    def close(self) -> None:
        """关闭底层 Session。"""
        self.session.close()

    def __enter__(self) -> "Crawler":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        expected_statuses: tuple[int, ...],
        **kwargs: Any,
    ) -> Response:
        @retry(
            reraise=True,
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=self.backoff, min=1, max=20),
            retry=retry_if_exception_type(
                (requests.Timeout, requests.ConnectionError, RetryableCrawlerError)
            ),
        )
        def _do() -> Response:
            try:
                with self._session_lock:
                    response = self.session.request(
                        method,
                        url,
                        timeout=self.timeout,
                        **kwargs,
                    )
            except requests.RequestException as exc:
                hint = ""
                if isinstance(
                    exc,
                    (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError),
                ):
                    hint = (
                        "（若访问 Fandom 超时，可在 .env.local 设置 "
                        "HTTPS_PROXY=http://127.0.0.1:端口）"
                    )
                logger.warning("请求失败: %s %s: %s", method, url, exc)
                raise RetryableCrawlerError(
                    f"请求失败: {method} {url}: {exc}{hint}"
                ) from exc

            if response.status_code not in expected_statuses:
                if response.status_code >= 500 or response.status_code == 429:
                    raise RetryableCrawlerError(
                        f"可重试状态码 {response.status_code}: {method} {url}"
                    )
                raise CrawlerError(
                    f"意外状态码 {response.status_code}: {method} {url}"
                )
            return response

        return _do()


class FandomWikiCrawler:
    """
    针对 Fandom 等 MediaWiki 站点的页面抓取。
    只走 api.php?action=parse，不请求原始 /wiki/ HTML。
    """

    def __init__(self, crawler: Optional[Crawler] = None) -> None:
        self.crawler = crawler or Crawler()

    def fetch_html(self, url: str) -> str:
        """把 wiki 页面 URL 转成 MediaWiki API 请求并返回正文 HTML。"""
        try:
            return self.fetch_via_mediawiki_api(url)
        except CrawlerError as exc:
            logger.error("抓取失败：%s (%s)", url, exc)
            return ""

    def fetch_html_many(self, urls: list[str]) -> dict[str, str]:
        """把每个 URL 丢进 crawler TaskQueue，join 后汇总；失败的跳过。"""
        if not urls:
            return {}

        queue = crawler_queue()
        futures = [queue.submit(self.fetch_html, url) for url in urls]
        results: dict[str, str] = {}
        for url, html in zip(urls, queue.gather(futures)):
            if not html:
                continue
            results[url] = html
        return results

    def fetch_via_mediawiki_api(self, url: str) -> str:
        """URL 形如 .../wiki/Page_Title → api.php?action=parse。"""
        parsed, page = self._wiki_page_from_url(url)
        api_url = f"{parsed.scheme}://{parsed.netloc}/api.php"
        response = self.crawler.get(
            api_url,
            params={
                "action": "parse",
                "page": page,
                "prop": "text|displaytitle",
                "format": "json",
                "redirects": 1,
                "disabletoc": 1,
            },
            headers={
                "Accept": "application/json,text/javascript,*/*;q=0.1",
                "Referer": f"{parsed.scheme}://{parsed.netloc}/",
            },
        )
        title, body = self._parse_mediawiki_json(response, api_url, url, page)
        return self.wrap_article_html(title, body)

    @staticmethod
    def _wiki_page_from_url(url: str) -> tuple[Any, str]:
        parsed = urlparse(url)
        match = _WIKI_PATH_RE.match(parsed.path or "")
        if not match or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CrawlerError(f"不是有效的 MediaWiki 词条 URL: {url}")
        page = unquote(match.group(1))
        if not page or page.endswith(".php"):
            raise CrawlerError(f"无法从 URL 解析词条名: {url}")
        return parsed, page

    @staticmethod
    def _parse_mediawiki_json(
        response: Response,
        api_url: str,
        url: str,
        page: str,
    ) -> tuple[str, str]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise CrawlerError(f"MediaWiki API 返回非 JSON: {api_url}") from exc

        if "error" in payload:
            err = payload["error"]
            code = err.get("code", "unknown")
            info = err.get("info", str(err))
            raise CrawlerError(f"MediaWiki API 错误 ({code}): {info}")

        parse = payload.get("parse")
        if not parse:
            raise CrawlerError(f"MediaWiki API 响应缺少 parse: {url}")

        body = (parse.get("text") or {}).get("*") or ""
        if not body.strip():
            raise CrawlerError(f"MediaWiki API 返回空正文: {url}")

        raw_title = parse.get("displaytitle") or parse.get("title") or page
        title = re.sub(r"<[^>]+>", "", str(raw_title)).strip() or page
        return title, body

    @staticmethod
    def wrap_article_html(title: str, body: str) -> str:
        """包一层标准结构，便于后续按 #mw-content-text / h2 拆章。"""
        return (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            f"<title>{title}</title></head><body>"
            f"<h1>{title}</h1>"
            f'<div id="mw-content-text" class="mw-parser-output">{body}</div>'
            "</body></html>"
        )

    def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        self.crawler.close()

    def __enter__(self) -> "FandomWikiCrawler":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
