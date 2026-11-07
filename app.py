"""
Поиск ТОЛЬКО ОРИГИНАЛЬНЫХ (OEM) автозапчастей по каталожному номеру
и расчёт средней рыночной цены.

Запуск через браузер:
    streamlit run app.py
"""

import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import quote_plus
from typing import List, Dict
import time

st.set_page_config(
    page_title="Оригинальные запчасти — поиск по артикулу",
    page_icon="🔧",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Сайты с акцентом на оригинальные (OEM) запчасти
# ---------------------------------------------------------------------------

SITES = {
    "Exist.ru (оригинал)": {
        "search_url": "https://exist.ru/Price/?pcode={article}",
        "note": "Агрегатор. Смотрите предложения с пометкой «Оригинал» / OEM.",
    },
    "Emex.ru": {
        "search_url": "https://emex.ru/products?detailNum={article}",
        "note": "Крупный поставщик. Фильтруйте по бренду производителя авто.",
    },
    "Autodoc.ru": {
        "search_url": "https://www.autodoc.ru/search?q={article}+оригинал",
        "note": "Поиск с фильтром «оригинал».",
    },
    "ZZap.ru": {
        "search_url": "https://www.zzap.ru/public/search.aspx?searchtext={article}",
        "note": "Маркетплейс. Выбирайте продавцов с оригиналом.",
    },
    "Part-Kom.ru": {
        "search_url": "https://www.part-kom.ru/search/?q={article}",
        "note": "Хороший поиск по OEM-номерам.",
    },
    "Google — только оригинал": {
        "search_url": "https://www.google.com/search?q=%22{article}%22+оригинал+OEM+запчасть+цена+-аналог+-aftermarket",
        "note": "Поиск строго по оригиналу (исключает аналоги).",
    },
}


def clean_article(article: str) -> str:
    """Очистка артикула: только буквы и цифры."""
    return re.sub(r"[^A-Za-z0-9]", "", article).upper()


def try_parse_exist_prices(article: str) -> List[Dict]:
    """Best-effort парсинг Exist.ru (часто блокируется)."""
    results = []
    try:
        url = f"https://exist.ru/Price/?pcode={quote_plus(article)}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return results

        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        price_matches = re.findall(
            r"(\d[\d\s]{0,10}(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|RUB)",
            text,
            flags=re.IGNORECASE,
        )
        for p in price_matches[:12]:
            try:
                val = float(p.replace(" ", "").replace(",", "."))
                if 80 < val < 400000:
                    results.append({
                        "source": "Exist.ru",
                        "brand": "OEM / Оригинал",
                        "name": article,
                        "price": val,
                        "currency": "RUB",
                        "link": url,
                        "is_original": True,
                    })
            except ValueError:
                continue

        seen = set()
        unique = []
        for r in results:
            if r["price"] not in seen:
                seen.add(r["price"])
                unique.append(r)
        return unique[:8]
    except Exception:
        return []


def search_web_prices(article: str) -> List[Dict]:
    """Поиск цен только по оригинальным запчастям через DuckDuckGo."""
    results = []
    try:
        # Жёсткий акцент на оригинал / OEM
        query = f'"{article}" оригинал OEM запчасть цена -аналог -aftermarket -заменитель'
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        for result in soup.select(".result")[:10]:
            title_el = result.select_one(".result__a")
            snippet_el = result.select_one(".result__snippet")
            link = title_el.get("href") if title_el else ""
            title = title_el.get_text(strip=True) if title_el else ""
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            text = f"{title} {snippet}".lower()

            # Отбрасываем явные аналоги
            if any(x in text for x in ["аналог", "aftermarket", "заменитель", "неоригинал"]):
                continue

            prices = re.findall(
                r"(\d[\d\s]{0,8}(?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|rub)",
                text,
                flags=re.IGNORECASE,
            )
            for p in prices:
                try:
                    val = float(p.replace(" ", "").replace(",", "."))
                    if 100 < val < 350000:
                        results.append({
                            "source": "Веб-поиск (OEM)",
                            "brand": "Оригинал",
                            "name": title[:70] if title else article,
                            "price": val,
                            "currency": "RUB",
                            "link": link or "",
                            "is_original": True,
                        })
                except ValueError:
                    continue

        seen = set()
        unique = []
        for r in results:
            if r["price"] not in seen:
                seen.add(r["price"])
                unique.append(r)
        return unique[:6]
    except Exception:
        return []


def calculate_stats(prices: List[float]) -> Dict:
    if not prices:
        return {}
    s = pd.Series(prices)
    return {
        "count": len(prices),
        "min": float(s.min()),
        "max": float(s.max()),
        "mean": float(s.mean()),
        "median": float(s.median()),
    }


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("🔧 Только оригинальные (OEM) запчасти")
st.markdown(
    """
Введите **каталожный номер** оригинальной запчасти.  
Приложение ищет **только оригиналы** (OEM), показывает ссылки на сайты  
и считает **среднюю рыночную цену** по найденным предложениям.
"""
)

with st.sidebar:
    st.header("Режим")
    st.success("Только оригиналы (OEM)")
    st.info(
        "Автоматический парсинг часто блокируется сайтами.\n\n"
        "Для точных данных используйте API:\n"
        "• FAPI (fapi.iisis.ru) — бесплатный пробный пакет\n"
        "• UMAPI / PartsAPI / ABCP\n\n"
        "Добавляйте найденные цены вручную — так точнее."
    )
    use_auto_search = st.checkbox("Автоматический поиск цен", value=True)
    st.markdown("---")
    st.caption("Запуск: streamlit run app.py → откроется в браузере")

article_input = st.text_input(
    "Каталожный номер (OEM)",
    placeholder="Например: 04465-02280  |  1J0698151B  |  W75/3",
    help="Пробелы и дефисы будут убраны автоматически.",
)

search_btn = st.button("🔍 Найти оригиналы", type="primary", use_container_width=True)

if "offers" not in st.session_state:
    st.session_state.offers = []

if search_btn and article_input.strip():
    article = clean_article(article_input)
    st.session_state.current_article = article
    st.session_state.offers = []

    st.subheader(f"Оригинальные предложения для: `{article}`")

    # Ссылки
    st.markdown("### 🔗 Поиск на сайтах (только оригинал)")
    cols = st.columns(3)
    for i, (name, info) in enumerate(SITES.items()):
        url = info["search_url"].format(article=quote_plus(article))
        with cols[i % 3]:
            st.link_button(name, url, use_container_width=True)
            st.caption(info["note"])

    # Автопоиск
    if use_auto_search:
        with st.spinner("Ищем оригинальные предложения..."):
            auto = []
            auto.extend(try_parse_exist_prices(article))
            time.sleep(0.4)
            auto.extend(search_web_prices(article))

        if auto:
            st.success(f"Автоматически найдено: {len(auto)} предложений")
            st.session_state.offers.extend(auto)
        else:
            st.warning(
                "Автопоиск не вернул надёжных цен (защита сайтов). "
                "Откройте ссылки выше и добавьте цены вручную."
            )

# ---------------------------------------------------------------------------
# Ручное добавление (только оригинал)
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader("➕ Добавить оригинальное предложение")

with st.form("add_offer", clear_on_submit=True):
    c1, c2, c3, c4 = st.columns([2.2, 2, 1.5, 1])
    with c1:
        source = st.text_input("Магазин / источник", placeholder="Exist, Emex, дилер...")
    with c2:
        brand = st.text_input("Бренд (OEM)", placeholder="Toyota, VW, Bosch OEM...")
    with c3:
        price = st.number_input("Цена, ₽", min_value=0.0, step=50.0, format="%.0f")
    with c4:
        st.write("")
        st.write("")
        submitted = st.form_submit_button("Добавить", use_container_width=True)

    if submitted and price > 0:
        st.session_state.offers.append({
            "source": source or "Ручной ввод",
            "brand": brand or "Оригинал",
            "name": st.session_state.get("current_article", "—"),
            "price": float(price),
            "currency": "RUB",
            "link": "",
            "is_original": True,
        })
        st.success("Добавлено")

# ---------------------------------------------------------------------------
# Таблица + статистика
# ---------------------------------------------------------------------------

if st.session_state.offers:
    st.markdown("---")
    st.subheader("📋 Оригинальные предложения")

    df = pd.DataFrame(st.session_state.offers)
    display = df[["source", "brand", "name", "price"]].copy()
    display.columns = ["Источник", "Бренд", "Артикул / название", "Цена, ₽"]
    display = display.sort_values("Цена, ₽")
    st.dataframe(display, use_container_width=True, hide_index=True)

    prices = [o["price"] for o in st.session_state.offers]
    stats = calculate_stats(prices)

    st.markdown("### 📊 Средняя рыночная цена (только оригиналы)")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Предложений", stats["count"])
    m2.metric("Минимум", f"{stats['min']:,.0f} ₽")
    m3.metric("Максимум", f"{stats['max']:,.0f} ₽")
    m4.metric("Средняя цена", f"{stats['mean']:,.0f} ₽")

    st.metric("Медиана", f"{stats['median']:,.0f} ₽")

    chart_df = pd.DataFrame({"Цена, ₽": prices})
    st.bar_chart(chart_df, height=220)

    if st.button("Очистить список"):
        st.session_state.offers = []
        st.rerun()
else:
    st.info("Введите OEM-номер и нажмите «Найти оригиналы» или добавьте цену вручную.")

st.markdown("---")
st.caption(
    "Только оригинальные запчасти (OEM). "
    "Автоматический сбор цен приблизительный. "
    "Для продакшена подключайте API каталогов и проценки поставщиков."
)
