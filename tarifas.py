"""
FASE 5: Cálculo de Tarifas y Validaciones de Negocio
RF-C04: Tarifa según tramo recorrido y clase del vehículo.
RF-C05: Vigencias de Tag y descuentos a beneficiarios.
"""

import pandas as pd
from datetime import date


# ─────────────────────────────────────────────
# Ejemplo de matriz tarifaria (reemplaza con tu data real)
# Estructura: {(tramo, clase): importe}
# Tramo = combinación "caseta_entrada-caseta_salida"
# Clase = clasificación vehicular (1=Auto, 2=Moto, 3=Bus, 4=Camión, etc.)
# ─────────────────────────────────────────────
MATRIZ_TARIFAS: dict[tuple, float] = {
    ("NORTE-SUR", "1"): 85.00,
    ("NORTE-SUR", "2"): 45.00,
    ("NORTE-SUR", "3"): 165.00,
    ("NORTE-SUR", "4"): 210.00,
    ("SUR-NORTE", "1"): 85.00,
    ("SUR-NORTE", "2"): 45.00,
    ("SUR-NORTE", "3"): 165.00,
    ("SUR-NORTE", "4"): 210.00,
    # Agrega todos tus tramos aquí...
}

# Porcentaje de descuento para beneficiarios (residentes, etc.)
DESCUENTO_BENEFICIARIO = 0.50   # 50%


def calcular_tarifa(df_matched: pd.DataFrame, matriz_tarifas: dict = None) -> pd.DataFrame:
    """
    RF-C04: Determina la tarifa para cada par Entrada-Salida según
    el tramo recorrido y la clase validada del vehículo.

    Requiere columnas en df_matched:
        - caseta_entrada  (o similar, que identifique el punto de entrada)
        - caseta_salida
        - clase_entrada o clase_salida (clase del vehículo)
    """
    if df_matched.empty:
        return df_matched

    df = df_matched.copy()
    tarifas = matriz_tarifas or MATRIZ_TARIFAS

    # ── 1. Construir el identificador del tramo ────────────────────────
    # Ajusta los nombres de columna a los de tu CLR real
    col_caseta_e = _buscar_columna(df, ["caseta_entrada", "plaza_entrada", "punto_entrada"])
    col_caseta_s = _buscar_columna(df, ["caseta_salida",  "plaza_salida",  "punto_salida"])
    col_clase    = _buscar_columna(df, ["clase_entrada", "clase_salida", "clase"])

    if not col_caseta_e or not col_caseta_s:
        print("[TARIFAS] ADVERTENCIA: No se encontraron columnas de caseta. "
              "Verificar nombres de columnas.")
        df["tramo"] = "DESCONOCIDO"
    else:
        df["tramo"] = df[col_caseta_e].str.upper() + "-" + df[col_caseta_s].str.upper()

    df["clase_normalizada"] = df[col_clase].astype(str).str.strip() if col_clase else "1"

    # ── 2. Lookup en la matriz tarifaria ──────────────────────────────
    df["tarifa_calculada"] = df.apply(
        lambda row: tarifas.get(
            (row["tramo"], row["clase_normalizada"]),
            None   # None = tramo/clase no encontrado en la matriz
        ),
        axis=1
    )

    # Alertar sobre tramos sin tarifa
    sin_tarifa = df["tarifa_calculada"].isna().sum()
    if sin_tarifa > 0:
        print(f"[TARIFAS] ALERTA: {sin_tarifa:,} registros sin tarifa definida "
              f"(tramo/clase no encontrado en la matriz).")
        # Los marcaremos pero NO los rechazamos, necesitan revisión
        df.loc[df["tarifa_calculada"].isna(), "alerta_tarifa"] = "SIN_TARIFA_EN_MATRIZ"

    print(f"[TARIFAS] Tarifas calculadas: {df['tarifa_calculada'].notna().sum():,}")
    return df


def aplicar_descuentos_vigencias(
    df: pd.DataFrame,
    df_tags_beneficiarios: pd.DataFrame = None,
    df_vigencias_tags: pd.DataFrame = None
) -> pd.DataFrame:
    """
    RF-C05: Aplica descuentos si el Tag pertenece a beneficiarios
    y valida que el Tag esté vigente a la fecha de la transacción.

    Args:
        df                    : DataFrame con pares matched y tarifas calculadas.
        df_tags_beneficiarios : DataFrame con columna 'tag' de beneficiarios.
                                Si es None, no se aplican descuentos.
        df_vigencias_tags     : DataFrame con columnas ['tag', 'fecha_inicio', 'fecha_fin'].
                                Si es None, no se validan vigencias.
    """
    if df.empty:
        return df

    df = df.copy()
    df["descuento_aplicado"] = 0.0
    df["tarifa_final"] = df["tarifa_calculada"]
    df["tag_vigente"] = True
    df["es_beneficiario"] = False

    # ── Validar vigencia del Tag ───────────────────────────────────────
    if df_vigencias_tags is not None and "tag_entrada" in df.columns:
        df_vigencias_tags = df_vigencias_tags.copy()
        df_vigencias_tags["tag"] = df_vigencias_tags["tag"].str.upper()
        df_vigencias_tags["fecha_inicio"] = pd.to_datetime(df_vigencias_tags["fecha_inicio"]).dt.date
        df_vigencias_tags["fecha_fin"]    = pd.to_datetime(df_vigencias_tags["fecha_fin"]).dt.date

        # Merge para obtener vigencia de cada tag
        df = df.merge(
            df_vigencias_tags.rename(columns={
                "tag": "tag_entrada",
                "fecha_inicio": "vig_inicio",
                "fecha_fin": "vig_fin"
            }),
            on="tag_entrada",
            how="left"
        )

        # Verificar que la fecha de transacción esté dentro de la vigencia
        fecha_tx = pd.to_datetime(df["dia_operativo_entrada"]).dt.date
        df["tag_vigente"] = (
            (fecha_tx >= df["vig_inicio"]) & (fecha_tx <= df["vig_fin"])
        ).fillna(True)  # Si no hay registro de vigencia, asumir vigente (para no bloquear)

        n_vencidos = (~df["tag_vigente"]).sum()
        if n_vencidos > 0:
            print(f"[TARIFAS] Tags vencidos detectados: {n_vencidos:,}")

    # ── Aplicar descuentos a beneficiarios ────────────────────────────
    if df_tags_beneficiarios is not None and "tag_entrada" in df.columns:
        tags_benef = set(
            df_tags_beneficiarios["tag"].str.upper().dropna().unique()
        )
        df["es_beneficiario"] = df["tag_entrada"].isin(tags_benef)

        # Solo aplica descuento si el tag ESTÁ vigente y ES beneficiario
        mask_descuento = df["es_beneficiario"] & df["tag_vigente"]
        df.loc[mask_descuento, "descuento_aplicado"] = DESCUENTO_BENEFICIARIO
        df.loc[mask_descuento, "tarifa_final"] = (
            df.loc[mask_descuento, "tarifa_calculada"] * (1 - DESCUENTO_BENEFICIARIO)
        )

        print(f"[TARIFAS] Beneficiarios con descuento aplicado: {mask_descuento.sum():,}")

    return df


# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────

def _buscar_columna(df: pd.DataFrame, candidatos: list) -> str | None:
    """Busca la primera columna que exista en el DataFrame de una lista de candidatos."""
    for c in candidatos:
        if c in df.columns:
            return c
    return None


def cargar_matriz_desde_excel(ruta: str) -> dict:
    """
    Carga la matriz tarifaria desde un Excel con columnas:
    [tramo, clase, tarifa]
    """
    df_tarifas = pd.read_excel(ruta, dtype=str)
    df_tarifas["tarifa"] = pd.to_numeric(df_tarifas["tarifa"], errors="coerce")
    return {
        (row["tramo"].upper(), row["clase"].strip()): row["tarifa"]
        for _, row in df_tarifas.iterrows()
        if pd.notna(row["tarifa"])
    }
