"""项目配置：从环境变量 / .env / .env.local 读取。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.request import getproxies

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
SUMMARIES_DIR = DATA_DIR / "summaries"
VECTOR_STORE_DIR = DATA_DIR / "chroma"

# .env.local 覆盖 .env，便于本地密钥不进仓库
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / ".env.local", override=True)

ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_BASE_URL = os.getenv(
    "ARK_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/v3",
)
ARK_CHAT_MODEL = os.getenv("ARK_CHAT_MODEL", "deepseek-v4-pro-260425")
ARK_EMBEDDING_MODEL = os.getenv(
    "ARK_EMBEDDING_MODEL",
    "doubao-embedding-vision-251215",
)

# HTTP 爬虫默认参数
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
HTTP_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "3"))
HTTP_RETRY_BACKOFF = float(os.getenv("HTTP_RETRY_BACKOFF", "1.5"))
# Fandom 等站点在国内常需代理，例如 http://127.0.0.1:7890
HTTP_PROXY = os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or ""
HTTPS_PROXY = (
    os.getenv("HTTPS_PROXY")
    or os.getenv("https_proxy")
    or HTTP_PROXY
)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 出站任务队列：下载与 LLM 分开限速/并发
CRAWLER_REQUEST_INTERVAL = float(os.getenv("CRAWLER_REQUEST_INTERVAL", "0.5"))
CRAWLER_MAX_WORKERS = int(os.getenv("CRAWLER_MAX_WORKERS", "20"))
LLM_REQUEST_INTERVAL = float(os.getenv("LLM_REQUEST_INTERVAL", "0.2"))
LLM_MAX_WORKERS = int(os.getenv("LLM_MAX_WORKERS", "10"))

# 业务默认值（CLI / Flask 共用）
WIKI_HEADING = os.getenv("WIKI_HEADING", "h2")
ASK_TOP_K = int(os.getenv("ASK_TOP_K", "5"))
ASK_SHOW_SOURCES = False
INGEST_RESET = False

# Flask API
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_DEBUG = os.getenv("API_DEBUG", "1") == "1"


def http_proxies() -> dict[str, str] | None:
    """供 requests 使用的 proxies；优先环境变量，否则用系统代理。"""
    proxies: dict[str, str] = {}
    if HTTP_PROXY:
        proxies["http"] = HTTP_PROXY
    if HTTPS_PROXY:
        proxies["https"] = HTTPS_PROXY
    if proxies:
        return proxies

    system = getproxies()
    if system.get("http"):
        proxies["http"] = system["http"]
    if system.get("https"):
        proxies["https"] = system["https"]
    return proxies or None


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36 WorldWikiBot/0.1"
)


def setup_logging(level: str | None = None) -> None:
    """配置根 logger；CLI 入口调用一次即可。"""
    logging.basicConfig(
        level=getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def require_ark_api_key() -> str:
    if not ARK_API_KEY:
        raise RuntimeError(
            "未配置 ARK_API_KEY。请在 .env 或 .env.local 中填入密钥。"
        )
    return ARK_API_KEY
