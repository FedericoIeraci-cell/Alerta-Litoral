"""No acreditar datos completos mediante sumas parciales o unidades ajenas."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import Mock, patch

from test_horizonte_7d import app, REF, datos_horarios
import meteorologia_compartida as meteo


class IntegridadMeteorologicaTests(unittest.TestCase):
    def evaluar(self, datos):
        return app.procesar_nodo(app.NODOS[0], datos, referencia_historica=REF)

    def test_cero_real_completo_conserva_indice_y_acumulados(self):
        datos = datos_horarios(168)
        datos['hourly']['precipitation'] = [0.] * len(datos['hourly']['time'])
        fila = self.evaluar(datos)
        self.assertTrue(fila['cobertura_meteorologica'])
        self.assertEqual(fila['lluvia_7d'], 0.)
        self.assertEqual(fila['lluvia_futura_7d'], 0.)

    def test_escorrentia_ausente_no_se_muestra_como_cero(self):
        datos = datos_horarios(168)
        del datos['hourly']['runoff']
        fila = self.evaluar(datos)
        for campo in ('runoff_24', 'runoff_72', 'runoff_7d', 'runoff_futuro_24', 'runoff_futuro_72'):
            with self.subTest(campo=campo):
                self.assertTrue(app.np.isnan(fila[campo]))
        self.assertFalse(fila['cobertura_meteorologica'])

    def test_antecedente_con_un_nulo_no_declara_total_parcial(self):
        datos = datos_horarios(168)
        indice = datos['hourly']['time'].index((REF-timedelta(hours=10)).isoformat())
        datos['hourly']['precipitation'][indice] = None
        fila = self.evaluar(datos)
        for campo in ('lluvia_24', 'lluvia_72', 'lluvia_7d'):
            self.assertTrue(app.np.isnan(fila[campo]), campo)
        self.assertFalse(fila['cobertura_meteorologica'])
        self.assertEqual(fila['lluvia_futura_72'], 72.)

    def test_conteo_de_168_marcas_no_sustituye_secuencia_horaria(self):
        datos = datos_horarios(168)
        indice = datos['hourly']['time'].index((REF-timedelta(hours=100)).isoformat())
        datos['hourly']['time'][indice] = (REF-timedelta(hours=100)+timedelta(minutes=30)).isoformat()
        fila = self.evaluar(datos)
        self.assertFalse(fila['cobertura_meteorologica'])
        self.assertTrue(app.np.isnan(fila['lluvia_7d']))

    def test_unidades_de_escorrentia_y_suelo_se_verifican(self):
        for caso in ('escorrentia', 'suelo'):
            datos = datos_horarios(168)
            if caso == 'escorrentia':
                datos['hourly_units']['runoff'] = '°C'
            else:
                for nombre in datos['hourly_units']:
                    if nombre.startswith('soil_moisture_'):
                        datos['hourly_units'][nombre] = '%'
            with self.subTest(caso=caso):
                fila = self.evaluar(datos)
                self.assertFalse(fila['cobertura_meteorologica'])
                self.assertEqual(fila['nivel'], 'SIN DATOS')

    def test_humedad_vieja_o_fuera_de_rango_no_habilita_indice_actual(self):
        for caso in ('nula_actual', 'mayor_que_uno', 'negativa'):
            datos = datos_horarios(168)
            indice = datos['hourly']['time'].index(REF.isoformat())
            for nombre in datos['hourly']:
                if nombre.startswith('soil_moisture_'):
                    datos['hourly'][nombre][indice] = {'nula_actual': None, 'mayor_que_uno': 5., 'negativa': -.1}[caso]
            with self.subTest(caso=caso):
                fila = self.evaluar(datos)
                self.assertFalse(fila['cobertura_meteorologica'])
                self.assertTrue(app.np.isnan(fila['humedad_suelo_promedio']))

    def test_unidad_ajena_no_acredita_lluvia_7d_ni_extendida(self):
        datos = datos_horarios(168)
        datos['hourly_units']['precipitation'] = '°C'
        fila = self.evaluar(datos)
        self.assertFalse(fila['cobertura_pronostico_7d'])
        self.assertFalse(fila['cobertura_pronostico_extendido'])
        self.assertTrue(app.np.isnan(fila['lluvia_futura_7d']))

    def test_celda_de_otra_localidad_no_se_atribuye_al_nodo(self):
        datos = dict(datos_horarios(168), latitude=0., longitude=0.)
        fila = self.evaluar(datos)
        self.assertEqual(fila['nivel'], 'SIN DATOS')
        self.assertIn('coordenadas', fila['error_datos'])

    def test_horarios_fuera_de_orden_no_se_acreditan(self):
        datos = datos_horarios(168)
        for valores in datos['hourly'].values():
            valores[10], valores[11] = valores[11], valores[10]
        fila = self.evaluar(datos)
        self.assertEqual(fila['nivel'], 'SIN DATOS')

    def test_modelo_explicito_y_captura_legada_sin_identidad_no_se_reutiliza(self):
        nodo = app.NODOS[0]
        raw = datos_horarios(168)
        fecha = REF.replace(tzinfo=app.TZ)
        captura = meteo.crear_captura([nodo], [raw], fecha)
        self.assertEqual(captura['fuente'], app.API_URL + '?models=ecmwf_ifs025')
        legada = deepcopy(captura)
        legada['fuente'] = app.API_URL
        with self.assertRaises(meteo.CapturaMeteorologicaError):
            meteo.validar_captura(legada, [nodo], fecha)
        app.obtener_datos_ecmwf.clear()
        respuesta = Mock()
        respuesta.json.return_value = raw
        with patch.object(app, 'NODOS', [nodo]), patch.object(app, 'ahora', return_value=fecha), \
                patch.object(app.requests, 'get', return_value=respuesta) as solicitud:
            datos = app.obtener_datos_ecmwf()
            self.assertEqual(solicitud.call_args.kwargs['params']['models'], 'ecmwf_ifs025')
            self.assertEqual(datos[0]['_ala_consultado'], fecha.isoformat())
        app.obtener_datos_ecmwf.clear()

    def test_captura_rechaza_unidades_ajenas_y_celdas_de_otro_lugar(self):
        nodo = app.NODOS[0]
        for caso in ('unidad', 'coordenadas'):
            raw = datos_horarios(168)
            if caso == 'unidad':
                raw['hourly_units']['runoff'] = '°C'
            else:
                raw.update(latitude=0., longitude=0.)
            with self.subTest(caso=caso), self.assertRaises(meteo.CapturaMeteorologicaError):
                meteo.crear_captura([nodo], [raw], REF.replace(tzinfo=app.TZ))

    def test_extremos_y_probabilidad_parciales_no_se_atribuyen_a_24_72_h(self):
        datos = datos_horarios(168)
        for nombre, unidad, valor in [('temperature_2m', '°C', -2.), ('wind_speed_10m', 'km/h', 15.),
                                      ('wind_gusts_10m', 'km/h', 30.), ('cape', 'J/kg', 200.),
                                      ('precipitation_probability', '%', 60.)]:
            datos['hourly'][nombre] = [valor] * len(datos['hourly']['time'])
            datos['hourly_units'][nombre] = unidad
        completa = self.evaluar(datos)
        self.assertEqual(completa['temp_min_24'], -2.)
        self.assertEqual(completa['prob_lluvia_72'], 60.)
        indice = datos['hourly']['time'].index((REF+timedelta(hours=12)).isoformat())
        for nombre in ('temperature_2m', 'wind_speed_10m', 'wind_gusts_10m', 'cape', 'precipitation_probability'):
            datos['hourly'][nombre][indice] = None
        parcial = self.evaluar(datos)
        for campo in ('temp_min_24', 'temp_max_24', 'viento_max_24', 'rafaga_max_24', 'cape_max_24',
                      'prob_lluvia_24', 'prob_lluvia_72', 'prob_lluvia'):
            self.assertTrue(app.np.isnan(parcial[campo]), campo)
        self.assertEqual(parcial['indice'], completa['indice'])

    def test_diario_directo_no_presenta_grados_como_mm(self):
        fecha = app.ahora().date().isoformat()
        datos = {'daily': {'time': [fecha], 'temperature_2m_max': [20.], 'precipitation_sum': [30.]},
                 'daily_units': {'temperature_2m_max': '°C', 'precipitation_sum': '°C'}}
        tabla = app.extraer_pronostico_diario(datos)
        self.assertEqual(tabla.iloc[0]['Temp. máxima'], 20.)
        self.assertTrue(app.np.isnan(tabla.iloc[0]['Precipitación']))


if __name__ == '__main__':
    unittest.main()
