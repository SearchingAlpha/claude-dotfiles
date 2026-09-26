#!/usr/bin/env python3
"""
clients.py — clientes mínimos (urllib) de las fuentes gratuitas.

Brave Search API, Bing Webmaster Tools (keyword research y enlaces propios),
SerpAPI y Common Crawl. Funciones planas con prefijo por fuente; cada una
devuelve JSON ya normalizado donde procede. La cuota la contabiliza quien llama.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

from core import domain, get_json

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BING_API = "https://ssl.bing.com/webmaster/api.svc/json"
SERPAPI_URL = "https://serpapi.com/search.json"
SERPAPI_ACCOUNT = "https://serpapi.com/account"
CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"


# ------------------------------------------------------------------ Brave --

def brave_search(key: str, q: str, count: int = 20, country: str = "US",
                 lang: str = "en", offset: int = 0, timeout: int = 30) -> dict:
    """Top-N orgánico de Brave. Sin geo por ciudad: el metro va en el texto."""
    data, hdrs = get_json(BRAVE_URL,
                          {"q": q, "count": min(count, 20), "country": country,
                           "search_lang": lang, "offset": offset},
                          headers={"X-Subscription-Token": key}, timeout=timeout)
    results = ((data or {}).get("web") or {}).get("results") or []
    items = [{"rank": i + 1 + offset * 20, "url": r.get("url", ""),
              "domain": domain(r.get("url", "")), "title": r.get("title", ""),
              "description": r.get("description", ""), "type": "organic"}
             for i, r in enumerate(results)]
    return {"query": q, "items": items, "n": len(items),
            "ratelimit": {"remaining": hdrs.get("X-RateLimit-Remaining"),
                          "limit": hdrs.get("X-RateLimit-Limit")}}


# ----------------------------------------------------- Bing WMT · keywords --

def bingkw_date(d: date, style: str = "iso") -> str:
    if style == "ms":
        ms = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        return f"/Date({ms})/"
    return d.isoformat()


def bingkw_get(key: str, method: str, timeout: int = 60, **params):
    """GET sin siteUrl (los métodos de keyword no lo llevan). Devuelve r["d"].

    Verificado 2026-09-26: country en minúsculas ("us"), language "en-US",
    fechas ISO. "US"/"en" → HTTP 400 ErrorCode 8.
    """
    params["apikey"] = key
    if "country" in params:
        params["country"] = str(params["country"]).lower()
    data, _ = get_json(f"{BING_API}/{method}", params, timeout=timeout)
    return (data or {}).get("d")


def bingkw_keyword(key: str, q: str, country: str, language: str,
                   start: date, end: date, style: str = "iso"):
    return bingkw_get(key, "GetKeyword", q=q, country=country, language=language,
                      startDate=bingkw_date(start, style), endDate=bingkw_date(end, style))


def bingkw_keyword_stats(key: str, q: str, country: str, language: str):
    return bingkw_get(key, "GetKeywordStats", q=q, country=country, language=language)


def bingkw_related(key: str, q: str, country: str, language: str,
                   start: date, end: date, style: str = "iso"):
    return bingkw_get(key, "GetRelatedKeywords", q=q, country=country, language=language,
                      startDate=bingkw_date(start, style), endDate=bingkw_date(end, style))


# ------------------------------------------------------ Bing WMT · enlaces --

def bingwmt_get(key: str, site_url: str, method: str, timeout: int = 30, **params):
    params.update({"apikey": key, "siteUrl": site_url})
    data, _ = get_json(f"{BING_API}/{method}", params, timeout=timeout)
    return (data or {}).get("d")


def bingwmt_link_counts(key: str, site_url: str, page: int = 0):
    """Páginas propias con enlaces entrantes: [{Url, Count}] (paginado)."""
    return bingwmt_get(key, site_url, "GetLinkCounts", page=page) or []


def bingwmt_url_links(key: str, site_url: str, url: str, page: int = 0):
    """Enlaces entrantes a una URL propia: [{Url, AnchorText}] (paginado)."""
    return bingwmt_get(key, site_url, "GetUrlLinks", link=url, page=page) or []


# ---------------------------------------------------------------- SerpAPI --

def serpapi_search(key: str, engine: str, q: str, location: str | None,
                   num: int = 20, timeout: int = 120, **extra) -> dict:
    """SERP geolocalizada (Google/Bing). 3 reintentos en timeout (patrón serp_watch)."""
    params = {"engine": engine, "q": q, "num": num, "api_key": key}
    if location:
        params["location"] = location
    if engine == "google":
        params.update({"hl": "en", "gl": "us"})
    params.update(extra)
    for attempt in range(3):
        try:
            data, _ = get_json(SERPAPI_URL, params, timeout=timeout, retries=1)
            return data
        except RuntimeError as e:
            if "red:" not in str(e) or attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    return {}


def serpapi_left(key: str) -> int | None:
    try:
        data, _ = get_json(SERPAPI_ACCOUNT, {"api_key": key}, timeout=30, retries=1)
        return int(data.get("total_searches_left"))
    except (RuntimeError, TypeError, ValueError, AttributeError):
        return None


# ----------------------------------------------------------- Common Crawl --

def cc_latest_crawl_id() -> str:
    data, _ = get_json(CC_COLLINFO, timeout=30)
    return data[0]["id"]


def cc_lookup(crawl_id: str, dom: str, limit: int = 1) -> bool:
    """¿Hay capturas de <dom>/* en ese crawl? 404 = no hay."""
    try:
        data, _ = get_json(f"https://index.commoncrawl.org/{crawl_id}-index",
                           {"url": f"{dom}/*", "output": "json", "limit": limit},
                           timeout=60, retries=3, backoff=8.0)   # el índice da 504 a ratos
    except RuntimeError as e:
        if "HTTP 404" in str(e):
            return False
        raise
    return bool(data)
