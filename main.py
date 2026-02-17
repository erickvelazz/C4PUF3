"""
main.py — Orquestador VET
Uso: uv run python main.py --ausur Cruces_AUSUR.xlsx --salidas report.csv
"""

import pandas as pd
import argparse
from pathlib import Path
from datetime import datetime

from ingesta     import (leer_xlsx_ausur, leer_csv_salidas,
                          limpiar_ausur, preparar_salidas_csv,
                          extraer_pendientes_ausur)
from matching    import hacer_match
from match_placa import match_por_placa

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


def run_pipeline(ruta_ausur: str, ruta_salidas: str) -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n{'='*55}\n  MÓDULO VET — {ts}\n{'='*55}")

    # ── PROCESO 1: Limpieza AUSUR ─────────────────────────────────────
    print("\n── PROCESO 1: Limpieza AUSUR ──")
    df_ausur_crudo = leer_xlsx_ausur(ruta_ausur)
    res_ausur      = limpiar_ausur(df_ausur_crudo)
    df_entradas    = res_ausur["entradas_limpias"]
    df_duplicados  = res_ausur["duplicados"]

    # Pendientes del AUSUR (estatus 4/8 si existen)
    df_pend_ausur  = extraer_pendientes_ausur(df_ausur_crudo)

    # ── PROCESO 2: Preparar salidas CSV ───────────────────────────────
    print("\n── PROCESO 2: Preparar Salidas ──")
    df_csv_crudo   = leer_csv_salidas(ruta_salidas)
    res_csv        = preparar_salidas_csv(df_csv_crudo)
    df_salidas     = res_csv["salidas"]
    df_pend_csv    = res_csv["pendientes"]
    df_descartados = res_csv["descartados"]

    # Unir pendientes de ambas fuentes
    df_pendientes = pd.concat([df_pend_csv, df_pend_ausur], ignore_index=True)

    # ── PROCESO 3: Match por Tag ──────────────────────────────────────
    print("\n── PROCESO 3: Match por Tag ──")
    res_match     = hacer_match(df_entradas, df_salidas)
    df_matched    = res_match["matched"]
    df_sin_e      = res_match["sin_match_e"]
    df_sin_s      = res_match["sin_match_s"]

    # ── PROCESO 4: Match por Placa (Pendientes) ───────────────────────
    print("\n── PROCESO 4: Match por Placa ──")
    res_placa      = match_por_placa(df_pendientes, df_sin_e, df_sin_s)
    df_match_placa = res_placa["matched_placa"]
    df_pend_sin    = res_placa["pendientes_sin_match"]
    df_sin_e_final = res_placa["sin_match_e_resto"]
    df_sin_s_final = res_placa["sin_match_s_resto"]

    # ── Reporte ───────────────────────────────────────────────────────
    ruta_out = _generar_excel(
        df_matched, df_match_placa, df_duplicados,
        df_pendientes, df_pend_sin,
        df_sin_e_final, df_sin_s_final,
        df_descartados, ts
    )

    _imprimir_resumen(
        df_ausur_crudo, df_entradas, df_duplicados,
        df_salidas, df_pendientes,
        df_matched, df_match_placa,
        df_sin_e_final, df_sin_s_final
    )

    return {
        "matched":       df_matched,
        "matched_placa": df_match_placa,
        "duplicados":    df_duplicados,
        "pendientes":    df_pendientes,
        "pend_sin":      df_pend_sin,
        "sin_match_e":   df_sin_e_final,
        "sin_match_s":   df_sin_s_final,
        "ruta":          ruta_out,
    }


def _generar_excel(df_m, df_mp, df_dup, df_pend, df_pend_sin,
                   df_se, df_ss, df_desc, ts) -> Path:
    ruta = OUTPUT_DIR / f"VET_{ts}.xlsx"
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        sheets = [
            (df_m,        "Conciliados_Tag"),
            (df_mp,       "Conciliados_Placa"),
            (df_dup,      "Duplicados_AUSUR"),
            (df_pend,     "Pendientes"),
            (df_pend_sin, "Pendientes_Sin_Match"),
            (df_se,       "Sin_Match_Entradas"),
            (df_ss,       "Sin_Match_Salidas"),
            (df_desc,     "Descartados_CSV"),
        ]
        for df, sheet in sheets:
            if not df.empty:
                df.to_excel(w, sheet_name=sheet, index=False)
    print(f"\n[OUTPUT] {ruta}")
    return ruta


def _imprimir_resumen(df_crudo, df_e, df_dup, df_s, df_pend,
                      df_m, df_mp, df_se, df_ss):
    total_conc = len(df_m) + len(df_mp)
    total_e    = len(df_e)
    pct        = total_conc / max(total_e, 1) * 100
    print(f"\n{'='*55}")
    print("  RESUMEN")
    print(f"{'='*55}")
    print(f"  AUSUR crudos           : {len(df_crudo):>7,}")
    print(f"  Entradas limpias       : {len(df_e):>7,}")
    print(f"  Duplicados AUSUR       : {len(df_dup):>7,}")
    print(f"  Salidas válidas (B+10) : {len(df_s):>7,}")
    print(f"  Pendientes (est. 4/8)  : {len(df_pend):>7,}")
    print(f"  ─────────────────────────────────")
    print(f"  Conciliados por Tag    : {len(df_m):>7,}")
    print(f"  Conciliados por Placa  : {len(df_mp):>7,}")
    print(f"  Total conciliados      : {total_conc:>7,}")
    print(f"  Sin match (entradas)   : {len(df_se):>7,}")
    print(f"  Sin match (salidas)    : {len(df_ss):>7,}")
    print(f"  Tasa conciliación      : {pct:>6.1f}%")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ausur",   required=True)
    parser.add_argument("--salidas", required=True)
    args = parser.parse_args()
    run_pipeline(args.ausur, args.salidas)