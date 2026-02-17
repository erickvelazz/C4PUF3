"""
match_placa.py — Match difuso por placa entre Pendientes y sin-match
Proceso:
  1. Separar pendientes (status 4 y 8) del resto de descartados
  2. Para cada pendiente buscar la mejor placa en entradas sin match Y salidas sin match
  3. Score carácter por carácter usando SequenceMatcher
  4. Umbral mínimo: 75% (captura hasta 2 dígitos distintos en placa de 7 chars)
  5. Los que superan el umbral van a Conciliados con bandera origen="PENDIENTE_PLACA"
"""

import pandas as pd
import numpy as np
from difflib import SequenceMatcher

UMBRAL_PLACA     = 0.75   # 75% — permite hasta 2 chars distintos en placa de 7
STATUS_PENDIENTE = ["4", "8"]

# Columnas de placa en cada archivo
PLACA_CSV  = "Placa"
PLACA_XLSX = "PLACA"


# ══════════════════════════════════════════════════════════════════════
# SEPARAR PENDIENTES DEL RESTO DE DESCARTADOS
# ══════════════════════════════════════════════════════════════════════

def separar_pendientes(df_descartados: pd.DataFrame) -> dict:
    """
    Divide df_descartados en:
      'pendientes' : status 4 y 8  → van al match por placa
      'resto'      : cualquier otro status
    """
    if df_descartados.empty:
        return {"pendientes": pd.DataFrame(), "resto": pd.DataFrame()}

    col_status = "Id Clasifica"
    if col_status not in df_descartados.columns:
        # Buscar columna alternativa
        for c in df_descartados.columns:
            if "clasifica" in c.lower() or "status" in c.lower() or "estatus" in c.lower():
                col_status = c
                break

    mask_pend    = df_descartados[col_status].astype(str).isin(STATUS_PENDIENTE)
    df_pendientes = df_descartados[mask_pend].copy()
    df_resto      = df_descartados[~mask_pend].copy()

    print(f"[PENDIENTES] Status 4 y 8: {len(df_pendientes):,}")
    print(f"[PENDIENTES] Resto descartados: {len(df_resto):,}")

    return {"pendientes": df_pendientes, "resto": df_resto}


# ══════════════════════════════════════════════════════════════════════
# MATCH DIFUSO POR PLACA
# ══════════════════════════════════════════════════════════════════════

def match_por_placa(
    df_pendientes: pd.DataFrame,
    df_sin_match_e: pd.DataFrame,
    df_sin_match_s: pd.DataFrame,
) -> dict:
    """
    Hace match difuso por placa entre:
      - df_pendientes  ← los status 4 y 8
      - df_sin_match_e ← entradas AUSUR sin match de Tag
      - df_sin_match_s ← salidas CSV sin match de Tag

    Para cada pendiente:
      1. Extrae la placa y la normaliza
      2. Compara carácter por carácter contra todas las placas en sin_match_e y sin_match_s
      3. Toma el mejor candidato si supera el umbral 75%
      4. Prioriza sin_match_e, si no encuentra ahí busca en sin_match_s

    Retorna:
      'nuevos_conciliados': pares con bandera origen='PENDIENTE_PLACA' y columna pct_match
      'pendientes_sin_match': pendientes que no encontraron pareja
      'sin_match_e_restante': entradas que siguen sin match
      'sin_match_s_restante': salidas que siguen sin match
    """
    if df_pendientes.empty:
        return {
            "nuevos_conciliados":    pd.DataFrame(),
            "pendientes_sin_match":  df_pendientes,
            "sin_match_e_restante":  df_sin_match_e,
            "sin_match_s_restante":  df_sin_match_s,
        }

    # Normalizar placas en los tres DataFrames
    pend  = df_pendientes.copy()
    sin_e = df_sin_match_e.copy()
    sin_s = df_sin_match_s.copy()

    pend["_placa_norm"]  = _norm_placa(pend,  PLACA_CSV)
    sin_e["_placa_norm"] = _norm_placa(sin_e, PLACA_XLSX)
    sin_s["_placa_norm"] = _norm_placa(sin_s, PLACA_CSV)

    nuevos          = []
    idx_e_usados    = set()
    idx_s_usados    = set()
    idx_pend_sin    = []

    print(f"\n[MATCH PLACA] Pendientes: {len(pend):,} | Sin match E: {len(sin_e):,} | Sin match S: {len(sin_s):,}")

    for idx_p, pendiente in pend.iterrows():
        placa_p = pendiente.get("_placa_norm", "")

        if not placa_p or placa_p == "-":
            idx_pend_sin.append(idx_p)
            continue

        # ── Buscar en entradas sin match ──────────────────────────────
        mejor_e = _buscar_mejor(placa_p, sin_e, idx_e_usados)

        # ── Buscar en salidas sin match ───────────────────────────────
        mejor_s = _buscar_mejor(placa_p, sin_s, idx_s_usados)

        # ── Elegir el mejor entre E y S ───────────────────────────────
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

    # Consolidar resultados
    df_nuevos = pd.DataFrame(nuevos) if nuevos else pd.DataFrame()

    df_pend_sin = pend.loc[idx_pend_sin].drop(columns=["_placa_norm"], errors="ignore").copy()
    df_pend_sin["motivo"] = "Sin placa coincidente (score < 75%)"

    df_sin_e_rest = sin_e[~sin_e.index.isin(idx_e_usados)].drop(columns=["_placa_norm"], errors="ignore").copy()
    df_sin_s_rest = sin_s[~sin_s.index.isin(idx_s_usados)].drop(columns=["_placa_norm"], errors="ignore").copy()

    print(f"[MATCH PLACA] Nuevos conciliados por placa: {len(df_nuevos):,}")
    print(f"[MATCH PLACA] Pendientes sin match:         {len(df_pend_sin):,}")

    return {
        "nuevos_conciliados":   df_nuevos,
        "pendientes_sin_match": df_pend_sin,
        "sin_match_e_restante": df_sin_e_rest,
        "sin_match_s_restante": df_sin_s_rest,
    }


# ══════════════════════════════════════════════════════════════════════
# UTILIDADES INTERNAS
# ══════════════════════════════════════════════════════════════════════

def _norm_placa(df: pd.DataFrame, col: str) -> pd.Series:
    """Normaliza placa: mayúsculas, sin espacios, '-' y nulos → ''."""
    if col not in df.columns:
        return pd.Series([""] * len(df), index=df.index)
    return (
        df[col]
        .fillna("")
        .astype(str)
        .str.upper()
        .str.strip()
        .str.replace("-", "", regex=False)
        .str.replace(" ", "", regex=False)
        .replace("NAN", "")
    )


def _score_placa(a: str, b: str) -> float:
    """Score carácter por carácter usando SequenceMatcher (0.0 a 1.0)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _buscar_mejor(placa_p: str, df_candidatos: pd.DataFrame, idx_usados: set) -> dict | None:
    """
    Busca el mejor match de placa_p en df_candidatos.
    Retorna dict con idx, score y origen, o None si no supera el umbral.
    """
    disponibles = df_candidatos[~df_candidatos.index.isin(idx_usados)]
    if disponibles.empty:
        return None

    scores = disponibles["_placa_norm"].apply(
        lambda p: _score_placa(placa_p, p) if p else 0.0
    )

    mejor_score = scores.max()
    if mejor_score < UMBRAL_PLACA:
        return None

    mejor_idx    = scores.idxmax()
    origen       = "entrada" if "_placa_norm" in df_candidatos.columns and \
                   "NUMERO_TAG" in df_candidatos.columns else "salida"
    # Detectar si es entrada (AUSUR) o salida (CSV) por columnas presentes
    if "NUMERO_TAG" in df_candidatos.columns or "FECHA_HORA" in df_candidatos.columns:
        origen = "entrada"
    else:
        origen = "salida"

    return {
        "idx":    mejor_idx,
        "score":  mejor_score,
        "origen": origen,
        "fila":   disponibles.loc[mejor_idx],
    }


def _construir_par_placa(pendiente: pd.Series, candidato: dict) -> dict:
    """Construye el par conciliado por placa con bandera y score."""
    par = {}
    fila = candidato["fila"]

    if candidato["origen"] == "entrada":
        # pendiente es la salida, fila es la entrada
        for k, v in fila.items():
            if k != "_placa_norm":
                par[f"{k}_entrada" if not k.endswith("_entrada") else k] = v
        for k, v in pendiente.items():
            if k != "_placa_norm":
                par[f"{k}_salida" if not k.endswith("_salida") else k] = v
    else:
        # pendiente es la entrada, fila es la salida
        for k, v in pendiente.items():
            if k != "_placa_norm":
                par[f"{k}_entrada" if not k.endswith("_entrada") else k] = v
        for k, v in fila.items():
            if k != "_placa_norm":
                par[f"{k}_salida" if not k.endswith("_salida") else k] = v

    par["metodo_match"] = "TAG_EXACTO"   # se sobreescribirá abajo
    par["metodo_match"] = "PLACA_DIFUSA"
    par["pct_match_placa"] = f"{candidato['score']:.0%}"
    par["origen"]          = "PENDIENTE_PLACA"   # ← bandera
    par["tiempo_recorrido"] = None               # no calculable sin datetime confiable

    # Intentar calcular tiempo si hay datetime en ambos lados
    dt_e = par.get("datetime_entrada") or par.get("FECHA_HORA_entrada")
    dt_s = par.get("datetime_salida")  or par.get("Fecha Hora_salida")
    if dt_e and dt_s:
        try:
            total_seg = int((pd.Timestamp(dt_s) - pd.Timestamp(dt_e)).total_seconds())
            if total_seg > 0:
                hh = total_seg // 3600
                mm = (total_seg % 3600) // 60
                ss = total_seg % 60
                par["tiempo_recorrido"] = f"{hh:02d}:{mm:02d}:{ss:02d}"
        except Exception:
            pass

    return par