"""Presentación del RONI: el signo de la anomalía no clasifica una fase ENOS."""
from html import escape
import math


def color_roni(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError, OverflowError):
        return '#64748b'
    if isinstance(valor, bool) or not math.isfinite(numero) or numero == 0:
        return '#64748b'
    return '#dc2626' if numero > 0 else '#2563eb'


def tarjeta_roni(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError, OverflowError):
        numero = float('nan')
    valido = not isinstance(valor, bool) and math.isfinite(numero)
    texto = f'{numero:+.1f} °C' if valido else 'Sin dato'
    signo = ('Anomalía positiva' if numero > 0 else
             'Anomalía negativa' if numero < 0 else 'Anomalía cero') if valido else 'Sin valor verificable'
    return ('<div role="group" aria-label="Índice RONI">'
            '<div>RONI · anomalía relativa Niño 3.4</div>'
            f'<strong style="font-size:2rem;color:{color_roni(valor)}">{escape(texto)}</strong>'
            f'<div>{signo}</div></div>')


def grafico_roni(vista):
    """Barras por trimestre; los huecos no se unen ni se rellenan con cero."""
    import plotly.graph_objects as go

    fig = go.Figure(go.Bar(
        x=vista['fecha'], y=vista['RONI'],
        marker_color=[color_roni(v) for v in vista['RONI']],
        customdata=vista[['trimestre', 'año']].to_numpy(), name='RONI',
        hovertemplate='%{customdata[0]} %{customdata[1]}<br>RONI: %{y:+.1f} °C<extra></extra>',
    ))
    fig.add_hline(y=0, line_color='#64748b', line_width=1)
    fig.add_hline(y=.5, line_color='#dc2626', line_dash='dot')
    fig.add_hline(y=-.5, line_color='#2563eb', line_dash='dot')
    fig.update_layout(xaxis_title='Trimestre (mes central)', yaxis_title='RONI (°C)',
                      showlegend=False, bargap=.15, margin=dict(l=12, r=12, t=18, b=12))
    return fig
