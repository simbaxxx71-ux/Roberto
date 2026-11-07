"""
Поиск ТОЛЬКО ОРИГИНАЛЬНЫХ (OEM) автозапчастей по каталожному номеру
и расчёт средней рыночной цены.

Запуск:  streamlit run app.py
"""

import re
from dataclasses import asdict
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

import parsers
from parsers import (SITES, drop_outliers, make_custom_sites, parse_blob,
                     playwright_available, search_all)

st.set_page_config(page_title="Оригинальные запчасти — поиск по артикулу",
                   page_icon="🔧", layout="wide")

STATUS_ICON = {"ok": "✅", "empty": "⚪", "blocked": "⛔", "js_required": "🧩",
               "timeout": "⏱️", "error": "❌"}
ORIGIN_LABEL = {"original": "оригинал", "unknown": "не определено",
                "other": "др. бренд", "analog": "аналог"}


def clean_article(article: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", article).upper()


@st.cache_data(ttl=1800, show_spinner=False)
def cached_search(article, car_brand, site_keys, use_js, debug, custom_text):
    return search_all(article, car_brand, list(site_keys), use_js, debug,
                      extra_sites=make_custom_sites(custom_text))


def add_offers(results):
    """Кладёт в таблицу предложения нужного типа; возвращает сколько добавлено."""
    allowed = {"original"} | ({"unknown"} if include_unknown else set())
    n = 0
    for r in results:
        for o in r.offers:
            if o.origin in allowed:
                d = asdict(o)
                d["origin"] = ORIGIN_LABEL[o.origin]
                st.session_state.offers.append(d)
                n += 1
    return n


def calculate_stats(prices):
    if not prices:
        return {}
    s = pd.Series(prices)
    return {"count": len(prices), "min": float(s.min()), "max": float(s.max()),
            "mean": float(s.mean()), "median": float(s.median())}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Парсинг")
    parsable = [c for c in SITES if not c.link_only]
    chosen = st.multiselect("Сайты для парсинга", [c.key for c in parsable],
                            default=[c.key for c in parsable if c.verified],
                            format_func=lambda k: next(c.title + ("" if c.verified else " ⚠")
                                                       for c in parsable if c.key == k),
                            help="⚠ — адрес поиска не проверен, смотрите HTTP-код в диагностике.")
    custom_text = st.text_area("Свои сайты (по одному в строке)", height=90,
                               placeholder="Мой магазин | https://site.ru/search?q={article}",
                               help="Формат: Название | URL, где вместо артикула стоит {article}. "
                                    "Селекторы не нужны — карточки ищутся автоматически.")
    car_brand = st.text_input("Бренд авто (для фильтра оригинала)",
                              placeholder="Toyota, Volkswagen, BMW…",
                              help="Если указан — оригиналом считается только этот бренд.")
    include_unknown = st.checkbox("Включать предложения без бренда", value=False)
    drop_out = st.checkbox("Отсекать выбросы цен", value=True)

    pw = playwright_available()
    use_js = st.checkbox("Режим браузера (Playwright) для JS-сайтов", value=False, disabled=not pw,
                         help="Медленнее, но берёт сайты, где цены подгружаются скриптом.")
    if not pw:
        st.caption("Playwright не установлен: `pip install playwright && playwright install chromium`")
    debug = st.checkbox("Отладка (сохранять HTML)", value=False)
    if st.button("Сбросить кэш поиска"):
        st.cache_data.clear()

    st.info("Сайты могут блокировать парсинг. Для стабильной работы лучше API "
            "(ABCP / UMAPI / PartsAPI / API самих поставщиков) и ручной ввод цен.")

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("🔧 Только оригинальные (OEM) запчасти")
st.markdown("Введите **каталожный номер** — приложение опросит выбранные сайты, "
            "оставит оригинальные предложения и посчитает среднюю цену.")

article_input = st.text_input("Каталожный номер (OEM)",
                              placeholder="04465-02280  |  1J0698151B  |  W75/3",
                              help="Пробелы и дефисы убираются автоматически.")
search_btn = st.button("🔍 Найти оригиналы", type="primary", use_container_width=True)

st.session_state.setdefault("offers", [])
st.session_state.setdefault("site_results", [])

if search_btn and article_input.strip():
    article = clean_article(article_input)
    st.session_state.current_article = article
    st.session_state.offers = []

    with st.spinner(f"Опрашиваем {len(chosen)} сайт(ов)…"):
        results = cached_search(article, car_brand.strip(), tuple(chosen), use_js, debug, custom_text)
    st.session_state.site_results = list(results)
    add_offers(results)

    if not st.session_state.offers:
        st.warning("Оригинальных предложений не найдено — смотрите диагностику ниже "
                   "или добавьте цены вручную.")

# Ссылки на сайты (всегда полезны, если парсер не сработал)
if st.session_state.get("current_article"):
    art = st.session_state.current_article
    st.subheader(f"Артикул: `{art}`")
    st.caption("Если сайт просит капчу — откройте его здесь, пройдите проверку сами и загрузите страницу ниже.")
    cols = st.columns(4)
    for i, c in enumerate(SITES + make_custom_sites(custom_text)):
        with cols[i % 4]:
            st.link_button(c.title, c.url.format(article=quote_plus(art)), use_container_width=True)

# Диагностика по сайтам
_blocked = [r.site for r in st.session_state.site_results if r.status == "blocked"]
if _blocked:
    st.error(f"Капча/блокировка: {', '.join(_blocked)}. Автоматически это не обойти (и не нужно): "
             "откройте сайт кнопкой выше, пройдите проверку, затем загрузите страницу в блоке "
             "«Загрузить страницу» ниже. Из облака Streamlit блокировок будет больше, чем с домашнего IP.")
if st.session_state.site_results:
    with st.expander("🩺 Диагностика парсинга по сайтам", expanded=not st.session_state.offers):
        rows = [{
            "Сайт": r.site,
            "Статус": f"{STATUS_ICON.get(r.status, '?')} {r.status}",
            "Стратегия": r.strategy or "—",
            "Найдено": len(r.offers),
            "Оригиналов": sum(o.origin == "original" for o in r.offers),
            "Время, с": round(r.elapsed, 1),
            "Комментарий": r.message,
        } for r in st.session_state.site_results]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        for r in st.session_state.site_results:
            if r.suggestions and r.status != "ok" or (r.suggestions and debug):
                st.markdown(f"**{r.site}** — кандидаты в `card_selectors`:")
                st.dataframe(pd.DataFrame(r.suggestions, columns=["Селектор", "Блоков", "Пример"]),
                             hide_index=True, use_container_width=True)
            if r.raw_html:
                st.download_button(f"⬇ HTML: {r.site}", r.raw_html,
                                   file_name=f"debug_{r.site}.html", key=f"dl_{r.site}")

# ---------------------------------------------------------------------------
# Ручное добавление
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader("📥 Загрузить страницу, открытую в браузере")
st.caption("Откройте выдачу сайта, пройдите капчу, затем сохраните страницу (Ctrl+S → «Веб-страница, только HTML») "
           "или выделите всё (Ctrl+A, Ctrl+C) и вставьте текст сюда.")
up_files = st.file_uploader("HTML-файлы", type=["html", "htm", "txt"], accept_multiple_files=True)
pasted = st.text_area("…или вставьте HTML / скопированный текст страницы", height=120)
if st.button("Разобрать загруженное"):
    blobs = [(f.name, f.getvalue().decode("utf-8", errors="ignore")) for f in up_files]
    if pasted.strip():
        blobs.append(("Вставленная страница", pasted))
    if not blobs:
        st.warning("Загрузите файл или вставьте содержимое.")
    parsed = [parse_blob(txt, name, car_brand.strip()) for name, txt in blobs]
    n = add_offers(parsed)
    st.session_state.site_results = list(st.session_state.site_results) + parsed
    for r in parsed:
        st.write(f"{STATUS_ICON.get(r.status, '?')} **{r.site}** — {r.message}")
    if n:
        st.success(f"Добавлено в таблицу: {n}")
    elif parsed:
        st.info("Оригиналов не добавлено. Укажите бренд авто слева или включите «Включать предложения без бренда».")

st.markdown("---")
st.subheader("➕ Добавить оригинальное предложение")
with st.form("add_offer", clear_on_submit=True):
    c1, c2, c3, c4 = st.columns([2.2, 2, 1.5, 1])
    source = c1.text_input("Магазин / источник", placeholder="Exist, Emex, дилер…")
    brand = c2.text_input("Бренд (OEM)", placeholder="Toyota, VW…")
    price = c3.number_input("Цена, ₽", min_value=0.0, step=50.0, format="%.0f")
    with c4:
        st.write("")
        st.write("")
        submitted = st.form_submit_button("Добавить", use_container_width=True)
    if submitted and price > 0:
        st.session_state.offers.append({
            "source": source or "Ручной ввод", "brand": brand or "Оригинал",
            "name": st.session_state.get("current_article", "—"),
            "price": float(price), "link": "", "origin": "оригинал", "via": "manual",
        })
        st.success("Добавлено")

# ---------------------------------------------------------------------------
# Таблица + статистика
# ---------------------------------------------------------------------------

if st.session_state.offers:
    st.markdown("---")
    st.subheader("📋 Оригинальные предложения")

    df = pd.DataFrame(st.session_state.offers)
    show = df[["source", "brand", "name", "price", "origin", "link"]].copy()
    show.columns = ["Источник", "Бренд", "Название", "Цена, ₽", "Тип", "Ссылка"]
    st.dataframe(show.sort_values("Цена, ₽"), use_container_width=True, hide_index=True,
                 column_config={"Ссылка": st.column_config.LinkColumn("Ссылка", display_text="открыть")})

    prices = [o["price"] for o in st.session_state.offers]
    used = drop_outliers(prices) if drop_out else prices
    if len(used) != len(prices):
        st.caption(f"Отсечено выбросов: {len(prices) - len(used)}")
    stats = calculate_stats(used)

    st.markdown("### 📊 Средняя рыночная цена (только оригиналы)")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Предложений", stats["count"])
    m2.metric("Минимум", f"{stats['min']:,.0f} ₽")
    m3.metric("Максимум", f"{stats['max']:,.0f} ₽")
    m4.metric("Средняя", f"{stats['mean']:,.0f} ₽")
    m5.metric("Медиана", f"{stats['median']:,.0f} ₽")
    st.bar_chart(pd.DataFrame({"Цена, ₽": sorted(used)}), height=220)

    if st.button("Очистить список"):
        st.session_state.offers = []
        st.rerun()
else:
    st.info("Введите OEM-номер и нажмите «Найти оригиналы» или добавьте цену вручную.")

st.markdown("---")
st.caption("Автоматический сбор цен приблизительный и зависит от верстки сайтов. "
           "Соблюдайте условия использования сайтов и не делайте частых запросов.")
