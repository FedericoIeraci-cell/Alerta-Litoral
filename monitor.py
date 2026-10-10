#!/usr/bin/env python3
"""Ejecuta una evaluación de ALA sin abrir Streamlit.

El workflow programa este proceso. El registro se confirma en una rama de
GitHub antes de enviar a Telegram y después de conocer el resultado.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests


ART = ZoneInfo("America/Argentina/Buenos_Aires")
DB_REMOTE_PATH = "telegram_alerts.sqlite3"
STATUS_REMOTE_PATH = "monitor_status.json"
MAX_DB_BYTES = 20_000_000
MAX_STATUS_BYTES = 500_000
HISTORY_FIELDS = (
    "id", "creado", "intento", "localidad", "provincia", "tipo", "motivo",
    "indice_anterior", "indice", "estado", "message_id", "intentos", "detalle",
)
RESULT_FIELDS = (
    "configurado", "enviadas", "fallidas", "pendientes", "omitidas",
    "inicializadas", "sin_datos", "atrasadas", "inciertas", "pausado_sesion", "registro_error",
)


class MonitorError(RuntimeError):
    """Error público que no incluye respuestas ni credenciales."""


class StateStoreError(MonitorError):
    """El estado durable no pudo leerse o confirmarse."""


def _valid_repository(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}", value
    ):
        raise StateStoreError("GITHUB_REPOSITORY debe tener el formato propietario/repositorio.")
    if value.split("/", 1)[1] in {".", ".."}:
        raise StateStoreError("El repositorio configurado no es válido.")
    return value


def _valid_branch(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,150}", value)
        or ".." in value
        or "//" in value
        or value.endswith(("/", ".", ".lock"))
        or any(part.startswith(".") for part in value.split("/"))
    ):
        raise StateStoreError("La rama de estado configurada no es válida.")
    return value


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        raise StateStoreError("GitHub devolvió un identificador de estado inválido.")
    return value


def _validate_database(path: Path) -> None:
    connection = None
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_DB_BYTES:
            raise StateStoreError("El registro de alertas tiene un tamaño inválido.")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise StateStoreError("El registro de alertas no supera la comprobación de integridad.")
        expected = {
            "telegram_estado": {"destino", "nodo", "estado"},
            "telegram_envios": {
                "id", "destino", "nodo", "localidad", "provincia", "tipo",
                "motivo", "creado", "intento", "indice", "indice_anterior",
                "mensaje", "estado", "message_id", "detalle", "intentos",
            },
        }
        for table, columns in expected.items():
            actual = {record[1] for record in connection.execute(f"PRAGMA table_info({table})")}
            if not columns.issubset(actual):
                raise StateStoreError("El registro de alertas tiene una estructura incompatible.")
        # Cada fila de estado debe conservar un documento JSON válido.
        for (payload,) in connection.execute("SELECT estado FROM telegram_estado"):
            value = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise StateStoreError("El registro de una localidad está dañado.")
    except StateStoreError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise StateStoreError("No se pudo validar el registro de alertas.") from None
    finally:
        if connection is not None:
            connection.close()


def _validate_status(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise StateStoreError("El informe del monitor tiene una estructura incompatible.")
    try:
        checked = datetime.fromisoformat(value["checked_at"])
        if checked.tzinfo is None:
            raise ValueError()
        if not isinstance(value["frequency_minutes"], int) or not 5 <= value["frequency_minutes"] <= 1440:
            raise ValueError()
        if value["scope"] != "ECMWF / índice meteorológico":
            raise ValueError()
        if any(not isinstance(value[key], int) or value[key] < 0 for key in ("nodes_total", "nodes_valid", "nodes_red")):
            raise ValueError()
        if value["nodes_valid"] > value["nodes_total"] or value["nodes_red"] > value["nodes_valid"]:
            raise ValueError()
        if not isinstance(value["result"], dict) or not isinstance(value["history"], list):
            raise ValueError()
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(raw) > MAX_STATUS_BYTES:
            raise ValueError()
        forbidden = {"token", "chat_id", "destino", "mensaje", "telegram_bot_token", "telegram_chat_id", "github_token"}
        def inspect(item: Any) -> None:
            if isinstance(item, dict):
                if any(str(key).lower() in forbidden for key in item):
                    raise ValueError()
                for child in item.values():
                    inspect(child)
            elif isinstance(item, list):
                for child in item:
                    inspect(child)
        inspect(value)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise StateStoreError("El informe del monitor es inválido o contiene campos privados.") from None
    return value


class GitHubStateStore:
    """Estado durable con comparación SHA; nunca reemplaza un estado desconocido."""

    def __init__(self, token: str, repository: str, branch: str = "ala-monitor-state", session=None):
        if not isinstance(token, str) or not token.strip():
            raise StateStoreError("Falta la credencial de GitHub para guardar el estado.")
        self.repository = _valid_repository(repository)
        self.branch = _valid_branch(branch)
        self.session = session or requests.Session()
        self._headers = {
            "Authorization": "Bearer " + token.strip(),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "Alerta-Litoral-Agro-Monitor",
        }
        self._shas: dict[str, str | None] = {}
        self._ready = False

    @classmethod
    def from_environment(cls) -> "GitHubStateStore":
        return cls(
            os.environ.get("GITHUB_TOKEN", ""),
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("ALA_MONITOR_STATE_BRANCH", "ala-monitor-state"),
        )

    def _request(self, method: str, endpoint: str, *, allow_missing: bool = False, **kwargs):
        try:
            response = self.session.request(
                method, "https://api.github.com/repos/" + self.repository + endpoint,
                headers=self._headers, timeout=(10, 45), allow_redirects=False, **kwargs,
            )
        except requests.RequestException:
            raise StateStoreError("No se pudo contactar a GitHub; no se enviarán avisos sin guardar su registro.") from None
        if allow_missing and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            if response.status_code in {409, 422}:
                raise StateStoreError("El estado cambió o entró en conflicto; se detuvo el envío para evitar duplicados.")
            if response.status_code in {401, 403}:
                raise StateStoreError("GitHub rechazó el acceso al registro; revisá los permisos del workflow.")
            raise StateStoreError("GitHub no pudo confirmar el registro; se detuvo la evaluación.")
        return response

    @staticmethod
    def _json(response) -> dict[str, Any]:
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except (ValueError, TypeError):
            raise StateStoreError("GitHub devolvió un registro que no se pudo interpretar.") from None

    def _ensure_branch(self) -> None:
        if self._ready:
            return
        # Primero verifica el repositorio: un 404 por permisos nunca es un estado vacío.
        metadata = self._json(self._request("GET", ""))
        default = metadata.get("default_branch")
        if not isinstance(default, str) or not default:
            raise StateStoreError("No se pudo identificar la rama principal del repositorio.")
        branch = self._request("GET", "/branches/" + quote(self.branch, safe=""), allow_missing=True)
        if branch is None:
            reference = self._json(self._request("GET", "/git/ref/heads/" + quote(default, safe="")))
            commit_sha = _sha(reference.get("object", {}).get("sha"))
            self._request("POST", "/git/refs", json={"ref": "refs/heads/" + self.branch, "sha": commit_sha})
        self._ready = True

    def _read_file(self, filename: str, maximum: int) -> bytes | None:
        self._ensure_branch()
        response = self._request(
            "GET", "/contents/" + quote(filename, safe=""),
            params={"ref": self.branch}, allow_missing=True,
        )
        if response is None:
            self._shas[filename] = None
            return None
        record = self._json(response)
        blob_sha = _sha(record.get("sha"))
        size = record.get("size")
        if record.get("type") != "file" or not isinstance(size, int) or not 0 < size <= maximum:
            raise StateStoreError("El archivo de estado remoto tiene un formato o tamaño inválido.")
        if record.get("encoding") == "none":
            # GitHub no incluye contenido grande en /contents. Lee el mismo blob
            # identificado, sin redirecciones ni URLs de descarga ajenas al API.
            record = self._json(self._request("GET", "/git/blobs/" + blob_sha))
        if record.get("encoding") != "base64" or not isinstance(record.get("content"), str):
            raise StateStoreError("GitHub no devolvió el contenido completo del registro.")
        try:
            decoded = base64.b64decode("".join(record["content"].split()), validate=True)
        except (ValueError, binascii.Error):
            raise StateStoreError("El contenido remoto del registro está dañado.") from None
        if len(decoded) != size or not 0 < len(decoded) <= maximum:
            raise StateStoreError("El tamaño del registro recibido no coincide con el remoto.")
        self._shas[filename] = blob_sha
        return decoded

    def restore_db(self, path: str | Path) -> bool:
        path = Path(path)
        payload = self._read_file(DB_REMOTE_PATH, MAX_DB_BYTES)
        status = self._read_file(STATUS_REMOTE_PATH, MAX_STATUS_BYTES)
        if status is not None:
            try:
                _validate_status(json.loads(status))
            except (ValueError, UnicodeError):
                raise StateStoreError("El informe remoto del monitor está dañado.") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        if payload is None:
            if status is not None:
                raise StateStoreError("Falta el registro remoto de un monitor existente; se detuvo la evaluación para evitar duplicados.")
            # Una copia local de otra ejecución no sustituye la falta del remoto.
            path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            return False
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".restaurar-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            _validate_database(temporary)
            os.chmod(temporary, 0o600)
            for suffix in ("-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            os.replace(temporary, path)
        except StateStoreError:
            raise
        except OSError:
            raise StateStoreError("No se pudo restaurar el registro local de alertas.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True

    def _write_file(self, filename: str, payload: bytes, message: str) -> None:
        self._ensure_branch()
        if filename not in self._shas:
            raise StateStoreError("El estado debe restaurarse antes de poder actualizarlo.")
        document = {
            "message": message,
            "content": base64.b64encode(payload).decode("ascii"),
            "branch": self.branch,
        }
        if self._shas[filename] is not None:
            document["sha"] = self._shas[filename]
        result = self._json(self._request(
            "PUT", "/contents/" + quote(filename, safe=""), json=document,
        ))
        confirmed = result.get("content")
        if not isinstance(confirmed, dict):
            raise StateStoreError("GitHub no confirmó la actualización del estado.")
        self._shas[filename] = _sha(confirmed.get("sha"))

    def save_db(self, path: str | Path) -> None:
        path = Path(path)
        if not path.is_file():
            raise StateStoreError("Falta el registro local; no se puede confirmar el envío.")
        temporary = None
        source = target = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".copia-", delete=False) as stream:
                temporary = Path(stream.name)
            source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            target = sqlite3.connect(str(temporary))
            source.backup(target)
            target.close()
            target = None
            source.close()
            source = None
            _validate_database(temporary)
            payload = temporary.read_bytes()
            self._write_file(DB_REMOTE_PATH, payload, "ALA: confirmar registro de avisos")
        except StateStoreError:
            raise
        except (sqlite3.Error, OSError):
            raise StateStoreError("No se pudo preparar una copia consistente del registro.") from None
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save_status(self, value: dict[str, Any]) -> None:
        _validate_status(value)
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n"
        if len(payload) > MAX_STATUS_BYTES:
            raise StateStoreError("El informe del monitor supera el tamaño permitido.")
        self._write_file(STATUS_REMOTE_PATH, payload, "ALA: actualizar estado del monitor")


def load_app(path: str | Path):
    path = Path(path).resolve()
    if not path.is_file():
        raise MonitorError("No se encontró app.py; verificá el archivo del proyecto.")
    os.environ["ALA_MONITOR_HEADLESS"] = "1"
    spec = importlib.util.spec_from_file_location("ala_app_monitor", path)
    if spec is None or spec.loader is None:
        raise MonitorError("No se pudo cargar el módulo de Alerta Litoral Agro.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise MonitorError("La aplicación no pudo cargarse en modo de monitoreo.") from None
    return module


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (ValueError, TypeError):
            return None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return None


def _history(app) -> list[dict[str, Any]]:
    frame = app.historial_envios_telegram(30)
    if frame is None or getattr(frame, "empty", True):
        return []
    secrets = [os.environ.get(key, "") for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_TOKEN")]
    records = []
    for row in frame.to_dict(orient="records"):
        item = {key: _json_value(row.get(key)) for key in HISTORY_FIELDS}
        for key, value in item.items():
            if isinstance(value, str):
                for secret in secrets:
                    if secret:
                        value = value.replace(secret, "[oculto]")
                item[key] = value
        records.append(item)
    return records


def _frequency_minutes() -> int:
    try:
        value = int(os.environ.get("ALA_MONITOR_FREQUENCY_MINUTES", "60"))
        if not 5 <= value <= 1440:
            raise ValueError()
        return value
    except (TypeError, ValueError):
        raise MonitorError("La frecuencia del monitor debe estar entre 5 y 1440 minutos.") from None


def run_monitor(
    app_path: str | Path = "app.py", *, store=None, dry_run: bool = False,
    state_dir: str | Path | None = None, app_loader=None,
) -> dict[str, Any]:
    """Una evaluación; las dependencias pueden sustituirse en pruebas sin red."""
    directory = Path(state_dir or os.environ.get("ALA_MONITOR_STATE_DIR", ".ala-monitor")).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = directory / DB_REMOTE_PATH
    if not dry_run:
        store = store if store is not None else GitHubStateStore.from_environment()
        store.restore_db(database)
    os.environ["ALA_MONITOR_HEADLESS"] = "1"
    app = (app_loader or load_app)(app_path)
    app.TELEGRAM_DB_FILE = database
    if not dry_run:
        token, chat = app.obtener_secrets_telegram()
        if not token or not chat:
            raise MonitorError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en los secretos del repositorio.")
    try:
        frame = app.integrar_semaforo(app.obtener_todos_los_nodos())
    except Exception:
        raise MonitorError("No se pudo completar la consulta y evaluación meteorológica.") from None
    total = len(frame)
    levels = frame["nivel"].astype(str).str.upper() if "nivel" in frame else None
    if levels is None:
        raise MonitorError("La evaluación no devolvió niveles de alerta válidos.")
    valid = int(levels.isin(["VERDE", "AMARILLO", "ROJO"]).sum())
    checked = datetime.now(timezone.utc)
    status = {
        "schema_version": 1,
        "checked_at": checked.isoformat(),
        "checked_at_art": checked.astimezone(ART).isoformat(),
        "frequency_minutes": _frequency_minutes(),
        "scope": "ECMWF / índice meteorológico",
        "nodes_total": total,
        "nodes_valid": valid,
        "nodes_red": int(levels.eq("ROJO").sum()),
        "nodes_invalid": total - valid,
        "nodes_yellow": int(levels.eq("AMARILLO").sum()),
        "nodes_green": int(levels.eq("VERDE").sum()),
        "configured_nodes": len(app.NODOS),
        "result": {},
        "history": [],
        "health": "dry_run" if dry_run else "ok",
        "ok": False,
        "errors": [],
        "dry_run": bool(dry_run),
    }
    if dry_run:
        if valid == 0:
            raise MonitorError("La prueba no recibió ningún nodo con datos meteorológicos válidos.")
        return status
    if valid == 0:
        status["health"] = "sin_datos"
        status["errors"] = ["No se recibieron nodos con datos meteorológicos válidos."]
        status["history"] = _history(app)
        # El informe y la base deben existir juntos, incluso antes de disponer
        # de datos válidos. El historial crea la base vacía, sin inicializar nodos.
        store.save_db(database)
        store.save_status(status)
        raise MonitorError("Ninguna localidad tuvo datos válidos; no se enviaron avisos y el workflow falló.")
    try:
        result = app.procesar_alertas_telegram(frame, guardar_registro=lambda: store.save_db(database))
    except StateStoreError:
        raise
    except Exception:
        raise MonitorError("No se pudo confirmar la evaluación de alertas; revisá el workflow y el registro del canal.") from None
    if not isinstance(result, dict):
        raise MonitorError("La evaluación de Telegram no devolvió un resultado válido.")
    status["result"] = {key: _json_value(result.get(key, 0)) for key in RESULT_FIELDS}
    status["result"]["registro_error"] = bool(result.get("registro_error"))
    status["history"] = _history(app)
    if result.get("registro_error"):
        status["health"] = "error_registro"
        status["errors"] = ["No se pudo confirmar el registro durable de alertas."]
    elif result.get("inciertas"):
        status["health"] = "envio_sin_confirmacion"
        status["errors"] = ["Hay un envío sin confirmación de Telegram; revisá el canal."]
    elif result.get("fallidas"):
        status["health"] = "envio_rechazado"
        status["errors"] = ["Telegram rechazó uno o más avisos."]
    elif result.get("pausado_sesion") or not result.get("configurado"):
        status["health"] = "configuracion_invalida"
        status["errors"] = ["La configuración no permitió evaluar los envíos automáticos."]
    elif valid < total:
        status["health"] = "datos_parciales"
        status["errors"] = ["Una parte de las localidades no tuvo datos válidos."]
    status["ok"] = status["health"] == "ok"
    # También confirma inicializaciones y cambios que no produjeron mensajes.
    store.save_db(database)
    store.save_status(status)
    if status["health"] in {"error_registro", "envio_sin_confirmacion", "envio_rechazado", "configuracion_invalida"}:
        raise MonitorError("El monitor registró un problema de envío o persistencia; revisá el historial y el workflow.")
    return status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evalúa las alertas ALA sin abrir la página.")
    parser.add_argument("--app", default="app.py", help="Ruta del archivo principal de Alerta Litoral Agro.")
    parser.add_argument("--dry-run", action="store_true", help="Consulta datos sin enviar ni modificar el registro remoto.")
    args = parser.parse_args(argv)
    try:
        status = run_monitor(args.app, dry_run=args.dry_run)
        # Resumen deliberadamente pequeño: no imprime mensajes, secretos ni respuestas HTTP.
        print(json.dumps({
            "checked_at_art": status["checked_at_art"],
            "health": status["health"], "nodes_total": status["nodes_total"],
            "nodes_valid": status["nodes_valid"], "nodes_red": status["nodes_red"],
            "result": status["result"], "dry_run": status["dry_run"],
        }, ensure_ascii=False, allow_nan=False))
        return 0
    except MonitorError as error:
        print("Monitor detenido: " + str(error), file=sys.stderr)
        return 1
    except Exception:
        print("Monitor detenido por un error interno; no se publican credenciales ni detalles HTTP.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
