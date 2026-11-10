"""El informe reciente no extiende la vigencia de la causa del semáforo."""
from copy import deepcopy
from datetime import timedelta
import unittest

import red_integrada as red
from test_red_integrada import NOW, build_fixture, node_of


class VigenciaCausasRedTests(unittest.TestCase):
    def documento(self, nivel="ROJO", tipo="smn"):
        document = build_fixture()
        node = node_of(document, "Litoral", "Goya")
        rango = {"VERDE": 0, "AMARILLO": 1, "ROJO": 2}[nivel]
        causa = {"tipo": tipo, "rango": rango, "verificada": True, "vigente": True,
                 "fuente": "https://ssl.smn.gob.ar/CAP/AR.php",
                 "valida_hasta": (NOW + timedelta(minutes=10)).isoformat()}
        node.update(estado_integrado_ala=nivel, nivel_experimental=nivel,
                    fecha_evaluacion_integrada=NOW.isoformat(), explicacion_version=1,
                    desencadenantes=[deepcopy(causa)], evidencias_alerta=[deepcopy(causa)])
        return document

    def test_color_expira_con_su_unica_causa_aunque_el_informe_siga_reciente(self):
        for nivel in ("VERDE", "AMARILLO", "ROJO"):
            with self.subTest(nivel=nivel):
                document = self.documento(nivel)
                original = deepcopy(document)
                checked = red.validar_red_integrada(document, NOW + timedelta(minutes=11))
                node = node_of(checked, "Litoral", "Goya")
                self.assertTrue(checked["snapshot_vigente"])
                self.assertEqual(node["nivel_integrado_vigente"], "SIN DATOS")
                self.assertEqual(node["estado_integrado_ala"], nivel)
                self.assertFalse(node["desencadenantes"][0]["vigente"])
                self.assertEqual(document, original)
                row = next(row for row in red.filas_publicas_nodos(checked) if row["Localidad"] == "Goya")
                self.assertEqual(row["Evaluación integrada"], "SIN DATOS")

    def test_limite_inclusivo_y_otra_causa_del_mismo_rango_conservan_color(self):
        document = self.documento()
        checked = red.validar_red_integrada(document, NOW + timedelta(minutes=10))
        self.assertEqual(node_of(checked, "Litoral", "Goya")["nivel_integrado_vigente"], "ROJO")
        node = node_of(document, "Litoral", "Goya")
        segunda = deepcopy(node["desencadenantes"][0])
        segunda.update(tipo="observacion_fluvial", valida_hasta=(NOW + timedelta(hours=1)).isoformat(),
                       precision_fecha="fecha_hora_publicada", fecha_observada=NOW.isoformat())
        node["desencadenantes"].append(segunda)
        checked = red.validar_red_integrada(document, NOW + timedelta(minutes=11))
        self.assertEqual(node_of(checked, "Litoral", "Goya")["nivel_integrado_vigente"], "ROJO")

    def test_snapshot_que_daba_por_vigente_una_fecha_registro_no_sostiene_alerta(self):
        for tipo in ("observacion_fluvial", "aguas_arriba"):
            for precision in (None, "fecha_registro_fuente", "fecha_sin_hora",
                              "hora_no_verificada_en_reporte_diario"):
                with self.subTest(tipo=tipo, precision=precision):
                    document = self.documento(tipo=tipo)
                    node = node_of(document, "Litoral", "Goya")
                    for campo in ("desencadenantes", "evidencias_alerta"):
                        node[campo][0].update(precision_fecha=precision, fecha_observada=NOW.isoformat())
                    checked = red.validar_red_integrada(document, NOW)
                    verified = node_of(checked, "Litoral", "Goya")
                    self.assertEqual(verified["nivel_integrado_vigente"], "SIN DATOS")
                    self.assertEqual(verified["nivel_vigente"], "SIN DATOS")
                    self.assertFalse(verified["desencadenantes"][0]["vigente"])

    def test_hora_publicada_sin_verificacion_no_sostiene_alerta(self):
        document = self.documento(tipo="observacion_fluvial")
        node = node_of(document, "Litoral", "Goya")
        for campo in ("desencadenantes", "evidencias_alerta"):
            node[campo][0].update(precision_fecha="fecha_hora_publicada",
                fecha_observada=NOW.isoformat(), verificada=None)
        checked = red.validar_red_integrada(document, NOW)
        self.assertEqual(node_of(checked, "Litoral", "Goya")["nivel_integrado_vigente"], "SIN DATOS")

    def test_observacion_confirmada_limite_seis_horas_y_nunca_futura(self):
        for desplazamiento, vigente in ((timedelta(hours=-6), True),
                                        (timedelta(hours=-6, seconds=-1), False),
                                        (timedelta(seconds=1), False)):
            with self.subTest(desplazamiento=desplazamiento):
                document = self.documento(tipo="observacion_fluvial")
                node = node_of(document, "Litoral", "Goya")
                for campo in ("desencadenantes", "evidencias_alerta"):
                    node[campo][0].update(precision_fecha="fecha_hora_publicada",
                        fecha_observada=(NOW + desplazamiento).isoformat())
                checked = red.validar_red_integrada(document, NOW)
                verified = node_of(checked, "Litoral", "Goya")
                self.assertEqual(verified["nivel_integrado_vigente"], "ROJO" if vigente else "SIN DATOS")

    def test_una_evidencia_de_menor_rango_no_sostiene_el_rojo_anterior(self):
        document = self.documento()
        node = node_of(document, "Litoral", "Goya")
        menor = deepcopy(node["desencadenantes"][0])
        menor.update(rango=1, valida_hasta=(NOW + timedelta(hours=1)).isoformat())
        node["desencadenantes"].append(menor)
        checked = red.validar_red_integrada(document, NOW + timedelta(minutes=11))
        self.assertEqual(node_of(checked, "Litoral", "Goya")["nivel_integrado_vigente"], "SIN DATOS")

    def test_fecha_limite_invalida_no_se_convierte_en_vigencia_indefinida(self):
        for limite in ("sin fecha", "2026-10-14", "2026-10-14T20:00:00"):
            with self.subTest(limite=limite):
                document = self.documento()
                node = node_of(document, "Litoral", "Goya")
                for campo in ("desencadenantes", "evidencias_alerta"):
                    node[campo][0]["valida_hasta"] = limite
                checked = red.validar_red_integrada(document, NOW)
                self.assertEqual(node_of(checked, "Litoral", "Goya")["nivel_integrado_vigente"], "SIN DATOS")

    def test_color_sin_trazabilidad_versionada_conserva_compatibilidad_legada(self):
        document = self.documento()
        node_of(document, "Litoral", "Goya")["explicacion_version"] = None
        checked = red.validar_red_integrada(document, NOW + timedelta(minutes=11))
        self.assertEqual(node_of(checked, "Litoral", "Goya")["nivel_integrado_vigente"], "ROJO")
        self.assertIn("sin desencadenante", node_of(checked, "Litoral", "Goya")["explicacion_estado"])


if __name__ == "__main__":
    unittest.main()
