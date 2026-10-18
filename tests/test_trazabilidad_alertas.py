"""Regresiones de los rojos del 15/10 y su explicación. Red bloqueada."""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_integracion_app import app, met_row, panorama_vigente, estacion, cap_lluvia, fuente_cap, SMN_URL, ValidarHTMLTelegram
import hidrologia as h
import explicaciones_alerta as e
import red_integrada as red

FIX = Path(__file__).parent / 'fixtures'
NOW = datetime.fromisoformat('2026-10-15T01:31:20-03:00')
HIST = json.loads((FIX/'alertas_20261015_0131.json').read_text())


def panorama_archivado():
    p = h.parsear_reporte_ina((FIX/'ina_diario_20261014.html').read_text(), NOW)
    p['fuentes'] = [p['fuente']]
    p['represas'] = {}
    # Alturas exactas del snapshot de las 01:31; la previsión textual es el
    # reporte INA del 14/10 preservado como fixture, nunca una respuesta de red.
    for r in HIST['rios']:
        p['estaciones'][h.normalizar(r['estacion'])] = {
            'nombre':r['estacion'],'altura_m':r['altura_m'],'alerta_m':r['alerta_m'],
            'evacuacion_m':r['evacuacion_m'],'fecha':r['fecha_observada_original'],
            'fuente':r['fuente'],'fuente_umbrales':h.INA_URL,
        }
    return p


class TrazabilidadTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Red/Telegram prohibidos'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.now = NOW
        self.patches = [patch.object(app,'ahora',side_effect=lambda:self.now),
                        patch.object(app.st,'session_state',{}),
                        patch.object(app,'componentes_localidad',return_value=(None,None,None,None))]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def evaluar(self, nombre='Reconquista', indice=23.7, panorama=None, **campos):
        provincia = next(n['provincia'] for n in app.NODOS if n['localidad']==nombre)
        row = met_row(nombre, provincia, indice=indice, actualizado=self.now.strftime('%d/%m/%Y %H:%M'), **campos)
        return app.integrar_semaforo(app.pd.DataFrame([row]), panorama=panorama if panorama is not None else panorama_archivado()).iloc[0].to_dict()

    def test_despliegue_recarga_motor_previo_sin_evidencias(self):
        with patch.object(h, 'VERSION_EVIDENCIAS', 0), \
             patch.object(h, 'evaluar_localidad', return_value={'hidrologia_rango':2}):
            _, evaluar = app.cargar_motor_hidrologico()
            lectura = evaluar('Reconquista', panorama_archivado(), referencia=NOW)
            self.assertEqual(h.VERSION_EVIDENCIAS, 1)
            self.assertTrue(lectura['hidrologia_evidencias'])
        with patch('importlib.reload', side_effect=AssertionError('Motor vigente no se recarga')):
            app.cargar_motor_hidrologico()

    def test_despliegue_recarga_motor_previo_con_esquema_vigente(self):
        for version_anterior in (None, '2026-10-15.1'):
            with self.subTest(version_anterior=version_anterior), \
                 patch.object(h, 'VERSION_EVIDENCIAS', 1), \
                 patch.object(h, 'VERSION_MOTOR', version_anterior), \
                 patch.object(h, 'evaluar_localidad', side_effect=TypeError('Motor retenido')):
                if version_anterior is None:
                    del h.VERSION_MOTOR
                _, evaluar = app.cargar_motor_hidrologico()
                lectura = evaluar('Reconquista', panorama_archivado(), referencia=NOW)
                self.assertEqual(h.VERSION_EVIDENCIAS, 1)
                self.assertEqual(h.VERSION_MOTOR, '2026-10-17.2')
                self.assertTrue(lectura['hidrologia_evidencias'])

    def test_despliegue_motor_actual_no_necesita_recarga(self):
        with patch.object(h, 'VERSION_EVIDENCIAS', 1), \
             patch.object(h, 'VERSION_MOTOR', '2026-10-17.2'), \
             patch('importlib.reload', side_effect=AssertionError('Motor vigente no se recarga')):
            panorama, evaluar = app.cargar_motor_hidrologico()
            self.assertIs(panorama, h.obtener_panorama)
            self.assertIs(evaluar, h.evaluar_localidad)

    def test_ocho_rojos_con_causa_y_mismo_indice(self):
        tipos = {'Reconquista':'pronostico_fluvial','Corrientes':'pronostico_fluvial',
                 'Goya':'pronostico_fluvial','Resistencia':'pronostico_fluvial',
                 'Barranqueras':'pronostico_fluvial','Paso de los Libres':'observacion_fluvial',
                 'Puerto Iguazú':'observacion_fluvial','Andresito':'meteorologia'}
        for node in HIST['nodos']:
            with self.subTest(node=node['localidad']):
                row = self.evaluar(node['localidad'],node['indice_experimental'])
                self.assertEqual(row['nivel'],'ROJO')
                self.assertEqual(row['indice'],node['indice_experimental'])
                self.assertEqual(row['desencadenantes'][0]['tipo'],tipos[node['localidad']])
                self.assertTrue(all(c['rango']==2 for c in row['desencadenantes']))
                self.assertFalse(any(c['tipo']=='represa' for c in row['desencadenantes']))
                self.assertEqual(e.motivo_publico(row),row['motivo_principal'])

    def test_reconquista_ingresa_72h_con_indice_en_descenso_y_registro_explicito(self):
        self.now = datetime.fromisoformat('2026-10-14T23:31:00-03:00')
        previo = self.evaluar(indice=24.1)
        self.assertEqual(previo['nivel'],'AMARILLO')
        self.now += timedelta(hours=1)
        actual = self.evaluar(indice=24.0)
        self.assertEqual(actual['nivel'],'ROJO')
        razon = e.motivo_transicion(actual,previo['nivel'],24.1,app._referencia_hidrologica_telegram(previo))
        for texto in ('Reconquista','18/10/2026','72 h','24,1 → 24,0','no demuestra una subida'):
            self.assertIn(texto,razon)
        self.assertIn('hora no publicada',razon)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(app,'TELEGRAM_DB_FILE',Path(tmp)/'registro.sqlite3'), \
             patch.object(app,'obtener_secrets_telegram',return_value=('prueba','destino-prueba')), \
             patch.object(app,'enviar_telegram_detallado',return_value={'ok':True,'message_id':1}) as enviar:
            app.procesar_alertas_telegram(app.pd.DataFrame([previo]))
            result=app.procesar_alertas_telegram(app.pd.DataFrame([actual]))
            self.assertEqual(result['enviadas'],1)
            self.assertEqual(enviar.call_count,1)
            with sqlite3.connect(Path(tmp)/'registro.sqlite3') as db:
                motivo,mensaje=db.execute('select motivo,mensaje from telegram_envios').fetchone()
            self.assertIn('18/10/2026',motivo)
            self.assertIn('72 h',motivo)
            self.assertIn(h.INA_URL,mensaje)
            self.assertIn('4.35 m',mensaje)
            self.assertIn('VERDE',mensaje)
            self.assertNotIn('Yacyretá',motivo)
            app.procesar_alertas_telegram(app.pd.DataFrame([actual]))
            self.assertEqual(enviar.call_count,1,'Explicación nueva no altera deduplicación')

    def test_forecast_no_inventa_altura_y_fuente_no_es_represa(self):
        for nombre in ('Reconquista','Corrientes','Goya'):
            row=self.evaluar(nombre)
            c=row['desencadenantes'][0]
            self.assertEqual(c['fuente'],h.INA_URL)
            self.assertIsNone(c['central_m'])
            self.assertIn('sin altura numérica',row['motivo_principal'])
            self.assertEqual(row['nivel_hidrologico_observado'],'BAJO')
            self.assertEqual(c['precision_desde'],'fecha_sin_hora')
            self.assertIn('14/10/2026 · hora no publicada',e.detalle_evidencia(c))

    def test_sin_fuente_o_fallo_documentado_no_atribuye_causa(self):
        for fuente in (None,'https://noticias.example/crecida','javascript:alert(1)',h.INA_URL):
            p=panorama_archivado()
            for c in p['pronosticos']: c['fuente']=fuente
            if fuente==h.INA_URL: p['fuentes'][0]['error']='Fallo de consulta'
            row=self.evaluar(panorama=p)
            self.assertNotEqual(row['nivel'],'ROJO')
            self.assertFalse(any(c['tipo']=='pronostico_fluvial' for c in row['desencadenantes']))

    def test_vencida_futura_y_mas_alla_72h_no_son_causa_roja(self):
        for delta in (-49,1):
            p=panorama_archivado()
            for c in p['pronosticos']: c['fecha_emision']=(NOW+timedelta(hours=delta)).isoformat()
            self.assertNotEqual(self.evaluar(panorama=p)['nivel'],'ROJO')
        self.now=datetime.fromisoformat('2026-10-14T23:00:00-03:00')
        row=self.evaluar()
        self.assertEqual(row['nivel'],'AMARILLO')
        self.assertIn('Fuera de la ventana roja',e.motivo_publico(row))

    def test_numerico_y_banda_superior_conservan_significado(self):
        p=panorama_archivado()
        p['pronosticos']=[dict(localidades=['corrientes'],nivel='EVACUACION',
            fecha_emision=NOW.isoformat(),desde=(NOW+timedelta(hours=24)).isoformat(),
            hasta=(NOW+timedelta(hours=48)).isoformat(),fuente=h.INA_URL,
            tipo='prevision_oficial_numerica',central_m=7.2,inferior_m=6.9,superior_m=7.5)]
        row=self.evaluar('Corrientes',panorama=p)
        self.assertIn('central 7,20 m',row['motivo_principal'])
        self.assertEqual(row['desencadenantes'][0]['evacuacion_m'],7.0)
        p['pronosticos'][0].update(tipo='banda_superior_incierta',nivel='ALERTA',central_m=6.4)
        row=self.evaluar('Corrientes',panorama=p)
        self.assertEqual(row['nivel'],'AMARILLO')

    def test_umbral_observado_y_fuente_umbrales_separada(self):
        row=self.evaluar('Paso de los Libres',indice=27.0)
        c=row['desencadenantes'][0]
        self.assertEqual(c['altura_m'],9.37)
        self.assertEqual(c['evacuacion_m'],8.5)
        self.assertIn('saltogrande.org',c['fuente'])
        self.assertEqual(c['fuente_umbrales'],h.INA_URL)
        self.assertIn('9,37 m',e.hover_nodo(row))

    def test_multiples_causas_y_rojo_meteorologico(self):
        row=self.evaluar('Puerto Iguazú',56.1)
        self.assertEqual({c['tipo'] for c in row['desencadenantes']},{'observacion_fluvial','meteorologia'})
        row=self.evaluar('Rafaela',60)
        self.assertEqual(row['desencadenantes'][0]['tipo'],'meteorologia')
        self.assertIn('rojo ≥ 55',e.motivo_publico(row))

    def test_smn_se_identifica_sin_atribucion_fluvial(self):
        # Fixture CAP relativo al NOW de test_integracion_app.
        from test_integracion_app import NOW as CAP_NOW
        self.now=CAP_NOW
        p=panorama_vigente();p['smn']=fuente_cap(app.parsear_cap_smn(cap_lluvia(),SMN_URL))
        row=self.evaluar('Rafaela',20,panorama=p)
        self.assertEqual(row['desencadenantes'][0]['tipo'],'smn')
        self.assertIn('Aviso SMN',e.motivo_transicion(row,'AMARILLO',20))
        self.assertNotIn('Empeoró la condición fluvial',e.motivo_publico(row))

    def test_sin_traza_no_reconstruye_una_atribucion(self):
        row={'nivel':'ROJO','indice':24,'hidrologia_fuentes':h.INA_URL}
        self.assertIn('no verificable',e.motivo_publico(row))
        self.assertNotIn(h.INA_URL,app.generar_mensaje_alerta(row))

    def test_panel_mapa_telegram_y_snapshot_comparten_causa(self):
        row=self.evaluar('Reconquista',23.7,lat=-29.144,lon=-59.643)
        card=e.tarjeta_nodo_html(row); hover=e.hover_nodo(row); message=app.generar_mensaje_alerta(row)
        for texto in ('Riesgo integrado','Índice meteorológico','18/10/2026'):
            for salida in (card,hover,message): self.assertIn(texto,salida)
        self.assertNotIn('lluvia_futura_72',hover)
        self.assertIn('preparar evacuación',hover)
        import plotly.express as px
        import plotly.graph_objects as go
        with patch.object(app,'px',px),patch.object(app,'go',go):
            fig=app.crear_mapa(app.pd.DataFrame([row]))
        self.assertEqual(fig.data[0].hovertemplate,'%{customdata[0]}<extra></extra>')
        self.assertEqual(fig.data[0].customdata[0][0],hover)
        snapshot=red.construir_red_integrada([row],panorama_archivado(),referencia=NOW)
        node=next(n for n in snapshot['nodos'] if n['localidad']=='Reconquista')
        self.assertEqual(node['nivel_meteorologico'],'VERDE')
        self.assertEqual(node['indice_meteorologico'],23.7)
        self.assertEqual(node['estado_integrado_ala'],'ROJO')
        self.assertEqual(node['motivo_principal'],row['motivo_principal'])
        self.assertEqual(node['desencadenantes'][0]['fuente'],h.INA_URL)
        aged=red.validar_red_integrada(snapshot,NOW+timedelta(hours=4))
        node=next(n for n in aged['nodos'] if n['localidad']=='Reconquista')
        self.assertFalse(node['desencadenantes'][0]['vigente'])

    def test_hidrologia_actual_se_conserva_en_red_sin_meteo_actual(self):
        row=self.evaluar('Reconquista')
        row.update(actualizado=(NOW-timedelta(hours=13)).strftime('%d/%m/%Y %H:%M'),
                   cobertura_meteorologica=False, estado_datos='INCOMPLETO', nivel_meteorologico='SIN DATOS')
        snapshot=red.construir_red_integrada([row],panorama_archivado(),referencia=NOW)
        node=next(n for n in snapshot['nodos'] if n['localidad']=='Reconquista')
        self.assertFalse(node['vigente'])
        self.assertEqual(node['nivel_integrado_vigente'],'ROJO')
        self.assertTrue(node['desencadenantes'][0]['vigente'])

    def test_telegram_realista_longitud_enlaces_completos_y_html_seguro(self):
        for node in HIST['nodos']:
            row=self.evaluar(node['localidad'],node['indice_experimental'])
            evento={'tipo':'ALERTA NUEVA','id':'prueba','motivo':e.motivo_transicion(row,'AMARILLO',24.1),'indice_anterior':24.1}
            mensaje=app._mensaje_evento_telegram(row,evento)
            self.assertLessEqual(len(mensaje),4096,node['localidad'])
            parser=ValidarHTMLTelegram();parser.feed(mensaje);parser.close()
            self.assertEqual(parser.pila,[])
        row['localidad']='<img src=x onerror=alert(1)>'
        self.assertNotIn('<img ',e.tarjeta_nodo_html(row))
        self.assertNotIn('<img ',e.hover_nodo(row))
        self.assertNotRegex(app.generar_mensaje_alerta(dict(row,indice=float('nan'))).lower(),r'\bnan\b')


if __name__=='__main__':
    unittest.main()
