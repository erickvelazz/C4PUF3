
import sys
import os
import pandas as pd
import unittest
from datetime import datetime, timedelta

# Agregar path para importar módulos
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Importar funciones y constantes
# Necesitamos acceder a las constantes de ingesta.py para simular datos correctos
import ingesta
from ingesta import preparar_salidas_csv
from match_placa import match_por_placa

class TestMatchPlaca(unittest.TestCase):
    def setUp(self):
        # Configurar constantes para pruebas
        self.CSV_CUERPO = ingesta.CSV_CUERPO
        self.CSV_ESTATUS = ingesta.CSV_ESTATUS
        self.CSV_PLACA = ingesta.CSV_PLACA
        self.CSV_TAG = ingesta.CSV_TAG
        self.CSV_DATETIME = ingesta.CSV_DATETIME

    def test_filtro_estatus_ingesta(self):
        """Validar que solo Id clasifica 8 va a pendientes"""
        print("\n=== TEST: Filtro de Estatus en Ingesta ===")
        
        # Datos simulados de salida CSV
        data = {
            self.CSV_CUERPO: ["B", "B", "B", "B", "B", "B"],
            self.CSV_ESTATUS: ["8", "10", "4", "6", "7", "8"],
            self.CSV_TAG: ["T1", "T2", "T3", "T4", "T5", "T6"],
            self.CSV_PLACA: ["P1", "P2", "P3", "P4", "P5", "P6"],
            self.CSV_DATETIME: ["01/01/2023 10:00:00"] * 6
        }
        df = pd.DataFrame(data)
        
        # Ejecutar preparación
        res = preparar_salidas_csv(df)
        
        # Validar salidas (Id 10)
        self.assertEqual(len(res["salidas"]), 1, "Debería haber 1 salida (Id 10)")
        self.assertEqual(res["salidas"].iloc[0][self.CSV_ESTATUS], "10")
        
        # Validar pendientes (Id 8 solamente)
        self.assertEqual(len(res["pendientes"]), 2, "Debería haber 2 pendientes (Id 8)")
        self.assertTrue(all(res["pendientes"][self.CSV_ESTATUS] == "8"), "Todos los pendientes deben ser Id 8")
        
        # Validar descartados (4, 6, 7)
        self.assertEqual(len(res["descartados"]), 3, "Debería haber 3 descartados (4, 6, 7)")
        estatus_descartados = res["descartados"][self.CSV_ESTATUS].unique()
        for est in ["4", "6", "7"]:
            self.assertIn(est, estatus_descartados, f"Estatus {est} debería estar en descartados")

    def test_match_placa_direccionalidad(self):
        """Validar que solo hace match A vs B"""
        print("\n=== TEST: Direccionalidad de Match ===")
        
        # 1. Crear Entradas (A) simuladas (vienen de AUSUR)
        # En ingesta.py se agrega columna "Cuerpo": "A"
        df_e = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-01-01 10:00:00")],
            "PLACA": ["ABC-123"],
            "Cuerpo": ["A"],
            "_placa_norm": ["ABC123"], # Normalizado
            "NUMERO_TAG": ["TAG_A"]
        })
        
        # 2. Crear Salidas Pendientes (B) simuladas (vienen de CSV Salidas)
        # Id 8
        df_pend = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-01-01 10:30:00")], # 30 min despues
            self.CSV_PLACA: ["ABC-123"],
            "Cuerpo": ["B"],
            self.CSV_ESTATUS: ["8"],
            "_placa_norm": ["ABC123"],
            "origen": ["CSV"]
        })
        
        # Ejecutar match
        # match_por_placa(pendientes, sin_match_e, sin_match_s)
        res = match_por_placa(df_pend, df_e, pd.DataFrame())
        
        # Validar éxito
        nuevos = res["nuevos_conciliados"]
        # Debería haber 1 match
        self.assertEqual(len(nuevos), 1, "Debería haber match A-B")
        if not nuevos.empty:
            match = nuevos.iloc[0]
            # Verificar nombres de columnas en resultado
            # match_por_placa retorna dict con claves normalizadas o las originales?
            # _construir_par_placa retorna un dict
            print(f"Match exitoso: {match}")

    def test_match_placa_fallo_BB(self):
        """Validar que NO hace match si el candidato es B (Salida vs Salida)"""
        print("\n=== TEST: Fallo Match B-B ===")
        
        # Candidato falso: Es una Salida (B) haciéndose pasar por Entrada en la lista de candidatos
        df_candidato_fake = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-01-01 10:00:00")],
            "PLACA": ["ABC-123"],
            "Cuerpo": ["B"], # ERROR: Es B
            "_placa_norm": ["ABC123"],
            "NUMERO_TAG": ["TAG_FAKE"]
        })
        
        df_pend = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-01-01 10:30:00")],
            self.CSV_PLACA: ["ABC-123"],
            "Cuerpo": ["B"],
            "_placa_norm": ["ABC123"],
            "origen": ["CSV"]
        })
        
        res = match_por_placa(df_pend, df_candidato_fake, pd.DataFrame())
        
        self.assertEqual(len(res["nuevos_conciliados"]), 0, "No debería haber match B-B")
        self.assertEqual(len(res["pendientes_sin_match"]), 1, "El pendiente debe quedar sin match")

    def test_match_placa_fallo_AA(self):
        """Validar que NO hace match si el pendiente es A (Entrada vs Entrada)"""
        print("\n=== TEST: Fallo Match A-A ===")
        
        # Pendiente falso: Es una Entrada (A)
        df_pend_fake = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-01-01 10:30:00")],
            self.CSV_PLACA: ["ABC-123"],
            "Cuerpo": ["A"], # ERROR: Es A
            "_placa_norm": ["ABC123"],
            "origen": ["CSV"]
        })
        
        df_e = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-01-01 10:00:00")],
            "PLACA": ["ABC-123"],
            "Cuerpo": ["A"],
            "_placa_norm": ["ABC123"]
        })
        
        res = match_por_placa(df_pend_fake, df_e, pd.DataFrame())
        
        self.assertEqual(len(res["nuevos_conciliados"]), 0, "No debería haber match A-A")

    def test_resolucion_duplicados_lejanos(self):
        """Validar que NO aplica resolución si están lejos (>15s)"""
        print("\n=== TEST: Resolución Lejanos (Gana Cercanía) ===")
        
        # Pendiente (Salida B)
        df_pend = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-01-01 10:30:00")],
            self.CSV_PLACA: ["ABC-123"],
            "Cuerpo": ["B"],
            self.CSV_ESTATUS: ["8"],
            "_placa_norm": ["ABC123"],
            "origen": ["CSV"]
        }, index=[0])
        
        # Candidatos (Entradas A)
        # TAG_1: 5 min diff (Cercano). Tiene Placa (para ser candidato válido).
        # TAG_2: 20 min diff (Lejano). Tiene Placa.
        # Diferencia entre ellos = 15 min > 15 seg. NO son duplicados entre sí.
        
        df_e = pd.DataFrame({
            "datetime": [
                pd.Timestamp("2023-01-01 10:25:00"), # TAG_1
                pd.Timestamp("2023-01-01 10:10:00")  # TAG_2
            ],
            "PLACA": [
                "ABC-123",   # Con placa (Válido)
                "ABC-123"    # Con placa (Válido)
            ],
            "Cuerpo": ["A", "A"],
            "_placa_norm": ["ABC123", "ABC123"], 
            "NUMERO_TAG": ["TAG_1", "TAG_2"]
        }, index=[10, 20])
        
        res = match_por_placa(df_pend, df_e, pd.DataFrame())
        match = res["nuevos_conciliados"].iloc[0]
        
        # Debería ganar TAG_1 por ser el MEJOR candidato (más cercano)
        # Como TAG_2 está muy lejos de TAG_1, no se considera duplicado.
        print(f"Ganador Lejanos: {match.get('NUMERO_TAG_entrada')} - Método: {match.get('metodo_resolucion')}")
        self.assertEqual(match.get("NUMERO_TAG_entrada"), "TAG_1", "Debería ganar TAG_1 por cercanía")
        self.assertIn("Unico", match.get("metodo_resolucion"), "Método debería ser Unico")

    def test_resolucion_duplicados_cercanos(self):
        """Validar resolución de duplicados en ventana 15s (Cercanía Temporal si ambos tienen Placa)"""
        print("\n=== TEST: Resolución Cercanos (Conflicto) ===")
        
        # Pendiente T=10:30:00
        df_pend = pd.DataFrame({
            "datetime": [pd.Timestamp("2023-01-01 10:30:00")],
            self.CSV_PLACA: ["ABC-123"],
            "Cuerpo": ["B"],
            self.CSV_ESTATUS: ["8"],
            "_placa_norm": ["ABC123"],
            "origen": ["CSV"]
        }, index=[0])
        
        # Candidatos
        # A: T=10:29:55 (5s diff). Con Placa.
        # B: T=10:29:45 (15s diff). Con Placa.
        # Distancia A-B = 10s <= 15s. SON DUPLICADOS.
        # Ambos tienen placa -> Empate en Regla 1.
        # Regla 2: Cercanía Temporal -> A es más cercano (5s vs 15s).
        
        df_e = pd.DataFrame({
            "datetime": [
                pd.Timestamp("2023-01-01 10:29:55"), # TAG_A
                pd.Timestamp("2023-01-01 10:29:45")  # TAG_B
            ],
            "PLACA": [
                "ABC-123", 
                "ABC-123"
            ],
            "Cuerpo": ["A", "A"],
            "_placa_norm": ["ABC123", "ABC123"], 
            "NUMERO_TAG": ["TAG_A", "TAG_B"]
        }, index=[100, 200])
        
        res = match_por_placa(df_pend, df_e, pd.DataFrame())
        match = res["nuevos_conciliados"].iloc[0]
        
        # Ganador: TAG_A
        print(f"Ganador Cercanos: {match.get('NUMERO_TAG_entrada')} - Método: {match.get('metodo_resolucion')}")
        self.assertEqual(match.get("NUMERO_TAG_entrada"), "TAG_A", "Debería ganar TAG_A por cercanía (empate en placa)")
        
        # Validar que se detectó duplicado
        metodo = match.get("metodo_resolucion")
        self.assertTrue("Duplicado" in metodo or "Cercanía Temporal" in metodo, 
                        f"Método '{metodo}' debería indicar resolución de duplicado")



if __name__ == '__main__':
    unittest.main()
