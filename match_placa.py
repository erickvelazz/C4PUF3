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

    stats = {
        "total_procesados": 0,
        "match_exitoso": 0,
        "rechazados": 0,
        "duplicados_detectados": 0,
        "resuelto_por_placa": 0,
        "resuelto_por_tiempo": 0,
        "resuelto_unico": 0,
        "detalle_rechazos": {}
    }

    print(f"\n[MATCH PLACA] Pendientes (B): {len(pend):,} | Candidatos Entradas (A): {len(sin_e):,}")

    for idx_p, pendiente in pend.iterrows():
        stats["total_procesados"] += 1
        
        # ── VALIDACIÓN DIRECCIONALIDAD A-B ─────────────────────────────
        # El pendiente debe ser SALIDA (B) para buscar en ENTRADA (A).
        cuerpo_p = pendiente.get("Cuerpo")
        if cuerpo_p and cuerpo_p != "B":
            idx_pend_sin.append(idx_p)
            motivo = f"Direccionalidad incorrecta: Pendiente es {cuerpo_p}"
            stats["detalle_rechazos"][motivo] = stats["detalle_rechazos"].get(motivo, 0) + 1
            continue

        placa_p = pendiente.get("_placa_norm", "")

        if not placa_p or placa_p == "-":
            idx_pend_sin.append(idx_p)
            motivo = "Sin información de placa"
            stats["detalle_rechazos"][motivo] = stats["detalle_rechazos"].get(motivo, 0) + 1
            continue

        # Solo buscamos en sin_e (Entradas - A)
        res_match = _buscar_mejor_placa(
            placa_p, sin_e, idx_e_usados,
            dt_ref=pendiente.get("datetime"), es_entrada=True
        )

        if res_match:
            candidato = res_match
            # Validación final de direccionalidad antes de aceptar
            cuerpo_c = candidato["fila"].get("Cuerpo", "A") 
            
            if cuerpo_c == "B":
                 idx_pend_sin.append(idx_p)
                 motivo = "Direccionalidad incorrecta: Candidato es Salida (B)"
                 stats["detalle_rechazos"][motivo] = stats["detalle_rechazos"].get(motivo, 0) + 1
                 continue

            par = _construir_par_placa(pendiente, candidato)
            nuevos.append(par)
            idx_e_usados.add(candidato["idx"])
            stats["match_exitoso"] += 1
            
            # Actualizar estadísticas de resolución
            metodo = candidato.get("metodo_resolucion", "Unico")
            if "Prioridad Placa" in metodo:
                stats["resuelto_por_placa"] += 1
                stats["duplicados_detectados"] += 1
            elif "Cercanía Temporal" in metodo and "Duplicado" in metodo:
                stats["resuelto_por_tiempo"] += 1
                stats["duplicados_detectados"] += 1
            else:
                stats["resuelto_unico"] += 1

        else:
            idx_pend_sin.append(idx_p)
            motivo = "Sin coincidencia (Score < 75% o fuera de tiempo)"
            stats["detalle_rechazos"][motivo] = stats["detalle_rechazos"].get(motivo, 0) + 1

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
        "stats":                stats
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
    
    # 1. Valid candidates
    candidatos_validos = disponibles[scores >= UMBRAL_PLACA].copy()
    candidatos_validos["_score"] = scores[scores >= UMBRAL_PLACA]
    
    if candidatos_validos.empty:
        return None

    # Calculate time diff for all (Referencia vs Candidato)
    dt_ref_ts = pd.Timestamp(dt_ref)
    candidatos_validos["_diff_abs"] = (candidatos_validos["datetime"] - dt_ref_ts).abs()
    
    # 2. Sort by Score (Desc) then Time Diff (Asc) to find the "Default Best"
    # Esto prioriza Score alto y luego cercanía a la referencia
    candidatos_validos = candidatos_validos.sort_values(
        by=["_score", "_diff_abs"], 
        ascending=[False, True]
    )
    
    best_candidate = candidatos_validos.iloc[0]
    best_idx = candidatos_validos.index[0]
    top_score = best_candidate["_score"]
    
    # 3. Detect duplicates within 15s of the Best Candidate
    # Definición de duplicado: Score idéntico (o top) Y tiempo dentro de ±15s del mejor candidato
    
    # Primero filtramos por score (solo los top)
    score_ties = candidatos_validos[candidatos_validos["_score"] == top_score].copy()
    
    # Filtrar los que están en la ventana de 15s del MEJOR candidato
    best_time = best_candidate["datetime"]
    
    def is_within_15s(row):
        delta = abs(row["datetime"] - best_time)
        return delta.total_seconds() <= 15
        
    duplicates_in_window = score_ties[score_ties.apply(is_within_15s, axis=1)]
    
    origen = "entrada" if "NUMERO_TAG" in df_candidatos.columns else "salida"

    if len(duplicates_in_window) == 1:
        # No hay duplicados en conflicto (solo el ganador)
        return {
            "idx":    best_idx,
            "score":  top_score,
            "origen": origen,
            "fila":   best_candidate,
            "metodo_resolucion": "Unico (Mejor Score/Tiempo)"
        }
        
    # 4. Resolver conflicto de duplicados
    # Regla 1: Prioridad Placa Registrada
    col_placa_orig = PLACA_XLSX if "NUMERO_TAG" in df_candidatos.columns else PLACA_CSV
    
    def tiene_placa_valida(row):
        val = str(row.get(col_placa_orig, "")).strip()
        return val and val not in ["-", "nan", "NaN", "NULL", ""]
        
    duplicates_in_window["_tiene_placa"] = duplicates_in_window.apply(tiene_placa_valida, axis=1)
    
    con_placa = duplicates_in_window[duplicates_in_window["_tiene_placa"]]
    
    metodo_res = ""
    pool_final = duplicates_in_window
    
    if not con_placa.empty and len(con_placa) < len(duplicates_in_window):
        # Algunos tienen placa y otros no -> Ganan los que tienen placa
        pool_final = con_placa
        metodo_res = "Prioridad Placa"
    else:
        # Todos tienen o ninguno tiene -> Decidimos por cercanía
        metodo_res = "Cercanía Temporal"
        
    # Regla 2: Cercanía Geográfica (Tiempo)
    # Ordenamos el pool final por cercanía a la REFERENCIA (dt_ref)
    pool_final = pool_final.sort_values("_diff_abs", ascending=True)
    
    ganador_idx = pool_final.index[0]
    ganador_row = pool_final.loc[ganador_idx]
    
    if metodo_res == "Prioridad Placa":
        metodo_res += " + Cercanía"
    else:
        metodo_res += " (Duplicado Resuelto)"
        
    return {
        "idx":    ganador_idx,
        "score":  ganador_row["_score"],
        "origen": origen,
        "fila":   ganador_row,
        "metodo_resolucion": metodo_res
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

    # NUEVO: Registrar método de resolución de duplicados si existe
    if "metodo_resolucion" in candidato:
        par["metodo_resolucion"] = candidato["metodo_resolucion"]
    
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