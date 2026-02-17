"""
matching.py — Match entre entradas AUSUR y salidas CSV
Reglas:
  - Solo por Tag exacto (todos los estatus 10 tienen Tag)
  - La salida debe ser POSTERIOR a la entrada
  - Ventana de tiempo configurable (default 120 min)
  - Una entrada solo puede matchear con una salida (1 a 1)
  - Una salida solo puede matchear con una entrada (1 a 1)
"""

import pandas as pd
from datetime import timedelta
from thefuzz import fuzz

from ingesta import XLSX_TAG, CSV_TAG

VENTANA_MAX_MINUTOS = 120


def hacer_match(
    df_entradas: pd.DataFrame,
    df_salidas:  pd.DataFrame,
) -> dict:

    entradas = df_entradas.copy()
    salidas  = df_salidas.copy()

    entradas["datetime"] = pd.to_datetime(entradas["datetime"])
    salidas["datetime"]  = pd.to_datetime(salidas["datetime"])

    entradas = entradas.sort_values("datetime").reset_index(drop=True)
    salidas  = salidas.sort_values("datetime")

    # 🔥 OPTIMIZACIÓN CLAVE:
    # Agrupar salidas por TAG una sola vez
    salidas_por_tag = {
        tag: grupo for tag, grupo in salidas.groupby(CSV_TAG, sort=False)
    }

    resultados = []
    ids_salidas_usadas = set()

    print(f"\n[MATCH] Entradas: {len(entradas):,} | Salidas: {len(salidas):,}")

    # 🔥 usar itertuples (mucho más rápido que iterrows)
    for entrada in entradas.itertuples(index=True):

        tag = getattr(entrada, XLSX_TAG)
        if pd.isna(tag) or str(tag).strip() == "":
            continue

        if tag not in salidas_por_tag:
            continue

        candidatas = salidas_por_tag[tag]

        dt_entrada = entrada.datetime

        candidatas = candidatas[
            (~candidatas.index.isin(ids_salidas_usadas)) &
            (candidatas["datetime"] > dt_entrada) &
            (candidatas["datetime"] <= dt_entrada + timedelta(minutes=VENTANA_MAX_MINUTOS))
        ]

        if candidatas.empty:
            continue

        mejor_idx = (candidatas["datetime"] - dt_entrada).abs().idxmin()
        salida = salidas.loc[mejor_idx]

        par = _construir_par(pd.Series(entrada._asdict()), salida)
        par["_idx_entrada"] = entrada.Index

        resultados.append(par)
        ids_salidas_usadas.add(mejor_idx)

    df_matched = pd.DataFrame(resultados) if resultados else pd.DataFrame()

    indices_matched = {r["_idx_entrada"] for r in resultados}
    df_sin_e = entradas[~entradas.index.isin(indices_matched)].copy()
    df_sin_e["motivo"] = "Sin salida con mismo Tag en ventana de tiempo"

    df_sin_s = salidas[~salidas.index.isin(ids_salidas_usadas)].copy()
    df_sin_s["motivo"] = "Sin entrada con mismo Tag en ventana de tiempo"

    if not df_matched.empty and "_idx_entrada" in df_matched.columns:
        df_matched = df_matched.drop(columns=["_idx_entrada"])

    print(f"[MATCH] Conciliados    : {len(df_matched):,}")
    print(f"[MATCH] Sin match (E)  : {len(df_sin_e):,}")
    print(f"[MATCH] Sin match (S)  : {len(df_sin_s):,}")

    return {
        "matched":     df_matched,
        "sin_match_e": df_sin_e,
        "sin_match_s": df_sin_s,
    }


def _construir_par(entrada: pd.Series, salida: pd.Series) -> dict:
    """Une los campos de entrada y salida en un dict, con sufijos claros."""
    par = {}
    for k, v in entrada.items():
        par[f"{k}_entrada" if not k.endswith("_entrada") else k] = v
    for k, v in salida.items():
        par[f"{k}_salida" if not k.endswith("_salida") else k] = v

    # Campos calculados
    dt_e = entrada.get("datetime")
    dt_s = salida.get("datetime")
    if pd.notna(dt_e) and pd.notna(dt_s):
        total_seg = int((pd.Timestamp(dt_s) - pd.Timestamp(dt_e)).total_seconds())
        hh = total_seg // 3600
        mm = (total_seg % 3600) // 60
        ss = total_seg % 60
        par["tiempo_recorrido"] = f"{hh:02d}:{mm:02d}:{ss:02d}"
    else:
        par["tiempo_recorrido"] = None

    par["metodo_match"] = "TAG_EXACTO"
    return par

def _porcentaje_match_placa(p1: str, p2: str) -> float:
    if pd.isna(p1) or pd.isna(p2):
        return 0.0
    p1 = str(p1).upper().strip()
    p2 = str(p2).upper().strip()
    return fuzz.ratio(p1, p2)


def match_por_placa(
    df_sin_e: pd.DataFrame,
    df_sin_s: pd.DataFrame,
    df_pendientes: pd.DataFrame,
    umbral: int = 80
):
    """
    Match optimizado por placa.
    Reduce complejidad usando indexado por primera letra.
    """

    from collections import defaultdict

    nuevos_matches = []
    usados_e = set()
    usados_s = set()

    candidatos = pd.concat([df_sin_s, df_pendientes], ignore_index=False)

    # 🔥 INDEXAR POR PRIMERA LETRA
    indice_placas = defaultdict(list)

    for idx, row in candidatos.iterrows():
        placa = row.get("Placa")
        if placa is None or pd.isna(placa):
            placa = row.get("PLACA")

        if placa is None or pd.isna(placa):
            continue

        placa_str = str(placa).upper().strip()
        if len(placa_str) == 0:
            continue

        indice_placas[placa_str[0]].append((idx, placa_str, row))

    print(f"[PLACA] Índice generado con {len(indice_placas)} grupos")

    # 🔥 MATCH INTELIGENTE
    for idx_e, row_e in df_sin_e.iterrows():

        placa_e = row_e.get("PLACA")
        if placa_e is None or pd.isna(placa_e):
            continue

        placa_e_str = str(placa_e).upper().strip()
        if len(placa_e_str) == 0:
            continue

        primera_letra = placa_e_str[0]

        if primera_letra not in indice_placas:
            continue

        mejor_score = 0
        mejor_idx = None
        mejor_row = None

        candidatos_filtrados = indice_placas[primera_letra]

        for idx_s, placa_s_str, row_s in candidatos_filtrados:

            if idx_s in usados_s:
                continue

            # filtro por longitud similar (máx 2 diferencia)
            if abs(len(placa_s_str) - len(placa_e_str)) > 2:
                continue

            score = fuzz.partial_ratio(placa_e_str, placa_s_str)

            if score > mejor_score:
                mejor_score = score
                mejor_idx = idx_s
                mejor_row = row_s

        if mejor_score >= umbral and mejor_row is not None:

            par = _construir_par(row_e, mejor_row)
            par["metodo_match"] = "PLACA_PENDIENTES"
            par["porcentaje_match_placa"] = mejor_score
            par["flag_desde_pendientes"] = (
                mejor_idx in df_pendientes.index
            )

            nuevos_matches.append(par)

            usados_e.add(idx_e)
            usados_s.add(mejor_idx)

    df_nuevos = pd.DataFrame(nuevos_matches)

    df_sin_e_rest = df_sin_e.drop(index=usados_e, errors="ignore")
    df_sin_s_rest = candidatos.drop(index=usados_s, errors="ignore")

    print(f"[PLACA] Nuevos conciliados: {len(df_nuevos)}")

    return df_nuevos, df_sin_e_rest, df_sin_s_rest


def XLSX_UUID_COL(df: pd.DataFrame) -> str | None:
    """Retorna el nombre de la columna UUID en el DataFrame de entradas."""
    for c in ["UUID", "uuid_entrada", "UUID_entrada"]:
        if c in df.columns:
            return c
    return None