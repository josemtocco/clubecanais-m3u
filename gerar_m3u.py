#!/usr/bin/env python3
"""Gera uma playlist M3U otimizada para SS IPTV a partir do ClubeCanais.

Uso: python gerar_m3u.py
Saída: clubecanais.m3u, cxtv-discovery.json (diagnóstico opcional), canais.json

O scraper usa HTTP primeiro e Playwright como fallback quando o conteúdo é
carregado dinamicamente. Não tenta contornar login, CAPTCHA, paywall ou
qualquer proteção de acesso.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://clubecanais.com.br/"
INDEX = urljoin(BASE, "index.php")
OUT_M3U = Path("clubecanais.m3u")
OUT_JSON = Path("canais.json")
OUT_DIAG = Path("cxtv-discovery.json")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "12"))
MIN_VALID_CHANNELS = int(os.getenv("MIN_VALID_CHANNELS", "5"))

HEADERS = {
    "User-Agent": os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("clubecanais")


@dataclass
class Channel:
    id: str
    name: str
    category: str
    location: str = ""
    logo: str = ""
    stream: str = ""
    page: str = ""
    active: bool = False
    status: int | None = None


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n-|•")


def norm_category(value: str) -> str:
    value = clean(value)
    value = re.sub(r"^Canais\s*\|\s*", "", value, flags=re.I)
    return value or "VARIEDADES"


def is_channel_url(url: str) -> bool:
    p = urlparse(url)
    return p.netloc.endswith("clubecanais.com.br") and p.path.endswith("/channel.php") and "id=" in p.query


def channel_id(url: str) -> str:
    m = re.search(r"(?:^|[?&])id=(\d+)", url)
    return m.group(1) if m else ""


def absolute(url: str, base: str = BASE) -> str:
    if not url:
        return ""
    return urljoin(base, url.strip())


def looks_like_stream(url: str) -> bool:
    if not url or not re.match(r"^https?://", url, re.I):
        return False
    low = url.lower()
    bad = ("clubecanais.com.br/channel.php", "youtube.com/watch", "youtu.be/")
    if any(x in low for x in bad):
        return False
    return any(x in low for x in (".m3u8", ".mpd", ".ts", "/live/", "/stream", "/playlist", ":80/", ":1935/"))


def extract_streams_from_html(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []

    # Atributos e tags comuns de players.
    attrs = ("src", "data-src", "data-url", "data-stream", "data-file", "data-video", "href")
    for tag in soup.find_all(True):
        for attr in attrs:
            val = tag.get(attr)
            if isinstance(val, str):
                val = absolute(val, base_url)
                if looks_like_stream(val):
                    candidates.append(val)

    # Variáveis JavaScript e JSON embutido.
    patterns = [
        r"(?:file|source|src|stream|streamUrl|stream_url|videoUrl|video_url|hls|hlsUrl)\s*[:=]\s*[\"'](https?://[^\"']+)",
        r"[\"'](https?://[^\"']+\.(?:m3u8|mpd)(?:\?[^\"']*)?)[\"']",
        r"[\"'](https?://[^\"']+(?:/live/|/stream/|/playlist)[^\"']*)[\"']",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, html, re.I):
            val = m.group(1).replace("\\/", "/")
            if looks_like_stream(val):
                candidates.append(val)

    # Iframes externos às vezes são o próprio player/stream.
    for iframe in soup.find_all("iframe"):
        src = absolute(iframe.get("src", ""), base_url)
        if looks_like_stream(src):
            candidates.append(src)

    seen = set()
    out = []
    for item in candidates:
        item = item.replace("&amp;", "&")
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_channel_page(html: str, url: str) -> Channel:
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "")
    if not title:
        title = clean(soup.title.get_text(" ", strip=True) if soup.title else "Canal")
        title = re.sub(r"\s*[-|:]\s*Clube Canais.*$", "", title, flags=re.I)

    text = clean(soup.get_text(" ", strip=True))
    category = "VARIEDADES"
    location = ""
    m = re.search(r"Categoria:\s*([^•]+?)(?:\s*•\s*(.*?))?(?:\s+Views:|$)", text, re.I)
    if m:
        category = norm_category(m.group(1))
        location = clean(m.group(2) or "")
    else:
        # Novo layout pode apresentar categoria sem o rótulo "Categoria:".
        m = re.search(r"\b(Canais\s*\|\s*[^•]+)\s*•\s*([^\s].*?)(?:\s+\d+\s+views|$)", text, re.I)
        if m:
            category = norm_category(m.group(1))
            location = clean(m.group(2))

    logo = ""
    for img in soup.find_all("img"):
        src = absolute(img.get("src", ""), url)
        if src and ("logo" in src.lower() or "uploads" in src.lower()):
            logo = src
            break
    if not logo:
        first_img = soup.find("img")
        if first_img and first_img.get("src"):
            logo = absolute(first_img.get("src"), url)

    streams = extract_streams_from_html(html, url)
    return Channel(
        id=channel_id(url), name=title or f"Canal {channel_id(url)}", category=category,
        location=location, logo=logo, stream=streams[0] if streams else "", page=url,
    )


def discover_channel_pages_http(session: requests.Session) -> list[str]:
    """Coleta links já presentes no HTML, incluindo páginas de categorias."""
    urls: set[str] = set()
    pages = [INDEX]
    # IDs das categorias visíveis no ClubeCanais; a busca também funciona caso algum ID mude.
    for cat in range(1, 80):
        pages.append(f"{INDEX}?category={cat}")

    for page in pages:
        try:
            r = session.get(page, headers=HEADERS, timeout=TIMEOUT)
            if r.ok:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    u = absolute(a["href"], page)
                    if is_channel_url(u):
                        urls.add(u.split("&")[0])
        except requests.RequestException as exc:
            log.debug("Falha ao consultar %s: %s", page, exc)
    log.info("Descobertos %d links de canais por HTTP", len(urls))
    return sorted(urls, key=lambda x: int(channel_id(x) or 0))


async def discover_channel_pages_browser() -> list[str]:
    """Fallback para listas/paginação carregadas por JavaScript."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("Playwright não instalado; fallback de navegador indisponível.")
        return []

    urls: set[str] = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=HEADERS["User-Agent"], locale="pt-BR")
        try:
            for url in (INDEX,):
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                for _ in range(100):
                    for a in await page.locator('a[href*="channel.php?id="]').all():
                        href = await a.get_attribute("href")
                        if href:
                            u = absolute(href, url)
                            if is_channel_url(u):
                                urls.add(u.split("&")[0])
                    buttons = page.get_by_text(re.compile(r"mostrar mais", re.I))
                    if await buttons.count() == 0:
                        break
                    try:
                        await buttons.last.click(timeout=3000)
                        await page.wait_for_timeout(700)
                    except Exception:
                        break
        finally:
            await browser.close()
    log.info("Descobertos %d links de canais pelo navegador", len(urls))
    return sorted(urls, key=lambda x: int(channel_id(x) or 0))


async def fetch_dynamic_channel(url: str) -> tuple[str, str]:
    """Obtém HTML renderizado quando o stream é criado por JavaScript."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return "", ""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=HEADERS["User-Agent"], locale="pt-BR")
        responses: list[str] = []
        def on_response(resp):
            u = resp.url
            if looks_like_stream(u):
                responses.append(u)
        page.on("response", on_response)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            for selector in ("text=Iniciar", "button:has-text('Iniciar')"):
                try:
                    loc = page.locator(selector)
                    if await loc.count():
                        await loc.first.click(timeout=2000)
                        await page.wait_for_timeout(4000)
                        break
                except Exception:
                    pass
            html = await page.content()
            return html, next(iter(dict.fromkeys(responses)), "")
        except Exception as exc:
            log.debug("Browser channel %s: %s", url, exc)
            return "", ""
        finally:
            await browser.close()


def check_stream(session: requests.Session, stream: str) -> tuple[bool, int | None]:
    """Teste leve: HEAD e depois GET parcial. Não baixa a mídia inteira."""
    try:
        r = session.get(
            stream,
            headers={**HEADERS, "Range": "bytes=0-2047", "Accept": "*/*"},
            timeout=TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        ok = r.status_code in (200, 206, 301, 302, 303, 307, 308)
        code = r.status_code
        r.close()
        return ok, code
    except requests.RequestException:
        return False, None


def dedupe_channels(channels: Iterable[Channel]) -> list[Channel]:
    by_id: dict[str, Channel] = {}
    by_stream: set[str] = set()
    for c in channels:
        if not c.id:
            continue
        # Um ID é a identidade principal; se aparecer novamente, conserva a versão mais completa.
        old = by_id.get(c.id)
        if old is None or (not old.stream and c.stream):
            by_id[c.id] = c
    result = []
    for c in by_id.values():
        if c.stream and c.stream in by_stream:
            continue
        if c.stream:
            by_stream.add(c.stream)
        result.append(c)
    return sorted(result, key=lambda x: (x.category.casefold(), x.name.casefold()))


def m3u_escape(value: str) -> str:
    return (value or "").replace("\n", " ").replace("\r", " ").replace('"', "'").strip()


def write_m3u(channels: list[Channel]) -> None:
    # Sintaxe simples e amplamente compatível com SS IPTV.
    lines = [
        '#EXTM3U',
        '#PLAYLIST:ClubeCanais',
        '# Generated automatically from https://clubecanais.com.br/',
    ]
    last_group = None
    for c in channels:
        group = f"{c.category}"
        if group != last_group:
            lines.append(f"# ===== {group} =====")
            last_group = group
        attrs = [f'tvg-name="{m3u_escape(c.name)}"']
        if c.logo:
            attrs.append(f'tvg-logo="{m3u_escape(c.logo)}"')
        attrs.append(f'group-title="{m3u_escape(group)}"')
        display = c.name
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{display}")
        lines.append(c.stream)
    OUT_M3U.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(channels: list[Channel], discovered: int) -> None:
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": INDEX,
        "discovered": discovered,
        "active": len(channels),
        "channels": [asdict(c) for c in channels],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_diag(channels: list[Channel], discovered: int) -> None:
    # Nome mantido por compatibilidade com projetos anteriores; é apenas diagnóstico.
    payload = {
        "source": INDEX,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "discovered_channels": discovered,
        "active_channels": len(channels),
        "channels": [{"id": c.id, "name": c.name, "category": c.category, "stream": c.stream, "active": c.active} for c in channels],
    }
    OUT_DIAG.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def main() -> int:
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = discover_channel_pages_http(session)
    if len(urls) < MIN_VALID_CHANNELS:
        urls = sorted(set(urls) | set(await discover_channel_pages_browser()), key=lambda x: int(channel_id(x) or 0))

    if not urls:
        log.error("Nenhum canal foi descoberto; não vou substituir uma playlist existente.")
        return 2

    channels: list[Channel] = []
    # HTTP assíncrono via asyncio.to_thread evita adicionar outra dependência.
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(url: str):
        async with sem:
            try:
                r = await asyncio.to_thread(session.get, url, timeout=TIMEOUT)
                if not r.ok:
                    return None
                c = parse_channel_page(r.text, url)
                if not c.stream:
                    html, browser_stream = await fetch_dynamic_channel(url)
                    if html:
                        c = parse_channel_page(html, url)
                    if browser_stream:
                        c.stream = browser_stream
                if not c.stream:
                    return None
                ok, status = await asyncio.to_thread(check_stream, session, c.stream)
                c.active, c.status = ok, status
                return c if ok else None
            except Exception as exc:
                log.debug("Canal %s falhou: %s", url, exc)
                return None

    results = await asyncio.gather(*(one(u) for u in urls))
    channels = dedupe_channels(c for c in results if c)

    log.info("Canais descobertos: %d | streams ativos: %d", len(urls), len(channels))
    if len(channels) < MIN_VALID_CHANNELS:
        log.error("Apenas %d streams ativos; limite de segurança=%d. Playlist anterior preservada.", len(channels), MIN_VALID_CHANNELS)
        return 3

    write_m3u(channels)
    write_json(channels, len(urls))
    write_diag(channels, len(urls))
    log.info("Gerado %s com %d canais", OUT_M3U, len(channels))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
