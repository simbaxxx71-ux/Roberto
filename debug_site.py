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

from parsers import SITES, run_site


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site", choices=[c.key for c in SITES])
    ap.add_argument("article")
    ap.add_argument("--brand", default="")
    ap.add_argument("--js", action="store_true", help="рендер через Playwright")
    a = ap.parse_args()

    cfg = next(c for c in SITES if c.key == a.site)
    article = re.sub(r"[^A-Za-z0-9]", "", a.article).upper()
    res = run_site(cfg, article, a.brand, use_js=a.js, debug=True)

    print(f"URL:      {res.url}")
    print(f"Статус:   {res.status}  ({res.message})")
    print(f"Стратегия:{res.strategy or '-'}   Время: {res.elapsed:.1f}s")
    for o in res.offers[:15]:
        print(f"  {o.price:>10,.0f} ₽ | {o.origin:<8} | {o.brand[:18]:<18} | {o.name[:50]}")

    out = Path(f"debug_{a.site}.html")
    out.write_text(res.raw_html, encoding="utf-8")
    print(f"HTML сохранён: {out} ({len(res.raw_html)} симв.)")


if __name__ == "__main__":
    main()
