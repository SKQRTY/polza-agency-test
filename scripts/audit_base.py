#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_base.py - аудит базы перед рассылкой («будь начеку», задача 4).

Проверки для каждой строки:
  1. Синтаксис email.
  2. Совпадение домена email и домена сайта (нормализованное сравнение -
     ловит перепутанные контакты: чужие домены, несовпадения).
  3. Личные ящики (mail.ru, gmail, 126.com и т.п.) - отдельный флаг.
  4. MX-записи у домена email (dnspython, fallback: nslookup).
  5. Доступность сайта (HTTP-код).
  6. Дубликаты: по email и по домену сайта.

Вход:  CSV (company,email,site - имена колонок настраиваются флагами)
Выход: CSV с колонками «Статус аудита», «Комментарий».

Запуск:  python audit_base.py --input base.csv --output base_audited.csv
"""
import argparse, csv, os, re, socket, subprocess, sys

try:
    import dns.resolver  # dnspython
    HAS_DNS = True
except Exception:
    HAS_DNS = False

PERSONAL_DOMAINS = ("mail.ru", "gmail.com", "yandex.ru", "ya.ru", "bk.ru", "list.ru",
                    "inbox.ru", "internet.ru", "rambler.ru", "126.com", "163.com",
                    "qq.com", "outlook.com", "hotmail.com")

def norm(s):
    """Нормализация строки для сравнения: только буквы/цифры, нижний регистр."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def domain_of(email):
    return email.split("@")[-1].lower().strip()

def site_domain(site):
    d = re.sub(r"^https?://", "", (site or "").lower()).split("/")[0]
    return d[4:] if d.startswith("www.") else d

def same_domain(email_dom, site_dom):
    """Совпадают ли домены (терпимо к дефисам и поддоменам: jat-carbide.com ~ jatcarbide.com)."""
    a, b = norm(email_dom), norm(site_dom)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # общий префикс >= 6 символов (jatcarbide vs jatcarbidecom)
    for n in range(min(len(a), len(b)), 5, -1):
        if a[:n] == b[:n]:
            return True
    return False

def mx_ok(domain):
    if HAS_DNS:
        try:
            ans = dns.resolver.resolve(domain, "MX", lifetime=6)
            return len(ans) > 0, ";".join(sorted(str(r.exchange).rstrip(".") for r in ans)[:3])
        except Exception as e:
            return False, str(e)[:60]
    # fallback: nslookup (Windows)
    try:
        p = subprocess.run(["nslookup", "-type=mx", domain], capture_output=True, text=True, timeout=15)
        out = (p.stdout or "")
        found = "mail exchanger" in out.lower()
        return found, "" if found else "нет MX (nslookup)"
    except Exception as e:
        return False, str(e)[:60]

def http_status(url):
    try:
        p = subprocess.run(["curl.exe", "-s", "-o", "NUL" if os.name == "nt" else "/dev/null",
                            "-w", "%{http_code}", "--max-time", "15", "-L", url], capture_output=True)
        return p.stdout.decode("ascii", "ignore").strip()
    except Exception:
        return "err"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--col-company", default="company")
    ap.add_argument("--col-email", default="email")
    ap.add_argument("--col-site", default="site")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.input, encoding="utf-8-sig", newline="")))
    seen_emails, seen_sites = set(), set()

    out = []
    for row in rows:
        company = (row.get(args.col_company) or "").strip()
        email = (row.get(args.col_email) or "").strip()
        site = (row.get(args.col_site) or "").strip()

        notes, status = [], "OK"
        # 1. синтаксис
        if not re.match(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$", email):
            status = "Ошибка email"
            notes.append("некорректный синтаксис email")
        else:
            ed = domain_of(email)
            # 2. домен email vs домен сайта
            sd = site_domain(site)
            if sd and not same_domain(ed, sd):
                status = "ПРОВЕРИТЬ: домен не совпадает"
                notes.append(f"email @{ed} и сайт {sd} не совпадают - вероятно, перепутаны контакты")
            # 3. личный ящик
            if any(ed == pd or ed.endswith("." + pd) for pd in PERSONAL_DOMAINS):
                if status == "OK":
                    status = "Проверить: личный ящик"
                notes.append("личный/публичный почтовый сервис - не корпоративный домен")
            # 4. MX
            ok, mx = mx_ok(ed)
            if not ok:
                if status == "OK":
                    status = "ПРОВЕРИТЬ: нет MX"
                notes.append(f"MX не найден ({mx})")
            else:
                notes.append(f"MX ok: {mx[:60]}")
            # 6. дубликаты
            if email.lower() in seen_emails:
                notes.append("дубликат email")
            seen_emails.add(email.lower())
        if sd := site_domain(site):
            if sd in seen_sites:
                notes.append(f"дубликат домена сайта: {sd}")
            seen_sites.add(sd)
        # 5. доступность сайта
        if site:
            code = http_status(site)
            notes.append(f"сайт: HTTP {code}")
        row_out = dict(row)
        row_out["Статус аудита"] = status
        row_out["Комментарий"] = " | ".join(notes)
        out.append(row_out)
        print(f"{company[:40]:40} {email[:35]:35} -> {status}", flush=True)

    fields = list(out[0].keys()) if out else list(rows[0].keys()) + ["Статус аудита", "Комментарий"]
    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    bad = sum(1 for r in out if "ПРОВЕРИТЬ" in r["Статус аудита"] or "Ошибка" in r["Статус аудита"])
    print(f"\nИтого: {len(out)} строк, требуют внимания: {bad} -> {args.output}")

if __name__ == "__main__":
    main()
