"""Colector de una consulta; ejecutar cada minuto fuera de Streamlit.

El SQLite requiere disco persistente y respaldo. No usa el estado de Telegram.
La importación de un JSON siempre queda marcada como simulada.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from estacion_propia import acquire, adapt_ecowitt, assess, advance_reference, configuration


class StationArchive:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS observations (
                    digest TEXT PRIMARY KEY, station_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL, provider TEXT NOT NULL,
                    observed_at TEXT, received_at TEXT NOT NULL,
                    record_json TEXT NOT NULL, quality_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS station_time ON observations
                    (station_id, source_kind, provider, observed_at);
                CREATE TABLE IF NOT EXISTS attempts (
                    attempted_at TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sensor_reference (
                    station_id TEXT NOT NULL, source_kind TEXT NOT NULL, provider TEXT NOT NULL,
                    quality_json TEXT NOT NULL, PRIMARY KEY(station_id, source_kind, provider));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def previous(self, record):
        with self.connect() as db:
            return self._previous(db, record)

    @staticmethod
    def _previous(db, record):
        row = db.execute("""SELECT quality_json FROM sensor_reference
            WHERE station_id=? AND source_kind=? AND provider=?""",
            (record["station_id"], record["source_kind"], record["provider"])).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, record, *, now=None):
        # Validar antes de usar la identidad. Transacción de referencia + registro
        # para que dos colectores no comparen contra una referencia diferente.
        assess(record, now=now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._previous(db, record)
            quality = assess(record, now=now, previous=previous)
            inserted = self._save(db, quality)
            reference = advance_reference(previous, quality)
            db.execute("INSERT OR REPLACE INTO sensor_reference VALUES (?,?,?,?)",
                       (quality["station_id"], quality["source_kind"], quality["provider"],
                        json.dumps(reference, allow_nan=False)))
        return quality, inserted

    @staticmethod
    def _save(db, quality):
        # Reconstruir sólo los campos de observación; no archivar claves añadidas.
        clean = {key: quality[key] for key in
                 ("schema_version", "station_id", "source_kind", "provider", "received_at", "risk_eligible")}
        clean["measurements"] = {
            key: {"value": item["raw_value"], "unit": item["raw_unit"], "observed_at": item["raw_observed_at"]}
            for key, item in quality["measurements"].items() if "sin_dato" not in item["flags"]}
        identity = {k: v for k, v in clean.items() if k != "received_at"}
        serialized = json.dumps(identity, sort_keys=True, allow_nan=False)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        dates = [m["observed_at"] for m in quality["measurements"].values()
                 if m["observed_at"] and "fecha_futura" not in m["flags"]]
        latest = max(dates) if dates else None
        cursor = db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?)",
            (digest, clean["station_id"], clean["source_kind"], clean["provider"], latest,
             clean["received_at"], json.dumps(clean, allow_nan=False),
             json.dumps(quality, allow_nan=False)))
        return cursor.rowcount == 1

    def attempt(self, status, now=None):
        with self.connect() as db:
            db.execute("INSERT INTO attempts VALUES (?,?)",
                       ((now or datetime.now(timezone.utc)).isoformat(), status))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="SQLite en disco persistente, fuera del repositorio")
    parser.add_argument("--simulate", type=Path, help="JSON Ecowitt sintético; nunca se marca como medición real")
    args = parser.parse_args(argv)
    config = configuration(os.environ)
    archive = StationArchive(args.db)
    if args.simulate:
        payload = json.loads(args.simulate.read_text())
        record = adapt_ecowitt(payload, config["station_id"], simulated=True)
        result = {"status": "SIMULADA", "record": record}
    else:
        result = acquire(config)
    archive.attempt(result["status"])
    if result["record"] is None:
        print(result["status"])
        return 0 if result["status"] == "DESACTIVADA" else 1
    quality, inserted = archive.save(result["record"])
    print(json.dumps({"status": quality["status"], "inserted": inserted,
                      "source_kind": quality["source_kind"], "risk_eligible": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
