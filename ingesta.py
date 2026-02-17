"""
ingesta.py — Lectura y limpieza
PROCESO 1: Limpiar AUSUR (entradas) — detectar duplicados por Tag + 10 seg
PROCESO 2: Leer salidas CSV — filtrar estatus 10
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════
# COLUMNAS REALES DE LOS ARCHIVOS
# ══════════════════════════════════════════════════════════════════════

XLSX_TAG      = "NUMERO_TAG"
XLSX_DATETIME = "FECHA_HORA"
XLSX_CARRIL   = "CVE_CARRIL"
XLSX_PLACA    = "PLACA"
XLSX_TRAMO    = "TRAMO"
XLSX_UUID     = "UUID"

CSV_TAG       = "Numero Tag"
CSV_DATETIME  = "Fecha Hora"
CSV_CUERPO    = "Cve Cuerpo"   # A=entrada, B=salida
CSV_ESTATUS   = "Id Clasifica" # 10 = válido
CSV_CARRIL    = "Cve Carril"
CSV_PLACA     = "Placa"
CSV_UUID      = "Uuid"
CSV_CLASIF    = "Clasificacion"

VENTANA_DUPLICADO_SEG = 10   # segundos para considerar mismo registro


# ══════════════════════════════════════════════════════════════════════
# LECTURA
# ══════════════════════════════════════════════════════════════════════

def leer_xlsx_ausur(ruta: str) -> pd.DataFrame:
    """Lee el XLSX de entradas AUSUR."""
    ruta = Path(ruta)
    print(f"[INGESTA] Leyendo AUSUR: {ruta.name}")
    df = pd.read_excel(ruta, dtype=str)
    df.columns = df.columns.str.strip()
    df = df.apply(lambda c: c.str.strip() if c.dtype == object else c)
    print(f"[INGESTA] Registros AUSUR crudos: {len(df):,}")
    return df


def leer_csv_salidas(ruta: str) -> pd.DataFrame:
    """Lee el CSV de salidas (report)."""
    ruta = Path(ruta)
    print(f"[INGESTA] Leyendo salidas: {ruta.name}")
    df = pd.read_csv(
        ruta, dtype=str, encoding="utf-8",
        encoding_errors="replace", skip_blank_lines=True,
    )
    df.columns = df.columns.str.strip().str.replace('"', '')
    df = df.apply(lambda c: c.str.strip().str.replace('"', '')
                  if c.dtype == object else c)
    print(f"[INGESTA] Registros salidas crudos: {len(df):,}")
    return df


# ══════════════════════════════════════════════════════════════════════
# PROCESO 1 — LIMPIAR AUSUR
# ══════════════════════════════════════════════════════════════════════

def limpiar_ausur(df: pd.DataFrame) -> dict:
    """
    Limpia el archivo AUSUR (entradas).

    Lógica de duplicados:
      - Mismo Tag + misma fecha/hora dentro de 10 segundos → duplicado
      - Se conserva el PRIMERO en tiempo (más antiguo)
      - Los duplicados van a pestaña de revisión manual

    Retorna dict con:
      'entradas_limpias' : DataFrame listo para match
      'duplicados'       : DataFrame para revisión manual
    """
    df = df.copy()

    # ── 1. Limpiar nulos ──────────────────────────────────────────────
    df.replace(["-", "nan", "NaN", "", "NULL"], np.nan, inplace=True)

    # Limpiar basura en NOM_IMG_PLACA
    if "NOM_IMG_PLACA" in df.columns:
        df["NOM_IMG_PLACA"] = df["NOM_IMG_PLACA"].apply(
            lambda x: np.nan if pd.isna(x) or "_x0000_" in str(x) else x
        )

    # ── 2. Normalizar Tag ─────────────────────────────────────────────
    df[XLSX_TAG] = df[XLSX_TAG].str.upper().str.strip()

    # ── 3. Parsear datetime (formato XLSX: YYYY-MM-DD HH:MM:SS) ──────
    df["datetime"] = pd.to_datetime(df[XLSX_DATETIME], format="%Y-%m-%d %H:%M:%S", errors="coerce")

    mask_fecha_mala = df["datetime"].isna()
    if mask_fecha_mala.any():
        print(f"[AUSUR] Fechas no parseables descartadas: {mask_fecha_mala.sum():,}")
        df = df[~mask_fecha_mala].copy()

    # ── 4. Ordenar por Tag + datetime (para quedarnos con el primero) ─
    df = df.sort_values([XLSX_TAG, "datetime"]).reset_index(drop=True)

    # ── 5. Detectar duplicados: mismo Tag, diferencia <= 10 segundos ──
    #
    # Estrategia: dentro de cada grupo de Tag,
    # comparar cada registro contra el ANTERIOR del mismo Tag.
    # Si la diferencia de tiempo es <= 10 seg → es duplicado del anterior.

    df["_tag_ant"]  = df[XLSX_TAG].shift(1)
    df["_dt_ant"]   = df["datetime"].shift(1)
    df["_diff_seg"] = (df["datetime"] - df["_dt_ant"]).dt.total_seconds()

    # Es duplicado si: mismo Tag que el anterior Y diferencia <= 10 seg
    df["_es_duplicado"] = (
        (df[XLSX_TAG] == df["_tag_ant"]) &
        (df["_diff_seg"].abs() <= VENTANA_DUPLICADO_SEG)
    )

    df_duplicados = df[df["_es_duplicado"]].copy()
    df_limpios    = df[~df["_es_duplicado"]].copy()

    # Limpiar columnas auxiliares
    cols_aux = ["_tag_ant", "_dt_ant", "_diff_seg", "_es_duplicado"]
    df_duplicados = df_duplicados.drop(columns=cols_aux, errors="ignore")
    df_limpios    = df_limpios.drop(columns=cols_aux, errors="ignore")

    df_duplicados["motivo_revision"] = "Duplicado AUSUR (mismo Tag ≤10 seg)"

    print(f"[AUSUR] Entradas limpias  : {len(df_limpios):,}")
    print(f"[AUSUR] Duplicados marcados: {len(df_duplicados):,}")

    return {
        "entradas_limpias": df_limpios,
        "duplicados":       df_duplicados,
    }


# ══════════════════════════════════════════════════════════════════════
# PROCESO 2 — PREPARAR SALIDAS CSV
# ══════════════════════════════════════════════════════════════════════

# Estatus que van a la pestaña Pendientes (para match por placa)
ESTATUS_PENDIENTES = {"4", "8"}

def preparar_salidas_csv(df: pd.DataFrame) -> dict:
    """
    Del CSV de salidas extrae:
      - df_salidas    : tipo B estatus 10  → match por Tag
      - df_pendientes : tipo B estatus 4 u 8 → match por placa (paso 2)
      - df_descartados: tipo B cualquier otro estatus

    Retorna dict con las tres tablas.
    """
    df = df.copy()

    df_b = df[df[CSV_CUERPO] == "B"].copy()
    print(f"[SALIDAS] Total registros B: {len(df_b):,}")

    # Separar en tres grupos
    mask_10   = df_b[CSV_ESTATUS] == "10"
    mask_pend = df_b[CSV_ESTATUS].isin(ESTATUS_PENDIENTES)

    df_salidas      = df_b[mask_10].copy()
    df_pendientes   = df_b[mask_pend].copy()
    df_descartados  = df_b[~mask_10 & ~mask_pend].copy()

    print(f"[SALIDAS] Estatus 10 (match Tag)  : {len(df_salidas):,}")
    print(f"[SALIDAS] Pendientes (est. 4 y 8) : {len(df_pendientes):,}")
    print(f"[SALIDAS] Descartados             : {len(df_descartados):,}")
    if CSV_CLASIF in df_descartados.columns and len(df_descartados) > 0:
        for motivo, cnt in df_descartados[CSV_CLASIF].value_counts().items():
            print(f"          · {motivo}: {cnt:,}")

    # Normalizar salidas estatus 10
    df_salidas.replace(["-", "nan", "NaN", "", "NULL"], np.nan, inplace=True)
    df_salidas[CSV_TAG]   = df_salidas[CSV_TAG].str.upper().str.strip()
    df_salidas[CSV_PLACA] = df_salidas[CSV_PLACA].str.upper().str.strip()
    df_salidas["datetime"] = pd.to_datetime(
        df_salidas[CSV_DATETIME], format="%d/%m/%Y %H:%M:%S", errors="coerce"
    )
    mask_mala = df_salidas["datetime"].isna()
    if mask_mala.any():
        df_salidas = df_salidas[~mask_mala].copy()

    # Normalizar pendientes
    df_pendientes.replace(["-", "nan", "NaN", "", "NULL"], np.nan, inplace=True)
    df_pendientes[CSV_PLACA] = df_pendientes[CSV_PLACA].str.upper().str.strip()
    df_pendientes["datetime"] = pd.to_datetime(
        df_pendientes[CSV_DATETIME], format="%d/%m/%Y %H:%M:%S", errors="coerce"
    )
    df_pendientes["origen"] = "CSV"

    print(f"[SALIDAS] Salidas válidas para match Tag: {len(df_salidas):,}")
    return {
        "salidas":      df_salidas,
        "pendientes":   df_pendientes,
        "descartados":  df_descartados,
    }


def extraer_pendientes_ausur(df_ausur_crudo: pd.DataFrame) -> pd.DataFrame:
    """
    Extrae del AUSUR original los registros con estatus 4 u 8.
    El AUSUR no tiene columna de estatus explícita en los datos vistos,
    pero si la tuviera se filtraría aquí. Por ahora retorna vacío
    y se puede extender cuando se confirme el campo de estatus en AUSUR.
    """
    # Si el AUSUR tiene columna de estatus, filtrar aquí
    col_est = next((c for c in df_ausur_crudo.columns
                    if "clasif" in c.lower() or "status" in c.lower() or "estatus" in c.lower()), None)
    if col_est:
        mask = df_ausur_crudo[col_est].astype(str).isin(ESTATUS_PENDIENTES)
        df_pend = df_ausur_crudo[mask].copy()
        df_pend["origen"] = "AUSUR"
        print(f"[AUSUR] Pendientes (est. 4/8): {len(df_pend):,}")
        return df_pend
    return pd.DataFrame()