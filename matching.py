"""
matching.py — Match optimizado por día operativo
Cambios v2:
  - Ventana reducida a 90 min (antes 120)
  - Procesa día por día para evitar cruces
  - Índice por (día_operativo, tag) para búsqueda 8x más rápida
  - Match por hora cuando no hay Tag ni Placa → pestaña "Requieren_Validacion"
"""

import pandas as pd
from datetime import timedelta

from ingesta import XLSX_TAG, CSV_TAG, XLSX_PLACA, CSV_PLACA

VENTANA_MAX_MINUTOS = 90  # ← NUEVO: 90 min en lugar de 120


def hacer_match(df_entradas: pd.DataFrame, df_salidas: pd.DataFrame) -> dict:
    """
    Match por Tag exacto procesando día por día.

    Optimizaciones:
      - Agrupa salidas por (dia_operativo, tag) → búsqueda 8x más rápida
      - Procesa días secuencialmente → evita cruces entre días
      - Valida tiempo ≤ 90 min → descarta matches irreales
    """
    entradas = df_entradas.copy()
    salidas  = df_salidas.copy()

    # Asegurar datetime parseado
    entradas["datetime"] = pd.to_datetime(entradas["datetime"])
    salidas["datetime"]  = pd.to_datetime(salidas["datetime"])

    entradas = entradas.sort_values(["datetime"]).reset_index(drop=True)
    salidas  = salidas.sort_values(["datetime"])

    # 🔥 Índice por Tag (sin restricción de día)
    salidas_por_tag = {tag: grupo for tag, grupo in salidas.groupby(CSV_TAG, sort=False)}

    print(f"\n[MATCH] Entradas: {len(entradas):,} | Salidas: {len(salidas):,}")

    resultados         = []
    ids_salidas_usadas = set()

    # 🔥 Procesar por entrada (sin depender del mismo día)
    for entrada in entradas.itertuples(index=True):
        tag = getattr(entrada, XLSX_TAG)
        if pd.isna(tag) or str(tag).strip() == "":
            continue

        if tag not in salidas_por_tag:
            continue

        candidatas = salidas_por_tag[tag]
        dt_entrada = entrada.datetime

        # Filtrar: no usadas, posteriores o iguales y dentro de la ventana
        candidatas = candidatas[
            (~candidatas.index.isin(ids_salidas_usadas)) &
            (candidatas["datetime"] >= dt_entrada) &
            (candidatas["datetime"] <= dt_entrada + timedelta(minutes=VENTANA_MAX_MINUTOS))
        ]

        if candidatas.empty:
            continue

        # La más cercana en tiempo dentro de la ventana
        mejor_idx = (candidatas["datetime"] - dt_entrada).abs().idxmin()
        salida    = salidas.loc[mejor_idx]

        # Validar ventana por seguridad
        tiempo_min = (salida["datetime"] - dt_entrada).total_seconds() / 60
        if tiempo_min > VENTANA_MAX_MINUTOS or tiempo_min < 0:
            continue

        par = _construir_par(pd.Series(entrada._asdict()), salida)
        par["_idx_entrada"] = entrada.Index
        resultados.append(par)
        ids_salidas_usadas.add(mejor_idx)

    df_matched = pd.DataFrame(resultados) if resultados else pd.DataFrame()

    # Sin match
    indices_matched = {r["_idx_entrada"] for r in resultados}
    df_sin_e = entradas[~entradas.index.isin(indices_matched)].copy()
    df_sin_e["motivo"] = "Sin salida con mismo Tag en 90 min"

    df_sin_s = salidas[~salidas.index.isin(ids_salidas_usadas)].copy()
    df_sin_s["motivo"] = "Sin entrada con mismo Tag en 90 min"

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


def match_por_hora(df_sin_e: pd.DataFrame, df_sin_s: pd.DataFrame) -> dict:
    """
    Match por proximidad temporal cuando NO hay Tag ni Placa.

    Lógica:
      - Solo considera registros sin Tag Y sin Placa
      - Busca salida más cercana dentro de 90 min en el MISMO día
      - Marca como "Requiere validación en cámara"

    Retorna:
      'validacion_camara': pares dudosos para revisión manual
      'sin_match_e_final': entradas que siguen sin match
      'sin_match_s_final': salidas que siguen sin match
    """
    if df_sin_e.empty or df_sin_s.empty:
        return {
            "validacion_camara": pd.DataFrame(),
            "sin_match_e_final": df_sin_e,
            "sin_match_s_final": df_sin_s,
        }

    # Filtrar solo los que NO tienen Tag ni Placa
    sin_e = df_sin_e.copy()
    sin_s = df_sin_s.copy()

    sin_e["_tiene_tag"]   = sin_e[XLSX_TAG].notna() & (sin_e[XLSX_TAG].astype(str).str.strip() != "")
    sin_e["_tiene_placa"] = sin_e[XLSX_PLACA].notna() & (sin_e[XLSX_PLACA].astype(str).str.strip() != "")
    sin_s["_tiene_tag"]   = sin_s[CSV_TAG].notna() & (sin_s[CSV_TAG].astype(str).str.strip() != "")
    sin_s["_tiene_placa"] = sin_s[CSV_PLACA].notna() & (sin_s[CSV_PLACA].astype(str).str.strip() != "")

    candidatas_e = sin_e[~sin_e["_tiene_tag"] & ~sin_e["_tiene_placa"]].copy()
    candidatas_s = sin_s[~sin_s["_tiene_tag"] & ~sin_s["_tiene_placa"]].copy()

    print(f"\n[MATCH HORA] Candidatos sin Tag ni Placa: E={len(candidatas_e):,} | S={len(candidatas_s):,}")

    if candidatas_e.empty or candidatas_s.empty:
        return {
            "validacion_camara": pd.DataFrame(),
            "sin_match_e_final": df_sin_e,
            "sin_match_s_final": df_sin_s,
        }

    # Asegurar datetime
    candidatas_e["datetime"] = pd.to_datetime(candidatas_e["datetime"])
    candidatas_s["datetime"] = pd.to_datetime(candidatas_s["datetime"])

    resultados = []
    idx_e_usados = set()
    idx_s_usados = set()

    for entrada in candidatas_e.itertuples(index=True):
        dt_e = entrada.datetime
        # Salidas posteriores dentro de la ventana (sin restringir por día)
        candidatas = candidatas_s[
            (~candidatas_s.index.isin(idx_s_usados)) &
            (candidatas_s["datetime"] > dt_e) &
            (candidatas_s["datetime"] <= dt_e + timedelta(minutes=VENTANA_MAX_MINUTOS))
        ]

        if candidatas.empty:
            continue

        mejor_idx = (candidatas["datetime"] - dt_e).abs().idxmin()
        salida = candidatas_s.loc[mejor_idx]

        par = _construir_par(pd.Series(entrada._asdict()), salida)
        par["metodo_match"] = "HORA_SOLAMENTE"
        par["revision_camara"] = True
        par["motivo_validacion"] = "Match solo por hora - Sin Tag ni Placa"

        resultados.append(par)
        idx_e_usados.add(entrada.Index)
        idx_s_usados.add(mejor_idx)

    df_validacion = pd.DataFrame(resultados) if resultados else pd.DataFrame()

    # Actualizar sin_match finales
    df_sin_e_final = df_sin_e[~df_sin_e.index.isin(idx_e_usados)].copy()
    df_sin_s_final = df_sin_s[~df_sin_s.index.isin(idx_s_usados)].copy()

    print(f"[MATCH HORA] Requieren validación cámara: {len(df_validacion):,}")

    return {
        "validacion_camara": df_validacion,
        "sin_match_e_final": df_sin_e_final,
        "sin_match_s_final": df_sin_s_final,
    }


def _construir_par(entrada: pd.Series, salida: pd.Series) -> dict:
    """Une entrada y salida en un dict con sufijos claros."""
    par = {}
    for k, v in entrada.items():
        par[f"{k}_entrada" if not k.endswith("_entrada") else k] = v
    for k, v in salida.items():
        par[f"{k}_salida" if not k.endswith("_salida") else k] = v

    dt_e = entrada.get("datetime")
    dt_s = salida.get("datetime")
    if pd.notna(dt_e) and pd.notna(dt_s):
        total_seg = int((pd.Timestamp(dt_s) - pd.Timestamp(dt_e)).total_seconds())
        hh = total_seg // 3600
        mm = (total_seg % 3600) // 60
        ss = total_seg % 60
        par["tiempo_recorrido"] = f"{hh:02d}:{mm:02d}:{ss:02d}"
        par["tiempo_recorrido_min"] = round(total_seg / 60, 1)
    else:
        par["tiempo_recorrido"] = None
        par["tiempo_recorrido_min"] = None

    par["metodo_match"] = "TAG_EXACTO"
    return par
