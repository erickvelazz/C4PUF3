"""
match_placa.py — Match por placa optimizado
v3: Fix orden de columnas consistente
"""

import pandas as pd
import numpy as np
from difflib import SequenceMatcher
from datetime import timedelta

UMBRAL_PLACA          = 0.75
VENTANA_MAX_MINUTOS   = 90
PLACA_CSV  = "Placa"
PLACA_XLSX = "PLACA"


def match_por_placa(
    df_pendientes: pd.DataFrame,
    df_sin_match_e: pd.DataFrame,
    df_sin_match_s: pd.DataFrame,
) -> dict:
    """
    Match por placa procesando día por día.
    Solo acepta si tiempo ≤ 90 min.
    """
    if df_pendientes.empty:
        return {
            "nuevos_conciliados":   pd.DataFrame(),
            "pendientes_sin_match": df_pendientes,
            "sin_match_e_final":    df_sin_match_e,
            "sin_match_s_final":    df_sin_match_s,
        }

    pend  = df_pendientes.copy()
    sin_e = df_sin_match_e.copy()
    sin_s = df_sin_match_s.copy()

    # Normalizar placas
    pend["_placa_norm"]  = _norm_placa(pend,  PLACA_CSV)
    sin_e["_placa_norm"] = _norm_placa(sin_e, PLACA_XLSX)
    sin_s["_placa_norm"] = _norm_placa(sin_s, PLACA_CSV)

    # Asegurar dia_operativo
    for df in [pend, sin_e]:
        if "dia_operativo" not in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df["dia_operativo"] = df["datetime"].dt.date

    nuevos       = []
    idx_e_usados = set()
    idx_s_usados = set() # Se mantiene para compatibilidad de retorno, pero no se usa en búsqueda
    idx_pend_sin = []

    print(f"\n[MATCH PLACA] Pendientes (B): {len(pend):,} | Candidatos Entradas (A): {len(sin_e):,}")

    for idx_p, pendiente in pend.iterrows():
        # ── VALIDACIÓN DIRECCIONALIDAD A-B ─────────────────────────────
        # El pendiente debe ser SALIDA (B) para buscar en ENTRADA (A).
        # Si por alguna razón llega un registro que no es B, se salta o se maneja.
        # Asumimos que ingesta.py ya filtró, pero validamos campo Cuerpo si existe.
        cuerpo_p = pendiente.get("Cuerpo")
        if cuerpo_p and cuerpo_p != "B":
            # Si no es B, no debería estar aquí según la lógica actual (pendientes vienen de Salidas)
            # O si es A, debería buscar en Salidas, pero el flujo actual es Salidas -> Entradas.
            print(f"[MATCH PLACA] IGNORADO: Pendiente con Cuerpo '{cuerpo_p}' (se esperaba B)")
            idx_pend_sin.append(idx_p)
            continue

        placa_p = pendiente.get("_placa_norm", "")

        if not placa_p or placa_p == "-":
            idx_pend_sin.append(idx_p)
            continue

        # Solo buscamos en sin_e (Entradas - A)
        # Nunca buscamos en sin_s (Salidas - B) para evitar match B-B
        mejor_e = _buscar_mejor_placa(
            placa_p, sin_e, idx_e_usados,
            dt_ref=pendiente.get("datetime"), es_entrada=True
        )

        candidato = mejor_e

        if candidato:
            # Validación final de direccionalidad antes de aceptar
            cuerpo_c = candidato["fila"].get("Cuerpo", "A") # Asumimos A para sin_e si no tiene campo
            
            # Match válido solo si son opuestos (A vs B)
            # Como sabemos que pendiente es B (o asumimos), candidato debe ser A.
            if cuerpo_c == "B":
                 # Error: Match B-B detectado
                 print(f"[MATCH PLACA] RECHAZADO: Candidato es Cuerpo B (Salida) para Pendiente {placa_p}")
                 idx_pend_sin.append(idx_p)
                 continue

            par = _construir_par_placa(pendiente, candidato)
            nuevos.append(par)
            idx_e_usados.add(candidato["idx"])
        else:
            idx_pend_sin.append(idx_p)

    df_nuevos     = pd.DataFrame(nuevos) if nuevos else pd.DataFrame()
    df_pend_sin   = pend.loc[idx_pend_sin].drop(columns=["_placa_norm"], errors="ignore").copy()
    df_sin_e_rest = sin_e[~sin_e.index.isin(idx_e_usados)].drop(columns=["_placa_norm"], errors="ignore").copy()
    # sin_s no se toca en este proceso
    df_sin_s_rest = sin_s.drop(columns=["_placa_norm"], errors="ignore").copy()

    df_pend_sin["motivo"] = "Sin placa con score ≥75% en 90 min"

    print(f"[MATCH PLACA] Nuevos conciliados: {len(df_nuevos):,}")
    print(f"[MATCH PLACA] Pendientes sin match: {len(df_pend_sin):,}")

    return {
        "nuevos_conciliados":   df_nuevos,
        "pendientes_sin_match": df_pend_sin,
        "sin_match_e_final":    df_sin_e_rest,
        "sin_match_s_final":    df_sin_s_rest,
    }


def _norm_placa(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    return (
        df[col].fillna("").astype(str).str.upper().str.strip()
        .str.replace("-", "", regex=False).str.replace(" ", "", regex=False)
        .replace("NAN", "")
    )


def _score_placa(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _buscar_mejor_placa(placa_p: str, df_candidatos: pd.DataFrame,
                        idx_usados: set, dt_ref=None, es_entrada=True) -> dict | None:
    """
    Busca mejor match validando tiempo ≤ 90 min.
    """
    disponibles = df_candidatos.loc[
        ~df_candidatos.index.isin(idx_usados)
    ].copy()
    if disponibles.empty:
        return None

    # 🔥 Filtrar por tiempo si hay datetime
    if dt_ref is not None and pd.notna(dt_ref):
        dt_ref = pd.Timestamp(dt_ref)
        disponibles["datetime"] = pd.to_datetime(disponibles["datetime"], errors="coerce")

        if es_entrada:
            # pendiente es salida, busco entrada anterior
            disponibles = disponibles[
                (disponibles["datetime"] < dt_ref) &
                (disponibles["datetime"] >= dt_ref - timedelta(minutes=VENTANA_MAX_MINUTOS))
            ]
        else:
            # pendiente es entrada, busco salida posterior
            disponibles = disponibles[
                (disponibles["datetime"] > dt_ref) &
                (disponibles["datetime"] <= dt_ref + timedelta(minutes=VENTANA_MAX_MINUTOS))
            ]

    if disponibles.empty:
        return None

    scores = disponibles["_placa_norm"].apply(
        lambda p: _score_placa(placa_p, p) if p else 0.0
    )

    mejor_score = scores.max()
    if mejor_score < UMBRAL_PLACA:
        return None

    mejor_idx = scores.idxmax()
    origen = "entrada" if "NUMERO_TAG" in df_candidatos.columns else "salida"

    return {
        "idx":    mejor_idx,
        "score":  mejor_score,
        "origen": origen,
        "fila":   disponibles.loc[mejor_idx],
    }


def _construir_par_placa(pendiente: pd.Series, candidato: dict) -> dict:
    """
    Construye par con orden CONSISTENTE de columnas.
    
    Estrategia:
    1. Primero todas las columnas de ENTRADA con sufijo _entrada
    2. Luego todas las columnas de SALIDA con sufijo _salida
    3. Al final campos calculados (metodo_match, tiempo, etc)
    """
    par = {}
    fila = candidato["fila"]
    
    # Determinar quién es entrada y quién es salida
    if candidato["origen"] == "entrada":
        entrada_series = fila
        salida_series  = pendiente
    else:
        entrada_series = pendiente
        salida_series  = fila
    
    # 🔥 PASO 1: Agregar TODAS las columnas de entrada (orden preservado)
    for k, v in entrada_series.items():
        if k == "_placa_norm":  # Saltar columnas internas
            continue
        # Asegurar sufijo _entrada
        col_name = k if k.endswith("_entrada") else f"{k}_entrada"
        par[col_name] = v
    
    # 🔥 PASO 2: Agregar TODAS las columnas de salida (orden preservado)
    for k, v in salida_series.items():
        if k == "_placa_norm":
            continue
        # Asegurar sufijo _salida
        col_name = k if k.endswith("_salida") else f"{k}_salida"
        par[col_name] = v
    
    # 🔥 PASO 3: Campos calculados al final (siempre en el mismo orden)
    par["metodo_match"]    = "PLACA_DIFUSA"
    par["pct_match_placa"] = f"{candidato['score']:.0%}"
    par["origen"]          = "PENDIENTE_PLACA"
    
    # Calcular tiempo de recorrido
    dt_e = par.get("datetime_entrada") or par.get("FECHA_HORA_entrada")
    dt_s = par.get("datetime_salida")  or par.get("Fecha Hora_salida")
    
    if dt_e and dt_s:
        try:
            total_seg = int((pd.Timestamp(dt_s) - pd.Timestamp(dt_e)).total_seconds())
            if 0 < total_seg <= VENTANA_MAX_MINUTOS * 60:
                hh = total_seg // 3600
                mm = (total_seg % 3600) // 60
                ss = total_seg % 60
                par["tiempo_recorrido"]     = f"{hh:02d}:{mm:02d}:{ss:02d}"
                par["tiempo_recorrido_min"] = round(total_seg / 60, 1)
            else:
                par["tiempo_recorrido"]     = None
                par["tiempo_recorrido_min"] = None
        except Exception:
            par["tiempo_recorrido"]     = None
            par["tiempo_recorrido_min"] = None
    else:
        par["tiempo_recorrido"]     = None
        par["tiempo_recorrido_min"] = None

    return par