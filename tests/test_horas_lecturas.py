"""La fecha del registro no se presenta como una hora de medición."""
from datetime import datetime
import unittest

from explicaciones_alerta import fecha as fecha_explicacion
from panel_hidro_actual import hora as fecha_panel
from portada_local import fecha_publica as fecha_portada, _fechada
from red_integrada import fecha_publica as fecha_red


class HorasLecturasTests(unittest.TestCase):
    FORMATOS = (fecha_explicacion, fecha_panel, fecha_portada, fecha_red)

    def test_registro_sin_hora_medida_muestra_solo_el_dia_local(self):
        # Tanto medianoche como cualquier otra hora de registro son distintas
        # de una hora de medición confirmada. El cambio de día usa ART.
        for valor, dia in (("2026-10-20T03:00:00Z", "20/10/2026"),
                           ("2026-10-20T15:45:00Z", "20/10/2026"),
                           ("2026-10-20T01:00:00Z", "19/10/2026")):
            for formato in self.FORMATOS:
                with self.subTest(valor=valor, modulo=formato.__module__):
                    self.assertEqual(formato(valor, "fecha_registro_fuente"),
                                     dia + " · hora de medición no confirmada")

    def test_medicion_confirmada_conserva_su_hora_incluso_a_medianoche(self):
        for valor, hora in (("2026-10-20T03:00:00Z", "00:00"),
                            ("2026-10-20T14:00:00Z", "11:00")):
            for formato in self.FORMATOS:
                with self.subTest(valor=valor, modulo=formato.__module__):
                    self.assertEqual(formato(valor, "fecha_hora_publicada"),
                                     "20/10/2026 " + hora + " ART")

    def test_lecturas_diarias_tampoco_agregan_horas(self):
        for precision in ("fecha_sin_hora", "hora_no_verificada_en_reporte_diario"):
            for formato in self.FORMATOS:
                with self.subTest(precision=precision, modulo=formato.__module__):
                    self.assertEqual(formato("2026-10-20T03:00:00Z", precision),
                                     "20/10/2026 · hora no publicada")

    def test_no_calcula_antiguedad_horaria_de_una_medicion_sin_hora(self):
        referencia = datetime.fromisoformat("2026-10-20T13:30:00-03:00")
        self.assertEqual(_fechada("Lectura", "2026-10-20T03:00:00Z", referencia,
                                 "fecha_registro_fuente"),
                         "Lectura: 20/10/2026 · hora de medición no confirmada")
        self.assertEqual(_fechada("Consulta", "2026-10-20T16:00:00Z", referencia),
                         "Consulta: 20/10/2026 13:00 ART · hace 30 min")


if __name__ == "__main__":
    unittest.main()
