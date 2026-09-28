#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_emails.py - сбор контактов с сайтов компаний (задача 1 тестового задания).
Вход:  candidates.txt (по одному домену в строке)
Выход: crawl/<domain>.json + crawl_summary.tsv
Логика: главная -> страницы «контакты»/«о компании» -> регуляркой вытаскиваем email, телефоны,
        заголовок, текстовый сниппет и «факты» для персонализации (задача 2).
Запуск: python collect_emails.py
"""
import subprocess, re, json, time, os, sys, io
from urllib.parse import urljoin, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(BASE), "data")
OUT  = os.path.join(DATA, "crawl")
os.makedirs(OUT, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+7|8)[\s\-\(]?\d{3}[\)\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
NAME_RE  = re.compile(r"(?:Генеральный директор|Генеральный Директор|Директор|Основатель|"
                      r"Коммерческий директор|Руководитель отдела продаж)\s*[:\---]?\s*"
                      r"([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)")
FACT_KEYS = ("компани", "завод", "производ", "поставщик", "основан", "является", "занимается",
             "специализир", "работает", "более лет", "лет на рынке")

BAD_EMAIL_PARTS = ("@sentry", "@wixpress", "@example", "@test", "@mail.wix", "u003", "@sentry.io")
BAD_EMAIL_END = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".css", ".js")

def fetch(url, timeout=25):
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
    t = re.sub(r"(?s)<script.*?</script>", " ", html, flags=re.I)
    t = re.sub(r"(?s)<style.*?</style>", " ", t, flags=re.I)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()

def get_title(html):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if not m:
        return ""
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t[:200]

def find_contact_links(html, base_url):
    links, seen = [], set()
    for m in re.finditer(r'(?is)<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html):
        href = m.group(1).strip()
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        text = re.sub(r"\s+", " ", text).strip().lower()
        hay = (href + " " + text).lower()
        if not re.search(r"(kontakt|contact|контакт|about|о-?компани|o-?kompanii|company|rekvizit|реквизит)", hay):
            continue
        full = urljoin(base_url, href)
        if full in seen or "javascript:" in full or "mailto:" in full:
            continue
        if urlparse(full).netloc.replace("www.", "") != urlparse(base_url).netloc.replace("www.", ""):
            continue
        seen.add(full)
        # приоритет: контакты > о компании
        pr = 0 if re.search(r"(kontakt|contact|контакт)", hay) else 1
        links.append((pr, full))
    links.sort(key=lambda x: x[0])
    return [u for _, u in links][:2]

def sentences_with_facts(text, limit=6):
    parts = re.split(r"(?<=[.!?])\s+|\n", text)
    out = []
    for s in parts:
        s = s.strip()
        if 40 <= len(s) <= 300 and any(k in s.lower() for k in FACT_KEYS):
            if s not in out:
                out.append(s)
        if len(out) >= limit:
            break
    return out

def clean_emails(raw):
    out = []
    for e in raw:
        e = e.strip().strip(".,;:").lower()
        if any(b in e for b in BAD_EMAIL_PARTS):
            continue
        if e.endswith(BAD_EMAIL_END):
            continue
        if len(e) > 60:
            continue
        if e not in out:
            out.append(e)
    # сортировка: сначала корпоративные, sales/mail/info - выше
    def score(e):
        s = 0
        local = e.split("@")[0]
        if local in ("sales", "sale", "info", "mail", "zakaz", "market"): s -= 10
        if "sales" in local or "info" in local: s -= 5
        if any(fr in e for fr in ("gmail.", "mail.ru", "yandex", "bk.ru", "list.ru", "inbox", "126.com", "163.com", "qq.com")): s += 3
        return s
    out.sort(key=score)
    return out

def process(domain):
    res = {"domain": domain, "ok": False, "site": None, "title": "", "emails": [],
           "phones": [], "names": [], "facts": [], "snippet": "", "pages": []}
    base = None
    for scheme in ("https://", "http://"):
        html = fetch(scheme + domain)
        if html:
            base = scheme + domain
            break
    if not base:
        return res
    res["ok"] = True
    res["site"] = base

    pages_to_fetch = [base]
    home_html = fetch(base)
    if not home_html:
        return res
    res["pages"].append(base)
    pages_to_fetch += find_contact_links(home_html, base)

    all_html = [(base, home_html)]
    for u in pages_to_fetch[1:]:
        h = fetch(u)
        if h:
            all_html.append((u, h))
            res["pages"].append(u)
        time.sleep(1.0)

    emails, phones, names, facts = [], [], [], []
    for u, h in all_html:
        if not res["title"]:
            res["title"] = get_title(h)
        text_full = strip_html(h)
        emails += EMAIL_RE.findall(text_full)
        # mailto в href
        for m in re.finditer(r'href=["\']mailto:([^"\']+)', h, re.I):
            emails.append(m.group(1).split("?")[0])
        phones += PHONE_RE.findall(text_full)
        names += NAME_RE.findall(text_full)
        if len(facts) < 6:
            facts += sentences_with_facts(text_full, 6 - len(facts))
        if not res["snippet"] and len(text_full) > 200:
            res["snippet"] = text_full[:1200]

    res["emails"] = clean_emails(emails)
    seen_p, uniq_p = set(), []
    for p in phones:
        p = re.sub(r"\s+", " ", p)
        if p not in seen_p:
            seen_p.add(p); uniq_p.append(p)
    res["phones"] = uniq_p[:3]
    seen_n, uniq_n = set(), []
    for n in names:
        if n not in seen_n:
            seen_n.add(n); uniq_n.append(n)
    res["names"] = uniq_n[:3]
    res["facts"] = facts[:6]
    return res

def main():
    lst = os.path.join(DATA, "candidates.txt")
    domains = [d.strip() for d in open(lst, encoding="utf-8") if d.strip() and not d.startswith("#")]
    summary = os.path.join(DATA, "crawl_summary.tsv")
    with io.open(summary, "w", encoding="utf-8") as fh:
        fh.write("domain\tok\ttitle\temails\tphones\tpages\n")
        for i, d in enumerate(domains, 1):
            r = process(d)
            with io.open(os.path.join(OUT, d + ".json"), "w", encoding="utf-8") as jf:
                json.dump(r, jf, ensure_ascii=False, indent=1)
            fh.write("\t".join([
                d, "1" if r["ok"] else "0", (r["title"] or "")[:80],
                ";".join(r["emails"][:4]), ";".join(r["phones"][:2]), str(len(r["pages"]))
            ]) + "\n")
            fh.flush()
            print(f"[{i}/{len(domains)}] {d} ok={r['ok']} emails={len(r['emails'])}", flush=True)
            time.sleep(0.6)

if __name__ == "__main__":
    main()
