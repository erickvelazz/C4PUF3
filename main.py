"""
main.py — Orquestador VET optimizado
Uso: uv run python main.py --ausur Cruces_AUSUR.xlsx --salidas report.csv
"""

import pandas as pd
import argparse
from pathlib import Path
from datetime import datetime

from ingesta       import (leer_xlsx_ausur, leer_csv_salidas,
                            limpiar_ausur, preparar_salidas_csv)
from matching      import hacer_match_optimizado
from normalizacion import asignar_dia_operativo
from match_placa   import match_por_placa

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


def run_pipeline(ruta_ausur: str, ruta_salidas: str) -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n{'='*60}\n  MÓDULO VET — {ts}\n{'='*60}")

    # ── PROCESO 1: Limpieza AUSUR ─────────────────────────────────────
    print("\n── PROCESO 1: Limpieza AUSUR ──")
    df_ausur_crudo = leer_xlsx_ausur(ruta_ausur)
    res_ausur      = limpiar_ausur(df_ausur_crudo)
    df_entradas    = res_ausur["entradas_limpias"]
    df_duplicados  = res_ausur["duplicados"]

    # ── PROCESO 2: Preparar salidas CSV ───────────────────────────────
    print("\n── PROCESO 2: Preparar Salidas ──")
    df_csv_crudo   = leer_csv_salidas(ruta_salidas)
    res_csv        = preparar_salidas_csv(df_csv_crudo)
    df_salidas     = res_csv["salidas"]
    df_pendientes  = res_csv["pendientes"]
    df_descartados = res_csv["descartados"]

    # ── Asignar día operativo ─────────────────────────────────────────
    df_entradas = asignar_dia_operativo(df_entradas)
    df_salidas  = asignar_dia_operativo(df_salidas)

    # ── PROCESO 3: Match optimizado por día ───────────────────────────
    print("\n── PROCESO 3: Match (Tag → Placa → Hora) ──")
    res_match      = hacer_match_optimizado(df_entradas, df_salidas)
    df_tag         = res_match["matched_tag"]
    df_placa       = res_match["matched_placa"]
    df_hora        = res_match["matched_hora"]
    df_sin_e       = res_match["sin_match_e"]
    df_sin_s       = res_match["sin_match_s"]

    # ── PROCESO 4: Pendientes (status 4/8) ────────────────────────────
    print("\n── PROCESO 4: Pendientes ──")
    res_pend       = match_por_placa(df_pendientes, df_sin_e, df_sin_s)
    df_pend_match  = res_pend["nuevos_conciliados"]
    df_pend_sin    = res_pend["pendientes_sin_match"]
    df_sin_e_final = res_pend["sin_match_e_final"]
    df_sin_s_final = res_pend["sin_match_s_final"]

    # ── Reporte ───────────────────────────────────────────────────────
    ruta_out = _generar_excel(
        df_tag, df_placa, df_hora, df_pend_match,
        df_duplicados, df_pend_sin,
        df_sin_e_final, df_sin_s_final,
        df_descartados, ts
    )

    _imprimir_resumen(
        df_ausur_crudo, df_entradas, df_duplicados,
        df_tag, df_placa, df_hora, df_pend_match,
        df_sin_e_final, df_sin_s_final
    )

    return {
        "matched_tag":   df_tag,
        "matched_placa": df_placa,
        "matched_hora":  df_hora,
        "pend_matched":  df_pend_match,
        "duplicados":    df_duplicados,
        "pend_sin":      df_pend_sin,
        "sin_match_e":   df_sin_e_final,
        "sin_match_s":   df_sin_s_final,
        "ruta":          ruta_out,
    }


def _generar_excel(df_tag, df_placa, df_hora, df_pend_m,
                   df_dup, df_pend_sin, df_se, df_ss, df_desc, ts) -> Path:
    ruta = OUTPUT_DIR / f"VET_{ts}.xlsx"
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        sheets = [
            (df_tag,       "Conciliados_Tag"),
            (df_placa,     "Conciliados_Placa"),
            (df_hora,      "Requieren_Validacion"),
            (df_pend_m,    "Pendientes_Conciliados"),
            (df_dup,       "Duplicados_AUSUR"),
            (df_pend_sin,  "Pendientes_Sin_Match"),
            (df_se,        "Sin_Match_Entradas"),
            (df_ss,        "Sin_Match_Salidas"),
            (df_desc,      "Descartados_CSV"),
        ]
        for df, sheet in sheets:
            if not df.empty:
                df.to_excel(w, sheet_name=sheet, index=False)
    print(f"\n[OUTPUT] {ruta}")
    return ruta


def _imprimir_resumen(df_crudo, df_e, df_dup,
                      df_tag, df_placa, df_hora, df_pend_m,
                      df_se, df_ss):
    total = len(df_tag) + len(df_placa) + len(df_hora) + len(df_pend_m)
    pct   = total / max(len(df_e), 1) * 100
    print(f"\n{'='*60}")
    print("  RESUMEN")
    print(f"{'='*60}")
    print(f"  AUSUR crudos              : {len(df_crudo):>7,}")
    print(f"  Entradas limpias          : {len(df_e):>7,}")
    print(f"  Duplicados AUSUR          : {len(df_dup):>7,}")
    print(f"  ──────────────────────────────────")
    print(f"  Match por Tag             : {len(df_tag):>7,}")
    print(f"  Match por Placa           : {len(df_placa):>7,}")
    print(f"  Match solo Hora (cámara)  : {len(df_hora):>7,}")
    print(f"  Pendientes conciliados    : {len(df_pend_m):>7,}")
    print(f"  ──────────────────────────────────")
    print(f"  Total conciliados         : {total:>7,}")
    print(f"  Sin match (entradas)      : {len(df_se):>7,}")
    print(f"  Sin match (salidas)       : {len(df_ss):>7,}")
    print(f"  Tasa conciliación         : {pct:>6.1f}%")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ausur",   required=True)
    parser.add_argument("--salidas", required=True)
    args = parser.parse_args()
    run_pipeline(args.ausur, args.salidas)