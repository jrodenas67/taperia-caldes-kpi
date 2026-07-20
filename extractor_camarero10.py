#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extractor de Camarero10 para La Tapería de Caldes.

Hace login en el panel /admin y extrae del informe "Ventas Diarias"
(endpoint interno /Reports/dailysales/) los datos por día y por turno:
tickets, euros, comensales y ticket medio.

Truco clave: la firma de la llamada (public_key/secret_key/time/token) NO
depende de los parámetros de negocio, así que capturamos UNA llamada firmada
al entrar y luego pedimos cualquier fecha/turno sustituyendo date/hourInit/hourEnd.

Uso:
    python3 extractor_camarero10.py 2026-07-12
    python3 extractor_camarero10.py 2026-07-10 2026-07-12        # rango (inclusive)
    python3 extractor_camarero10.py 2026-07-12 --json salida.json
    python3 extractor_camarero10.py 2026-07-12 --ver             # navegador visible (debug)

Credenciales en:
    ~/Library/Application Support/taperia-tickets/camarero10.env
    (CAMARERO10_USER=...  /  CAMARERO10_PASS=...)
"""
import os
import re
import sys
import json
import argparse
import datetime as dt

from playwright.sync_api import sync_playwright

BASE = "https://c1570783304.camarero10.com"
LOGIN_URL = BASE + "/admin/#/login"
VENTAS_DIARIAS = BASE + "/admin/#/informes/ventasdiarias"
ENV_PATH = os.path.expanduser(
    "~/Library/Application Support/taperia-tickets/camarero10.env"
)

# Franjas horarias. hourInit acepta "all" o "00".."23"; hourEnd acepta "all" o "00:59".."23:59".
# Corte comida/cena a las 17:00 (ajustable).
TURNOS = {
    "dia":    ("all", "all"),
    "comida": ("00", "16:59"),
    "cena":   ("17", "23:59"),
}

# Camarero10 da los euros CON IVA. El KPI de la Tapería trabaja en NETO = Total / 1.10
# (misma convención que el "Subtotal" del cierre de caja: Subtotal == Total / 1.10).
IVA = 1.10


def cargar_credenciales():
    if not os.path.exists(ENV_PATH):
        sys.exit(f"ERROR: no existe el archivo de credenciales:\n  {ENV_PATH}")
    creds = {}
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
    user = creds.get("CAMARERO10_USER", "")
    pw = creds.get("CAMARERO10_PASS", "")
    if not user or not pw or user == "tu_usuario_de_admin":
        sys.exit(f"ERROR: pon tu usuario/contraseña reales en {ENV_PATH}")
    return user, pw


def rango_fechas(args_fechas):
    """Devuelve lista de fechas 'YYYY-MM-DD'. 1 arg = un día; 2 args = rango inclusive."""
    fechas = []
    parsed = [dt.date.fromisoformat(x) for x in args_fechas]
    if len(parsed) == 1:
        fechas = [parsed[0]]
    else:
        a, b = min(parsed), max(parsed)
        d = a
        while d <= b:
            fechas.append(d)
            d += dt.timedelta(days=1)
    return [d.isoformat() for d in fechas]


def _sub(url, param, valor):
    """Sustituye ?param=... por el valor dado (url-encoded para ':')."""
    valor = valor.replace(":", "%3A")
    return re.sub(rf"([?&]{param}=)[^&]*", lambda m: m.group(1) + valor, url)


def _num_euros(txt):
    """'1070.90 €' -> 1070.90 (float) o None."""
    if txt is None:
        return None
    m = re.search(r"[-\d.,]+", str(txt))
    if not m:
        return None
    s = m.group(0).replace(".", "").replace(",", ".") if "," in m.group(0) else m.group(0)
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def login(page, user, pw):
    page.goto(LOGIN_URL, wait_until="networkidle")
    # Esperar al formulario real (Angular lo renderiza tras cargar).
    campo_user = page.get_by_placeholder("usuario")
    try:
        campo_user.wait_for(state="visible", timeout=20000)
    except Exception:
        # fallback: primer input de texto visible
        campo_user = page.locator("input[type=text]:visible").last
        campo_user.wait_for(state="visible", timeout=10000)
    campo_user.fill(user)
    campo_pass = page.get_by_placeholder("contraseña")
    try:
        campo_pass.wait_for(state="visible", timeout=5000)
    except Exception:
        campo_pass = page.locator("input[type=password]:visible").first
    campo_pass.fill(pw)
    # Enviar: botón ACCEDER o Enter.
    try:
        page.get_by_text("ACCEDER", exact=False).first.click(timeout=3000)
    except Exception:
        campo_pass.press("Enter")
    # Esperar a salir del login.
    page.wait_for_function(
        "!location.hash.toLowerCase().includes('login')", timeout=25000
    )


def capturar_url_firmada(page):
    """Entra en Ventas Diarias y captura una URL firmada de /Reports/dailysales/.

    dailysales NO salta en la carga inicial (ahí solo va comparativesales); se
    dispara al cambiar la fecha. Forzamos ese cambio pulsando la flecha prev_day.
    """
    pred = lambda r: "/Reports/dailysales/" in r.url
    # entrar en el informe (cambio de ruta del SPA)
    page.evaluate("window.location.hash = '#/informes/ventasdiarias'")
    try:
        page.wait_for_selector('[ng-click="prev_day()"]', state="attached", timeout=20000)
    except Exception:
        sys.exit("ERROR: no cargó el informe Ventas Diarias.")
    # El clic directo lo tapa un overlay de 'loading'; llamamos a la función
    # Angular prev_day() en el scope, que cambia el día y dispara dailysales.
    disparo = """
        var el = document.querySelector('[ng-click="prev_day()"]');
        var s = angular.element(el).scope();
        s.prev_day(); if (s.$apply) { try { s.$apply(); } catch(e){} }
        'ok';
    """
    try:
        with page.expect_request(pred, timeout=20000) as info:
            page.evaluate(disparo)
        return info.value.url
    except Exception:
        sys.exit("ERROR: no se capturó ninguna llamada firmada de dailysales.")


def pedir(page, tmpl_url, fecha, hi, he):
    url = _sub(tmpl_url, "date", fecha)
    url = _sub(url, "hourInit", hi)
    url = _sub(url, "hourEnd", he)
    data = page.evaluate(
        "async (u) => { const r = await fetch(u); return await r.json(); }", url
    )
    tot = {}
    for it in (data or {}).get("items", []):
        if it.get("employee") == "Totales":
            tot = it
            break
    return {
        "tickets": tot.get("total_tickets"),
        "euros_txt": tot.get("total_euros"),
        "euros": _num_euros(tot.get("total_euros")),
        "comensales": tot.get("total_commensals"),
        "ticket_medio": tot.get("ticket_medio"),
    }


def extraer(fechas, headless=True):
    user, pw = cargar_credenciales()
    resultado = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        print("· Entrando en Camarero10…", file=sys.stderr)
        login(page, user, pw)
        print("· Sesión iniciada. Capturando llamada firmada…", file=sys.stderr)
        tmpl = capturar_url_firmada(page)
        for fecha in fechas:
            dia = {}
            for turno, (hi, he) in TURNOS.items():
                dia[turno] = pedir(page, tmpl, fecha, hi, he)
            # neto (÷IVA) y gasto medio por comensal en neto (convención del KPI)
            for turno, d in dia.items():
                c = d.get("comensales") or 0
                e = d.get("euros")
                d["neto"] = round(e / IVA, 2) if e is not None else None
                d["gasto_medio_comensal_neto"] = (
                    round(d["neto"] / c, 2) if (d["neto"] and c) else None
                )
            resultado[fecha] = dia
            t = dia["dia"]
            print(
                f"  {fecha}: {t['comensales']} comensales · neto {t['neto']} € · "
                f"{t['gasto_medio_comensal_neto']} €/comensal "
                f"(comida {dia['comida']['comensales']} / cena {dia['cena']['comensales']})",
                file=sys.stderr,
            )
        browser.close()
    return resultado


def main():
    ap = argparse.ArgumentParser(description="Extractor Camarero10 (comensales/ventas).")
    ap.add_argument("fechas", nargs="+", help="YYYY-MM-DD (una) o dos para un rango")
    ap.add_argument("--json", help="guardar resultado en este archivo JSON")
    ap.add_argument("--csv", help="guardar una fila por día y turno en este CSV")
    ap.add_argument("--ver", action="store_true", help="navegador visible (debug)")
    args = ap.parse_args()

    fechas = rango_fechas(args.fechas)
    res = extraer(fechas, headless=not args.ver)

    if args.csv:
        import csv as _csv
        cols = ["fecha", "turno", "tickets", "comensales",
                "euros_con_iva", "neto", "ticket_medio", "gasto_medio_comensal_neto"]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            w.writerow(cols)
            for fecha, dia in res.items():
                for turno, d in dia.items():
                    w.writerow([fecha, turno, d.get("tickets"), d.get("comensales"),
                                d.get("euros"), d.get("neto"), d.get("ticket_medio"),
                                d.get("gasto_medio_comensal_neto")])
        print(f"\nCSV guardado en {args.csv}", file=sys.stderr)

    salida = json.dumps(res, ensure_ascii=False, indent=2)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(salida)
        print(f"JSON guardado en {args.json}", file=sys.stderr)
    if not args.json and not args.csv:
        print(salida)


if __name__ == "__main__":
    main()
