#!/usr/bin/env python3
"""Descobre os canais públicos do ClubeCanais e gera uma M3U para SS IPTV."""
from __future__ import annotations

import asyncio
import base64
import html as htmlmod
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://clubecanais.com.br/"
INDEX = urljoin(BASE, "index.php")
OUT_M3U = Path("clubecanais.m3u")
OUT_JSON = Path("canais.json")
OUT_DIAG = Path("cxtv-discovery.json")
SEED_JSON = Path("canais-seed.json")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "8"))
MIN_VALID_CHANNELS = int(os.getenv("MIN_VALID_CHANNELS", "5"))
BROWSER_WAIT_MS = int(os.getenv("BROWSER_WAIT_MS", "2500"))

HEADERS = {
    "User-Agent": os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
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
    validation: str = ""


def clean(value: str) -> str:
    value = htmlmod.unescape(value or "")
    value = value.replace("\\/", "/").replace("\\u002F", "/")
    return re.sub(r"\s+", " ", value).strip(" \t\r\n-|•")


def norm_category(value: str) -> str:
    value = clean(value)
    value = re.sub(r"^Canais\s*\|\s*", "", value, flags=re.I)
    value = re.sub(r"^\d+\s*-\s*", "", value)
    return value or "VARIEDADES"


def absolute(url: str, base: str = BASE) -> str:
    if not url:
        return ""
    return urljoin(base, htmlmod.unescape(url.strip()).replace("\\/", "/"))


def channel_id(url: str) -> str:
    m = re.search(r"(?:^|[?&])id=(\d+)", url)
    return m.group(1) if m else ""


def is_channel_url(url: str) -> bool:
    p = urlparse(url)
    return p.netloc.endswith("clubecanais.com.br") and p.path.endswith("/channel.php") and bool(channel_id(url))


def looks_like_stream(url: str) -> bool:
    if not url or not re.match(r"^https?://", url, re.I):
        return False
    u = unquote(htmlmod.unescape(url).replace("\\/", "/")).strip().strip('"\'')
    low = u.lower()
    if "clubecanais.com.br/channel.php" in low:
        return False
    if any(x in low for x in ("youtube.com/watch", "youtu.be/", "facebook.com/", "instagram.com/")):
        return False
    return (
        ".m3u8" in low or ".mpd" in low or ".m3u" in low or
        "/playlist" in low or "/chunklist" in low or "/manifest" in low or
        ".ts" in low or
        "/hls" in low or "/live/" in low or "/stream" in low or
        ":1935/" in low or ":8080/" in low or ":8081/" in low
    )


def normalize_stream(url: str, base: str) -> str:
    u = absolute(url, base)
    u = u.replace("\\/", "/")
    u = htmlmod.unescape(u)
    u = u.replace("\\u0026", "&").replace("\\x26", "&")
    return u.strip().strip('"\'')


def extract_streams_from_html(source: str, base_url: str) -> list[str]:
    """Extrai URLs de stream de HTML, atributos, JSON, JS e strings codificadas."""
    candidates: list[str] = []
    soup = BeautifulSoup(source, "html.parser")

    attrs = ("src", "data-src", "data-url", "data-stream", "data-file", "data-video",
             "data-hls", "data-hls-url", "data-source", "data-playlist", "href")
    for tag in soup.find_all(True):
        for attr in attrs:
            val = tag.get(attr)
            if isinstance(val, str):
                val = normalize_stream(val, base_url)
                if looks_like_stream(val):
                    candidates.append(val)

    # Strings HTTP completas dentro de scripts/JSON.
    url_patterns = [
        r"https?://[^\"'<>\s\\]+",
        r"https?:\\/\\/[^\"'<>\s]+",
    ]
    for pattern in url_patterns:
        for m in re.findall(pattern, source, re.I):
            val = normalize_stream(m, base_url)
            if looks_like_stream(val):
                candidates.append(val)

    # Pares chave/valor usados por players.
    key_re = r"(?:file|source|src|stream|streamUrl|stream_url|videoUrl|video_url|hls|hlsUrl|url|playlist|manifest)"
    for m in re.finditer(rf"{key_re}\s*[:=]\s*[\"']([^\"']+)[\"']", source, re.I):
        val = normalize_stream(m.group(1), base_url)
        if looks_like_stream(val):
            candidates.append(val)

    # Base64 em strings próximas de chaves de player.
    for token in re.findall(r"[A-Za-z0-9+/]{40,}={0,2}", source):
        try:
            raw = base64.b64decode(token + "===", validate=False).decode("utf-8", "ignore")
            for m in re.findall(r"https?://[^\"'<>\s]+", raw):
                val = normalize_stream(m, base_url)
                if looks_like_stream(val):
                    candidates.append(val)
        except Exception:
            pass

    # Iframes: o navegador pode revelar o stream ao carregar o player externo.
    for iframe in soup.find_all("iframe"):
        src = absolute(iframe.get("src", ""), base_url)
        if src and not src.startswith(BASE) and ("player" in src.lower() or "stream" in src.lower()):
            candidates.append(src)
        if looks_like_stream(src):
            candidates.append(src)

    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        item = normalize_stream(item, base_url)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def parse_channel_page(source: str, url: str) -> Channel:
    soup = BeautifulSoup(source, "html.parser")
    title = clean(soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "")
    if not title:
        title = clean(soup.title.get_text(" ", strip=True) if soup.title else "Canal")
        title = re.sub(r"\s*[-|:]\s*Clube Canais.*$", "", title, flags=re.I)

    text = clean(soup.get_text(" ", strip=True))
    category, location = "VARIEDADES", ""
    m = re.search(r"Categoria:\s*(?:\d+\s*-\s*)?(.+?)\s*•\s*(.+?)(?:\s+Views:|$)", text, re.I)
    if m:
        category, location = norm_category(m.group(1)), clean(m.group(2))
    else:
        # Layout atual: "NOME CATEGORIA LOCALIZAÇÃO views".
        for cat in re.findall(r"\b[A-ZÀ-Ú][A-ZÀ-Ú0-9 ()_,+\-&/.-]{2,}\b", text):
            if cat.strip() in {"VARIEDADES", "MUSICA", "FILMES", "NOTÍCIAS", "ESPORTES", "KIDS", "INTERNACIONAL", "DESENHOS", "EVANGÉLICA", "CATOLICA"}:
                category = norm_category(cat.strip())
                break

    logo = ""
    for img in soup.find_all("img"):
        src = absolute(img.get("src", ""), url)
        if src and ("logo" in src.lower() or "uploads" in src.lower()):
            logo = src
            break
    if not logo:
        img = soup.find("img")
        if img and img.get("src"):
            logo = absolute(img["src"], url)

    streams = extract_streams_from_html(source, url)
    return Channel(channel_id(url), title or f"Canal {channel_id(url)}", category, location, logo,
                   streams[0] if streams else "", url)


def discover_channel_pages_http(session: requests.Session) -> list[str]:
    urls: set[str] = set()
    pages = [INDEX] + [f"{INDEX}?category={i}" for i in range(1, 101)]
    for page_url in pages:
        try:
            r = session.get(page_url, headers=HEADERS, timeout=TIMEOUT)
            if not r.ok:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                u = absolute(a["href"], page_url).split("&")[0]
                if is_channel_url(u):
                    urls.add(u)
        except requests.RequestException as exc:
            log.debug("HTTP descoberta %s: %s", page_url, exc)
    log.info("Descobertos %d links de canais por HTTP", len(urls))
    return sorted(urls, key=lambda x: int(channel_id(x) or 0))


async def discover_channel_pages_browser() -> list[str]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []
    urls: set[str] = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=HEADERS["User-Agent"], locale="pt-BR")
        try:
            await page.goto(INDEX, wait_until="domcontentloaded", timeout=60000)
            previous = 0
            for _ in range(300):
                for a in await page.locator('a[href*="channel.php?id="]').all():
                    href = await a.get_attribute("href")
                    if href:
                        u = absolute(href, INDEX).split("&")[0]
                        if is_channel_url(u):
                            urls.add(u)
                count = len(urls)
                if count == previous:
                    # dá tempo para AJAX terminar antes de concluir que acabou
                    await page.wait_for_timeout(900)
                    for a in await page.locator('a[href*="channel.php?id="]').all():
                        href = await a.get_attribute("href")
                        if href:
                            u = absolute(href, INDEX).split("&")[0]
                            if is_channel_url(u):
                                urls.add(u)
                    if len(urls) == count:
                        break
                previous = len(urls)
                buttons = page.get_by_text(re.compile(r"mostrar mais", re.I))
                if await buttons.count() == 0:
                    break
                try:
                    await buttons.last.scroll_into_view_if_needed()
                    await buttons.last.click(timeout=5000)
                    await page.wait_for_timeout(900)
                except Exception:
                    break
        finally:
            await browser.close()
    log.info("Descobertos %d links de canais pelo navegador", len(urls))
    return sorted(urls, key=lambda x: int(channel_id(x) or 0))


async def fetch_dynamic_channel(page, url: str) -> tuple[str, list[str]]:
    """Renderiza a página e captura streams tanto das URLs quanto do conteúdo das respostas XHR/fetch."""
    network: list[str] = []
    response_bodies: list[str] = []

    async def on_response(resp):
        try:
            u = resp.url
            if looks_like_stream(u):
                network.append(u)
                return
            # O player atual pode receber a URL do stream dentro de JSON/texto de uma API.
            ctype = (resp.headers.get("content-type") or "").lower()
            if any(x in ctype for x in ("json", "javascript", "text", "xml")):
                if resp.request.resource_type in {"xhr", "fetch", "script", "document"}:
                    try:
                        body = await resp.text()
                        if body and len(body) < 2_000_000:
                            response_bodies.append(body)
                    except Exception:
                        pass
        except Exception:
            pass

    page.on("response", on_response)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(BROWSER_WAIT_MS)

        # O layout atual mostra os botões Iniciar/Recarregar quando o player ainda não iniciou.
        for selector in [
            "button:has-text('Iniciar')", "button:has-text('Recarregar')",
            "text=Iniciar", "text=Recarregar",
            "video", "[aria-label*='play' i]", "[title*='play' i]",
        ]:
            try:
                loc = page.locator(selector)
                n = await loc.count()
                for i in range(min(n, 2)):
                    try:
                        await loc.nth(i).click(timeout=2000)
                        await page.wait_for_timeout(1500)
                    except Exception:
                        pass
            except Exception:
                pass

        # Aguarda mais um pouco para XHR/fetch do player.
        await page.wait_for_timeout(1800)
        html = await page.content()
        dom_urls = await page.evaluate("""() => {
            const out = [];
            const attrs = ['src','href','data-src','data-url','data-stream','data-file','data-hls','data-hls-url','data-source','data-playlist','data-video'];
            for (const el of document.querySelectorAll('*')) {
                for (const a of attrs) {
                    const v = el.getAttribute && el.getAttribute(a);
                    if (v) out.push(v);
                }
            }
            try { for (const e of performance.getEntriesByType('resource')) out.push(e.name); } catch(e) {}
            try {
                for (const s of document.scripts) if (s.textContent) out.push(s.textContent);
            } catch(e) {}
            return out;
        }""")

        streams: list[str] = []
        blobs = [html, *response_bodies]
        for blob in blobs:
            for u in extract_streams_from_html(blob, url):
                if u not in streams:
                    streams.append(u)
        for u in network + dom_urls:
            if isinstance(u, str):
                u = normalize_stream(u, url)
                if looks_like_stream(u) and u not in streams:
                    streams.append(u)
        return html, streams
    except Exception as exc:
        log.debug("Browser channel %s: %s", url, exc)
        return "", []
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass


async def collect_dynamic_channels(urls: list[str]) -> dict[str, tuple[str, list[str]]]:
    """Usa um único Chromium e várias abas, evitando abrir um navegador por canal."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {}

    results: dict[str, tuple[str, list[str]]] = {}
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=HEADERS["User-Agent"], locale="pt-BR")

        async def one(url: str):
            async with sem:
                page = await context.new_page()
                try:
                    result = await fetch_dynamic_channel(page, url)
                    results[url] = result
                finally:
                    await page.close()

        await asyncio.gather(*(one(u) for u in urls))
        await context.close()
        await browser.close()
    return results

def check_stream(session: requests.Session, stream: str, page_url: str) -> tuple[bool, int | None, str]:
    """Validação tolerante: rejeita erros definitivos, mas não elimina CDNs que bloqueiam HEAD/robôs."""
    variants = [
        {"Range": "bytes=0-4095", "Accept": "*/*", "Referer": page_url, "Origin": "https://clubecanais.com.br"},
        {"Range": "bytes=0-4095", "Accept": "application/vnd.apple.mpegurl,*/*", "Referer": page_url},
        {"Accept": "*/*"},
    ]
    last_status = None
    saw_soft_block = False
    for extra in variants:
        try:
            r = session.get(stream, headers={**HEADERS, **extra}, timeout=TIMEOUT, allow_redirects=True, stream=True)
            last_status = r.status_code
            r.close()
            if r.status_code in (200, 206, 301, 302, 303, 307, 308):
                return True, r.status_code, "ok"
            if r.status_code in (401, 403, 429):
                saw_soft_block = True
                continue
            if r.status_code in (404, 410, 451):
                return False, r.status_code, "inactive_http"
        except requests.RequestException:
            continue
    if saw_soft_block:
        # O endereço existe, mas o servidor exige contexto/cabeçalhos que o runner pode não ter.
        return True, last_status, "unknown_blocked"
    return False, last_status, "unreachable"


def dedupe_channels(channels: Iterable[Channel]) -> list[Channel]:
    by_id: dict[str, Channel] = {}
    for c in channels:
        if not c.id or not c.stream:
            continue
        old = by_id.get(c.id)
        if old is None or (old.validation != "ok" and c.validation == "ok"):
            by_id[c.id] = c
    by_stream: dict[str, Channel] = {}
    for c in by_id.values():
        if c.stream not in by_stream:
            by_stream[c.stream] = c
    return sorted(by_stream.values(), key=lambda x: (x.category.casefold(), x.name.casefold()))


def m3u_escape(value: str) -> str:
    return (value or "").replace("\n", " ").replace("\r", " ").replace('"', "'").strip()


def write_m3u(channels: list[Channel]) -> None:
    lines = ["#EXTM3U", "#PLAYLIST:ClubeCanais", "# Generated automatically from https://clubecanais.com.br/"]
    last_group = None
    for c in channels:
        group = c.category or "VARIEDADES"
        if group != last_group:
            lines.append(f"# ===== {group} =====")
            last_group = group
        attrs = [f'tvg-name="{m3u_escape(c.name)}"']
        if c.logo:
            attrs.append(f'tvg-logo="{m3u_escape(c.logo)}"')
        attrs.append(f'group-title="{m3u_escape(group)}"')
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{m3u_escape(c.name)}")
        lines.append(c.stream)
    OUT_M3U.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(channels: list[Channel], discovered: int) -> None:
    OUT_JSON.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": INDEX, "discovered": discovered, "active": len(channels),
        "channels": [asdict(c) for c in channels],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def write_diag(channels: list[Channel], discovered: int) -> None:
    OUT_DIAG.write_text(json.dumps({
        "source": INDEX,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "discovered_channels": discovered, "active_channels": len(channels),
        "channels": [{"id": c.id, "name": c.name, "category": c.category, "stream": c.stream,
                      "active": c.active, "status": c.status, "validation": c.validation} for c in channels],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load_known_channels() -> list[Channel]:
    """Carrega canais da última execução e do seed.

    Eles não são descartados apenas porque a descoberta/extração desta execução
    falhou. O stream conhecido é revalidado; somente respostas definitivas
    (404/410/451) fazem o canal deixar de ser considerado ativo.
    """
    items: list[Channel] = []
    for path, validation_name in ((OUT_JSON, "previous_known"), (SEED_JSON, "seed")):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("channels", []):
                stream = normalize_stream(item.get("stream", ""), BASE)
                cid = str(item.get("id", ""))
                if not cid or not stream:
                    continue
                items.append(Channel(
                    id=cid,
                    name=clean(item.get("name", f"Canal {cid}")),
                    category=norm_category(item.get("category", "VARIEDADES")),
                    location=clean(item.get("location", "")),
                    logo=absolute(item.get("logo", ""), BASE),
                    stream=stream,
                    page=absolute(item.get("page", f"channel.php?id={cid}"), BASE),
                    active=True,
                    status=item.get("status"),
                    validation=validation_name,
                ))
        except Exception as exc:
            log.warning("Não foi possível carregar %s: %s", path, exc)
    # Deduplica por ID/stream sem depender da validação anterior.
    return dedupe_channels(items)


async def revalidate_known_channels(session: requests.Session, known: list[Channel]) -> list[Channel]:
    """Mantém os canais já conhecidos enquanto seus streams não derem erro definitivo."""
    if not known:
        return []
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(c: Channel):
        async with sem:
            ok, status, validation = await asyncio.to_thread(check_stream, session, c.stream, c.page or INDEX)
            if ok:
                c.active = True
                c.status = status
                c.validation = f"retained_{validation}"
                return c
            # 404/410/451: removemos somente este canal; uma falha transitória
            # de rede não deve apagar um canal que já funcionava.
            if status in (404, 410, 451):
                log.info("Canal removido por stream definitivamente indisponível: %s (%s)", c.name, status)
                return None
            c.active = True
            c.status = status
            c.validation = "retained_uncertain"
            return c

    return dedupe_channels(c for c in await asyncio.gather(*(one(c) for c in known)) if c)


async def main() -> int:
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1) Sempre tenta descobrir TODOS os canais novamente.
    urls = discover_channel_pages_http(session)
    browser_urls = await discover_channel_pages_browser()
    urls = sorted(set(urls) | set(browser_urls), key=lambda x: int(channel_id(x) or 0))
    if not urls:
        log.error("Nenhum canal descoberto nesta execução; mantendo canais conhecidos.")
        known = await revalidate_known_channels(session, load_known_channels())
        if known:
            write_m3u(known)
            write_json(known, 0)
            write_diag(known, 0)
            return 0
        return 2

    log.info("Total final de páginas de canais: %d", len(urls))

    # 2) Renderiza novamente todos os canais para tentar descobrir streams novos.
    dynamic = await collect_dynamic_channels(urls)
    log.info("Páginas renderizadas dinamicamente: %d", len(dynamic))

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(url: str):
        async with sem:
            try:
                r = await asyncio.to_thread(session.get, url, headers=HEADERS, timeout=TIMEOUT)
                if not r.ok:
                    return None
                c = parse_channel_page(r.text, url)

                candidates: list[str] = []
                if c.stream:
                    candidates.append(c.stream)
                dyn_html, dyn_streams = dynamic.get(url, ("", []))
                if dyn_html:
                    parsed = parse_channel_page(dyn_html, url)
                    if parsed.name and not parsed.name.lower().startswith("canal "):
                        c.name = parsed.name
                    if parsed.category != "VARIEDADES" or c.category == "VARIEDADES":
                        c.category = parsed.category
                    if parsed.location:
                        c.location = parsed.location
                    if parsed.logo:
                        c.logo = parsed.logo
                for stream in dyn_streams:
                    if stream not in candidates:
                        candidates.append(stream)

                # Tenta todos os candidatos. Um candidato inválido não encerra
                # a busca do canal.
                for stream in candidates:
                    ok, status, validation = await asyncio.to_thread(check_stream, session, stream, url)
                    if ok:
                        c.stream, c.active, c.status, c.validation = stream, True, status, validation
                        return c
                return None
            except Exception as exc:
                log.debug("Canal %s falhou: %s", url, exc)
                return None

    results = await asyncio.gather(*(one(u) for u in urls))
    newly_found = dedupe_channels(c for c in results if c)
    log.info("Canais descobertos: %d | streams novos utilizáveis: %d", len(urls), len(newly_found))

    # 3) NÃO substitui a playlist pelos resultados desta execução.
    # Revalida os canais já conhecidos e acrescenta os novos.
    known = load_known_channels()
    retained = await revalidate_known_channels(session, known)
    log.info("Canais conhecidos mantidos após revalidação: %d", len(retained))

    # 4) Novo canal encontrado = acrescenta. Canal antigo sem stream nesta
    # execução = continua na playlist se o stream conhecido ainda responder.
    channels = dedupe_channels([*retained, *newly_found])

    if not channels:
        log.error("Nenhum canal conhecido ou novo pôde ser mantido.")
        write_json([], len(urls))
        write_diag([], len(urls))
        return 3

    write_m3u(channels)
    write_json(channels, len(urls))
    write_diag(channels, len(urls))
    log.info("Gerado %s com %d canais (%d conhecidos + %d novos antes da deduplicação)",
             OUT_M3U, len(channels), len(retained), len(newly_found))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
