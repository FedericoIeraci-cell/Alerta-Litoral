"""Reloj simulado: la actualización pública nunca inicia el monitor ni envíos."""

import builtins
import unittest
from unittest.mock import Mock, patch

import actualizacion_publica as actualizacion


class StreamlitSimulado:
    def __init__(self):
        self.session_state = {}
        self.rerun = Mock()

    @property
    def secrets(self):
        raise AssertionError("La actualización pública no necesita secretos")

    def fragment(self, *, run_every):
        self.intervalo = run_every

        def registrar(funcion):
            self.comprobar = funcion
            return funcion

        return registrar


class ActualizacionPublicaTests(unittest.TestCase):
    def setUp(self):
        self.st = StreamlitSimulado()
        reloj = patch.object(actualizacion.time, "monotonic", return_value=1000)
        self.reloj = reloj.start()
        self.addCleanup(reloj.stop)

    def avanzar(self, segundos):
        self.reloj.return_value = 1000 + segundos
        self.st.comprobar()

    def test_primera_carga_y_antes_de_cinco_minutos_no_repiten_evaluacion(self):
        actualizacion.activar_actualizacion(self.st)
        self.assertEqual(self.st.intervalo, "60s")
        for segundos in (0, 60, 120, 240, 299):
            self.avanzar(segundos)
        self.st.rerun.assert_not_called()

    def test_cinco_minutos_solicitan_pagina_completa_una_sola_vez(self):
        actualizacion.activar_actualizacion(self.st)
        self.avanzar(300)
        self.st.rerun.assert_called_once_with()
        for segundos in (300, 301, 360, 599):
            self.avanzar(segundos)
        self.st.rerun.assert_called_once_with()

    def test_interaccion_del_usuario_reinicia_el_plazo_de_evaluacion(self):
        actualizacion.activar_actualizacion(self.st)
        self.reloj.return_value = 1200
        actualizacion.activar_actualizacion(self.st)
        self.avanzar(300)
        self.avanzar(499)
        self.st.rerun.assert_not_called()
        self.avanzar(500)
        self.st.rerun.assert_called_once_with()

    def test_estado_faltante_invalido_o_reloj_reiniciado_se_recupera(self):
        for valor in (None, True, "1000", {}, float("nan"), float("inf"), -float("inf"), 9999):
            with self.subTest(valor=valor):
                self.reloj.return_value = 1000
                actualizacion.activar_actualizacion(self.st)
                self.st.rerun.reset_mock()
                self.st.session_state[actualizacion.CLAVE_ULTIMA_EVALUACION] = valor
                self.avanzar(60)
                self.st.rerun.assert_not_called()
                self.avanzar(360)
                self.st.rerun.assert_called_once_with()
        actualizacion.activar_actualizacion(self.st)
        self.st.rerun.reset_mock()
        self.st.session_state.clear()
        self.st.comprobar()
        self.st.rerun.assert_not_called()

    def test_sesiones_tienen_plazos_independientes(self):
        actualizacion.activar_actualizacion(self.st)
        otra = StreamlitSimulado()
        self.reloj.return_value = 1200
        actualizacion.activar_actualizacion(otra)
        self.avanzar(300)
        otra.comprobar()
        self.st.rerun.assert_called_once_with()
        otra.rerun.assert_not_called()

    def test_pausa_prolongada_produce_una_actualizacion_sin_recuperar_ciclos(self):
        actualizacion.activar_actualizacion(self.st)
        self.avanzar(3600)
        self.avanzar(3601)
        self.st.rerun.assert_called_once_with()

    def test_refresco_no_accede_a_red_secretos_ni_monitor(self):
        importar = builtins.__import__

        def importar_sin_monitor(nombre, *args, **kwargs):
            if nombre.split(".")[0] in {"monitor", "telegram"}:
                raise AssertionError("La página no debe iniciar el monitor")
            return importar(nombre, *args, **kwargs)

        with patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP real prohibido")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("HTTP real prohibido")), \
                patch("subprocess.run", side_effect=AssertionError("No ejecutar procesos")), \
                patch("subprocess.Popen", side_effect=AssertionError("No ejecutar procesos")), \
                patch("builtins.__import__", side_effect=importar_sin_monitor):
            actualizacion.activar_actualizacion(self.st)
            self.avanzar(300)
        self.st.rerun.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
