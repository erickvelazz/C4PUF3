"""
match_placa.py — Match por placa optimizado
v2: Procesamiento por día + validación 90 min
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
    for df in [pend, sin_e, sin_s]:
        if "dia_operativo" not in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df["dia_operativo"] = df["datetime"].dt.date

    nuevos       = []
    idx_e_usados = set()
    idx_s_usados = set()
    idx_pend_sin = []

    print(f"\n[MATCH PLACA] Pendientes: {len(pend):,} | Sin match E: {len(sin_e):,} | S: {len(sin_s):,}")

    for idx_p, pendiente in pend.iterrows():
        placa_p = pendiente.get("_placa_norm", "")

        if not placa_p or placa_p == "-":
            idx_pend_sin.append(idx_p)
            continue

        mejor_e = _buscar_mejor_placa(
            placa_p, sin_e, idx_e_usados,
            dt_ref=pendiente.get("datetime"), es_entrada=True
        )

        mejor_s = _buscar_mejor_placa(
            placa_p, sin_s, idx_s_usados,
            dt_ref=pendiente.get("datetime"), es_entrada=False
        )

        candidato = None
        if mejor_e and mejor_s:
            candidato = mejor_e if mejor_e["score"] >= mejor_s["score"] else mejor_s
        elif mejor_e:
            candidato = mejor_e
        elif mejor_s:
            candidato = mejor_s

        if candidato:
            par = _construir_par_placa(pendiente, candidato)
            nuevos.append(par)
            if candidato["origen"] == "entrada":
                idx_e_usados.add(candidato["idx"])
            else:
                idx_s_usados.add(candidato["idx"])
        else:
            idx_pend_sin.append(idx_p)

    df_nuevos     = pd.DataFrame(nuevos) if nuevos else pd.DataFrame()
    df_pend_sin   = pend.loc[idx_pend_sin].drop(columns=["_placa_norm"], errors="ignore").copy()
    df_sin_e_rest = sin_e[~sin_e.index.isin(idx_e_usados)].drop(columns=["_placa_norm"], errors="ignore").copy()
    df_sin_s_rest = sin_s[~sin_s.index.isin(idx_s_usados)].drop(columns=["_placa_norm"], errors="ignore").copy()

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
    par = {}
    fila = candidato["fila"]

    def copiar_lado(s: pd.Series, lado: str) -> dict:
        out = {}
        for k, v in s.items():
            if k == "_placa_norm":
                continue
            key = k
            if not (k.endswith("_entrada") or k.endswith("_salida")):
                key = f"{k}_{lado}"
            if key not in out:
                out[key] = v
            else:
                if pd.isna(out[key]) and not pd.isna(v):
                    out[key] = v
        return out

    if candidato["origen"] == "entrada":
        entrada_dict = copiar_lado(fila, "entrada")
        salida_dict  = copiar_lado(pendiente, "salida")
    else:
        entrada_dict = copiar_lado(pendiente, "entrada")
        salida_dict  = copiar_lado(fila, "salida")

    for k, v in entrada_dict.items():
        if k not in par:
            par[k] = v
        else:
            if pd.isna(par[k]) and not pd.isna(v):
                par[k] = v
    for k, v in salida_dict.items():
        if k not in par:
            par[k] = v
        else:
            if pd.isna(par[k]) and not pd.isna(v):
                par[k] = v

    par["metodo_match"] = "PLACA_DIFUSA"
    par["pct_match_placa"] = f"{candidato['score']:.0%}"
    par["origen"] = "PENDIENTE_PLACA"

    dt_e = par.get("datetime_entrada") or par.get("FECHA_HORA_entrada")
    dt_s = par.get("datetime_salida") or par.get("Fecha Hora_salida")
    if dt_e and dt_s:
        try:
            total_seg = int((pd.Timestamp(dt_s) - pd.Timestamp(dt_e)).total_seconds())
            if 0 < total_seg <= VENTANA_MAX_MINUTOS * 60:
                hh = total_seg // 3600
                mm = (total_seg % 3600) // 60
                ss = total_seg % 60
                par["tiempo_recorrido"] = f"{hh:02d}:{mm:02d}:{ss:02d}"
                par["tiempo_recorrido_min"] = round(total_seg / 60, 1)
        except Exception:
            pass

    return par
