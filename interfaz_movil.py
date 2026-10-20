"""Presentación responsive. No consulta fuentes ni interpreta niveles de riesgo."""


def estilos_responsive():
    return '''<style>
.ala-accesos {display:flex;gap:8px;margin:0 0 10px;font-family:system-ui,sans-serif}
.ala-accesos a {display:flex;align-items:center;justify-content:center;gap:6px;min-height:46px;
 padding:8px 14px;border:1px solid #c6d6e0;border-radius:10px;background:#f4f9fc;
 color:#16445b!important;text-decoration:none!important;font-size:14px;font-weight:700}
.ala-accesos a:focus-visible {outline:3px solid #137a96;outline-offset:2px}
.ala-ancla,[id^="ala-mapa"],[id^="ala-localidad"] {scroll-margin-top:90px}
@media(max-width:700px) {
 [data-testid="stMainBlockContainer"] {padding:3.6rem .8rem calc(6rem + env(safe-area-inset-bottom))!important}
 [data-testid="stTabs"] [role="tablist"] {gap:6px;overflow-x:auto;scrollbar-width:thin;padding-bottom:5px}
 [data-testid="stTabs"] [role="tab"] {min-height:48px;flex-shrink:0;padding:8px 12px;font-size:15px}
 [data-testid="stButton"] button,[data-testid="stDownloadButton"] button,
 [data-testid="stLinkButton"] a,[data-testid="stExpander"] summary {min-height:48px}
 [data-testid="stSelectbox"] input,[data-testid="stTextInput"] input {font-size:16px!important}
 [data-testid="stSelectbox"] [data-baseweb="select"]>div {min-height:48px}
 [data-testid="stHeadingWithActionElements"] h2 {font-size:1.45rem;line-height:1.25}
 .ala-hero .ala-hero-contenido {min-height:96px;padding:12px;gap:10px}
 .ala-hero .ala-hero-marca {flex-basis:56px}
 .ala-hero .ala-hero-titulo {font-size:22px}
 .ala-hero .ala-hero-kicker {font-size:11px}
 .ala-accesos {position:fixed;bottom:0;left:0;right:0;z-index:999;
   display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;
   margin:0;padding:7px 8px calc(7px + env(safe-area-inset-bottom));
   background:#fff;border-top:1px solid #c6d6e0;box-shadow:0 -3px 18px #17324d14}
 .ala-accesos a {flex-direction:column;gap:2px;padding:4px 2px;min-height:48px;
   font-size:12px;border:0;background:transparent;border-radius:8px}
 .ala-accesos a:hover {background:#e6f2f5}
 .ala-accesos .ala-acceso-icono {font-size:19px;line-height:1.1}
}
@media(prefers-reduced-motion:reduce) {* {scroll-behavior:auto!important}}
</style>'''


def accesos_principales():
    return '''<nav class="ala-accesos" aria-label="Accesos rápidos de Alerta Litoral Agro">
<a href="#ala-mapa"><span class="ala-acceso-icono" aria-hidden="true">⌖</span>Mapa</a>
<a href="#ala-localidad"><span class="ala-acceso-icono" aria-hidden="true">⌂</span>Mi localidad</a>
<a href="#ala-alertas"><span class="ala-acceso-icono" aria-hidden="true">!</span>Alertas</a>
<a href="#ala-estado"><span class="ala-acceso-icono" aria-hidden="true">◷</span>Estado datos</a>
</nav>'''
