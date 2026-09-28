#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
personalize.py - персонализация базы для cold email (задача 2 тестового задания).

Что делает:
  1. Читает CSV со списком компаний (колонки: company, site).
  2. Для каждой компании скачивает сайт (главная + «контакты»/«о компании») и извлекает текст.
  3. Отправляет текст в LLM (любой OpenAI-совместимый API) с жёстким промптом:
     один конкретный факт, 1-2 предложения, ТОЛЬКО из текста, без выдумок.
  4. Пишет результат в CSV: колонки «Персонализация», «Источник» (URL факта), «Статус».

Настройка (переменные окружения):
  LLM_BASE_URL  - по умолчанию https://api.openai.com/v1
  LLM_API_KEY   - ключ API (обязателен, если не --dry-run)
  LLM_MODEL     - модель, по умолчанию gpt-4o-mini
  Пример:  set LLM_API_KEY=sk-...  &&  python personalize.py --input base.csv --output base_done.csv

Флаги:
  --dry-run   - не звать LLM: сохранить собранный текст сайта (отладка пайплайна).
  --limit N   - обработать только первые N строк (быстрый тест).

Требования: Python 3.8+, curl (Windows 10+ / macOS / Linux - уже в системе).
"""
import argparse, csv, json, os, re, subprocess, sys, time, urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

PROMPT_TEMPLATE = (
    "Ты - ассистент B2B-аутрича. Ниже текст с сайта компании «{company}».\n"
    "Напиши один конкретный факт об этой компании, который можно вставить в первое холодное письмо:\n"
    "- 1-2 предложения, по-русски, без воды и оценок («лидер», «лучший» - нельзя);\n"
    "- ТОЛЬКО информация из приведённого текста, ничего не придумывать;\n"
    "- если в тексте нет ничего конкретного - верни ровно один символ «-».\n\n"
    "ТЕКСТ С САЙТА:\n{text}\n\nОТВЕТ (только факт, без пояснений):"
)


def fetch(url, timeout=25):
    """Скачать страницу через curl (устойчиво к кодировкам и антиботу на уровне базовых запросов)."""
    try:
        p = subprocess.run(["curl.exe", "-sL", "--compressed", "--max-time", str(timeout),
                            "-A", UA, url], capture_output=True)
        b = p.stdout
        if not b or len(b) < 200:
            return None
        for enc in ("utf-8", "cp1251"):
            try:
                t = b.decode(enc)
                if "\ufffd" not in t[:20000]:
                    return t
            except Exception:
                continue
        return b.decode("utf-8", "replace")
    except Exception:
        return None


def strip_html(html):
    """HTML -> читаемый текст."""
    t = re.sub(r"(?s)<script.*?</script>", " ", html, flags=re.I)
    t = re.sub(r"(?s)<style.*?</style>", " ", t, flags=re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def collect_site_text(company, site):
    """Собрать текст: главная + до двух страниц «контакты»/«о компании»."""
    if not site.startswith("http"):
        site = "https://" + site
    home = fetch(site)
    if not home:
        return None, None
    pages = [(site, home)]
    html_links = re.findall(r'(?is)<a\s[^>]*href=["\']([^"\']+)["\']', home)
    extra = []
    for href in html_links:
        if re.search(r"(kontakt|contact|контакт|about|о-?компани|o-?kompanii)", href, re.I):
            full = href if href.startswith("http") else site.rstrip("/") + "/" + href.lstrip("/")
            if full not in extra:
                extra.append(full)
        if len(extra) >= 2:
            break
    for u in extra:
        h = fetch(u)
        if h:
            pages.append((u, h))
        time.sleep(0.8)
    text = "\n".join(strip_html(h) for _, h in pages)
    text = text[:6000]  # ограничение контекста
    return text, site


def call_llm(company, text):
    """Запрос к OpenAI-совместимому API. Возвращает строку результата."""
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    if not key:
        raise RuntimeError("Не задан LLM_API_KEY (или запустите с --dry-run)")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": PROMPT_TEMPLATE.format(company=company, text=text)}],
        "temperature": 0.2,
        "max_tokens": 200,
    }).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def main():
    ap = argparse.ArgumentParser(description="Персонализация базы (задача 2)")
    ap.add_argument("--input", required=True, help="входной CSV: company,site")
    ap.add_argument("--output", required=True, help="выходной CSV с персонализацией")
    ap.add_argument("--dry-run", action="store_true", help="без LLM: сохранить собранный текст")
    ap.add_argument("--limit", type=int, default=0, help="ограничить число строк")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[:args.limit]

    out_rows = []
    for i, row in enumerate(rows, 1):
        company = (row.get("company") or row.get("Компания") or "").strip()
        site = (row.get("site") or row.get("Сайт") or "").strip()
        status, fact, source = "ok", "-", ""
        try:
            text, real_site = collect_site_text(company, site)
            if not text:
                status, fact = "сайт недоступен", "-"
            elif args.dry_run:
                status, fact, source = "dry-run", text[:300].replace("\n", " "), real_site or ""
            else:
                fact = call_llm(company, text)
                source = real_site or ""
                if fact.strip() in ("-", "-", ""):
                    status, fact = "нет данных", "-"
        except Exception as e:
            status, fact = "ошибка: " + str(e)[:80], "-"
        row_out = dict(row)
        row_out["Персонализация"] = fact
        row_out["Источник"] = source
        row_out["Статус"] = status
        out_rows.append(row_out)
        print(f"[{i}/{len(rows)}] {company}: {status}", flush=True)
        time.sleep(0.5)

    fieldnames = list(out_rows[0].keys()) if out_rows else ["company", "site", "Персонализация", "Источник", "Статус"]
    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)
    print("Готово:", args.output)


if __name__ == "__main__":
    main()
