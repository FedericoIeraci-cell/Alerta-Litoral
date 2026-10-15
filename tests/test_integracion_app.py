"""Escenarios de impacto para la integración ALA, sin consultas ni envíos reales.

Se prueban comportamientos operativos: una crecida no puede quedar oculta por
un índice meteorológico bajo, una ausencia no equivale a verde y cada envío
requiere una reserva confirmada fuera del proceso antes de tocar Telegram.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from html.parser import HTMLParser
import importlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ["ALA_MONITOR_HEADLESS"] = "1"
app = importlib.import_module("app")

ART = ZoneInfo("America/Argentina/Buenos_Aires")
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=ART)
INA_URL = "https://www.ina.gov.ar/alerta/index.php?seccion=6"
EBY_URL = "https://www.eby.org.ar/"
SMN_URL = "https://ssl.smn.gob.ar/CAP/alerta_prueba.xml"


def met_row(localidad="Paso de los Libres", provincia="Corrientes", **changes):
    row = dict(
        localidad=localidad, provincia=provincia, lat=-29.71, lon=-57.09,
        indice=27.9, nivel="VERDE", actualizado=NOW.strftime("%d/%m/%Y %H:%M"),
        cobertura_meteorologica=True, estado_datos="OK", temperatura=23.0, humedad_relativa=70.0,
        viento=10.0, direccion_viento_texto="NE", rafaga=17.0, cape=300.0,
        humedad_suelo_promedio=.3, lluvia_24=0.0, lluvia_72=0.0, lluvia_7d=2.0,
        lluvia_futura_24=0.0, lluvia_futura_72=0.0, lluvia_futura_7d=5.0,
        runoff_72=.0, prob_lluvia=10.0, tendencia="ESTABLE",
        motivos_semaforo="Índice meteorológico 27,9; no hay lluvia local relevante.",
        cobertura_territorial=1,
    )
    row.update(changes)
    return row


def panorama_vigente():
    return dict(
        fuentes=[dict(id="ina_diario", url=INA_URL, estado="VIGENTE",
                      fecha_documento=(NOW-timedelta(hours=12)).isoformat())],
        estaciones={}, pronosticos=[], represas={}, avisos=[],
    )


def estacion(nombre, altura, alerta, evacuacion, rio="Uruguay", fecha=None):
    return dict(nombre=nombre, rio=rio, altura_m=altura, alerta_m=alerta,
                evacuacion_m=evacuacion, fecha=(fecha or NOW-timedelta(hours=2)).isoformat(),
                fuente=INA_URL)


def cap_lluvia(**changes):
    """Aviso sintético con polígono pequeño que contiene únicamente Rafaela."""
    fields = dict(identifier="lluvia-rafaela", sent=(NOW-timedelta(hours=1)).isoformat(),
                  onset=(NOW-timedelta(minutes=30)).isoformat(),
                  expires=(NOW+timedelta(hours=4)).isoformat(), msgType="Alert",
                  status="Actual", severity="Extreme", references="")
    fields.update(changes)
    return f'''<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
      <identifier>{fields['identifier']}</identifier><sender>SMN</sender>
      <sent>{fields['sent']}</sent><status>{fields['status']}</status>
      <msgType>{fields['msgType']}</msgType><scope>Public</scope>
      <references>{fields['references']}</references><info><language>es-AR</language>
      <event>LLUVIA</event><urgency>Immediate</urgency><severity>{fields['severity']}</severity>
      <certainty>Likely</certainty><onset>{fields['onset']}</onset>
      <expires>{fields['expires']}</expires><headline>Lluvia extrema prevista</headline>
      <area><areaDesc>Santa Fe</areaDesc>
      <polygon>-31.30,-61.55 -31.30,-61.43 -31.20,-61.43 -31.20,-61.55 -31.30,-61.55</polygon>
      </area></info></alert>'''.encode()


def fuente_cap(documento):
    return dict(filas=documento["filas"], fecha_feed=NOW-timedelta(minutes=5),
                consultado=NOW, total=1, leidos=1, fallos=0, truncado=False, error="")


class ValidarHTMLTelegram(HTMLParser):
    """Rechaza etiquetas inyectadas y etiquetas cortadas por un truncamiento."""
    permitidas = {"b", "i", "u", "s", "a", "code", "pre", "strong", "em",
                  "ins", "strike", "del", "tg-spoiler", "span", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.pila = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.permitidas:
            raise AssertionError(f"Etiqueta HTML no permitida: {tag}")
        self.pila.append(tag)

    def handle_endtag(self, tag):
        if not self.pila or self.pila.pop() != tag:
            raise AssertionError(f"Etiqueta HTML sin cierre correcto: {tag}")


class IntegracionFluvialTests(unittest.TestCase):
    def setUp(self):
        self.now_patch = patch.object(app, "ahora", return_value=NOW)
        self.now_patch.start()
        self.state_patch = patch.object(app.st, "session_state", {})
        self.state_patch.start()
        # Toda prueba debe fallar si accidentalmente intenta consultar la red.
        self.network_get = patch.object(app.requests, "get", side_effect=AssertionError("Consulta de red en prueba"))
        self.network_get.start()
        self.network_post = patch.object(app.requests, "post", side_effect=AssertionError("Envío real en prueba"))
        self.network_post.start()
        self.addCleanup(patch.stopall)

    def evaluar(self, row, panorama):
        with patch.object(app, "componentes_localidad", return_value=(None, None, None, None)):
            return app.integrar_semaforo(app.pd.DataFrame([row]), panorama=panorama).iloc[0]

    def test_paso_libres_supera_evacuacion_aun_con_indice_27_9(self):
        p = panorama_vigente()
        p["estaciones"]["paso de los libres"] = estacion("Paso de los Libres", 9.28, 7.50, 8.50)
        row = self.evaluar(met_row(), p)
        self.assertEqual(row.nivel, "ROJO")
        self.assertEqual(row.indice, 27.9, "El índice meteorológico debe conservar su significado")
        self.assertTrue(row.hidrologia_vigente)
        self.assertEqual(row.hidrologia_rango, 2)
        self.assertIn("evac", str(row.motivos_semaforo).lower())

    def test_pronostico_corrientes_por_encima_evacuacion_72h_prepara(self):
        p = panorama_vigente()
        p["estaciones"]["corrientes"] = estacion("Corrientes", 5.40, 6.50, 7.00, rio="Paraná")
        p["pronosticos"].append(dict(
            localidades=["corrientes"], nivel="EVACUACION",
            fecha_emision=(NOW-timedelta(hours=8)).isoformat(),
            desde=(NOW+timedelta(hours=48)).isoformat(),
            hasta=(NOW+timedelta(hours=72)).isoformat(),
            texto="Se prevé superar el umbral de evacuación en las próximas 72 horas.", fuente=INA_URL,
        ))
        row = self.evaluar(met_row("Corrientes"), p)
        self.assertEqual(row.nivel, "ROJO")
        self.assertTrue(row.hidrologia_pronostico_vigente)
        self.assertIn("evac", str(row.accion).lower())
        # La anticipación exige preparación; el prototipo no debe inventar una
        # orden institucional de evacuar ni una hora exacta de inundación.
        self.assertNotIn("orden de evacuación emitida", str(row.accion).lower())

    def test_fuente_hidrologica_ausente_no_presenta_verde_total(self):
        row = self.evaluar(met_row(), panorama_vigente())
        self.assertTrue(row.hidrologia_aplica)
        self.assertFalse(row.hidrologia_vigente)
        self.assertNotEqual(row.nivel, "VERDE")
        self.assertIn("sin", str(row.motivos_semaforo).lower())

    def test_texto_oficial_vigente_sin_prevision_clasificable_no_certifica_verde(self):
        p = panorama_vigente()
        p["estaciones"]["corrientes"] = estacion("Corrientes", 5.40, 6.50, 7.00, rio="Paraná")
        p["pronosticos"].append(dict(
            localidades=["corrientes"], nivel="SIN_DATOS",
            fecha_emision=(NOW-timedelta(hours=8)).isoformat(),
            desde=None, hasta=(NOW+timedelta(hours=24)).isoformat(),
            texto="Se prevé que Corrientes continúe en ascenso; sin fecha cuantificada de cruce.",
            tipo="perspectiva_oficial_cualitativa", fuente=INA_URL,
        ))
        row = self.evaluar(met_row("Corrientes"), p)
        self.assertFalse(row.hidrologia_pronostico_vigente)
        self.assertNotEqual(row.nivel, "VERDE")

    def test_fuente_hidrologica_vieja_o_futura_no_eleva_por_dato_invalido(self):
        for fecha in (NOW-timedelta(hours=73), NOW+timedelta(hours=1)):
            with self.subTest(fecha=fecha):
                p = panorama_vigente()
                p["estaciones"]["paso de los libres"] = estacion("Paso de los Libres", 9.28, 7.5, 8.5, fecha=fecha)
                row = self.evaluar(met_row(), p)
                self.assertFalse(row.hidrologia_vigente)
                self.assertNotEqual(row.nivel, "ROJO")
                self.assertNotEqual(row.nivel, "VERDE")

    def test_yacyreta_no_es_senal_uruguay_ni_ciudad_interior(self):
        p = panorama_vigente()
        p["represas"]["yacyreta"] = dict(
            fecha=(NOW-timedelta(hours=2)).isoformat(),
            hasta=(NOW+timedelta(hours=24)).isoformat(),
            texto="Ascenso del caudal aguas abajo por mayores aportes.",
            señal_aguas_arriba=True, fuente=EBY_URL,
        )
        p["estaciones"]["paso de los libres"] = estacion("Paso de los Libres", 3.0, 7.5, 8.5)
        for localidad, provincia in [("Paso de los Libres", "Corrientes"), ("Rafaela", "Santa Fe"),
                                     ("Formosa", "Formosa")]:
            with self.subTest(localidad=localidad):
                row = self.evaluar(met_row(localidad, provincia), p)
                self.assertFalse(row.represa_aplica)
                self.assertNotIn("yacyret", str(row.motivos_semaforo).lower())

    def test_rosario_no_se_propaga_aguas_arriba_a_avellaneda(self):
        p = panorama_vigente()
        p["estaciones"]["rosario"] = estacion("Rosario", 7.0, 5.3, 6.0, rio="Paraná")
        row = self.evaluar(met_row("Avellaneda", "Santa Fe"), p)
        señales = row.hidrologia_alertas_aguas_arriba
        self.assertFalse(any("rosario" in str(s).lower() for s in señales))
        self.assertNotEqual(row.hidrologia_rango, 1, "Una crecida aguas abajo no es señal aguas arriba")

    def test_meteorologia_vieja_no_impide_rojo_por_observacion_fluvial_actual(self):
        p = panorama_vigente()
        p["estaciones"]["paso de los libres"] = estacion("Paso de los Libres", 9.28, 7.5, 8.5)
        row = self.evaluar(met_row(actualizado=(NOW-timedelta(hours=13)).strftime("%d/%m/%Y %H:%M")), p)
        self.assertEqual(row.nivel, "ROJO")
        self.assertTrue(row.hidrologia_vigente)

    def test_obtener_riesgo_automatico_usa_misma_integracion(self):
        p = panorama_vigente()
        p["estaciones"]["paso de los libres"] = estacion("Paso de los Libres", 9.28, 7.5, 8.5)
        with patch.object(app, "obtener_todos_los_nodos", return_value=app.pd.DataFrame([met_row()])), \
                patch.object(app, "componentes_localidad", return_value=(None, None, None, None)):
            actual = app.obtener_riesgo_automatico(panorama=p)
        self.assertEqual(actual.iloc[0].nivel, "ROJO")
        self.assertTrue(actual.iloc[0].hidrologia_vigente)

    def test_smn_poligono_solo_cubre_nodo_exacto_no_provincia_completa(self):
        parsed = app.parsear_cap_smn(cap_lluvia(), SMN_URL)
        self.assertEqual(parsed["filas"][0]["nodos"], ["Rafaela"])
        p = panorama_vigente()
        p["smn"] = fuente_cap(parsed)
        for localidad, provincia in [("Rosario", "Santa Fe"), ("Corrientes", "Corrientes")]:
            with self.subTest(localidad=localidad):
                row = self.evaluar(met_row(localidad, provincia), p)
                self.assertIsNone(row.smn_rango)
                self.assertNotEqual(row.nivel, "ROJO")

    def test_smn_lluvia_extrema_vigente_eleva_rojo_con_indice_bajo(self):
        p = panorama_vigente()
        p["smn"] = fuente_cap(app.parsear_cap_smn(cap_lluvia(), SMN_URL))
        row = self.evaluar(met_row("Rafaela", "Santa Fe"), p)
        self.assertEqual(row.indice, 27.9)
        self.assertEqual(row.nivel, "ROJO")
        self.assertEqual(row.smn_rango, 2)
        self.assertTrue(row.smn_vigente)
        self.assertIn("SMN", row.motivos_semaforo)

    def test_smn_vencido_cancelado_o_emitido_en_futuro_no_eleva(self):
        for changes in (
            dict(expires=(NOW-timedelta(minutes=1)).isoformat()),
            dict(msgType="Cancel", references="SMN,lluvia-rafaela,2026-10-14T11:00:00-03:00"),
            dict(sent=(NOW+timedelta(hours=1)).isoformat()),
            dict(onset=(NOW+timedelta(hours=73)).isoformat(), expires=(NOW+timedelta(hours=80)).isoformat()),
        ):
            with self.subTest(cambio=changes):
                p = panorama_vigente()
                p["smn"] = fuente_cap(app.parsear_cap_smn(cap_lluvia(**changes), SMN_URL))
                row = self.evaluar(met_row("Rafaela", "Santa Fe"), p)
                self.assertIsNone(row.smn_rango)
                self.assertNotEqual(row.nivel, "ROJO")


class TelegramIntegradoTests(unittest.TestCase):
    def setUp(self):
        self.current = NOW
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.database = Path(self.tempdir.name)/"telegram.sqlite3"
        self.patches = [
            patch.object(app, "ahora", side_effect=lambda: self.current),
            patch.object(app, "TELEGRAM_DB_FILE", self.database),
            patch.object(app, "obtener_secrets_telegram", return_value=("TEST-BOT-FAKE", "TEST-CHAT")),
            patch.object(app.st, "session_state", {}),
            patch.object(app.requests, "get", side_effect=AssertionError("Red real prohibida")),
            patch.object(app.requests, "post", side_effect=AssertionError("Telegram real prohibido")),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(patch.stopall)
        self.calls = []
        self.send_patch = patch.object(app, "enviar_telegram_detallado", side_effect=self.fake_send)
        self.send_patch.start()

    def fake_send(self, mensaje):
        self.calls.append(mensaje)
        return dict(ok=True, incierto=False, message_id=len(self.calls), detalle="Confirmación simulada")

    def enviar(self, row, guardar_registro=None):
        row = dict(row, actualizado=self.current.strftime("%d/%m/%Y %H:%M"))
        return app.procesar_alertas_telegram(app.pd.DataFrame([row]), guardar_registro=guardar_registro)

    def test_cambio_de_hora_y_texto_no_generan_spam(self):
        row = met_row(nivel="ROJO", indice=60.0, hidrologia_rango=1,
                      hidrologia_firma="fuente-estable", nivel_hidrologico_observado="ALERTA")
        self.enviar(row)
        self.current += timedelta(hours=4)
        self.enviar(dict(row, motivos_semaforo="Texto actualizado, misma evidencia", hidrologia_fecha=self.current.isoformat()))
        self.assertEqual(self.calls, [])

    def test_escalada_fluvial_envia_aun_con_indice_y_rojo_estables(self):
        row = met_row(nivel="ROJO", indice=60.0, hidrologia_rango=1,
                      hidrologia_firma="alerta", nivel_hidrologico_observado="ALERTA")
        self.enviar(row)
        self.current += timedelta(minutes=5)
        result = self.enviar(dict(row, hidrologia_rango=2, nivel_hidrologico_observado="EVACUACION",
                                 hidrologia_firma="evacuacion", motivos_semaforo="Umbral INA de evacuación superado."))
        self.assertEqual(result["enviadas"], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("evac", self.calls[0].lower())

    def test_primera_crecida_critica_con_indice_bajo_envia_una_sola_vez(self):
        p = panorama_vigente()
        p["estaciones"]["paso de los libres"] = estacion("Paso de los Libres", 9.28, 7.5, 8.5)
        with patch.object(app, "componentes_localidad", return_value=(None, None, None, None)):
            row = app.integrar_semaforo(app.pd.DataFrame([met_row()]), panorama=p).iloc[0].to_dict()
        result = self.enviar(row)
        self.assertEqual(result["enviadas"], 1)
        self.assertIn("ALERTA HIDROLÓGICA INICIAL", self.calls[0])
        self.assertEqual(row["indice"], 27.9)
        self.current += timedelta(hours=1)
        self.enviar(dict(row, fecha_evaluacion_integrada=self.current.isoformat()))
        self.assertEqual(len(self.calls), 1)

    def test_primer_aviso_smn_extremo_envia_una_sola_vez(self):
        p = panorama_vigente()
        p["smn"] = fuente_cap(app.parsear_cap_smn(cap_lluvia(), SMN_URL))
        with patch.object(app, "componentes_localidad", return_value=(None, None, None, None)):
            row = app.integrar_semaforo(app.pd.DataFrame([met_row("Rafaela", "Santa Fe")]), panorama=p).iloc[0].to_dict()
        result = self.enviar(row)
        self.assertEqual(result["enviadas"], 1)
        self.assertIn("ALERTA OFICIAL SMN INICIAL", self.calls[0])
        self.assertIn("ssl.smn.gob.ar", self.calls[0])
        self.current += timedelta(hours=1)
        self.enviar(dict(row, fecha_evaluacion_integrada=self.current.isoformat()))
        self.assertEqual(len(self.calls), 1)

    def test_nuevo_aviso_smn_extremo_con_indice_estable_y_rojo_previos(self):
        row = met_row("Rafaela", "Santa Fe", nivel="ROJO", indice=60.0,
                      smn_rango=1, smn_vigente=True, smn_firma="aviso-moderado")
        self.enviar(row)
        self.assertEqual(self.calls, [])
        self.current += timedelta(minutes=5)
        extreme = dict(row, smn_rango=2, smn_firma="aviso-extremo-1",
                       smn_alertas="LLUVIA · Extreme", smn_fuentes=SMN_URL)
        result = self.enviar(extreme)
        self.assertEqual(result["enviadas"], 1)
        self.current += timedelta(minutes=5)
        self.enviar(dict(extreme, smn_alertas="Mismo aviso · hora de consulta nueva"))
        self.assertEqual(len(self.calls), 1)
        self.current += timedelta(minutes=5)
        self.enviar(dict(extreme, smn_firma="aviso-extremo-2"))
        self.assertEqual(len(self.calls), 2)

    def test_fuente_critica_ausente_no_cierra_rojo_por_color_meteorologico_bajo(self):
        cases = [
            met_row(nivel="ROJO", hidrologia_rango=2, hidrologia_aplica=True,
                    hidrologia_vigente=True, hidrologia_pronostico_vigente=True,
                    nivel_hidrologico_observado="EVACUACION", hidrologia_firma="evacuacion"),
            met_row("Rafaela", "Santa Fe", nivel="ROJO", smn_rango=2,
                    smn_vigente=True, smn_firma="aviso-extremo-1", smn_fuentes=SMN_URL),
        ]
        for rojo in cases:
            with self.subTest(localidad=rojo["localidad"]):
                self.enviar(rojo)
                iniciales = len(self.calls)
                faltante = dict(rojo, nivel="VERDE", hidrologia_rango=None, smn_rango=None,
                                hidrologia_vigente=False, hidrologia_pronostico_vigente=False,
                                smn_vigente=False)
                for _ in range(2):
                    self.current += timedelta(hours=1)
                    result = self.enviar(faltante)
                    self.assertEqual(result["sin_datos"], 1)
                self.current += timedelta(hours=1)
                self.enviar(rojo)
                self.assertEqual(len(self.calls), iniciales)

    def test_prevision_entra_en_72h_y_envia_sin_cambio_meteorologico(self):
        p = panorama_vigente()
        p["estaciones"]["corrientes"] = estacion("Corrientes", 5.4, 6.5, 7.0, rio="Paraná")
        p["pronosticos"].append(dict(
            localidades=["corrientes"], nivel="EVACUACION",
            fecha_emision=NOW.isoformat(), desde=(NOW+timedelta(hours=73)).isoformat(),
            hasta=(NOW+timedelta(hours=80)).isoformat(), ventana="17/10/2026 · previsión oficial",
            texto="Se prevé superar el umbral de evacuación en Corrientes.", fuente=INA_URL,
        ))
        def evaluar_actual():
            met = met_row("Corrientes", nivel="ROJO", indice=60.0,
                          actualizado=self.current.strftime("%d/%m/%Y %H:%M"))
            with patch.object(app, "componentes_localidad", return_value=(None, None, None, None)):
                return app.integrar_semaforo(app.pd.DataFrame([met]), panorama=p).iloc[0].to_dict()
        inicial = evaluar_actual()
        self.assertEqual(inicial["nivel_hidrologico_previsto"], "EVACUACION")
        self.assertEqual(inicial["hidrologia_rango"], 1)
        self.assertFalse(inicial["hidrologia_preparacion_evacuacion_72h"])
        self.enviar(inicial)
        self.assertEqual(self.calls, [])
        self.current += timedelta(hours=2)
        urgente = evaluar_actual()
        self.assertEqual(urgente["hidrologia_rango"], 2)
        self.assertTrue(urgente["hidrologia_preparacion_evacuacion_72h"])
        self.assertEqual(urgente["indice"], inicial["indice"])
        result = self.enviar(urgente)
        self.assertEqual(result["enviadas"], 1)
        self.assertEqual(len(self.calls), 1)

    def test_hidrologia_actual_envia_con_meteorologia_antigua(self):
        self.enviar(met_row(nivel="AMARILLO", indice=40.0, hidrologia_rango=1,
                            nivel_hidrologico_observado="ALERTA"))
        self.current += timedelta(minutes=5)
        p = panorama_vigente()
        p["estaciones"]["paso de los libres"] = estacion("Paso de los Libres", 9.28, 7.5, 8.5)
        antigua = (self.current-timedelta(hours=13)).strftime("%d/%m/%Y %H:%M")
        with patch.object(app, "componentes_localidad", return_value=(None, None, None, None)):
            frame = app.integrar_semaforo(app.pd.DataFrame([met_row(actualizado=antigua)]), panorama=p)
        self.assertEqual(frame.iloc[0].actualizado, antigua)
        self.assertFalse(frame.iloc[0].cobertura_meteorologica)
        result = app.procesar_alertas_telegram(frame)
        self.assertEqual(result["enviadas"], 1)
        self.assertEqual(len(self.calls), 1)

    def test_falta_datos_no_cierra_episodio_ni_duplica_al_recuperarse(self):
        rojo = met_row(nivel="ROJO", indice=60.0, hidrologia_rango=2,
                       nivel_hidrologico_observado="EVACUACION", hidrologia_firma="evacuacion")
        self.enviar(rojo)
        avisos_iniciales = len(self.calls)
        self.current += timedelta(hours=1)
        self.enviar(dict(rojo, nivel="SIN DATOS", hidrologia_rango=None))
        self.current += timedelta(hours=1)
        self.enviar(rojo)
        self.assertEqual(len(self.calls), avisos_iniciales)

    def test_reserva_durable_antes_envio_y_confirmacion_despues(self):
        self.enviar(met_row(nivel="AMARILLO", indice=40.0))
        snapshots = []
        def persistir():
            with sqlite3.connect(self.database) as conn:
                states = conn.execute("SELECT estado FROM telegram_envios").fetchall()
            snapshots.append([r[0] for r in states])
        def send_after_persistence(message):
            self.assertTrue(snapshots)
            self.assertEqual(snapshots[-1], ["reservado"])
            return self.fake_send(message)
        self.current += timedelta(minutes=5)
        with patch.object(app, "enviar_telegram_detallado", side_effect=send_after_persistence):
            result = self.enviar(met_row(nivel="ROJO", indice=60.0), persistir)
        self.assertEqual(result["enviadas"], 1)
        self.assertEqual(snapshots[0], ["reservado"])
        self.assertEqual(snapshots[-1], ["enviado"])

    def test_persistencia_falla_y_no_se_envia(self):
        self.enviar(met_row(nivel="AMARILLO", indice=40.0))
        self.current += timedelta(minutes=5)
        def falla():
            raise RuntimeError("Fallo simulado al confirmar reserva remota")
        try:
            result = self.enviar(met_row(nivel="ROJO", indice=60.0), falla)
        except RuntimeError:
            result = None
        self.assertEqual(self.calls, [])
        if result is not None:
            self.assertTrue(result.get("registro_error"))

    def test_mensaje_largo_limitado_y_html_equilibrado(self):
        row = met_row(localidad="Paso <script>alert(1)</script> & Libres", nivel="ROJO", indice=60.0,
                      motivos_semaforo="Evacuación: río excede umbral. " + "<riesgo & agua> "*800,
                      tendencia="SUBIDA <script>no ejecutar</script>",
                      direccion_viento_texto="NE <img src=x>")
        evento = dict(tipo="ALERTA NUEVA", id="test00000001", motivo="Crecida & riesgo", indice_anterior=27.9)
        mensaje = app._mensaje_evento_telegram(row, evento)
        self.assertLessEqual(len(mensaje), 4096)
        parser = ValidarHTMLTelegram()
        parser.feed(mensaje)
        parser.close()
        self.assertEqual(parser.pila, [])
        self.assertNotIn("<script>", mensaje)
        self.assertNotIn("<img ", mensaje)
        self.assertIn("evac", mensaje.lower())

    def test_mensaje_hidrologico_sin_indice_meteorologico_no_nan(self):
        row = met_row(nivel="ROJO", indice=float("nan"), temperatura=float("nan"),
                      motivos_semaforo="Estación fluvial supera el umbral de evacuación.",
                      hidrologia_rango=2, hidrologia_vigente=True)
        mensaje = app.generar_mensaje_alerta(row)
        self.assertNotRegex(mensaje.lower(), r"\bnan\b")
        self.assertIn("evac", mensaje.lower())


if __name__ == "__main__":
    unittest.main()
