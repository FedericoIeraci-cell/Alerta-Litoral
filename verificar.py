"""Entrada compatible del monitor antiguo: consulta integrada sin envíos.

La vigilancia horaria y los envíos usan monitor.py desde ala-monitor.yml.
Esta entrada evita un segundo circuito meteorológico sin registro durable.
"""
from monitor import main


if __name__ == "__main__":
    raise SystemExit(main(["--dry-run"]))
