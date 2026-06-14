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

from gmail_cierres import fetch_cierres

IVA = 1.10
HISTORICO = Path("historico_caja.json")
ULTIMO    = Path("ultimo_cierre.json")


def neto(bruto: float) -> float:
    return round(bruto / IVA, 2)


def main() -> int:
    print("📨 Descargando cierres de Gmail...")
    cierres = fetch_cierres(days=180)
    if not cierres:
        print("⚠  No se obtuvo ningún cierre desde Gmail. Nada que hacer.")
        return 0

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

    # ── historico_caja.json ───────────────────────────────────────────────
    # Cargamos el existente para preservar campos como "previsto" y "evento"
    historico_actual: list[dict] = []
    if HISTORICO.exists():
        try:
            historico_actual = json.loads(HISTORICO.read_text())
        except json.JSONDecodeError:
            historico_actual = []

    by_fecha: dict[str, dict] = {e["fecha"]: e for e in historico_actual
                                 if isinstance(e, dict) and e.get("fecha")}

    # Aplicamos los totales de los PDFs (sobrescribe / añade por fecha)
    cambiados = 0
    for c in cierres_ord:
        f = c["fecha"].strftime("%Y-%m-%d")
        entry = by_fecha.get(f, {"fecha": f, "previsto": 0, "evento": ""})
        nuevo_total = neto(c["total"])
        if entry.get("total") != nuevo_total:
            cambiados += 1
        entry["total"] = nuevo_total
        by_fecha[f] = entry

    historico = sorted(by_fecha.values(), key=lambda e: e["fecha"])
    HISTORICO.write_text(json.dumps(historico, indent=2, ensure_ascii=False))
    print(f"✅ historico_caja.json: {len(historico)} entradas "
          f"({cambiados} cambiadas con datos del PDF)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
