"""
update_ticket.py
────────────────────────────────────────────────────────────────────
Actualitza l'array TICKET de kpi_taperia.html amb dades reals de
comensals i ticket mitjà extretes de Camarero10 via Playwright.

S'executa després de generar_arqueo.py i update_kpi.py al workflow.
Processa el mes en curs (de l'1 fins a ahir).
"""
from __future__ import annotations

import json
import os
import re
import sys
import datetime
from pathlib import Path

KPI       = Path("kpi_taperia.html")
PAX_C1    = Path("pax_caja1.json")
IVA = 1.10

MESES_ES = ["","Enero","Febrero","Marzo","Abril","Mayo","Junio",
            "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


def crear_credencials():
    """Crea el fitxer de credencials que necessita l'extractor."""
    user = os.environ.get("CAMARERO10_USER", "")
    pw   = os.environ.get("CAMARERO10_PASS", "")
    if not user or not pw:
        print("⚠  CAMARERO10_USER / CAMARERO10_PASS no definits. Saltant update_ticket.")
        return False
    env_dir = Path.home() / "Library" / "Application Support" / "taperia-tickets"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "camarero10.env").write_text(
        f"CAMARERO10_USER={user}\nCAMARERO10_PASS={pw}\n"
    )
    return True


def extraer_mes(any_: int, mes: int) -> dict | None:
    """Crida l'extractor Playwright per obtenir dades del mes complet fins a ahir."""
    import subprocess, json as _json

    avui = datetime.date.today()
    data_inici = datetime.date(any_, mes, 1)
    data_fi    = avui - datetime.timedelta(days=1)

    if data_fi < data_inici:
        return None  # mes futur

    extractor = Path(__file__).parent / "extractor_camarero10.py"
    if not extractor.exists():
        # En CI el fitxer és al mateix directori
        extractor = Path("extractor_camarero10.py")
    if not extractor.exists():
        print("⚠  No es troba extractor_camarero10.py")
        return None

    cmd = [sys.executable, str(extractor),
           data_inici.isoformat(), data_fi.isoformat(),
           "--json", "/tmp/ticket_mes.json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"⚠  Error extractor: {result.stderr[-300:]}")
        return None

    dades = _json.loads(Path("/tmp/ticket_mes.json").read_text())
    pax   = sum(v["dia"]["comensales"] or 0 for v in dades.values())
    neto  = sum(v["dia"]["neto"] or 0  for v in dades.values())
    if pax == 0:
        return None
    ticket = round(neto / pax, 2)
    return {"mes": MESES_ES[mes], "ticket": ticket, "pax": pax,
            "vendes": round(neto, 2)}


def cargar_pax_c1() -> dict:
    """Carrega pax_caja1.json (pax Caixa 1 Mediodía+Noche per mes YYYY-MM)."""
    if not PAX_C1.exists():
        return {}
    try:
        return json.loads(PAX_C1.read_text())
    except Exception:
        return {}


def main() -> int:
    html = KPI.read_text()
    m = re.search(r"const TICKET\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not m:
        print("⚠  No es trova TICKET a kpi_taperia.html")
        return 1

    ticket_arr = json.loads(m.group(1))
    # Guardem pax_c2 original per a cada mes (per no acumular C1 cada execució)
    by_mes = {}
    for row in ticket_arr:
        # Si ja tenia pax_c2 (execució anterior), el recuperem; sinó, pax actual = pax_c2
        pax_c2 = row.get("pax_c2", row.get("pax", 0))
        vend_c2 = row.get("vendes_c2", row.get("vendes", 0))
        by_mes[row["mes"]] = {**row, "pax_c2": pax_c2, "vendes_c2": round(float(vend_c2), 2)}

    avui = datetime.date.today()
    actualitzats = []

    # Actualitzar pax_c2 i vendes_c2 des de Camarero10 (mesos actuals)
    if crear_credencials():
        for delta in (0, 1):
            d = avui.replace(day=1) - datetime.timedelta(days=delta * 28)
            any_, mes = d.year, d.month
            if any_ != 2026:
                continue
            resum = extraer_mes(any_, mes)
            if resum:
                mes_nom = resum["mes"]
                prev = by_mes.get(mes_nom, {})
                by_mes[mes_nom] = {**prev,
                                   "mes": mes_nom,
                                   "pax_c2": resum["pax"],
                                   "vendes_c2": resum["vendes"]}
                actualitzats.append(f"{mes_nom}: {resum['pax']} pax C2 · {resum['ticket']}€/comensal (C2)")
    else:
        print("ℹ  Sense credencials Camarero10 — s'aplica pax_caja1.json sense actualitzar C2")

    # Afegir pax Caixa 1 (Mediodía+Noche) a tots els mesos i recalcular ticket
    pax_c1_data = cargar_pax_c1()
    for mes_key, c1 in pax_c1_data.items():
        try:
            mes_num = int(mes_key.split("-")[1])
        except Exception:
            continue
        mes_nom = MESES_ES[mes_num]
        if mes_nom not in by_mes:
            continue
        row = by_mes[mes_nom]
        pax_c2   = row.get("pax_c2", 0)
        pax_c1   = c1.get("pax", 0)
        vend_c2  = row.get("vendes_c2", 0)
        vend_c1  = c1.get("vendes", 0)
        total_pax  = pax_c1 + pax_c2
        total_vend = vend_c1 + vend_c2
        if total_pax > 0:
            ticket = round(total_vend / total_pax, 2)
            by_mes[mes_nom] = {**row,
                               "pax": total_pax,
                               "pax_c1": pax_c1,
                               "pax_c2": pax_c2,
                               "vendes": round(total_vend, 2),
                               "vendes_c2": round(float(vend_c2), 2),
                               "ticket": ticket}

    if not actualitzats and not pax_c1_data:
        print("ℹ  Res a actualitzar a TICKET.")
        return 0

    # Reconstruir en ordre de calendari
    nou = sorted(by_mes.values(), key=lambda r: MESES_ES.index(r["mes"]))
    html = html[:m.start(1)] + json.dumps(nou, ensure_ascii=False) + html[m.end(1):]
    KPI.write_text(html)

    print("✅ TICKET actualitzat:")
    for a in actualitzats:
        print("   ·", a)
    if pax_c1_data:
        print(f"   · pax_caja1.json aplicat a {len(pax_c1_data)} mesos (C1 Mediodía+Noche)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
