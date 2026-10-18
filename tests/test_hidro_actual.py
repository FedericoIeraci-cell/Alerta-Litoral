"""Lecturas y refresco seguros con respuestas congeladas; ningún envío real."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import hidro_actual as hidro
import panel_hidro_actual as panel

FIX = Path(__file__).parent/'fixtures'
HTML = (FIX/'ctm_horarios_20261018.html').read_text()
WFS = json.loads((FIX/'ina_ultimas_20261018.json').read_text())
NOW = hidro._ahora('2026-10-18T20:45:00Z')


class LecturasTests(unittest.TestCase):
    def setUp(self):
        hidro._CACHE.clear()
        guard = patch('requests.sessions.Session.request',side_effect=AssertionError('HTTP real prohibido'))
        guard.start(); self.addCleanup(guard.stop)
        self.addCleanup(hidro._CACHE.clear)

    def test_captura_ctm_hora_unidades_y_magnitudes_separadas(self):
        d = hidro.parsear_horarios(HTML,NOW)
        self.assertEqual(d['fecha'],'2026-10-18T17:00:00-03:00')
        self.assertEqual(d['turbinas_m3s'],7785)
        self.assertEqual(d['vertedero_m3s'],8262)
        self.assertEqual(d['erogado_m3s'],16047)
        self.assertEqual(d['cota_embalse_m'],34.31)
        self.assertNotIn('altura_m',d)
        self.assertTrue(d['vigente'])
        self.assertNotIn('señal_aguas_arriba',d)

    def test_falta_hora_unidad_columna_o_fecha_ambigua_rechazadas(self):
        for h in (HTML.replace('Fecha: 18/10/2026 Hora: 17:00','Sin fecha'),
                  HTML.replace('7.785 m<sup>3</sup>/s','7.785 MW'),
                  HTML.replace('Caudal Vertido','Descarga sin identificar'),
                  HTML+'<h3>Fecha: 18/10/2026 Hora: 18:00</h3>'):
            with self.subTest(h=h[-80:]),self.assertRaises(ValueError):
                hidro.parsear_horarios(h,NOW)

    def test_ctm_limite_dos_horas_futuro_y_cero_real(self):
        fecha = hidro._fecha('2026-10-18T20:00:00Z')
        self.assertTrue(hidro.parsear_horarios(HTML,fecha+timedelta(hours=2))['vigente'])
        self.assertFalse(hidro.parsear_horarios(HTML,fecha+timedelta(hours=2,seconds=1))['vigente'])
        self.assertEqual(hidro.parsear_horarios(HTML,fecha-timedelta(seconds=1))['estado'],'FECHA FUTURA')
        cero = hidro.parsear_horarios(HTML.replace('8.262 m<sup>3</sup>/s','0 m<sup>3</sup>/s'),NOW)
        self.assertEqual(cero['vertedero_m3s'],0)
        self.assertEqual(cero['erogado_m3s'],7785)

    def test_ina_no_rejuvenece_lecturas_diarias(self):
        datos = hidro.parsear_rios(WFS,NOW)
        zarate = next(d for d in datos if d['serie']==40)
        self.assertEqual(zarate['altura_m'],1.3)
        self.assertEqual(zarate['fecha'],'2026-10-18T03:00:00+00:00')
        self.assertEqual(zarate['estado'],'LECTURA ATRASADA')
        self.assertEqual(zarate['consulta'],NOW.isoformat())
        self.assertEqual((zarate['alerta_m'],zarate['evacuacion_m']),(2,2.2))
        self.assertEqual((zarate['serie'],zarate['tabla'],zarate['var_id']),(40,'alturas_prefe',2))
        self.assertEqual(zarate['precision_fecha'],'fecha_registro_fuente')
        faltante = next(d for d in datos if d['serie']==8)
        self.assertIsNone(faltante['altura_m'])
        self.assertTrue(faltante['error'])

    def test_ina_rechaza_otra_escala_red_variable_duplicado_y_fecha_futura(self):
        for key,value in [('nombre','Otra estación'),('tabla','sat2'),('var_id',1),
                          ('valor',float('nan')),('valor',True),('fecha','2026-10-18T23:00:00Z'),
                          ('fecha','2026-10-18T17:00:00')]:
            datos = deepcopy(WFS); datos['features'][0]['properties'][key]=value
            d=next(d for d in hidro.parsear_rios(datos,NOW) if d['serie']==19)
            self.assertIsNone(d['altura_m']); self.assertTrue(d['error'])
        datos=deepcopy(WFS);datos['features'].append(datos['features'][0])
        self.assertIsNone(next(d for d in hidro.parsear_rios(datos,NOW) if d['serie']==19)['altura_m'])

    def test_cache_cinco_minutos_y_revalidacion_a_cada_lectura(self):
        dato = hidro.parsear_horarios(HTML,NOW)
        with patch.object(hidro,'_consulta',return_value=[dato]) as leer,patch.object(hidro.time,'monotonic',return_value=0):
            hidro._con_cache('salto',NOW)
        with patch.object(hidro,'_consulta',side_effect=AssertionError('No releer')) as leer,patch.object(hidro.time,'monotonic',return_value=299):
            cache = hidro._con_cache('salto',NOW+timedelta(hours=3))
            self.assertFalse(hidro.estado_lectura(cache[0],NOW+timedelta(hours=3))['vigente'])
            leer.assert_not_called()
        with patch.object(hidro,'_consulta',return_value=[dato]) as leer,patch.object(hidro.time,'monotonic',return_value=300):
            hidro._con_cache('salto',NOW)
            leer.assert_called_once()

    def test_caida_conserva_ultima_medicion_y_reintenta_sin_falso_exito(self):
        dato = hidro.parsear_horarios(HTML,NOW)
        with patch.object(hidro,'_consulta',return_value=[dato]),patch.object(hidro.time,'monotonic',return_value=0):
            hidro._con_cache('salto',NOW)
        ref=NOW+timedelta(minutes=5)
        with patch.object(hidro,'_consulta',side_effect=TimeoutError('secret-not-logged')),patch.object(hidro.time,'monotonic',return_value=301):
            d=hidro.estado_lectura(hidro._con_cache('salto',ref)[0],ref)
        self.assertEqual(d['erogado_m3s'],16047)
        self.assertEqual(d['fecha'],dato['fecha'])
        self.assertEqual(d['consulta'],ref.isoformat())
        self.assertFalse(d['vigente']);self.assertNotIn('secret-not-logged',d['error'])
        with patch.object(hidro,'_consulta',return_value=[dato]),patch.object(hidro.time,'monotonic',return_value=602):
            self.assertTrue(hidro.estado_lectura(hidro._con_cache('salto',ref)[0],ref)['vigente'])

    def test_fallo_de_serie_y_regresion_no_borran_ultima_medicion(self):
        dato={'nombre':'Zárate · automática RHN','fecha':'2026-10-18T20:00:00Z','altura_m':1.18,'error':''}
        for cambio in ({'fecha':None,'altura_m':None,'error':'Sin observaciones'},
                       {'fecha':'2026-10-18T19:00:00Z','altura_m':1.15,'error':''}):
            hidro._CACHE.clear()
            with patch.object(hidro,'_consulta',return_value=[dato]),patch.object(hidro.time,'monotonic',return_value=0):
                hidro._con_cache('zarate',NOW)
            with patch.object(hidro,'_consulta',return_value=[{**dato,**cambio}]),patch.object(hidro.time,'monotonic',return_value=301):
                d=hidro.estado_lectura(hidro._con_cache('zarate',NOW)[0],NOW)
            self.assertEqual(d['altura_m'],1.18);self.assertFalse(d['vigente'])

    def test_http_sin_redireccion_sin_secretos_y_tamano_limitado(self):
        class Response:
            status_code=200
            def iter_content(self,chunk_size): yield b'x'*(hidro.MAX_BYTES+1)
            def close(self): self.closed=True
        class Session:
            def get(self,url,**kw): self.kw=kw; self.response=Response();return self.response
        s=Session()
        with self.assertRaises(ValueError): hidro._descargar(hidro.CTM_HORARIOS,s)
        self.assertFalse(s.kw['allow_redirects']); self.assertTrue(s.response.closed)
        self.assertNotIn('Authorization',s.kw['headers'])
        with self.assertRaises(ValueError): hidro._descargar('https://evil.example/datos_horarios.php',s)

    def test_fragmento_refresca_solo_panel_y_no_reusa_informe_capturado(self):
        class St:
            def fragment(self,run_every):
                self.intervalo=run_every
                def wrap(fn):self.fn=fn;return fn
                return wrap
        st=St()
        with patch.object(panel,'dibujar_panel') as dibujar,patch.object(panel,'consultar_hidrologia_actual',return_value=[]) as leer,patch('red_integrada.consultar_red_integrada',return_value={}) as red:
            panel.mostrar_panel_hidrologia_actual(st);st.fn()
        self.assertEqual(st.intervalo,'60s');self.assertEqual(leer.call_count,2);self.assertEqual(red.call_count,2)
        self.assertEqual(dibujar.call_count,2)

    def test_ui_real_streamlit_no_etiqueta_boletines_como_vivo(self):
        from streamlit.testing.v1 import AppTest
        def app():
            import streamlit as st
            from panel_hidro_actual import dibujar_panel
            from hidro_actual import estado_lectura
            dato=estado_lectura({'nombre':'Salto Grande','tipo':'observacion_horaria_ctm',
                'fecha':'2026-10-17T12:00:00Z','consulta':'2026-10-18T20:45:00Z','erogado_m3s':16047},'2026-10-18T20:45:00Z')
            dibujar_panel(st,[dato],{})
        at=AppTest.from_function(app).run(timeout=20)
        self.assertFalse(at.exception)
        texto=' '.join(x.value for x in at.caption)+' '.join(x.value for x in at.warning)
        self.assertIn('LECTURA ATRASADA',texto)
        self.assertIn('17/10/2026',texto)
        self.assertIn('No se pudo confirmar un boletín',texto)
        self.assertEqual(at.metric[0].value,'16.047,00 m³/s')

    def test_horario_cruza_contrato_compartido_y_caduca(self):
        import red_integrada as red
        dato=hidro.parsear_horarios(HTML,NOW)
        documento=red.construir_red_integrada([],{'represas':{'salto_grande_horario':dato}},referencia=NOW)
        d=red.validar_red_integrada(documento,NOW)['represas'][0]
        self.assertEqual(d['cota_embalse_m'],34.31);self.assertEqual(d['vertedero_m3s'],8262)
        self.assertTrue(d['vigente'])
        self.assertFalse(red.validar_red_integrada(documento,NOW+timedelta(hours=2))['represas'][0]['vigente'])

if __name__=='__main__': unittest.main()
