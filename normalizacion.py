"""
FASE 2: Normalización del Día Operativo y Filtro de Estatus
Resuelve RN-01: El día operativo va de 22:00:00 del día anterior a 21:59:59 del día actual.
Resuelve RF-C01: Solo procesar transacciones con Estatus "10".
"""

import pandas as pd
from datetime import time, timedelta


# ─────────────────────────────────────────────
# Constantes del día operativo (RN-01)
# ─────────────────────────────────────────────
HORA_INICIO_DIA_OPERATIVO = 22   # 10:00 PM
HORA_FIN_DIA_OPERATIVO    = 21   # 9:59:59 PM del día siguiente

ESTATUS_VALIDO = "10"


def filtrar_estatus_valido(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    RF-C01: Conservar únicamente registros con Estatus == "10".
    Retorna (df_validos, df_descartados).
    """
    if "estatus" not in df.columns:
        print("[ADVERTENCIA] No existe columna 'estatus'. Saltando filtro RF-C01.")
        return df, pd.DataFrame()

    df["estatus"] = df["estatus"].astype(str).str.strip()
    mask_valido = df["estatus"] == ESTATUS_VALIDO

    df_validos    = df[mask_valido].copy()
    df_descartado = df[~mask_valido].copy()
    df_descartado["motivo_rechazo"] = "Estatus distinto de 10"

    print(f"[FILTRO] Con estatus 10: {len(df_validos):,} | Descartados: {len(df_descartado):,}")
    return df_validos, df_descartado


def asignar_dia_operativo(df: pd.DataFrame) -> pd.DataFrame:
    """
    RN-01: Asigna el 'dia_operativo' correcto a cada transacción.

    Lógica:
      - Si la hora es >= 22:00  → el día operativo es el día SIGUIENTE (ya inició el nuevo día operativo)
      - Si la hora es < 22:00   → el día operativo es el día ACTUAL (calendario)

    Ejemplo:
      23:30 del 5 de enero  → dia_operativo = 6 de enero
      08:00 del 6 de enero  → dia_operativo = 6 de enero
      21:59 del 6 de enero  → dia_operativo = 6 de enero  ← cierra el día

    Resultado: columna 'dia_operativo' tipo date.
    """
    if "datetime" not in df.columns:
        raise ValueError("El DataFrame debe tener columna 'datetime' antes de asignar día operativo.")

    df = df.copy()

    # Extraer la hora como número entero para comparar rápido
    hora = df["datetime"].dt.hour

    # Transacciones nocturnas (>= 22:00) pertenecen al día siguiente
    df["dia_operativo"] = df["datetime"].dt.date
    mask_nocturno = hora >= HORA_INICIO_DIA_OPERATIVO
    df.loc[mask_nocturno, "dia_operativo"] = (
        df.loc[mask_nocturno, "datetime"] + timedelta(days=1)
    ).dt.date

    print(f"[NORM] Día operativo asignado. "
          f"Transacciones nocturnas reasignadas: {mask_nocturno.sum():,}")
    return df


def asignar_decena(df: pd.DataFrame) -> pd.DataFrame:
    """
    RN-02: Asigna la decena de facturación según el día del mes del dia_operativo.

    1ª Decena: días 1-10
    2ª Decena: días 11-20
    3ª Decena: días 21 en adelante
    """
    if "dia_operativo" not in df.columns:
        raise ValueError("Ejecutar asignar_dia_operativo() antes de asignar_decena().")

    df = df.copy()
    dia_del_mes = pd.to_datetime(df["dia_operativo"]).dt.day

    condiciones = [
        dia_del_mes <= 10,
        (dia_del_mes >= 11) & (dia_del_mes <= 20),
        dia_del_mes >= 21,
    ]
    etiquetas = ["1ra_Decena", "2da_Decena", "3ra_Decena"]

    df["decena"] = pd.cut(
        dia_del_mes,
        bins=[0, 10, 20, 31],
        labels=etiquetas,
        right=True
    )

    print(f"[NORM] Distribución por decena:\n{df['decena'].value_counts().to_string()}")
    return df


def separar_entradas_salidas(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Divide el DataFrame en dos: registros de Entrada (E) y de Salida (S).
    Necesario para la Fase 3 de matching.
    """
    if "sentido" not in df.columns:
        raise ValueError("Se requiere columna 'sentido' con valores 'E' o 'S'.")

    df_entradas = df[df["sentido"] == "E"].copy().reset_index(drop=True)
    df_salidas  = df[df["sentido"] == "S"].copy().reset_index(drop=True)

    print(f"[NORM] Entradas: {len(df_entradas):,} | Salidas (AUSUR): {len(df_salidas):,}")
    return df_entradas, df_salidas
