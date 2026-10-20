"""Regresión del byte 0x89: recuperar datos sin perder el archivo original."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from historial_local import _fecha_local, guardar_registros, leer_historial

ROOT = Path(__file__).resolve().parents[1]
CABECERA = b'actualizado,localidad,indice,motivo\n'
BUENO = '20/10/2026 10:00,Chajarí,25,"Lluvia, sin daños\nsegunda línea"\n'.encode()
MALO = b'20/10/2026 11:00,Chajar\x89,50,dato ilegible\n'
OTRO = '20/10/2026 12:00,Ituzaingó,30,observación\n'.encode()


class HistorialLocalTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.ruta = Path(self.temporal.name) / 'historial.csv'

    def nuevos(self, localidad='Corrientes'):
        return pd.DataFrame([{'actualizado': '20/10/2026 13:00',
                              'localidad': localidad, 'indice': 0, 'motivo': 'Sin lluvia'}])

    def test_byte_invalido_no_oculta_filas_validas_ni_altera_acentos(self):
        original = CABECERA + BUENO + MALO + OTRO
        self.ruta.write_bytes(original)
        with self.assertRaises(UnicodeDecodeError):
            pd.read_csv(self.ruta)
        lectura = leer_historial(self.ruta)
        self.assertEqual(lectura.omitidas, 1)
        self.assertEqual(lectura.original, original)
        self.assertEqual(lectura.datos.localidad.tolist(), ['Chajarí', 'Ituzaingó'])
        self.assertEqual(lectura.datos.motivo.iloc[0], 'Lluvia, sin daños\nsegunda línea')
        self.assertFalse(lectura.cola_ilegible)
        self.assertEqual(self.ruta.read_bytes(), original, 'La lectura no modifica el archivo')

    def test_escritura_recupera_y_conserva_respaldo_exacto(self):
        original = CABECERA + BUENO + MALO + OTRO
        self.ruta.write_bytes(original)
        self.assertTrue(guardar_registros(self.ruta, self.nuevos())['ok'])
        respaldos = list(self.ruta.parent.glob('*.bin'))
        self.assertEqual(len(respaldos), 1)
        self.assertEqual(respaldos[0].read_bytes(), original)
        recuperado = pd.read_csv(self.ruta)
        self.assertEqual(recuperado.localidad.tolist(), ['Chajarí', 'Ituzaingó', 'Corrientes'])
        self.assertEqual(recuperado.indice.tolist(), [25, 30, 0])
        incidencias = json.loads(self.ruta.with_name('historial.csv.recuperacion.json').read_text())
        self.assertEqual(incidencias[0]['filas_omitidas'], 1)
        self.assertTrue(guardar_registros(self.ruta, self.nuevos('Paraná'))['ok'])
        self.assertEqual(len(list(self.ruta.parent.glob('*.bin'))), 1)
        self.assertEqual(len(pd.read_csv(self.ruta)), 4)

    def test_cabecera_corrupta_no_se_reemplaza(self):
        original = CABECERA.replace(b'localidad', b'locali\x89dad') + BUENO
        self.ruta.write_bytes(original)
        self.assertFalse(guardar_registros(self.ruta, self.nuevos())['ok'])
        self.assertEqual(self.ruta.read_bytes(), original)

    def test_comillas_truncadas_conservan_registros_previos(self):
        self.ruta.write_bytes(CABECERA + BUENO + b'20/10/2026 12:00,"sin cierre')
        lectura = leer_historial(self.ruta)
        self.assertTrue(lectura.cola_ilegible)
        self.assertEqual(lectura.datos.localidad.tolist(), ['Chajarí'])

    def test_utf16_solo_con_bom(self):
        self.ruta.write_bytes((CABECERA + BUENO).decode().encode('utf-16'))
        lectura = leer_historial(self.ruta)
        self.assertEqual(lectura.codificacion, 'utf-16')
        self.assertTrue(guardar_registros(self.ruta, self.nuevos())['ok'])
        self.assertEqual(pd.read_csv(self.ruta).localidad.iloc[0], 'Chajarí')

    def test_fallo_del_respaldo_impide_reemplazar_original(self):
        original = CABECERA + BUENO + MALO
        self.ruta.write_bytes(original)
        abrir = Path.open
        def sin_permiso(ruta, mode='r', *args, **kwargs):
            if mode == 'xb':
                raise PermissionError('Prueba de disco sin permisos')
            return abrir(ruta, mode, *args, **kwargs)
        with patch.object(Path, 'open', sin_permiso):
            resultado = guardar_registros(self.ruta, self.nuevos())
        self.assertFalse(resultado['ok'])
        self.assertEqual(self.ruta.read_bytes(), original)

    def test_fallo_de_reemplazo_atomico_conserva_original(self):
        original = CABECERA + BUENO
        self.ruta.write_bytes(original)
        with patch('historial_local.os.replace', side_effect=OSError('Disco lleno')):
            self.assertFalse(guardar_registros(self.ruta, self.nuevos())['ok'])
        self.assertEqual(self.ruta.read_bytes(), original)
        self.assertEqual(list(self.ruta.parent.glob('*.tmp')), [])

    def test_sesiones_concurrentes_no_pierden_registros(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            resultados = list(executor.map(lambda n: guardar_registros(self.ruta, self.nuevos(str(n))), range(12)))
        self.assertTrue(all(x['ok'] for x in resultados))
        self.assertEqual(set(pd.read_csv(self.ruta).localidad), set(range(12)))

    def pantalla(self):
        codigo = (f'import sys\nsys.path.insert(0, {str(ROOT)!r})\n'
                  'from pathlib import Path\nimport streamlit as st\nimport plotly.express as px\n'
                  'from historial_local import mostrar_historial\n'
                  f'mostrar_historial(st, px, Path({str(self.ruta)!r}))\n')
        return AppTest.from_string(codigo, default_timeout=20).run()

    def test_panel_parcial_y_recuperado_muestran_grafico_y_respaldo(self):
        self.ruta.write_bytes(CABECERA + BUENO + MALO + OTRO)
        parcial = self.pantalla()
        self.assertEqual(len(parcial.exception), 0)
        self.assertIn('Historial parcial', parcial.warning[0].value)
        self.assertEqual(len(parcial.get('plotly_chart')), 1)
        self.assertEqual(len(parcial.get('download_button')), 2)
        guardar_registros(self.ruta, self.nuevos())
        recuperado = self.pantalla()
        self.assertEqual(len(recuperado.exception), 0)
        self.assertTrue(any('Se recuperó' in x.value for x in recuperado.warning))
        self.assertEqual(len(recuperado.get('download_button')), 2)

    def test_fechas_mixtas_e_indices_invalidos_no_rompen_panel(self):
        self.ruta.write_text('actualizado,localidad,indice\n'
                             '20/10/2026 10:00,Chajarí,0\n'
                             '2026-10-20T14:00:00Z,Chajarí,30\n'
                             'sin fecha,Chajarí,20\n'
                             '20/10/2026 12:00,Chajarí,999\n', encoding='utf-8')
        at = self.pantalla()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(len(at.get('plotly_chart')), 1)
        self.assertTrue(any('2 registro(s) sin fecha' in x.value for x in at.caption))
        self.assertEqual(len(leer_historial(self.ruta).datos), 4)

    def test_iso_no_intercambia_dia_mes_y_respeta_zona_local(self):
        esperado = pd.Timestamp('2026-10-02 11:00')
        self.assertEqual(_fecha_local('2026-10-02T14:00:00Z'), esperado)
        self.assertEqual(_fecha_local('2026-10-02T11:00:00-03:00'), esperado)
        self.assertEqual(_fecha_local('02/10/2026 11:00'), esperado)


if __name__ == '__main__':
    unittest.main()
