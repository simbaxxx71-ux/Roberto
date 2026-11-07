"""
Отладка парсера одного сайта из консоли.

    python debug_site.py exist 04465-02280
    python debug_site.py emex 04465-02280 --js        # через Playwright
    python debug_site.py zzap 04465-02280 --brand Toyota

Сохраняет HTML в debug_<site>.html — откройте его, найдите контейнер одного
предложения и впишите селектор в card_selectors нужного SiteConfig (parsers.py).
"""

import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup

from parsers import SITES, classify_origin, extract, find_repeating_cards, run_site


def print_suggestions(html: str):
    cands = find_repeating_cards(BeautifulSoup(html, "html.parser"))
    if not cands:
        print("Повторяющихся блоков с ценой нет: страница пустая/JS/капча.")
        return
    print("Кандидаты в card_selectors (вставьте лучший в parsers.py):")
    for sel, n, sample, _ in cands:
        print(f"  {n:>3} шт. | {sel}\n         пример: {sample}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=[c.key for c in SITES])
    ap.add_argument("article")
    ap.add_argument("--brand", default="")
    ap.add_argument("--js", action="store_true", help="рендер через Playwright")
    ap.add_argument("--file", help="анализировать сохранённый HTML (например, скачанный из облака)")
    a = ap.parse_args()

    cfg = next(c for c in SITES if c.key == a.site)
    if a.file:
        html = Path(a.file).read_text(encoding="utf-8", errors="ignore")
        offers, strat = extract(html, cfg, "")
        print(f"Стратегия: {strat or '-'}, найдено: {len(offers)}")
        for o in offers[:15]:
            o.origin = classify_origin(o.brand, o.name, a.brand)
            print(f"  {o.price:>10,.0f} ₽ | {o.origin:<8} | {o.brand[:18]:<18} | {o.name[:50]}")
        print_suggestions(html)
        return
    article = re.sub(r"[^A-Za-z0-9]", "", a.article).upper()
    res = run_site(cfg, article, a.brand, use_js=a.js, debug=True)

    print(f"URL:      {res.url}")
    print(f"Статус:   {res.status}  ({res.message})")
    print(f"Стратегия:{res.strategy or '-'}   Время: {res.elapsed:.1f}s")
    for o in res.offers[:15]:
        print(f"  {o.price:>10,.0f} ₽ | {o.origin:<8} | {o.brand[:18]:<18} | {o.name[:50]}")

    print_suggestions(res.raw_html)
    out = Path(f"debug_{a.site}.html")
    out.write_text(res.raw_html, encoding="utf-8")
    print(f"HTML сохранён: {out} ({len(res.raw_html)} симв.)")


if __name__ == "__main__":
    main()
