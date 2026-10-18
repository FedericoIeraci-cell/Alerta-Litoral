"""Lectura documentada de la estación automática RHN de Zárate.

No calcula semáforos ni importa umbrales de la escala PNA/INA 40. El vínculo
con el pronóstico 29534 se verifica por metadatos, nunca por similitud del nombre.
No consulta la red al importar el módulo ni mantiene una caché propia.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


VERSION_HIDROMETRIA = "2026-10-18.1"
SERIE_OBSERVADA = 29437
SERIE_PRONOSTICO = 29534
ESTACION_ID = 5907
NOMBRE = "Paraná Las Palmas - Zárate"
RED = "sat2"
ID_EXTERNO = "313"
CERO_IGN_M = 0.243
COORDENADAS = (-59.0088888888889, -34.1019444444444)
INA_ENDPOINT = "https://alerta.ina.gob.ar/a5/getSeriesBySiteAndVar"
VIGENCIA_HORAS = 6
MAX_BYTES = 2_000_000


def _normalizar(valor):
    if not isinstance(valor, str):
        return ""
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", valor)
                            if not unicodedata.combining(c)).casefold().split())


def _numero(valor):
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        return None
    try:
        valor = float(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    return valor if math.isfinite(valor) else None


def _fecha(valor):
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(valor, datetime) or valor.tzinfo is None or valor.utcoffset() is None:
        return None
    return valor.astimezone(timezone.utc)


def _referencia(valor=None):
    if valor is None:
        return datetime.now(timezone.utc)
    fecha = _fecha(valor)
    if fecha is None:
        raise ValueError("La hora de referencia debe incluir zona horaria.")
    return fecha


def _url(serie, referencia):
    return INA_ENDPOINT + "?" + urlencode({
        "series_id": serie, "tipo": "puntual",
        "timestart": (referencia - timedelta(days=7)).isoformat(),
        "timeend": referencia.isoformat(), "includeProno": "false",
    })


def _url_verificada(url, serie):
    if not isinstance(url, str):
        return False
    try:
        partes = urlsplit(url)
        query = parse_qs(partes.query, keep_blank_values=True)
        return (partes.scheme == "https" and partes.netloc == "alerta.ina.gob.ar"
                and partes.path == "/a5/getSeriesBySiteAndVar" and not partes.fragment
                and query.get("series_id") == [str(serie)]
                and query.get("tipo") == ["puntual"]
                and query.get("includeProno") == ["false"]
                and set(query) <= {"series_id", "tipo", "timestart", "timeend", "includeProno"})
    except (ValueError, TypeError):
        return False


def _entero_igual(valor, esperado):
    return type(valor) is int and valor == esperado


def _identidad(payload, serie, procedimiento):
    if not isinstance(payload, dict) or not _entero_igual(payload.get("id"), serie):
        raise ValueError("La respuesta no corresponde a la serie INA configurada.")
    est, var, proc, unidades = (payload.get(k) for k in ("estacion", "var", "procedimiento", "unidades"))
    if not all(isinstance(x, dict) for x in (est, var, proc, unidades)):
        raise ValueError("Faltan metadatos de estación, variable, procedimiento o unidad.")
    if (not _entero_igual(est.get("id"), ESTACION_ID) or est.get("tabla") != RED
            or est.get("id_externo") != ID_EXTERNO or est.get("propietario") != "RHN"
            or est.get("automatica") is not True or _normalizar(est.get("nombre")) != _normalizar(NOMBRE)):
        raise ValueError("La estación no coincide con la referencia automática RHN de Zárate.")
    red = est.get("red")
    if not isinstance(red, dict) or not _entero_igual(red.get("id"), 27) or red.get("tabla_id") != RED:
        raise ValueError("La red de la estación no coincide con RHN-SAT.")
    cero = _numero(est.get("cero_ign"))
    geom = est.get("geom")
    coords = geom.get("coordinates") if isinstance(geom, dict) else None
    if (cero is None or abs(cero - CERO_IGN_M) > 1e-6 or not isinstance(coords, list)
            or len(coords) != 2 or geom.get("type") != "Point"
            or any(_numero(v) is None or abs(_numero(v) - e) > 1e-6 for v, e in zip(coords, COORDENADAS))):
        raise ValueError("El cero de escala o la ubicación de la estación son incompatibles.")
    if (not _entero_igual(var.get("id"), 2) or var.get("var") != "H"
            or type(var.get("def_unit_id")) not in (str, int) or str(var.get("def_unit_id")) != "11"):
        raise ValueError("La variable no es altura hidrométrica.")
    esperado = "medicion directa" if procedimiento == 1 else "simulado"
    if not _entero_igual(proc.get("id"), procedimiento) or _normalizar(proc.get("nombre")) != esperado:
        raise ValueError("El procedimiento no corresponde al tipo de serie solicitado.")
    if (not _entero_igual(unidades.get("id"), 11) or unidades.get("abrev") != "m"
            or _normalizar(unidades.get("nombre")) not in {"metro", "metros"}):
        raise ValueError("La unidad de altura en metros no está verificada.")


def _vacio(fuente, consulta, fuente_pronostico):
    return {"nombre": NOMBRE, "serie": SERIE_OBSERVADA, "estacion_id": ESTACION_ID,
            "red": RED, "id_externo": ID_EXTERNO, "altura_m": None,
            "fecha_observada": None, "edad_horas": None, "vigente": False,
            "unidad": "m", "cero_ign_m": CERO_IGN_M, "fuente": fuente,
            "consulta": consulta, "error": "", "coincidencia_documental_pronostico": False,
            "serie_pronostico": SERIE_PRONOSTICO,
            "fuente_metadatos_pronostico": fuente_pronostico,
            "error_vinculo_pronostico": "No se verificaron los metadatos de la serie de pronóstico.",
            "observaciones": [], "registros_descartados": 0}


def _normalizar_observaciones(filas, referencia):
    """Reconstruye sólo los cuatro campos públicos de cada lectura."""
    if not isinstance(filas, list) or len(filas) > 20_000:
        raise ValueError("La serie de observaciones no es utilizable.")
    puntos, descartados = {}, 0
    for fila in filas:
        if not isinstance(fila, dict):
            descartados += 1
            continue
        fecha, valor = _fecha(fila.get("fecha")), _numero(fila.get("altura_m"))
        if fecha is None or fecha > referencia or valor is None or not -10 <= valor <= 100:
            descartados += 1
            continue
        if fecha in puntos:
            if puntos[fecha]["altura_m"] != valor:
                raise ValueError("La serie tiene alturas contradictorias para una misma fecha.")
            continue
        original = fila.get("fecha_original")
        original = original if isinstance(original, str) and _fecha(original) == fecha else None
        obs_id = fila.get("id_observacion")
        obs_id = str(obs_id) if type(obs_id) in (str, int) else ""
        puntos[fecha] = {"fecha": fecha.isoformat(), "altura_m": valor,
                         "fecha_original": original,
                         "id_observacion": obs_id if obs_id.isascii() and obs_id.isdigit() and len(obs_id) <= 24 else None}
    if not puntos:
        raise ValueError("La serie no conserva observaciones verificables.")
    return [puntos[fecha] for fecha in sorted(puntos)], descartados


def parsear_altura_zarate(payload, referencia=None, consulta=None, fuente=None,
                         metadatos_pronostico=None, fuente_metadatos_pronostico=None):
    """Valida una respuesta congelada; no realiza consultas ni aplica umbrales."""
    ref = _referencia(referencia)
    consultado = _referencia(consulta) if consulta is not None else ref
    fuente = fuente or _url(SERIE_OBSERVADA, ref)
    fuente_prono = fuente_metadatos_pronostico or _url(SERIE_PRONOSTICO, ref)
    if not _url_verificada(fuente, SERIE_OBSERVADA):
        raise ValueError("El origen de las observaciones no es el endpoint INA autorizado.")
    _identidad(payload, SERIE_OBSERVADA, 1)
    filas = payload.get("observaciones")
    if not isinstance(filas, list) or len(filas) > 20_000:
        raise ValueError("La respuesta no contiene una lista utilizable de observaciones.")
    resultado = _vacio(fuente, consultado.isoformat(), fuente_prono)
    puntos = {}
    for fila in filas:
        if not isinstance(fila, (list, tuple)) or len(fila) < 3:
            resultado["registros_descartados"] += 1
            continue
        fecha, fin, valor = _fecha(fila[0]), _fecha(fila[1]), _numero(fila[2])
        if fecha is None or fin != fecha or fecha > ref or valor is None or not -10 <= valor <= 100:
            resultado["registros_descartados"] += 1
            continue
        if fecha in puntos and puntos[fecha]["altura_m"] != valor:
            raise ValueError("Hay alturas contradictorias para una misma fecha de observación.")
        puntos[fecha] = {"fecha": fecha.isoformat(), "altura_m": valor,
                         "fecha_original": fila[0],
                         "id_observacion": str(fila[3]) if len(fila) > 3 else None}
    if not puntos:
        raise ValueError("Sin observaciones numéricas, fechadas y no futuras verificables.")
    resultado["observaciones"] = [puntos[fecha] for fecha in sorted(puntos)]
    ultimo = resultado["observaciones"][-1]
    resultado.update(altura_m=ultimo["altura_m"], fecha_observada=ultimo["fecha"])
    if metadatos_pronostico is not None:
        try:
            if not _url_verificada(fuente_prono, SERIE_PRONOSTICO):
                raise ValueError("El origen de los metadatos de pronóstico no está verificado.")
            _identidad(metadatos_pronostico, SERIE_PRONOSTICO, 4)
            resultado.update(coincidencia_documental_pronostico=True, error_vinculo_pronostico="")
        except ValueError as exc:
            resultado["error_vinculo_pronostico"] = str(exc)
    return revalidar_altura_zarate(resultado, ref)


def revalidar_altura_zarate(datos, referencia=None):
    """Recalcula la vigencia al mostrar datos cacheados, conservando la última lectura.

    Un dato de más de seis horas conserva su valor y fecha, con vigente=False.
    La consulta reciente no rejuvenece una medición antigua. No modifica la entrada.
    """
    ref = _referencia(referencia)
    resultado = _vacio(_url(SERIE_OBSERVADA, ref), None, _url(SERIE_PRONOSTICO, ref))
    if not isinstance(datos, dict):
        resultado["error"] = "No hay una observación automática verificada."
        return resultado
    # Ningún campo adicional (umbrales, semáforos, curvas ajenas) atraviesa el contrato.
    for campo in resultado:
        if campo != "observaciones" and campo in datos:
            resultado[campo] = datos[campo]
    resultado.update(vigente=False, edad_horas=None)
    consulta = _fecha(resultado.get("consulta"))
    resultado["consulta"] = consulta.isoformat() if consulta is not None else None
    for campo in ("error", "error_vinculo_pronostico"):
        resultado[campo] = resultado[campo][:400] if isinstance(resultado.get(campo), str) else "Dato de error no verificable."

    def invalidar(mensaje):
        limpio = _vacio(resultado["fuente"] if _url_verificada(resultado.get("fuente"), SERIE_OBSERVADA) else "",
                        resultado["consulta"],
                        resultado["fuente_metadatos_pronostico"]
                        if _url_verificada(resultado.get("fuente_metadatos_pronostico"), SERIE_PRONOSTICO) else "")
        limpio["error"] = mensaje
        return limpio

    cero, altura = _numero(resultado.get("cero_ign_m")), _numero(resultado.get("altura_m"))
    identidad = (_entero_igual(resultado.get("serie"), SERIE_OBSERVADA)
                 and _entero_igual(resultado.get("estacion_id"), ESTACION_ID)
                 and resultado.get("red") == RED and resultado.get("id_externo") == ID_EXTERNO
                 and _normalizar(resultado.get("nombre")) == _normalizar(NOMBRE)
                 and resultado.get("unidad") == "m" and cero is not None
                 and abs(cero - CERO_IGN_M) <= 1e-6
                 and _url_verificada(resultado.get("fuente"), SERIE_OBSERVADA))
    if not identidad or consulta is None:
        return invalidar("La identidad o la fecha de consulta de la observación automática no están verificadas.")
    resultado.update(nombre=NOMBRE, unidad="m", cero_ign_m=cero)
    fecha = _fecha(resultado.get("fecha_observada"))
    if altura is None or not -10 <= altura <= 100 or fecha is None or fecha > ref:
        return invalidar(resultado.get("error") or "La altura o la fecha de la observación no son utilizables.")
    try:
        puntos, descartados = _normalizar_observaciones(datos.get("observaciones"), ref)
    except ValueError as exc:
        return invalidar(str(exc))
    ultimo = puntos[-1]
    if fecha != _fecha(ultimo["fecha"]) or abs(altura - ultimo["altura_m"]) > 1e-9:
        return invalidar("La última lectura no coincide con la fecha y altura de la serie publicada.")
    resultado["observaciones"] = puntos
    descartados_previos = resultado.get("registros_descartados")
    resultado["registros_descartados"] = (descartados_previos if type(descartados_previos) is int and 0 <= descartados_previos <= 20_000 else 0) + descartados
    edad = (ref - fecha).total_seconds() / 3600
    resultado.update(altura_m=altura, fecha_observada=fecha.isoformat(), edad_horas=edad,
                     vigente=edad <= VIGENCIA_HORAS and not bool(resultado.get("error")))
    vinculo = (resultado.get("coincidencia_documental_pronostico") is True
               and _entero_igual(resultado.get("serie_pronostico"), SERIE_PRONOSTICO)
               and _url_verificada(resultado.get("fuente_metadatos_pronostico"), SERIE_PRONOSTICO)
               and not resultado.get("error_vinculo_pronostico"))
    resultado["coincidencia_documental_pronostico"] = vinculo
    resultado["serie_pronostico"] = SERIE_PRONOSTICO
    if not _url_verificada(resultado.get("fuente_metadatos_pronostico"), SERIE_PRONOSTICO):
        resultado["fuente_metadatos_pronostico"] = ""
    if not vinculo and not resultado.get("error_vinculo_pronostico"):
        resultado["error_vinculo_pronostico"] = "El vínculo con la serie de pronóstico no está verificado."
    return resultado


class _SinRedireccion(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("El endpoint INA devolvió una redirección; origen no verificado.")


def _leer_json(url, serie, session=None):
    if not _url_verificada(url, serie):
        raise ValueError("Endpoint INA no autorizado.")
    headers = {"User-Agent": "ALA-ZC/hidrometria-local", "Accept": "application/json"}
    if session is None:
        with build_opener(_SinRedireccion()).open(Request(url, headers=headers), timeout=12) as response:
            if response.status != 200 or not _url_verificada(response.url, serie):
                raise ValueError("Respuesta HTTP de origen no verificado.")
            cuerpo = response.read(MAX_BYTES + 1)
    else:
        response = session.get(url, headers=headers, timeout=(3, 10), allow_redirects=False, stream=True)
        try:
            if response.status_code != 200 or not _url_verificada(response.url, serie):
                raise ValueError("Respuesta HTTP de origen no verificado.")
            partes, longitud = [], 0
            for parte in response.iter_content(chunk_size=65536):
                longitud += len(parte)
                if longitud > MAX_BYTES:
                    raise ValueError("La respuesta INA supera el tamaño permitido.")
                partes.append(parte)
            cuerpo = b"".join(partes)
        finally:
            response.close()
    if len(cuerpo) > MAX_BYTES:
        raise ValueError("La respuesta INA supera el tamaño permitido.")
    return json.loads(cuerpo)


def consultar_altura_zarate(referencia=None, session=None):
    """Consulta sólo dos series públicas INA; un fallo del vínculo no oculta la medición.

    `session` es opcional y facilita pruebas sin red. La respuesta es siempre un
    diccionario público. Los clientes pueden cachearlo y deben revalidarlo al mostrarlo.
    """
    ref = _referencia(referencia)
    fuente, fuente_prono = _url(SERIE_OBSERVADA, ref), _url(SERIE_PRONOSTICO, ref)
    consulta = datetime.now(timezone.utc).isoformat()
    try:
        payload = _leer_json(fuente, SERIE_OBSERVADA, session)
        # Verificar la observación antes de hacer una segunda solicitud.
        resultado = parsear_altura_zarate(payload, ref, consulta, fuente)
    except Exception as exc:
        resultado = _vacio(fuente, datetime.now(timezone.utc).isoformat(), fuente_prono)
        resultado["error"] = (str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError)
                              else "No se pudo verificar la consulta de observaciones INA (" + type(exc).__name__ + ").")
        return resultado
    try:
        metadatos = _leer_json(fuente_prono, SERIE_PRONOSTICO, session)
        resultado = parsear_altura_zarate(payload, ref, consulta, fuente, metadatos, fuente_prono)
    except Exception as exc:
        resultado["error_vinculo_pronostico"] = "No se pudo verificar la consulta de metadatos de pronóstico INA (" + type(exc).__name__ + ")."
    resultado["consulta"] = datetime.now(timezone.utc).isoformat()
    return revalidar_altura_zarate(resultado, ref if referencia is not None else None)
