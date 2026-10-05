#!/usr/bin/env python3
from __future__ import annotations
import asyncio, base64, html as htmlmod, json, logging, os, re, sys, time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE='https://clubecanais.com.br/'
INDEX=urljoin(BASE,'index.php')
OUT_M3U=Path('clubecanais.m3u'); OUT_JSON=Path('canais.json'); OUT_DIAG=Path('cxtv-discovery.json'); SEED=Path('canais-seed.json')
TIMEOUT=int(os.getenv('HTTP_TIMEOUT','20')); CONC=int(os.getenv('MAX_CONCURRENCY','8')); WAIT=int(os.getenv('BROWSER_WAIT_MS','1800'))
MAX_CATEGORY_CLICKS=int(os.getenv('MAX_CATEGORY_CLICKS','200'))
HEADERS={'User-Agent':os.getenv('USER_AGENT','Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36'),'Accept-Language':'pt-BR,pt;q=0.9,en;q=0.8'}
logging.basicConfig(level=logging.INFO,format='%(asctime)s | %(levelname)s | %(message)s'); log=logging.getLogger('clubecanais')

@dataclass
class Category:
    name:str; url:str
@dataclass
class Channel:
    id:str; name:str; category:str='VARIEDADES'; location:str=''; logo:str=''; stream:str=''; page:str=''; active:bool=False; status:int|None=None; validation:str=''

def clean(v:str)->str:
    v=htmlmod.unescape(v or '').replace('\\/','/').replace('\\u002F','/')
    return re.sub(r'\s+',' ',v).strip(' \t\r\n-|•')
def absolute(u:str,base=BASE)->str: return urljoin(base,htmlmod.unescape((u or '').strip()).replace('\\/','/'))
def cid(u:str)->str:
    m=re.search(r'(?:^|[?&])id=(\d+)',u); return m.group(1) if m else ''
def channel_url(u:str)->bool:
    p=urlparse(u); return p.netloc.endswith('clubecanais.com.br') and p.path.endswith('/channel.php') and bool(cid(u))
def category_url(u:str)->bool:
    p=urlparse(u); return p.netloc.endswith('clubecanais.com.br') and p.path.endswith('/index.php') and bool(re.search(r'[?&]category=\d+',u))
def normcat(v:str)->str:
    return clean(v) or 'VARIEDADES'
def looks_stream(u:str)->bool:
    if not u or not re.match(r'^https?://',u,re.I): return False
    x=unquote(htmlmod.unescape(u).replace('\\/','/')).strip('"\''); low=x.lower()
    if 'clubecanais.com.br/channel.php' in low: return False
    if any(x in low for x in ('youtube.com/watch','youtu.be/','facebook.com/','instagram.com/')): return False
    return any(x in low for x in ('.m3u8','.mpd','.m3u','/playlist','/chunklist','/manifest','.ts','/hls/','/live/','/stream',':1935/',':8080/',':8081/'))
def normstream(u,base):
    u=absolute(u,base); u=htmlmod.unescape(u).replace('\\u0026','&').replace('\\x26','&'); return u.strip('"\'')

def extract_streams(text,base):
    out=[]; soup=BeautifulSoup(text or '','html.parser')
    attrs=('src','href','data-src','data-url','data-stream','data-file','data-video','data-hls','data-hls-url','data-source','data-playlist','data-manifest')
    vals=[]
    for tag in soup.find_all(True):
        for a in attrs:
            v=tag.get(a)
            if isinstance(v,str): vals.append(v)
    vals += re.findall(r'https?://[^\s"\'<>\\]+',text or '',re.I)
    vals += re.findall(r'https?:\\/\\/[^\s"\'<>]+',text or '',re.I)
    for m in re.finditer(r'(?:file|source|src|stream|streamUrl|stream_url|videoUrl|video_url|hls|hlsUrl|url|playlist|manifest|embed)\s*[:=]\s*["\']([^"\']+)["\']',text or '',re.I): vals.append(m.group(1))
    for token in re.findall(r'[A-Za-z0-9+/]{40,}={0,2}',text or ''):
        try:
            raw=base64.b64decode(token+'===',validate=False).decode('utf-8','ignore'); vals += re.findall(r'https?://[^\s"\'<>]+',raw)
        except Exception: pass
    for v in vals:
        v=normstream(v,base)
        if looks_stream(v) and v not in out: out.append(v)
    return out

def parse_sidebar_categories(html):
    soup=BeautifulSoup(html,'html.parser'); found={}
    for a in soup.find_all('a',href=True):
        u=absolute(a['href'],INDEX)
        if category_url(u): found[u.split('#')[0]]=Category(clean(a.get_text(' ',strip=True)),u)
    return list(found.values())

def parse_category_page(html,category):
    soup=BeautifulSoup(html,'html.parser'); out={}
    for a in soup.find_all('a',href=True):
        base_url = category.url if hasattr(category, 'url') else str(category)
        u=absolute(a['href'],base_url)
        if channel_url(u):
            text=clean(a.get_text(' ',strip=True)); out[cid(u)]=Channel(cid(u),text or f'Canal {cid(u)}',normcat(category.name),page=u)
    return out

def discover_categories_http(session):
    r=session.get(INDEX,headers=HEADERS,timeout=TIMEOUT); r.raise_for_status(); cats=parse_sidebar_categories(r.text)
    log.info('Categorias encontradas na barra lateral: %d',len(cats)); return cats

def discover_channels_by_categories_http(session,cats):
    allc={}; counts={}
    # index também é uma fonte, mas a prioridade é categoria por categoria.
    for cat in cats:
        try:
            r=session.get(cat.url,headers=HEADERS,timeout=TIMEOUT); r.raise_for_status()
            got=parse_category_page(r.text,cat); counts[cat.name]=len(got)
            for k,v in got.items(): allc[k]=v
            log.info('Categoria [%s]: %d canais',cat.name,len(got))
        except Exception as e: log.warning('Categoria [%s] falhou: %s',cat.name,e)
    log.info('Canais únicos descobertos pelas categorias (HTTP): %d',len(allc)); return allc,counts

async def discover_categories_browser(cats):
    try: from playwright.async_api import async_playwright
    except ImportError: return {},{}
    allc={}; counts={}
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True); ctx=await browser.new_context(user_agent=HEADERS['User-Agent'],locale='pt-BR')
        for cat in cats:
            page=await ctx.new_page()
            try:
                await page.goto(cat.url,wait_until='domcontentloaded',timeout=60000); await page.wait_for_timeout(700)
                stable=0; last=0
                for _ in range(MAX_CATEGORY_CLICKS):
                    links=await page.locator('a[href*="channel.php?id="]').all()
                    for a in links:
                        h=await a.get_attribute('href')
                        if h:
                            u=absolute(h,cat.url).split('&')[0]
                            if channel_url(u): allc[cid(u)]=Channel(cid(u),clean(await a.get_text(' ',strip=True)) or f'Canal {cid(u)}',normcat(cat.name),page=u)
                    n=len([1 for x in allc.values() if x.category==cat.name])
                    if n==last: stable+=1
                    else: stable=0; last=n
                    buttons=page.get_by_text(re.compile(r'mostrar mais',re.I))
                    if await buttons.count()==0 or stable>=2: break
                    try:
                        await buttons.last.scroll_into_view_if_needed(); await buttons.last.click(timeout=3500); await page.wait_for_timeout(800)
                    except Exception: break
                counts[cat.name]=sum(1 for x in allc.values() if x.category==cat.name)
                log.info('Categoria [%s] navegador: %d canais',cat.name,counts[cat.name])
            except Exception as e: log.warning('Categoria navegador [%s]: %s',cat.name,e)
            finally: await page.close()
        await ctx.close(); await browser.close()
    return allc,counts

async def dynamic_channel(page,url):
    network=[]; bodies=[]
    async def response(resp):
        try:
            if looks_stream(resp.url): network.append(resp.url); return
            ct=(resp.headers.get('content-type') or '').lower()
            if resp.request.resource_type in {'xhr','fetch','script','document'} and any(x in ct for x in ('json','javascript','text','xml')):
                try:
                    b=await resp.text()
                    if b and len(b)<2500000: bodies.append(b)
                except Exception: pass
        except Exception: pass
    page.on('response',response)
    try:
        await page.goto(url,wait_until='domcontentloaded',timeout=60000); await page.wait_for_timeout(WAIT)
        for sel in ["button:has-text('Iniciar')","button:has-text('Recarregar')","text=Iniciar","text=Recarregar","video","[aria-label*='play' i]","[title*='play' i]"]:
            try:
                loc=page.locator(sel)
                for i in range(min(await loc.count(),2)):
                    try: await loc.nth(i).click(timeout=1500); await page.wait_for_timeout(900)
                    except Exception: pass
            except Exception: pass
        await page.wait_for_timeout(1200)
        html=await page.content()
        dom=await page.evaluate("""() => { const o=[]; const a=['src','href','data-src','data-url','data-stream','data-file','data-video','data-hls','data-hls-url','data-source','data-playlist','data-manifest']; for(const e of document.querySelectorAll('*')) for(const k of a){const v=e.getAttribute&&e.getAttribute(k); if(v)o.push(v)} try{for(const e of performance.getEntriesByType('resource'))o.push(e.name)}catch(e){} return o }""")
        streams=[]
        for blob in [html,*bodies]:
            for s in extract_streams(blob,url):
                if s not in streams: streams.append(s)
        for s in network+dom:
            if isinstance(s,str):
                s=normstream(s,url)
                if looks_stream(s) and s not in streams: streams.append(s)
        return html,streams
    except Exception: return '',[]
    finally:
        try: page.remove_listener('response',response)
        except Exception: pass

async def collect_dynamic(urls):
    try: from playwright.async_api import async_playwright
    except ImportError: return {}
    results={}; sem=asyncio.Semaphore(CONC)
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True); ctx=await browser.new_context(user_agent=HEADERS['User-Agent'],locale='pt-BR')
        async def one(u):
            async with sem:
                pg=await ctx.new_page()
                try: results[u]=await dynamic_channel(pg,u)
                finally: await pg.close()
        await asyncio.gather(*(one(u) for u in urls))
        await ctx.close(); await browser.close()
    return results

def check_stream(session,stream,page):
    for extra in ({'Range':'bytes=0-4095','Accept':'*/*','Referer':page,'Origin':'https://clubecanais.com.br'},{'Range':'bytes=0-4095','Accept':'application/vnd.apple.mpegurl,*/*','Referer':page},{'Accept':'*/*'}):
        try:
            r=session.get(stream,headers={**HEADERS,**extra},timeout=TIMEOUT,allow_redirects=True,stream=True); st=r.status_code; r.close()
            if st in (200,206,301,302,303,307,308): return True,st,'ok'
            if st in (401,403,429): return True,st,'soft_block'
            if st in (404,410,451): return False,st,'inactive_http'
        except requests.RequestException: pass
    return False,None,'unreachable'

def parse_channel_html(html,url,seed):
    soup=BeautifulSoup(html,'html.parser'); name=seed.name
    h=soup.find('h1')
    if h: name=clean(h.get_text(' ',strip=True)) or name
    loc=seed.location; cat=seed.category
    text=clean(soup.get_text(' ',strip=True))
    m=re.search(r'\b(.+?)\s+(VARIEDADES|MUSICA|FILMES|NOTÍCIAS|ESPORTES|KIDS|INTERNACIONAL|DESENHOS|EVANGÉLICA|CATOLICA|EDUCATIVOS|CARRO|MODA|CULINÁRIA|TEMPO|TELEVENDAS|DOCUMENTÁRIOS|AGRONEGÓCIO|CULTURA|PUBLICOS|RADIO - LIVE|GAMES TV|REDE NBX TV|ALTA DEFINIÇÃO \( HD,FHD \)|SERIADOS|NOVELA|FUTEBOL)\s+(.+?)\s+\d+\s+views\b',text,re.I)
    if m: loc=clean(m.group(3)); cat=normcat(m.group(2)) if not seed.category else seed.category
    logo=''
    for img in soup.find_all('img'):
        src=absolute(img.get('src',''),url)
        if src: logo=src; break
    return Channel(seed.id,name,cat,loc,logo,seed.stream,url,seed.active,seed.status,seed.validation)

def dedupe(items:Iterable[Channel]):
    by={}
    for c in items:
        if not c.id: continue
        old=by.get(c.id)
        if old is None or (not old.stream and c.stream) or (old.validation!='ok' and c.validation=='ok'): by[c.id]=c
    return sorted(by.values(),key=lambda x:(x.category.casefold(),x.name.casefold()))

def load_known():
    out=[]
    for p in (OUT_JSON,SEED):
        if not p.exists(): continue
        try:
            d=json.loads(p.read_text(encoding='utf-8'))
            for x in d.get('channels',[]):
                if x.get('id') and x.get('stream'): out.append(Channel(str(x['id']),clean(x.get('name','')),normcat(x.get('category','VARIEDADES')),clean(x.get('location','')),absolute(x.get('logo','')),x['stream'],absolute(x.get('page',f'channel.php?id={x["id"]}')),True,x.get('status'),'known'))
        except Exception as e: log.warning('Falha lendo %s: %s',p,e)
    return dedupe(out)

async def revalidate(session,known):
    sem=asyncio.Semaphore(CONC)
    async def one(c):
        async with sem:
            ok,st,val=await asyncio.to_thread(check_stream,session,c.stream,c.page or INDEX)
            if ok: c.active=True;c.status=st;c.validation='retained_'+val;return c
            if st in (404,410,451): return None
            c.active=True;c.status=st;c.validation='retained_uncertain';return c
    return dedupe(x for x in await asyncio.gather(*(one(c) for c in known)) if x)

def write_all(channels,discovered,cats,catcounts):
    lines=['#EXTM3U','#PLAYLIST:ClubeCanais','# Generated automatically from https://clubecanais.com.br/']; last=None
    for c in channels:
        if c.category!=last: lines.append(f'# ===== {c.category} ====='); last=c.category
        attrs=[f'tvg-name="{clean(c.name).replace(chr(34),chr(39))}"']
        if c.logo: attrs.append(f'tvg-logo="{clean(c.logo).replace(chr(34),chr(39))}"')
        attrs.append(f'group-title="{clean(c.category).replace(chr(34),chr(39))}"')
        lines.append(f'#EXTINF:-1 {" ".join(attrs)},{clean(c.name)}'); lines.append(c.stream)
    OUT_M3U.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    OUT_JSON.write_text(json.dumps({'generated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source':INDEX,'categories':cats,'discovered':discovered,'active':len(channels),'channels':[asdict(c) for c in channels]},ensure_ascii=False,indent=2),encoding='utf-8')
    OUT_DIAG.write_text(json.dumps({'generated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source':INDEX,'category_count':len(cats),'category_counts':catcounts,'discovered_channels':discovered,'active_channels':len(channels),'channels':[asdict(c) for c in channels]},ensure_ascii=False,indent=2),encoding='utf-8')

async def main():
    session=requests.Session(); session.headers.update(HEADERS)
    try: cats=discover_categories_http(session)
    except Exception as e:
        log.error('Não foi possível ler a barra de categorias: %s',e); cats=[]
    if not cats:
        known=await revalidate(session,load_known())
        if known: write_all(known,len(known),{},{}); return 0
        return 2
    http_map,counts=discover_channels_by_categories_http(session,cats)
    browser_map,bcounts=await discover_categories_browser(cats)
    for k,v in browser_map.items(): http_map[k]=v
    for k,v in bcounts.items(): counts[k]=max(counts.get(k,0),v)
    # Todas as categorias, não apenas a home.
    urls=sorted({v.page for v in http_map.values() if v.page},key=lambda u:int(cid(u) or 0))
    log.info('Total final de canais por categorias: %d',len(urls))
    dynamic=await collect_dynamic(urls); log.info('Páginas de canais renderizadas: %d',len(dynamic))
    found=[]
    for u,seed in http_map.items():
        html,streams=dynamic.get(seed.page,('',[]))
        c=seed
        if html:
            c=parse_channel_html(html,seed.page,seed)
            if not c.category or c.category=='VARIEDADES': c.category=seed.category
        candidates=[]
        if streams: candidates.extend(streams)
        if not candidates:
            candidates.extend(extract_streams(html,seed.page))
        for s in candidates:
            ok,st,val=await asyncio.to_thread(check_stream,session,s,seed.page)
            if ok: c.stream=s;c.active=True;c.status=st;c.validation=val;found.append(c);break
    found=dedupe(found)
    log.info('Canais por categorias: %d | streams novos utilizáveis: %d',len(http_map),len(found))
    known=await revalidate(session,load_known()); log.info('Canais já conhecidos mantidos: %d',len(known))
    merged=dedupe([*known,*found])
    if not merged:
        log.error('Nenhum canal disponível; playlist anterior não será substituída.'); return 3
    write_all(merged,len(http_map),{c.name:c.url for c in cats},counts)
    log.info('Playlist gerada: %d canais (%d mantidos + %d encontrados agora)',len(merged),len(known),len(found)); return 0

if __name__=='__main__': sys.exit(asyncio.run(main()))
