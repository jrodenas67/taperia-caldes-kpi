"""
generar_arqueo.py
─────────────────────────────────────────────────────────────────────────────
Lee los PDFs "cierreCaja.pdf" adjuntos a los correos "Cierre de caja" del
Gmail del usuario y genera dos archivos JSON usados por arqueo.html:

  - ultimo_cierre.json   {fecha, num_cierre, contado, visa, passo, total}
  - historico_caja.json  [{fecha, total, previsto, evento}, ...]

Los importes en JSON son NETOS (sin IVA), dividen entre 1.10 los del PDF.

La fecha asignada es la "Fecha inicio" del PDF (jornada operativa) — el bug
original asignaba "Fecha fin" (día siguiente, tras medianoche).

Variables de entorno necesarias:
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import datetime
import statistics

from gmail_cierres import fetch_cierres

IVA = 1.10
HISTORICO = Path("historico_caja.json")
ULTIMO    = Path("ultimo_cierre.json")
DIARIOS   = Path("cierres_diarios.json")


def _previsto_estimado(fecha_str: str, historico: list[dict]) -> float:
    """Estima el previsto como mediana de los mismos días de la semana
    de los últimos 8 valores conocidos (>0)."""
    try:
        d = datetime.date.fromisoformat(fecha_str)
    except ValueError:
        return 0.0
    dow = d.weekday()
    candidatos = []
    for e in reversed(historico):
        if e.get("previsto", 0) <= 0:
            continue
        try:
            ed = datetime.date.fromisoformat(e["fecha"])
        except (ValueError, KeyError):
            continue
        if ed >= d:
            continue
        if ed.weekday() == dow:
            candidatos.append(e["previsto"])
            if len(candidatos) >= 8:
                break
    return round(statistics.median(candidatos), 2) if candidatos else 0.0


def neto(bruto: float) -> float:
    return round(bruto / IVA, 2)


def main() -> int:
    print("📨 Descargando cierres de Gmail (2025 + 2026)...")
    cierres = fetch_cierres(days=600)
    if not cierres:
        print("❌ No se obtuvo ningún cierre desde Gmail. Revisa los secrets GMAIL_* en GitHub.")
        return 1  # falla visible en Actions para diagnosticar credenciales caducadas

    # Orden por fecha (descendente para encontrar el último)
    cierres_ord = sorted(cierres, key=lambda c: c["fecha"])
    ultimo = cierres_ord[-1]

    # ── ultimo_cierre.json ────────────────────────────────────────────────
    ultimo_json = {
        "fecha":        ultimo["fecha"].strftime("%Y-%m-%d"),
        "num_cierre":   str(ultimo["numero"]),
        "contado":      neto(ultimo["efectivo"]),
        "visa":         neto(ultimo["tarjeta"]),
        "passo":        neto(ultimo["tickets"]),
        "tickets_rest": neto(ultimo["tickets"]),
        "total":        neto(ultimo["total"]),
    }
    ULTIMO.write_text(json.dumps(ultimo_json, indent=2, ensure_ascii=False))
    print(f"✅ ultimo_cierre.json: cierre #{ultimo['numero']} fecha "
          f"{ultimo_json['fecha']} total={ultimo_json['total']} €")

    # ── cierres_diarios.json (todos los cierres del año) ──────────────────
    año_actual = datetime.date.today().year
    # Cargar excedentes manuales (Contado Caixa 1) preservándolos en el merge
    excedentes: dict = {}
    exc_path = Path("excedentes.json")
    if exc_path.exists():
        try:
            excedentes = json.loads(exc_path.read_text())
        except json.JSONDecodeError:
            pass
    # Preservar c1_contado ya grabado en cierres_diarios anteriores
    diarios_prev: dict = {}
    if DIARIOS.exists():
        try:
            diarios_prev = json.loads(DIARIOS.read_text())
        except json.JSONDecodeError:
            pass
    diarios = {}
    for c in cierres_ord:
        if c["fecha"].year != año_actual:
            continue
        f = c["fecha"].strftime("%Y-%m-%d")
        prev = diarios_prev.get(f, {})
        c1 = excedentes.get(f, prev.get("c1_contado"))
        entry = {
            "fecha":        f,
            "num_cierre":   str(c["numero"]),
            "contado":      neto(c["efectivo"]),
            "visa":         neto(c["tarjeta"]),
            "passo":        neto(c["tickets"]),
            "tickets_rest": neto(c["tickets"]),
            "total":        neto(c["total"]),
        }
        if c1 is not None:
            entry["c1_contado"] = c1
        diarios[f] = entry
    DIARIOS.write_text(json.dumps(diarios, indent=2, ensure_ascii=False))
    print(f"✅ cierres_diarios.json: {len(diarios)} cierres del {año_actual} "
          f"({sum(1 for v in diarios.values() if 'c1_contado' in v)} amb excedent)")

    # ── historico_caja.json ───────────────────────────────────────────────
    # Cargamos el existente para preservar campos como "previsto" y "evento"
    historico_actual: list[dict] = []
    if HISTORICO.exists():
        try:
            historico_actual = json.loads(HISTORICO.read_text())
        except json.JSONDecodeError:
            historico_actual = []

    # Filtrar fuera entradas que no sean del año actual (purga datos de 2025
    # si se colaron en runs anteriores).
    año_actual = datetime.date.today().year
    by_fecha: dict[str, dict] = {
        e["fecha"]: e for e in historico_actual
        if isinstance(e, dict) and e.get("fecha", "").startswith(str(año_actual))
    }

    # Aplicamos los totales de los PDFs SOLO del año actual
    cambiados = 0
    for c in cierres_ord:
        if c["fecha"].year != año_actual:
            continue
        f = c["fecha"].strftime("%Y-%m-%d")
        entry = by_fecha.get(f, {"fecha": f, "previsto": 0, "evento": ""})
        nuevo_total = neto(c["total"])
        if entry.get("total") != nuevo_total:
            cambiados += 1
        entry["total"] = nuevo_total
        by_fecha[f] = entry

    historico = sorted(by_fecha.values(), key=lambda e: e["fecha"])

    # Calcular previsto: ventas del mismo día de la semana del año anterior × 1.03
    # (364 días = exactamente 52 semanas → siempre cae en el mismo weekday)
    # Sólo aplica a entradas SIN evento (los eventos los gestiona el usuario manualmente).
    ventas_anteriores: dict[datetime.date, float] = {
        c["fecha"]: neto(c["total"]) for c in cierres_ord
    }
    previstos_actualizados = 0
    for entry in historico:
        if entry.get("evento", "").strip():
            continue
        try:
            fecha = datetime.date.fromisoformat(entry["fecha"])
        except ValueError:
            continue
        ref = fecha - datetime.timedelta(days=364)
        if ref in ventas_anteriores:
            nuevo = round(ventas_anteriores[ref] * 1.03, 2)
            if entry.get("previsto") != nuevo:
                entry["previsto"] = nuevo
                previstos_actualizados += 1

    HISTORICO.write_text(json.dumps(historico, indent=2, ensure_ascii=False))
    print(f"✅ historico_caja.json: {len(historico)} entradas "
          f"({cambiados} totales actualizados, {previstos_actualizados} previstos = ventas_2025×1.03)")

    # ── ref_meses.json ────────────────────────────────────────────────────
    # Totales NETOS de 2025 por mes (full-month) → referencia para update_kpi.py
    # (v2025/previsión/invDia de los meses alimentados por el arqueo).
    ref: dict[str, dict] = {}
    for c in cierres_ord:
        if c["fecha"].year != año_actual - 1:
            continue
        mm = str(c["fecha"].month)
        g = ref.setdefault(mm, {"v2025": 0.0, "dias": 0})
        g["v2025"] = round(g["v2025"] + neto(c["total"]), 2)
        g["dias"] += 1
    Path("ref_meses.json").write_text(json.dumps(ref, indent=2, ensure_ascii=False))
    print(f"✅ ref_meses.json: {len(ref)} meses de {año_actual - 1}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
