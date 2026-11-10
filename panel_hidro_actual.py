"""Panel con refresco independiente: no reejecuta meteorología ni alertas."""
from datetime import datetime, timezone
from hidro_actual import ART, _fecha, consultar_hidrologia_actual


def hora(value, precision=None):
    fecha = _fecha(value)
    texto = fecha.astimezone(ART).strftime('%d/%m/%Y %H:%M ART') if fecha else 'Sin fecha confirmada'
    if fecha and precision == 'fecha_registro_fuente':
        return fecha.astimezone(ART).strftime('%d/%m/%Y') + ' · hora de medición no confirmada'
    if fecha and precision in ('fecha_sin_hora', 'hora_no_verificada_en_reporte_diario'):
        return fecha.astimezone(ART).strftime('%d/%m/%Y') + ' · hora no publicada'
    return texto


def valor(value, unidad):
    return f'{value:,.2f}'.translate(str.maketrans(',.','.,'))+' '+unidad if isinstance(value,(int,float)) else 'Sin dato'


def dibujar_panel(st, lecturas, informe=None):
    st.subheader('Ríos y represas · actualización automática')
    st.caption('Consulta a las fuentes cada 5 minutos mientras esta página está abierta; el panel se refresca cada minuto. '
               'La frecuencia de medición depende de cada organismo. Una consulta nueva no renueva un dato antiguo.')
    st.caption('Panel revisado: '+hora(datetime.now(timezone.utc)))
    lecturas = [d for d in lecturas if d.get('vigente') is True and not d.get('error')]
    if not lecturas:
        st.info('No hay lecturas hidrológicas vigentes con hora de medición confirmada. Los valores no verificables se omiten.')
    salto = next((d for d in lecturas if d.get('nombre')=='Salto Grande'),{})
    corrientes = next((d for d in lecturas if d.get('serie')==19),{})
    columnas = st.columns(2)
    for col,d,t,c,u in ((columnas[0],salto,'Salto Grande · descarga horaria','erogado_m3s','m³/s'),
                        (columnas[1],corrientes,'Corrientes · altura del río','altura_m','m')):
        if not d:
            continue
        col.metric(t,valor(d.get(c),u))
        col.caption('Fecha de la fuente: '+hora(d.get('fecha'),d.get('precision_fecha'))+' · '+d.get('estado','SIN DATOS'))
        col.caption('Última consulta: '+hora(d.get('consulta')))
        if not d.get('vigente'):
            col.warning(d.get('error') or 'No hay una lectura reciente confirmada.')
    with st.expander('Ver todas las lecturas de ríos y las fuentes',expanded=False):
        st.dataframe([{'Estación':d.get('nombre'),'Serie INA':d.get('serie'),
            'Última altura':valor(d.get('altura_m'),'m'),'Fecha registrada por la fuente':hora(d.get('fecha'),d.get('precision_fecha')),
            'Estado':d.get('estado'),'Última consulta':hora(d.get('consulta')),
            'Detalle':d.get('error',''),'Fuente':d.get('fuente','')}
            for d in lecturas if d.get('nombre')!='Salto Grande'],hide_index=True,use_container_width=True)
        st.caption('Sólo se muestran lecturas vigentes con hora de medición confirmada. La fecha de un registro diario no acredita esa hora. '
                   'El límite operativo ALA es de 6 horas para ríos; no es una vigencia oficial. No se trasladan umbrales entre estaciones.')
    with st.expander('Ver operación de represas y boletín de Yacyretá',expanded=False):
        if salto:
            st.write('Salto Grande · cota del embalse: '+valor(salto.get('cota_embalse_m'),'m')+
                     ' · turbinado: '+valor(salto.get('turbinas_m3s'),'m³/s')+
                     ' · vertido: '+valor(salto.get('vertedero_m3s'),'m³/s'))
        st.caption('Datos horarios CTM. Más de 2 horas se indica como lectura atrasada. La cota del embalse no es la altura de un puerto.')
        st.markdown('[Fuente horaria de Salto Grande](https://www.saltogrande.org/datos_horarios.php)')
        st.write('Yacyretá · boletín oficial, sin telemetría en vivo verificada')
        st.caption('Se recibe del informe del Litoral, cuya actualización está programada aproximadamente cada 10 minutos. Conserva la fecha original del boletín.')
        partes = [d for d in (informe or {}).get('represas',[]) if d.get('nombre')=='Yacyretá'
                  and d.get('vigente') is True and (informe or {}).get('snapshot_vigente') is True]
        if partes:
            from red_integrada import fecha_publica, estado_publico
            st.dataframe([{'Fecha del boletín':fecha_publica(d.get('fecha') or d.get('fecha_original'),d.get('precision_fecha')),
                'Aporte informado':valor(d.get('afluente_m3s'),'m³/s'),
                'Tipo':'Previsto para el día' if d.get('afluente_tipo')=='prevision_del_dia' else 'Ver parte original',
                'Vigencia':estado_publico(d.get('estado_tiempo'),d.get('vigente'),(informe or {}).get('snapshot_vigente',False)),
                'Fuente':d.get('fuente')} for d in partes],hide_index=True,use_container_width=True)
        else:
            st.warning('No se pudo confirmar un boletín de Yacyretá en el informe del Litoral.')
        st.markdown('[Consultar los informes oficiales de Yacyretá](https://www.eby.org.ar/informe-hidrologico)')


def mostrar_panel_hidrologia_actual(st):
    @st.fragment(run_every='60s')
    def panel():
        from red_integrada import consultar_red_integrada
        dibujar_panel(st,consultar_hidrologia_actual(alcance='Litoral'),consultar_red_integrada())
    panel()
