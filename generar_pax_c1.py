"""
generar_pax_c1.py
─────────────────────────────────────────────────────
Script LOCAL (Mac) — actualitza pax_caja1.json des de la BD SQLite de Caixa 1.
Executa'l manualment quan hi hagi nous tickets de WhatsApp processats.
Després fes: git add pax_caja1.json && git commit -m "pax C1 actualitzat" && git push
"""
import json
import sqlite3
from pathlib import Path

DB = Path.home() / "Library/CloudStorage/OneDrive-ROMAGLOBALTELECOMS.L/Aplicaciones/Tracker-operativo/BDD/taperia_caldes.db"
OUT = Path(__file__).parent / "pax_caja1.json"

# Valors fixos gen-mai (de l'Excel Detalle Turnos Caja1, Nº Comensales2)
PAX_STATIC = {
    "2026-01": {"pax": 249, "vendes": 0},
    "2026-02": {"pax": 293, "vendes": 0},
    "2026-03": {"pax": 259, "vendes": 0},
    "2026-04": {"pax": 459, "vendes": 0},
    "2026-05": {"pax": 320, "vendes": 0},
}

def main():
    if not DB.exists():
        print(f"⚠  BD no trobada: {DB}")
        return 1

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # pax + vendes de C1 Mediodía+Noche, 1 vegada per ticket (juny en endavant)
    cur.execute('''
      SELECT substr(fecha,1,7) as mes,
             SUM(pax) as total_pax,
             SUM(importe) as total_vendes
      FROM (
        SELECT fecha, num_ticket, turno,
               MAX(CAST(comensales as integer)) as pax,
               SUM(CAST(total as float)) as importe
        FROM ventas_caja1
        WHERE turno IN ("Mediodía", "Noche")
          AND num_ticket IS NOT NULL
          AND comensales IS NOT NULL
          AND CAST(comensales as integer) > 0
        GROUP BY fecha, num_ticket
      )
      WHERE mes >= "2026-06"
      GROUP BY mes ORDER BY mes
    ''')

    data = dict(PAX_STATIC)
    for mes, pax, vendes in cur.fetchall():
        data[mes] = {"pax": int(pax or 0), "vendes": round(float(vendes or 0), 2)}

    conn.close()

    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"✅ pax_caja1.json actualitzat ({len(data)} mesos):")
    for k, v in sorted(data.items()):
        print(f"   {k}: {v['pax']} pax, {v['vendes']}€")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
