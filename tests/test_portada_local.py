"""La portada explica la evidencia sin modificar niveles ni ocultar faltantes."""
import copy
from datetime import datetime, timedelta
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import hidrologia as h
from explicaciones_alerta import construir_explicacion
from portada_local import construir_portada_local, fecha_publica, html_portada_local, mensaje_estado_monitor

NOW = datetime.fromisoformat("2026-10-14T12:00:00-03:00")
FIX = Path(__file__).parent / "fixtures"


class CifrasHTML(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.cifras, self.actual = {}, None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == 'article' and atributos.get('data-cifra'):
            self.actual = atributos['data-cifra']
            self.cifras[self.actual] = {'texto': [], 'fuentes': []}
        if self.actual and tag == 'a':
            self.cifras[self.actual]['fuentes'].append(atributos.get('href'))

    def handle_data(self, data):
        if self.actual and data.strip():
            self.cifras[self.actual]['texto'].append(data.strip())

    def handle_endtag(self, tag):
        if tag == 'article':
            self.actual = None


def datos():
    p = h.parsear_reporte_ina((FIX / "ina_diario_20261013.html").read_text(), NOW)
    p["fuentes"] = [p["fuente"]]
    p["smn"] = {"fecha_feed": NOW - timedelta(hours=2), "consultado": NOW}
    row = {"localidad": "Corrientes", "provincia": "Corrientes", "nivel": "ROJO",
           "indice": 24.0, "nivel_meteorologico": "VERDE", "cobertura_meteorologica": True,
           "actualizado": NOW.strftime("%d/%m/%Y %H:%M"), "fecha_evaluacion_integrada": NOW.isoformat(),
           "referencia_pronostico": (NOW-timedelta(hours=1)).isoformat(),
           "lluvia_futura_24": 0.0, "lluvia_futura_72": 1.0, "lluvia_futura_7d": 2.0,
           "cobertura_pronostico_7d": True, "smn_vigente": True, "smn_evidencias": []}
    row.update(h.evaluar_localidad("Corrientes", p, NOW))
    row.update(construir_explicacion(row, {"semaforo": "VERDE", "motivos": []}, 35, 55))
    return row, p


class PortadaLocalTests(unittest.TestCase):
    def setUp(self):
        self.red = patch("requests.sessions.Session.request", side_effect=AssertionError("Red y envíos prohibidos"))
        self.red.start()
        self.addCleanup(self.red.stop)

    def test_preserva_rojo_hidrologico_con_indice_meteorologico_bajo(self):
        row, p = datos()
        anterior = copy.deepcopy(row)
        panel = construir_portada_local(row, p, NOW)
        self.assertEqual(panel["nivel"], "ROJO")
        self.assertEqual(row, anterior)
        self.assertIn("Previsión", panel["motivo"])
        self.assertEqual(len(panel["bloques"]), 4)
        self.assertIn("Próximas 24 h: 0,0 mm", panel["bloques"][0]["datos"])
        self.assertTrue(panel["ciudadanos"])
        self.assertTrue(panel["productores"])
        self.assertIn("no es una orden de evacuación", html_portada_local(panel))

    def test_cifras_iniciales_tienen_fuente_fecha_y_cero_real(self):
        row,p=datos()
        panel=construir_portada_local(row,p,NOW)
        cifras=CifrasHTML(html_portada_local(panel)).cifras
        self.assertEqual(set(cifras),{'lluvia24','lluvia72','rio'})
        self.assertIn('0,0 mm',cifras['lluvia24']['texto'])
        self.assertIn('Período desde: 14/10/2026 11:00 ART',cifras['lluvia24']['texto'])
        self.assertTrue(cifras['lluvia24']['fuentes'])
        self.assertIn('5,32 m',cifras['rio']['texto'])
        self.assertIn('Lectura: 13/10/2026 · hora no publicada',cifras['rio']['texto'])
        self.assertTrue(cifras['rio']['fuentes'])
        self.assertNotIn('00:00',' '.join(cifras['rio']['texto']))
        # La causa roja sigue fuera de los detalles cerrados, aunque la lluvia sea baja.
        inicio=html_portada_local(panel).split('<details',1)[0]
        self.assertIn('ROJO · ALA experimental',inicio)
        self.assertIn('Previsión',inicio)

    def test_cifras_iniciales_no_promueven_lectura_sin_vigencia(self):
        row,p=datos()
        row.update(cobertura_meteorologica=False,hidrologia_vigente=False)
        panel=construir_portada_local(row,p,NOW)
        cifras=CifrasHTML(html_portada_local(panel)).cifras
        self.assertIn('Sin dato completo vigente',cifras['lluvia24']['texto'])
        self.assertNotIn('0,0 mm',cifras['lluvia24']['texto'])
        self.assertIn('Sin lectura vigente',cifras['rio']['texto'])
        self.assertNotIn('5,32 m',cifras['rio']['texto'])
        self.assertTrue(any('Último dato disponible:' in t for t in cifras['rio']['texto']))

    def test_no_reemplaza_faltantes_por_cero_ni_presume_ausencia_de_avisos(self):
        row, p = datos()
        row.update(nivel="SIN DATOS", cobertura_meteorologica=False, lluvia_futura_24=float("nan"),
                   cobertura_pronostico_7d=False, hidrologia_vigente=False, hidrologia_pronostico_vigente=False,
                   represa_vigente=False, smn_vigente=False)
        panel = construir_portada_local(row, p, NOW)
        texto = json.dumps(panel, ensure_ascii=False, allow_nan=False)
        self.assertNotIn("0,0 mm", texto)
        self.assertIn("sin dato completo vigente", texto)
        self.assertIn("no se puede afirmar que no haya avisos", texto)
        self.assertNotIn("Sin aviso de lluvia o tormenta aplicado", texto)
        self.assertGreaterEqual(len(panel["faltantes"]), 7)

    def test_parte_diario_no_muestra_medianoche_como_hora_medida(self):
        row, p = datos()
        panel = construir_portada_local(row, p, NOW)
        fechas = panel["bloques"][1]["fechas"]
        self.assertTrue(any("hora no publicada" in fecha for fecha in fechas))
        self.assertFalse(any("00:00" in fecha for fecha in fechas))
        self.assertFalse(any("hace" in fecha for fecha in fechas))
        self.assertEqual(fecha_publica("2026-10-14", "fecha_hora_publicada"), "14/10/2026 · hora no publicada")

    def test_consulta_smn_reciente_no_reescribe_fecha_de_su_fuente(self):
        row, p = datos()
        fechas = construir_portada_local(row, p, NOW)["bloques"][3]["fechas"]
        self.assertIn("Actualización del listado SMN: 14/10/2026 10:00 ART · hace 2,0 h", fechas)
        self.assertIn("Consulta al SMN: 14/10/2026 12:00 ART · hace menos de 1 min", fechas)
        self.assertIn("Emisión del modelo: no informada", construir_portada_local(row, p, NOW)["bloques"][0]["fechas"])
        self.assertIn("Períodos de lluvia contados desde: 14/10/2026 11:00 ART", construir_portada_local(row, p, NOW)["bloques"][0]["fechas"])

    def test_smn_sin_aviso_no_se_presenta_como_ausencia_total_de_alertas(self):
        row, p = datos()
        smn = construir_portada_local(row, p, NOW)["bloques"][3]
        self.assertEqual(smn["estado"], "Sin aviso de lluvia o tormenta aplicado a este punto")
        self.assertIn("72 h", smn["datos"][0])
        self.assertIn("otros fenómenos", smn["datos"][0])

    def test_aporte_previsto_de_yacyreta_no_se_titula_observado(self):
        row, p = datos()
        row.update(represa_aplica=True, represa_vigente=True)
        p["represas"]["yacyreta"] = {"nombre": "Yacyretá", "fecha": NOW.isoformat(), "fuente": h.EBY_URL,
            "afluente_m3s": 39500, "afluente_tipo": "prevision_del_dia"}
        represa = construir_portada_local(row, p, NOW)["bloques"][2]
        self.assertIn("Aporte previsto para el día: 39500 m³/s", represa["datos"])
        self.assertFalse(any("observado" in linea.lower() for linea in represa["datos"]))
        self.assertIn("no indica la altura ni la hora de llegada", " ".join(represa["datos"]))

    def test_horizonte_extendido_conserva_fin_real_sin_promesa_de_15_dias(self):
        row, p = datos()
        row.update(lluvia_futura_extendida=26.0, cobertura_pronostico_extendido=True,
                   pronostico_extendido_horas=288, pronostico_extendido_hasta="2026-10-26T12:00:00-03:00")
        lluvia = construir_portada_local(row, p, NOW)["bloques"][0]
        self.assertIn("Horizonte disponible: 26,0 mm hasta 26/10/2026 12:00 ART", lluvia["datos"])
        self.assertNotIn("15 días", " ".join(lluvia["datos"]))

    def test_textos_y_enlaces_de_datos_no_inyectan_html(self):
        row, p = datos()
        row["localidad"] = '<img src=x onerror="alert(1)">'
        panel = construir_portada_local(row, p, NOW)
        salida = html_portada_local(panel)
        self.assertNotIn("<img ", salida)
        self.assertIn("&lt;img", salida)
        self.assertNotIn('href="javascript:', salida)

    def test_monitor_parcial_reciente_distingue_cobertura_de_fallo_operativo(self):
        estado = {"checked_at": NOW.isoformat(), "health": "datos_parciales", "ok": False,
                  "red_integrada_publicada": True}
        estilo, texto = mensaje_estado_monitor(estado, NOW)
        self.assertEqual(estilo, "warning")
        self.assertIn("completó el control y publicó el informe compartido", texto)
        self.assertIn("datos parciales", texto)
        self.assertNotIn("problema operativo", texto)

    def test_monitor_antiguo_o_envio_rechazado_no_se_anuncia_correcto(self):
        for estado in ({"checked_at": (NOW-timedelta(hours=4)).isoformat(), "health": "datos_parciales", "ok": False},
                       {"checked_at": NOW.isoformat(), "health": "envio_rechazado", "ok": False}):
            with self.subTest(estado=estado):
                estilo, texto = mensaje_estado_monitor(estado, NOW)
                self.assertEqual(estilo, "warning")
                self.assertNotIn("completó el control", texto)


if __name__ == "__main__":
    unittest.main()
