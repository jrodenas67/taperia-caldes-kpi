"""
update_kpi.py
─────────────────────────────────────────────────────────────────────────────
Actualiza el array OBJECTIU embebido en kpi_taperia.html a partir de
historico_caja.json (los cierres netos diarios que mantiene generar_arqueo.py).

Reglas de negocio (idénticas a las usadas a mano):
  - Meses ANTERIORES a junio 2026 (enero–mayo) son FIJOS: provienen de la BD del
    TPV y NO se tocan.
  - Meses desde junio 2026 (incl.) se alimentan del arqueo:
        v2026     = suma de los totales diarios del mes (neto, sin IVA)
        dias      = nº de días con cierre
        hastaObj  = v2026 − prevision
        incentivo = 10% de hastaObj  (0 si negativo)
        efectivo  = v2026 (acumulado)
        pct24     = (v2026/v2025 − 1)·100  → null en el mes en curso
    La referencia del mes (v2025, prevision, diferencia, invDia) se conserva si
    ya existía en OBJECTIU; si no, se toma de ref_meses.json (lo escribe
    generar_arqueo.py con los totales de 2025) y, en su defecto, de la previsión
    diaria acumulada del histórico.

Se ejecuta después de generar_arqueo.py en el workflow diario.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

KPI       = Path("kpi_taperia.html")
HISTORICO = Path("historico_caja.json")
REF_MESES = Path("ref_meses.json")  # opcional: {"6": {"v2025": x, "dias": n}, ...}

ARQUEO_FROM = (2026, 6)  # primer mes alimentado por el arqueo (junio 2026)

MESES_ES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
MESOS_CA = ["", "Gener", "Febrer", "Març", "Abril", "Maig", "Juny",
            "Juliol", "Agost", "Setembre", "Octubre", "Novembre", "Desembre"]


def _r2(n: float) -> float:
    return round(float(n), 2)


def main() -> int:
    html = KPI.read_text()

    m = re.search(r"const OBJECTIU\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not m:
        print("⚠  No se encontró el array OBJECTIU en kpi_taperia.html")
        return 1
    objectiu = json.loads(m.group(1))
    by_mes = {row["mes"]: row for row in objectiu}

    historico = json.loads(HISTORICO.read_text())

    ref_meses = {}
    if REF_MESES.exists():
        try:
            ref_meses = json.loads(REF_MESES.read_text())
        except json.JSONDecodeError:
            ref_meses = {}

    # Agrupar totales 2026 por mes
    por_mes: dict[int, dict] = {}
    for e in historico:
        f = e.get("fecha", "")
        if not f.startswith("2026-"):
            continue
        mm = int(f[5:7])
        g = por_mes.setdefault(mm, {"total": 0.0, "dias": 0, "previsto": 0.0})
        g["total"] += float(e.get("total", 0) or 0)
        g["previsto"] += float(e.get("previsto", 0) or 0)
        if (e.get("total", 0) or 0) > 0:
            g["dias"] += 1

    if not por_mes:
        print("⚠  historico_caja.json no tiene datos de 2026; nada que actualizar.")
        return 0

    ultimo_mes = max(por_mes)
    desde = ARQUEO_FROM[1] if ARQUEO_FROM[0] == 2026 else 1

    actualizados = []
    for mm in sorted(por_mes):
        if mm < desde:
            continue  # enero–mayo: fijos, no se tocan
        nombre = MESES_ES[mm]
        g = por_mes[mm]
        v2026 = _r2(g["total"])
        dias = g["dias"]

        prev = by_mes.get(nombre, {})
        ref = ref_meses.get(str(mm)) or ref_meses.get(mm)

        if ref:
            v2025 = _r2(ref["v2025"])
            dias2025 = ref.get("dias") or prev.get("dias") or dias or 1
        elif prev.get("v2025"):
            v2025 = _r2(prev["v2025"])
            dias2025 = None  # conservar invDia/prevision existentes
        else:
            # Sin referencia: aproximar desde la previsión diaria acumulada
            v2025 = _r2(g["previsto"] / 1.03) if g["previsto"] else 0.0
            dias2025 = dias or 1

        if ref or not prev.get("prevision"):
            prevision = _r2(v2025 * 1.03)
            diferencia = _r2(v2025 * 0.03)
            invDia = _r2(diferencia / dias2025) if dias2025 else prev.get("invDia", 0.0)
        else:
            prevision = _r2(prev["prevision"])
            diferencia = _r2(prev.get("diferencia", v2025 * 0.03))
            invDia = _r2(prev.get("invDia", 0.0))

        hastaObj = _r2(v2026 - prevision)
        incentivo = _r2(hastaObj * 0.10) if hastaObj > 0 else 0.0
        pct24 = None if mm == ultimo_mes else (
            _r2((v2026 / v2025 - 1) * 100) if v2025 else None)

        row = {
            "mes": nombre, "v2025": v2025, "prevision": prevision,
            "v2026": v2026, "pct24": pct24, "diferencia": diferencia,
            "dias": dias, "invDia": invDia, "hastaObj": hastaObj,
            "incentivo": incentivo, "efectivo": v2026,
        }
        by_mes[nombre] = row
        actualizados.append(f"{nombre}: {v2026:.2f} € ({dias} dies, fins obj {hastaObj:+.2f})")

    # Reconstruir array en orden de calendario
    orden = {n: i for i, n in enumerate(MESES_ES) if n}
    nuevo = sorted(by_mes.values(), key=lambda r: orden.get(r["mes"], 99))
    html = html[:m.start(1)] + json.dumps(nuevo, ensure_ascii=False) + html[m.end(1):]

    # Cabecera: periodo y fecha de actualización
    fechas = sorted(e["fecha"] for e in historico if e.get("fecha", "").startswith("2026-"))
    ult = fechas[-1]
    dd = int(ult[8:10]); mmes = int(ult[5:7])
    html = re.sub(r'(<div class="period">)[^<]*(</div>)',
                  rf'\g<1>Gener – {MESOS_CA[ultimo_mes]} 2026\g<2>', html, count=1)
    html = re.sub(r'(<div class="updated">)Actualitzat:[^<]*(</div>)',
                  rf'\g<1>Actualitzat: {dd} {MESOS_CA[mmes]} 2026\g<2>', html, count=1)

    KPI.write_text(html)
    print("✅ kpi_taperia.html actualizado:")
    for a in actualizados:
        print("   ·", a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
