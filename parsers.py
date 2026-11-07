"""
Модуль парсинга цен по сайтам.

Идея: для каждого сайта — конфиг (SiteConfig). Страница обрабатывается
несколькими стратегиями по очереди, пока одна не вернёт предложения:

  1. json_ld     — schema.org (Product/Offer) в <script type="application/ld+json">
  2. embedded    — JSON внутри страницы (__NEXT_DATA__, window.__STATE__ и т.п.)
  3. html_cards  — CSS-селекторы карточек/строк таблицы (настраиваются в SiteConfig)
  4. text        — запасной вариант: цены из текста страницы (низкая надёжность)

Для каждого сайта возвращается SiteResult со статусом (ok / empty / blocked /
js_required / timeout / error) — по нему видно, что чинить.
"""

from __future__ import annotations

import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------

MIN_PRICE, MAX_PRICE = 50.0, 1_500_000.0


@dataclass
class Offer:
    source: str
    brand: str
    name: str
    price: float
    link: str = ""
    origin: str = "unknown"  # original | unknown | other | analog
    via: str = ""            # какая стратегия нашла


@dataclass
class SiteResult:
    site: str
    url: str
    status: str = "empty"    # ok | empty | blocked | js_required | timeout | error
    strategy: str = ""
    message: str = ""
    elapsed: float = 0.0
    offers: List[Offer] = field(default_factory=list)
    raw_html: str = ""       # только в debug-режиме
    suggestions: List[Tuple[str, int, str]] = field(default_factory=list)  # (селектор, кол-во, пример)


@dataclass
class SiteConfig:
    key: str
    title: str
    url: str                                   # {article} подставляется автоматически
    card_selectors: List[str] = field(default_factory=list)
    brand_selectors: List[str] = field(default_factory=list)
    name_selectors: List[str] = field(default_factory=list)
    needs_js: bool = False                     # сайт рисует выдачу через JS
    encoding: Optional[str] = None             # принудительная кодировка (cp1251 и т.п.)
    note: str = ""
    verified: bool = True                      # False = шаблон адреса не проверен вживую
    link_only: bool = False                    # только кнопка-ссылка, без парсинга (сильная защита)


# ---------------------------------------------------------------------------
# Конфиги сайтов
# ВАЖНО: селекторы ниже — стартовые догадки. Верстка меняется, поэтому
# откройте страницу выдачи → DevTools → найдите контейнер одного предложения
# и впишите его селектор в card_selectors (см. debug_site.py).
# ---------------------------------------------------------------------------

GENERIC_CARDS = [
    "[class*='offer']", "[class*='product-item']", "[class*='search-result'] tr",
    "[data-product-id]", "[data-offer-id]", "table tr",
]

SITES: List[SiteConfig] = [
    SiteConfig(
        key="exist", title="Exist.ru",
        url="https://exist.ru/Price/?pcode={article}",
        card_selectors=["tr[class*='row']", "[class*='price-row']", *GENERIC_CARDS],
        brand_selectors=["[class*='brand']", "[class*='manufacturer']"],
        name_selectors=["[class*='name']", "[class*='descr']", "a"],
        needs_js=True,
    ),
    SiteConfig(
        key="emex", title="Emex.ru",
        url="https://emex.ru/products?detailNum={article}",
        card_selectors=GENERIC_CARDS,
        brand_selectors=["[class*='brand']", "[class*='make']"],
        name_selectors=["[class*='name']", "[class*='title']"],
        needs_js=True,  # SPA: без рендера или их API часто пусто
    ),
    SiteConfig(
        key="autodoc", title="Autodoc.ru",
        url="https://www.autodoc.ru/search?q={article}",
        card_selectors=GENERIC_CARDS,
        brand_selectors=["[class*='brand']"],
        name_selectors=["[class*='name']", "[class*='title']"],
        needs_js=True,
    ),
    SiteConfig(
        key="zzap", title="ZZap.ru",
        url="https://www.zzap.ru/public/search.aspx?searchtext={article}",
        card_selectors=["tr[class*='row']", "table tr", *GENERIC_CARDS],
        brand_selectors=["[class*='brand']", "td:nth-of-type(2)"],
        name_selectors=["[class*='name']", "td:nth-of-type(3)"],
        encoding="utf-8",
    ),
    SiteConfig(
        key="partkom", title="Part-Kom.ru",
        url="https://www.part-kom.ru/search/?q={article}",
        card_selectors=GENERIC_CARDS,
        brand_selectors=["[class*='brand']", "[class*='manufacturer']"],
        name_selectors=["[class*='name']", "[class*='title']"],
        needs_js=True,
    ),
]

# Дополнительные сайты. verified=False: шаблон адреса я не смог проверить вживую —
# если в диагностике HTTP 404, поправьте url (откройте сайт, введите артикул, скопируйте адрес
# из браузера и замените артикул на {article}).
SITES += [
    SiteConfig(key="autopiter", title="Autopiter.ru", url="https://autopiter.ru/goods/{article}",
               card_selectors=GENERIC_CARDS, needs_js=True, verified=False),
    SiteConfig(key="armtek", title="Armtek.ru", url="https://armtek.ru/search?text={article}",
               card_selectors=GENERIC_CARDS, needs_js=True, verified=False),
    SiteConfig(key="rossko", title="Rossko.ru", url="https://rossko.ru/search?text={article}",
               card_selectors=GENERIC_CARDS, needs_js=True, verified=False),
    SiteConfig(key="trast", title="Trast-Zap.ru", url="https://www.trast-zap.ru/search/?search={article}",
               card_selectors=GENERIC_CARDS, verified=False),
    SiteConfig(key="autoeuro", title="Autoeuro.ru", url="https://autoeuro.ru/search/?search={article}",
               card_selectors=GENERIC_CARDS, needs_js=True, verified=False),
    SiteConfig(key="euroauto", title="Euroauto.ru", url="https://euroauto.ru/search/?q={article}",
               card_selectors=GENERIC_CARDS, verified=False),
    # Только ссылки: у них сильная антибот-защита, парсинг почти всегда упирается в капчу
    SiteConfig(key="ozon", title="Ozon", url="https://www.ozon.ru/search/?text={article}", link_only=True),
    SiteConfig(key="wb", title="Wildberries", url="https://www.wildberries.ru/catalog/0/search.aspx?search={article}", link_only=True),
    SiteConfig(key="ymarket", title="Яндекс Маркет", url="https://market.yandex.ru/search?text={article}", link_only=True),
    SiteConfig(key="avito", title="Avito", url="https://www.avito.ru/rossiya?q={article}", link_only=True),
    SiteConfig(key="yandex", title="Яндекс (оригинал)", url="https://yandex.ru/search/?text=%22{article}%22+оригинал+купить", link_only=True),
]


def make_custom_sites(text: str) -> List[SiteConfig]:
    """Строки вида  'Название | https://site.ru/search?q={article}'  или просто URL."""
    out = []
    for i, line in enumerate((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        title, _, url = line.partition("|") if "|" in line else ("", "", line)
        url = url.strip()
        if not url.startswith(("http://", "https://")) or "{article}" not in url:
            continue
        title = title.strip() or urlparse(url).netloc
        out.append(SiteConfig(key=f"custom{i}", title=title, url=url,
                              card_selectors=GENERIC_CARDS, needs_js=True, verified=False))
    return out


# ---------------------------------------------------------------------------
# Определение «оригинал / не оригинал»
# ---------------------------------------------------------------------------

OEM_BRANDS = {
    "toyota", "lexus", "honda", "acura", "nissan", "infiniti", "mazda", "mitsubishi",
    "subaru", "suzuki", "isuzu", "daihatsu", "hyundai", "kia", "genesis", "ssangyong",
    "vag", "vw", "volkswagen", "audi", "skoda", "škoda", "seat", "cupra", "porsche",
    "bmw", "mini", "mercedes", "mercedes-benz", "smart", "opel", "vauxhall", "ford",
    "renault", "dacia", "peugeot", "citroen", "citroën", "ds", "fiat", "alfa romeo",
    "volvo", "chevrolet", "gm", "general motors", "cadillac", "jeep", "chrysler", "dodge",
    "land rover", "jaguar", "lada", "vaz", "ваз", "лада", "uaz", "уаз", "gaz", "газ",
    "haval", "chery", "geely", "great wall", "changan", "exeed", "omoda", "jetour",
    "tesla", "byd", "faw", "jac",
}
ANALOG_WORDS = ("аналог", "aftermarket", "заменитель", "неоригинал", "не оригинал",
                "replacement", "copy", "копия", "дубликат")
ORIGINAL_WORDS = ("оригинал", "original", "genuine", "oem")


def classify_origin(brand: str, name: str, car_brand: str = "") -> str:
    text = f"{brand} {name}".lower()
    b = brand.lower().strip()
    if any(w in text for w in ANALOG_WORDS):
        return "analog"
    if car_brand:
        cb = car_brand.lower().strip()
        if b:
            return "original" if cb in b or b in cb else "other"
        return "original" if any(w in text for w in ORIGINAL_WORDS) else "unknown"
    if b and any(o == b or o in b.split() or (len(o) > 3 and o in b) for o in OEM_BRANDS):
        return "original"
    if any(w in text for w in ORIGINAL_WORDS):
        return "original"
    return "other" if b else "unknown"


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

PRICE_RE = re.compile(
    r"((?:\d{1,3}(?:[\s\u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|р\.|rub)",
    re.IGNORECASE,
)
BLOCK_MARKERS = ("captcha", "recaptcha", "access denied", "cloudflare", "are you a robot",
                 "подозрительн", "проверка безопасности", "вы не робот", "доступ ограничен")
JS_MARKERS = ('id="root"', 'id="__next"', "__NEXT_DATA__", "enable javascript",
              "включите javascript", "ng-app", 'id="app"')


def to_price(v) -> Optional[float]:
    try:
        if isinstance(v, (int, float)):
            val = float(v)
        elif isinstance(v, str):
            s = re.sub(r"[\s\u00a0\u202f]", "", v).replace(",", ".")
            s = re.sub(r"[^\d.]", "", s)
            val = float(s) if s else None
        else:
            return None
    except (ValueError, TypeError):
        return None
    if val is None or not (MIN_PRICE <= val <= MAX_PRICE):
        return None
    return val


def text_prices(text: str) -> List[float]:
    out = []
    for m in PRICE_RE.findall(text):
        p = to_price(m)
        if p:
            out.append(p)
    return out


def walk(o) -> Iterator[dict]:
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def _str(v) -> str:
    if isinstance(v, dict):
        return _str(v.get("name") or v.get("title") or "")
    return str(v).strip() if v else ""


# ---------------------------------------------------------------------------
# Загрузка страниц
# ---------------------------------------------------------------------------

def fetch_plain(url: str, encoding: Optional[str], timeout: int = 15, retries: int = 2,
                warmup: bool = True) -> Tuple[int, str]:
    """Одна сессия на сайт: прогрев главной (cookies), единый User-Agent, Referer, повторы.
    Это снижает долю капч, но не гарантирует обход защиты — особенно с облачных IP."""
    sess = requests.Session()
    ua = random.choice(UA_LIST)
    u = urlparse(url)
    origin = f"{u.scheme}://{u.netloc}/"
    base = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.6",
    }
    if warmup:
        try:
            sess.get(origin, headers=base, timeout=8)
            time.sleep(random.uniform(0.4, 1.0))
        except requests.RequestException:
            pass
    headers = {**base, "Referer": origin}
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            if r.status_code in (429, 503) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            if encoding:
                r.encoding = encoding
            elif r.encoding and r.encoding.lower() in ("iso-8859-1", "latin-1"):
                r.encoding = r.apparent_encoding
            return r.status_code, r.text
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            time.sleep(1.0 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def fetch_rendered(url: str, timeout: int = 25) -> Tuple[int, str]:
    """Загрузка через Playwright (опционально, для JS-сайтов)."""
    from playwright.sync_api import sync_playwright  # pip install playwright && playwright install chromium

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=random.choice(UA_LIST), locale="ru-RU")
            page = ctx.new_page()
            resp = page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            page.wait_for_timeout(1500)
            return (resp.status if resp else 200), page.content()
        finally:
            browser.close()


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Стратегии извлечения
# ---------------------------------------------------------------------------

def strategy_json_ld(soup: BeautifulSoup, cfg: SiteConfig, url: str) -> List[Offer]:
    offers: List[Offer] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        seen_ids = set()
        for d in walk(data):
            if id(d) in seen_ids:
                continue
            t = d.get("@type")
            types = t if isinstance(t, list) else [t]
            if "Product" in types:
                raw = d.get("offers", [])
                raw = raw if isinstance(raw, list) else [raw]
                for o in raw:
                    if not isinstance(o, dict):
                        continue
                    seen_ids.add(id(o))
                    price = to_price(o.get("price") or o.get("lowPrice"))
                    if price:
                        offers.append(Offer(cfg.title, _str(d.get("brand")), _str(d.get("name")),
                                            price, o.get("url") or d.get("url") or url, via="json_ld"))
            elif ("Offer" in types or "AggregateOffer" in types):
                price = to_price(d.get("price") or d.get("lowPrice"))
                if price:
                    offers.append(Offer(cfg.title, _str(d.get("brand")), _str(d.get("name")),
                                        price, d.get("url") or url, via="json_ld"))
    return offers


PRICE_KEYS = ("price", "Price", "cost", "Cost", "minPrice", "priceRub", "price_rub")
BRAND_KEYS = ("brand", "Brand", "brandName", "manufacturer", "maker", "make", "makeName", "producer")
NAME_KEYS = ("name", "Name", "title", "Title", "description", "partName", "detailName")


def strategy_embedded(soup: BeautifulSoup, cfg: SiteConfig, url: str) -> List[Offer]:
    offers: List[Offer] = []
    blobs = []
    for tag in soup.find_all("script"):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        if tag.get("type") == "application/json" or tag.get("id") == "__NEXT_DATA__":
            try:
                blobs.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
            continue
        # window.__STATE__ = {...};
        m = re.search(r"window\.__[A-Z_]+__\s*=\s*(\{.*\})\s*;?\s*$", raw, re.S)
        if m:
            try:
                blobs.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                pass
    for blob in blobs:
        for d in walk(blob):
            price = next((to_price(d[k]) for k in PRICE_KEYS if k in d and to_price(d[k])), None)
            if not price:
                continue
            brand = next((_str(d[k]) for k in BRAND_KEYS if k in d and _str(d[k])), "")
            name = next((_str(d[k]) for k in NAME_KEYS if k in d and _str(d[k])), "")
            if not (brand or name):
                continue
            link = d.get("url") or d.get("link") or d.get("href") or ""
            offers.append(Offer(cfg.title, brand, name[:90], price,
                                urljoin(url, link) if link else url, via="embedded"))
    return offers


def _first_text(card, selectors: Iterable[str]) -> str:
    for sel in selectors:
        el = card.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if t:
                return t
    return ""


def strategy_html_cards(soup: BeautifulSoup, cfg: SiteConfig, url: str) -> List[Offer]:
    for sel in cfg.card_selectors:
        try:
            cards = soup.select(sel)
        except Exception:
            continue
        offers: List[Offer] = []
        for card in cards[:200]:
            text = card.get_text(" ", strip=True)
            prices = text_prices(text)
            if not prices or len(text) > 600:   # слишком большой контейнер — не карточка
                continue
            a = card.select_one("a[href]")
            offers.append(Offer(
                cfg.title,
                _first_text(card, cfg.brand_selectors)[:40],
                _first_text(card, cfg.name_selectors)[:90] or text[:90],
                min(prices),
                urljoin(url, a["href"]) if a else url,
                via=f"html:{sel}",
            ))
        if offers:
            return offers
    return []



# ---------------------------------------------------------------------------
# Автоопределение карточек: ищем повторяющиеся блоки, в которых есть цена
# ---------------------------------------------------------------------------

_BAD_CLASS = re.compile(r"[^A-Za-z0-9_-]")


def _selector_for(el) -> str:
    classes = [c for c in (el.get("class") or []) if c and not _BAD_CLASS.search(c) and not c[0].isdigit()]
    sel = el.name + "".join(f".{c}" for c in classes[:3])
    if not classes and el.parent is not None and el.parent.name not in ("[document]", "html", "body"):
        pc = [c for c in (el.parent.get("class") or []) if c and not _BAD_CLASS.search(c) and not c[0].isdigit()]
        if pc:
            sel = f"{el.parent.name}.{pc[0]} > {el.name}"
    return sel


def find_repeating_cards(soup: BeautifulSoup, min_repeat: int = 3, limit: int = 5):
    """Возвращает [(selector, count, sample_text, elements)] — лучшие кандидаты в карточки."""
    groups: Dict[tuple, list] = {}
    for el in soup.find_all(True):
        if el.name in ("script", "style", "html", "body", "head", "svg", "path"):
            continue
        text = el.get_text(" ", strip=True)
        if not text or len(text) > 500 or not text_prices(text):
            continue
        sig = (el.name, tuple(sorted(el.get("class") or [])),
               el.parent.name if el.parent else "")
        groups.setdefault(sig, []).append(el)

    groups = {k: v for k, v in groups.items() if len(v) >= min_repeat}
    # убираем «вложенные» группы (td внутри tr) — оставляем внешний уровень
    all_ids = {k: {id(e) for e in v} for k, v in groups.items()}
    outer = {}
    for k, els in groups.items():
        nested = 0
        for e in els:
            for anc in e.parents:
                if any(id(anc) in ids for k2, ids in all_ids.items() if k2 != k):
                    nested += 1
                    break
        if nested < len(els) / 2:
            outer[k] = els

    ranked = sorted(outer.values(), key=lambda els: (-len(els), -len(els[0].get_text())))
    out = []
    for els in ranked[:limit]:
        out.append((_selector_for(els[0]), len(els), els[0].get_text(" ", strip=True)[:90], els))
    return out


def _guess_brand(text: str) -> str:
    low = text.lower()
    for b in sorted(OEM_BRANDS, key=len, reverse=True):
        if re.search(rf"(?<![a-zа-я0-9]){re.escape(b)}(?![a-zа-я0-9])", low):
            return b.title()
    return ""


def strategy_auto_cards(soup: BeautifulSoup, cfg: SiteConfig, url: str) -> List[Offer]:
    cands = find_repeating_cards(soup)
    if not cands:
        return []
    sel, _, _, els = cands[0]
    offers = []
    for card in els[:200]:
        text = card.get_text(" ", strip=True)
        prices = text_prices(text)
        if not prices:
            continue
        a = card.select_one("a[href]")
        offers.append(Offer(
            cfg.title,
            _first_text(card, cfg.brand_selectors)[:40] or _guess_brand(text),
            _first_text(card, cfg.name_selectors)[:90] or text[:90],
            min(prices),
            urljoin(url, a["href"]) if a else url,
            via=f"auto:{sel}",
        ))
    return offers

def strategy_text(soup: BeautifulSoup, cfg: SiteConfig, url: str) -> List[Offer]:
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    prices = text_prices(soup.get_text(" ", strip=True))
    return [Offer(cfg.title, "", "(цена из текста страницы)", p, url, via="text") for p in prices[:10]]


STRATEGIES = [
    ("json_ld", strategy_json_ld),
    ("embedded", strategy_embedded),
    ("html_cards", strategy_html_cards),
    ("auto_cards", strategy_auto_cards),
    ("text", strategy_text),
]


def extract(html: str, cfg: SiteConfig, url: str) -> Tuple[List[Offer], str]:
    for name, fn in STRATEGIES:
        # каждая стратегия работает со свежим деревом (text-стратегия мутирует его)
        soup = BeautifulSoup(html, "html.parser")
        offers = fn(soup, cfg, url)
        if offers:
            return offers, name
    return [], ""


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

def dedupe(offers: List[Offer]) -> List[Offer]:
    seen, out = set(), []
    for o in offers:
        k = (o.source, o.brand.lower(), round(o.price))
        if k not in seen:
            seen.add(k)
            out.append(o)
    return out


def run_site(cfg: SiteConfig, article: str, car_brand: str = "",
             use_js: bool = False, debug: bool = False) -> SiteResult:
    url = cfg.url.format(article=quote_plus(article))
    res = SiteResult(site=cfg.title, url=url)
    t0 = time.time()
    try:
        if use_js and cfg.needs_js and playwright_available():
            code, html = fetch_rendered(url)
        else:
            code, html = fetch_plain(url, cfg.encoding)
    except requests.Timeout:
        res.status, res.message = "timeout", "Таймаут соединения"
        res.elapsed = time.time() - t0
        return res
    except Exception as e:  # noqa: BLE001
        res.status, res.message = "error", f"{type(e).__name__}: {e}"[:200]
        res.elapsed = time.time() - t0
        return res

    res.elapsed = time.time() - t0
    if debug:
        res.raw_html = html[:400_000]
    low = html[:60_000].lower()

    if code in (401, 403, 429) or (any(m in low for m in BLOCK_MARKERS) and len(html) < 20_000):
        res.status, res.message = "blocked", f"HTTP {code}: сайт блокирует/просит капчу"
        return res
    if code >= 400:
        res.status = "error"
        res.message = f"HTTP {code}" + (": неверный URL-шаблон — откройте сайт вручную и поправьте url в SiteConfig"
                                        if code == 404 else "")
        return res

    offers, strategy = extract(html, cfg, url)
    if not offers or debug or strategy in ("auto_cards", "text"):
        res.suggestions = [(s, n, t) for s, n, t, _ in
                           find_repeating_cards(BeautifulSoup(html, "html.parser"))]
    if not offers:
        if any(m.lower() in low for m in JS_MARKERS):
            res.status = "js_required"
            res.message = "Выдача рисуется JS — включите режим браузера (Playwright) или используйте API"
        else:
            res.status = "empty"
            res.message = "Цены не найдены: проверьте селекторы в SiteConfig"
        return res

    for o in offers:
        o.origin = classify_origin(o.brand, o.name, car_brand)
    res.offers = dedupe(offers)
    res.strategy = strategy
    res.status = "ok"
    res.message = f"{len(res.offers)} предложений"
    return res


def search_all(article: str, car_brand: str = "", site_keys: Optional[List[str]] = None,
               use_js: bool = False, debug: bool = False, workers: int = 5,
               extra_sites: Optional[List[SiteConfig]] = None) -> List[SiteResult]:
    cfgs = [c for c in SITES if not c.link_only and (not site_keys or c.key in site_keys)]
    cfgs += list(extra_sites or [])
    results: List[SiteResult] = []
    # Playwright-запуски тяжёлые — ограничиваем параллелизм
    max_workers = 2 if use_js else workers
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(run_site, c, article, car_brand, use_js, debug): c for c in cfgs}
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception as e:  # noqa: BLE001
                c = futs[f]
                results.append(SiteResult(c.title, c.url, "error", message=str(e)[:200]))
    order = {c.title: i for i, c in enumerate(cfgs)}
    return sorted(results, key=lambda r: order.get(r.site, 99))



# ---------------------------------------------------------------------------
# Разбор страницы, которую пользователь сам открыл/сохранил в браузере
# (так можно обойти капчу легально: вы проходите её сами)
# ---------------------------------------------------------------------------

def strategy_lines(text: str, cfg: SiteConfig) -> List[Offer]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    offers = []
    for i, line in enumerate(lines):
        prices = text_prices(line)
        if not prices:
            continue
        name = line if len(line) > 25 or i == 0 else f"{lines[i - 1]} {line}"
        offers.append(Offer(cfg.title, _guess_brand(name), name[:90], min(prices), "", via="lines"))
    return offers


def parse_blob(content: str, source: str, car_brand: str = "") -> SiteResult:
    cfg = SiteConfig(key="upload", title=source, url="", card_selectors=GENERIC_CARDS, verified=False)
    res = SiteResult(site=source, url="")
    is_html = bool(re.search(r"<(html|div|table|tr|body|script|span|a)\b", content, re.I))
    if is_html:
        offers, strat = extract(content, cfg, "")
        if not offers:
            res.suggestions = [(s, n, t) for s, n, t, _ in
                               find_repeating_cards(BeautifulSoup(content, "html.parser"))]
    else:
        offers, strat = strategy_lines(content, cfg), "lines"
    if not offers:
        res.status, res.message = "empty", "Цены в содержимом не найдены (формат: «1 234 ₽» / «1234 руб»)"
        return res
    for o in offers:
        o.origin = classify_origin(o.brand, o.name, car_brand)
    res.offers, res.strategy, res.status = dedupe(offers), strat, "ok"
    res.message = f"{len(res.offers)} предложений"
    return res


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------

def drop_outliers(prices: List[float]) -> List[float]:
    """Отсекаем явные выбросы относительно медианы (нужно ≥4 цен)."""
    if len(prices) < 4:
        return prices
    s = sorted(prices)
    med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    return [p for p in prices if 0.4 * med <= p <= 2.5 * med]
