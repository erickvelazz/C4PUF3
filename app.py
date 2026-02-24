"""
app.py — Interfaz Streamlit VET
Uso: uv run streamlit run app.py
"""

import streamlit as st
import pandas as pd
import io
import tempfile
from pathlib import Path
from datetime import datetime

from ingesta     import leer_xlsx_ausur, leer_csv_salidas, limpiar_ausur, preparar_salidas_csv
from matching    import hacer_match, match_por_hora
from match_placa import match_por_placa, UMBRAL_PLACA
import matching as _m

st.set_page_config(page_title="SAC-Peaje · VET", page_icon="🛣️",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');
html,body,[class*="css"]{font-family:'IBM Plex Sans',sans-serif}
.stApp{background:#0d1117;color:#e6edf3}
[data-testid="stSidebar"]{background:#161b22;border-right:1px solid #30363d}
.hdr{background:linear-gradient(135deg,#0d1117,#161b22);border:1px solid #30363d;
  border-left:4px solid #f78166;padding:20px 28px;margin-bottom:20px;font-family:'IBM Plex Mono',monospace}
.hdr h1{color:#f78166;font-size:20px;font-weight:600;margin:0 0 4px 0;letter-spacing:.05em}
.hdr p{color:#8b949e;font-size:12px;margin:0}
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:18px 20px;text-align:center}
.card .v{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:600;line-height:1;margin-bottom:5px}
.card .l{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.1em}
.cg .v{color:#3fb950} .cr .v{color:#f85149} .cy .v{color:#e3b341}
.cb .v{color:#58a6ff} .cp .v{color:#d2a8ff}
.slbl{font-family:'IBM Plex Mono',monospace;font-size:11px;color:#f78166;
  text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px}
.stButton>button{background:#f78166!important;color:#0d1117!important;border:none!important;
  font-family:'IBM Plex Mono',monospace!important;font-weight:600!important;
  font-size:13px!important;border-radius:4px!important;width:100%}
.stButton>button:hover{background:#ff9580!important}
.stTabs [data-baseweb="tab-list"]{background:#161b22;border-bottom:1px solid #30363d}
.stTabs [data-baseweb="tab"]{font-family:'IBM Plex Mono',monospace;font-size:12px;color:#8b949e}
.stTabs [aria-selected="true"]{color:#f78166!important;border-bottom:2px solid #f78166!important;background:transparent!important}
hr{border-color:#30363d!important;margin:20px 0!important}
.log{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:14px;
  font-family:'IBM Plex Mono',monospace;font-size:12px;color:#8b949e;max-height:220px;overflow-y:auto}
.lok{color:#3fb950} .lwarn{color:#e3b341} .lerr{color:#f85149}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────
DEFAULTS = {
    "run":         False,
    "matched":     pd.DataFrame(),   # conciliados por Tag
    "nuevos":      pd.DataFrame(),   # conciliados por Placa
    "validacion":  pd.DataFrame(),   # requieren validación en cámara
    "duplicados":  pd.DataFrame(),   # duplicados AUSUR
    "sin_e":       pd.DataFrame(),   # entradas sin match final
    "sin_s":       pd.DataFrame(),   # salidas sin match final
    "pend_sin":    pd.DataFrame(),   # pendientes que no matchearon por placa
    "descartados": pd.DataFrame(),   # resto de descartados CSV
    "xls":         None,
    "xls_name":    None,
    "stats_placa": {},
    "logs":        [],
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Header ────────────────────────────────────────────────────────────
st.markdown("""
<div class="hdr">
  <h1>▸ SAC-PEAJE · MÓDULO VET</h1>
  <p>Conciliación Peaje Cerrado · AUSUR (entradas) vs CSV (salidas)</p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<p class="slbl">01 · Entradas — AUSUR</p>', unsafe_allow_html=True)
    f_ausur = st.file_uploader("Cruces_AUSUR_XXXXXXXX.xlsx", type=["xlsx"],
        help="Archivo AUSUR con las entradas. Se limpiará primero.")

    st.markdown("---")
    st.markdown('<p class="slbl">02 · Salidas — Report CSV</p>', unsafe_allow_html=True)
    f_csv = st.file_uploader("report_XXXX.csv", type=["csv", "txt"],
        help="CSV con entradas (A) y salidas (B). Solo se usan B estatus 10.")

    st.markdown("---")
    st.markdown('<p class="slbl">03 · Parámetros</p>', unsafe_allow_html=True)
    ventana = st.slider("Ventana máxima de match (min)", 30, 360, 120, 15,
        help="Tiempo máximo entre entrada y salida del mismo Tag")
    umbral_placa = st.slider("Umbral match por placa (%)", 60, 95, int(UMBRAL_PLACA * 100), 5,
        help="Score mínimo para aceptar match por placa. 75% = hasta 2 chars distintos")

    st.markdown("---")
    listos = f_ausur is not None and f_csv is not None
    boton  = st.button("▸  EJECUTAR", disabled=not listos, use_container_width=True)
    if not listos:
        falta = [n for n, f in [("AUSUR", f_ausur), ("CSV", f_csv)] if f is None]
        st.caption(f"⬆ Falta: {', '.join(falta)}")


# ── Helpers ───────────────────────────────────────────────────────────
def log(msg, t="ok"):
    cls = {"ok": "lok", "warn": "lwarn", "err": "lerr"}.get(t, "")
    ts  = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.append(f'<span class="{cls}">[{ts}] {msg}</span>')

def tmp(f):
    suf = Path(f.name).suffix
    t   = tempfile.NamedTemporaryFile(delete=False, suffix=suf)
    t.write(f.getvalue()); t.flush()
    return t.name

def generar_xls():
    s   = st.session_state
    buf = io.BytesIO()
    # Usar xlsxwriter si está disponible (5x más rápido), openpyxl como fallback
    try:
        engine = "xlsxwriter"
        import xlsxwriter as _  # noqa
    except ImportError:
        engine = "openpyxl"
    
    # ── Construir DataFrame de Reporte Resumido ──
    reporte_rows = []
    
    # 1. Matches por Método
    reporte_rows.append({"Concepto": "MATCHES POR MÉTODO", "Detalle": "", "Cantidad": ""})
    if not s.matched.empty:
        reporte_rows.append({"Concepto": "", "Detalle": "Tag Exacto (Id 10)", "Cantidad": len(s.matched)})
    if not s.nuevos.empty:
        reporte_rows.append({"Concepto": "", "Detalle": "Placa Difusa (Id 8)", "Cantidad": len(s.nuevos)})
    if not s.validacion.empty:
        reporte_rows.append({"Concepto": "", "Detalle": "Validación Manual", "Cantidad": len(s.validacion)})
    total_m = len(s.matched) + len(s.nuevos) + len(s.validacion)
    reporte_rows.append({"Concepto": "", "Detalle": "TOTAL MATCHES", "Cantidad": total_m})
    reporte_rows.append({"Concepto": "", "Detalle": "", "Cantidad": ""}) # Espacio

    # 2. Rechazos
    reporte_rows.append({"Concepto": "REGISTROS RECHAZADOS", "Detalle": "", "Cantidad": ""})
    
    # Rechazos detallados (match placa)
    stats = s.stats_placa
    if stats and "detalle_rechazos" in stats:
        for motivo, count in stats["detalle_rechazos"].items():
            reporte_rows.append({"Concepto": "", "Detalle": motivo, "Cantidad": count})
    elif not s.pend_sin.empty:
        reporte_rows.append({"Concepto": "", "Detalle": "Pendientes sin match (Id 8)", "Cantidad": len(s.pend_sin)})
        
    # Descartados CSV
    if not s.descartados.empty:
        if "Clasificacion" in s.descartados.columns:
            for motivo, count in s.descartados["Clasificacion"].value_counts().items():
                reporte_rows.append({"Concepto": "", "Detalle": f"Descartado: {motivo}", "Cantidad": count})
        else:
            reporte_rows.append({"Concepto": "", "Detalle": "Descartados CSV (Id 4, 6, 7)", "Cantidad": len(s.descartados)})
            
    # Duplicados AUSUR
    if not s.duplicados.empty:
        reporte_rows.append({"Concepto": "", "Detalle": "Duplicados AUSUR (Tag repetido ≤10s)", "Cantidad": len(s.duplicados)})
    reporte_rows.append({"Concepto": "", "Detalle": "", "Cantidad": ""}) # Espacio

    # 3. Resolución de Duplicados
    if stats and stats.get("duplicados_detectados", 0) > 0:
        reporte_rows.append({"Concepto": "RESOLUCIÓN DUPLICADOS (PLACA)", "Detalle": "", "Cantidad": ""})
        reporte_rows.append({"Concepto": "", "Detalle": "Conflictos Detectados", "Cantidad": stats.get("duplicados_detectados", 0)})
        reporte_rows.append({"Concepto": "", "Detalle": "Resueltos por Placa", "Cantidad": stats.get("resuelto_por_placa", 0)})
        reporte_rows.append({"Concepto": "", "Detalle": "Resueltos por Tiempo", "Cantidad": stats.get("resuelto_por_tiempo", 0)})
    
    df_reporte = pd.DataFrame(reporte_rows)

    with pd.ExcelWriter(buf, engine=engine) as w:
        # Escribir Reporte primero (o al final, según preferencia. Usualmente resumen va primero o ultimo)
        # El usuario pidió "una pestaña de excel", la pondremos al final o principio.
        # Pondré al final para no alterar orden existente si están acostumbrados, o principio si es resumen.
        # Lo pondré al final como Tab 8 en UI.
        
        for df, sheet in [
            (s.matched,     "Conciliados_Tag"),
            (s.nuevos,      "Conciliados_Placa"),
            (s.validacion,  "Requieren_Validacion"),
            (s.duplicados,  "Duplicados_AUSUR"),
            (s.sin_e,       "Sin_Match_Entradas"),
            (s.sin_s,       "Sin_Match_Salidas"),
            (s.pend_sin,    "Pendientes"),
            (s.descartados, "Descartados_CSV"),
        ]:
            if not df.empty:
                df.to_excel(w, sheet_name=sheet, index=False)
        
        # Agregar hoja de reporte
        if not df_reporte.empty:
            df_reporte.to_excel(w, sheet_name="Reporte_Operaciones", index=False)
            
    buf.seek(0)
    return buf.getvalue()

def card(col, cls, val, lbl):
    with col:
        st.markdown(f'<div class="card {cls}"><div class="v">{val}</div>'
                    f'<div class="l">{lbl}</div></div>', unsafe_allow_html=True)


# ── Pipeline ──────────────────────────────────────────────────────────
if boton and listos:
    st.session_state.logs = []
    st.session_state.run  = False
    _m.VENTANA_MAX_MINUTOS = ventana

    # Aplicar umbral dinámico del slider
    import match_placa as _mp
    _mp.UMBRAL_PLACA = umbral_placa / 100

    with st.spinner("Procesando..."):
        try:
            ra = tmp(f_ausur)
            rc = tmp(f_csv)

            # ── Proceso 1: Limpiar AUSUR ──────────────────────────────
            log(f"Leyendo AUSUR: {f_ausur.name}")
            df_ausur = leer_xlsx_ausur(ra)
            res_a    = limpiar_ausur(df_ausur)
            df_e     = res_a["entradas_limpias"]
            df_dup   = res_a["duplicados"]
            log(f"AUSUR limpias: {len(df_e):,} | Duplicados (≤10 seg): {len(df_dup):,}",
                "warn" if len(df_dup) > 0 else "ok")

            # ── Proceso 2: Preparar salidas ───────────────────────────
            log(f"Leyendo salidas: {f_csv.name}")
            df_csv_raw = leer_csv_salidas(rc)
            res_c      = preparar_salidas_csv(df_csv_raw)
            df_s       = res_c["salidas"]
            df_pend    = res_c["pendientes"]
            df_desc    = res_c["descartados"]
            log(f"Salidas (B est.10): {len(df_s):,} | Pendientes (4/8): {len(df_pend):,} | Descartados: {len(df_desc):,}")

            # ── Proceso 3: Match por Tag ──────────────────────────────
            log("Ejecutando match por Tag exacto...")
            res_m  = hacer_match(df_e, df_s)
            df_m   = res_m["matched"]
            df_se  = res_m["sin_match_e"]
            df_ss  = res_m["sin_match_s"]
            df_m["origen"] = "TAG_EXACTO"
            log(f"Tag exacto: {len(df_m):,} | Sin match E: {len(df_se):,} | S: {len(df_ss):,}",
                "warn" if len(df_se) > 0 else "ok")

            # ── Proceso 4: Match por Placa (Pendientes) ───────────────
            log(f"Match por placa — {len(df_pend):,} pendientes vs sin-match (umbral {umbral_placa}%)...")
            res_p       = match_por_placa(df_pend, df_se, df_ss)
            df_nuevos   = res_p["nuevos_conciliados"]
            df_pend_sin = res_p["pendientes_sin_match"]
            df_se2      = res_p["sin_match_e_final"]
            df_ss2      = res_p["sin_match_s_final"]
            stats_p     = res_p.get("stats", {})
            log(f"Por placa: {len(df_nuevos):,} | Pendientes sin match: {len(df_pend_sin):,}",
                "warn" if len(df_pend_sin) > 0 else "ok")

            total = len(df_m) + len(df_nuevos)
            pct   = total / max(len(df_e), 1) * 100
            log(f"Total conciliados: {total:,} ({pct:.1f}%)", "ok")

            # Guardar en session state
            st.session_state.matched     = df_m
            st.session_state.nuevos      = df_nuevos
            # st.session_state.validacion  = df_val
            st.session_state.duplicados  = df_dup
            st.session_state.sin_e       = df_se2
            st.session_state.sin_s       = df_ss2
            st.session_state.pend_sin    = df_pend_sin
            st.session_state.descartados = df_desc
            st.session_state.stats_placa = stats_p
            st.session_state.xls         = generar_xls()
            st.session_state.xls_name    = f"VET_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            st.session_state.run         = True
            log("Pipeline completado.")

        except Exception as e:
            log(f"ERROR: {e}", "err")
            st.error(f"Error: {e}")
            st.exception(e)


# ── Resultados ────────────────────────────────────────────────────────
if not st.session_state.run:
    c1, c2, c3 = st.columns(3)
    for col, n, t in [(c1,"01","Carga el AUSUR"), (c2,"02","Carga el CSV"), (c3,"03","Ejecuta")]:
        with col:
            st.markdown(f'<div class="card"><div class="v" style="color:#8b949e">{n}</div>'
                        f'<div class="l">{t}</div></div>', unsafe_allow_html=True)
else:
    s        = st.session_state
    total    = len(s.matched) + len(s.nuevos)
    pct      = total / max(total + len(s.sin_e), 1) * 100

    # ── Métricas ──────────────────────────────────────────────────────
    c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
    card(c1, "cg", f"{total:,}",            "Conciliados total")
    card(c2, "cg", f"{len(s.matched):,}",   "· Por Tag")
    card(c3, "cp", f"{len(s.nuevos):,}",    "· Por Placa")
    card(c4, "cy", f"{len(s.sin_e):,}",     "Sin match (E)")
    card(c5, "cr", f"{len(s.duplicados):,}","Duplicados AUSUR")
    card(c6, "cy", f"{len(s.validacion):,}","Validación cámara")
    card(c7, "cb", f"{pct:.1f}%",           "Tasa conciliación")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Descarga ──────────────────────────────────────────────────────
    cd, _ = st.columns([2, 6])
    with cd:
        fname = s.xls_name if s.xls_name else f"VET_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        st.download_button("⬇  DESCARGAR EXCEL", data=s.xls,
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_btn_{fname}",
            use_container_width=True)
        st.caption("Nota: Si la descarga no inicia, verifique si su navegador bloqueó contenido inseguro (icono en la barra de direcciones).")

    st.markdown("---")

    # ── Tabs ──────────────────────────────────────────────────────────
    t1, t2, t3, t4, t5, t6, t7, t8, t9 = st.tabs([
        f"✅ Tag ({len(s.matched):,})",
        f"🔤 Placa ({len(s.nuevos):,})",
        f"🔍 Duplicados AUSUR ({len(s.duplicados):,})",
        f"⚠️ Sin Match ({len(s.sin_e)+len(s.sin_s):,})",
        f"🕐 Pendientes sin match ({len(s.pend_sin):,})",
        f"📹 Validación cámara ({len(s.validacion):,})",
        f"🗑️ Descartados ({len(s.descartados):,})",
        "📊 Reporte",
        "🖥 Log",
    ])

    # Tab 1 — Conciliados por Tag
    with t1:
        if s.matched.empty:
            st.info("Sin conciliados por Tag.")
        else:
            if "datetime_entrada" in s.matched.columns:
                st.markdown('<p class="slbl">Cruces por hora del día</p>', unsafe_allow_html=True)
                df_h = s.matched.copy()
                df_h["hora"] = pd.to_datetime(df_h["datetime_entrada"], errors='coerce').dt.hour
                counts = df_h["hora"].dropna().value_counts().sort_index()
                if not counts.empty:
                    st.bar_chart(counts, color="#f78166")
                else:
                    st.info("Sin datos de hora para graficar.")
                st.markdown("---")
            st.dataframe(s.matched, use_container_width=True, hide_index=True, height=400)

    # Tab 2 — Conciliados por Placa
    with t2:
        if s.nuevos.empty:
            st.info("No se generaron conciliados por placa.")
        else:
            st.success(f"{len(s.nuevos):,} registros pendientes conciliados por similitud de placa.")
            if "pct_match_placa" in s.nuevos.columns:
                st.markdown('<p class="slbl">Distribución de score de match</p>', unsafe_allow_html=True)
                scores = s.nuevos["pct_match_placa"].str.replace("%","").astype(float)
                c_a, c_b, c_c = st.columns(3)
                with c_a:
                    st.markdown(f'<div class="card cb"><div class="v">{scores.mean():.0f}%</div><div class="l">Score promedio</div></div>', unsafe_allow_html=True)
                with c_b:
                    st.markdown(f'<div class="card cg"><div class="v">{scores.max():.0f}%</div><div class="l">Score máximo</div></div>', unsafe_allow_html=True)
                with c_c:
                    st.markdown(f'<div class="card cy"><div class="v">{scores.min():.0f}%</div><div class="l">Score mínimo</div></div>', unsafe_allow_html=True)
                st.markdown("---")
            st.caption("Bandera: origen = PENDIENTE_PLACA")
            st.dataframe(s.nuevos, use_container_width=True, hide_index=True, height=380)

    # Tab 3 — Duplicados AUSUR
    with t3:
        if s.duplicados.empty:
            st.success("No se encontraron duplicados en AUSUR.")
        else:
            st.warning(f"{len(s.duplicados):,} registros con mismo Tag dentro de 10 segundos. Requieren revisión manual.")
            st.caption("Estos registros NO entraron al proceso de match.")
            st.dataframe(s.duplicados, use_container_width=True, hide_index=True, height=380)

    # Tab 4 — Sin Match
    with t4:
        se_tab, ss_tab = st.tabs([
            f"Entradas sin match ({len(s.sin_e):,})",
            f"Salidas sin match ({len(s.sin_s):,})",
        ])
        with se_tab:
            if s.sin_e.empty:
                st.success("Todas las entradas encontraron match.")
            else:
                st.warning(f"{len(s.sin_e):,} entradas AUSUR sin salida correspondiente.")
                st.dataframe(s.sin_e, use_container_width=True, hide_index=True, height=360)
        with ss_tab:
            if s.sin_s.empty:
                st.success("Todas las salidas fueron emparejadas.")
            else:
                st.warning(f"{len(s.sin_s):,} salidas CSV sin entrada correspondiente.")
                st.dataframe(s.sin_s, use_container_width=True, hide_index=True, height=360)

    # Tab 5 — Pendientes sin match
    with t5:
        if s.pend_sin.empty:
            st.success("Todos los pendientes encontraron match por placa.")
        else:
            st.warning(f"{len(s.pend_sin):,} pendientes (status 4/8) sin placa con score ≥ {umbral_placa}%. Revisión manual.")
            st.dataframe(s.pend_sin, use_container_width=True, hide_index=True, height=380)

    # Tab 6 — Validación cámara
    with t6:
        if s.validacion.empty:
            st.success("No hay matches que requieran validación en cámara.")
        else:
            st.warning(f"{len(s.validacion):,} registros matcheados SOLO por hora (sin Tag ni Placa). Validar en cámara.")
            st.caption("Estos registros tienen alta probabilidad de ser el mismo cruce pero requieren confirmación visual.")
            if "tiempo_recorrido_min" in s.validacion.columns:
                st.markdown('<p class="slbl">Distribución de tiempos de recorrido</p>', unsafe_allow_html=True)
                tiempos = s.validacion["tiempo_recorrido_min"].dropna()
                c_a, c_b = st.columns(2)
                with c_a:
                    st.markdown(f'<div class="card cy"><div class="v">{tiempos.mean():.1f} min</div><div class="l">Tiempo promedio</div></div>', unsafe_allow_html=True)
                with c_b:
                    st.markdown(f'<div class="card cy"><div class="v">{tiempos.max():.1f} min</div><div class="l">Tiempo máximo</div></div>', unsafe_allow_html=True)
                st.markdown("---")
            st.dataframe(s.validacion, use_container_width=True, hide_index=True, height=380)

    # Tab 7 — Descartados
    with t7:
        if s.descartados.empty:
            st.success("Sin descartados del CSV.")
        else:
            if "Clasificacion" in s.descartados.columns:
                motivos = s.descartados["Clasificacion"].value_counts().reset_index()
                motivos.columns = ["Motivo", "Cantidad"]
                st.dataframe(motivos, use_container_width=True, hide_index=True)
                st.markdown("---")
            st.dataframe(s.descartados, use_container_width=True, hide_index=True, height=320)

    # Tab 8 — Reporte Resumido
    with t8:
        st.markdown('<p class="slbl">Resumen de Operaciones</p>', unsafe_allow_html=True)
        
        # 1. Total matches por método
        st.subheader("1. Total de Matches por Método")
        matches_data = []
        if not s.matched.empty:
            matches_data.append({"Método": "Tag Exacto (Id 10)", "Cantidad": len(s.matched)})
        if not s.nuevos.empty:
            matches_data.append({"Método": "Placa Difusa (Id 8)", "Cantidad": len(s.nuevos)})
        if not s.validacion.empty:
            matches_data.append({"Método": "Validación Manual", "Cantidad": len(s.validacion)})
            
        df_matches = pd.DataFrame(matches_data)
        if not df_matches.empty:
            # Calcular total
            total_matches = df_matches["Cantidad"].sum()
            df_matches.loc[len(df_matches)] = {"Método": "TOTAL", "Cantidad": total_matches}
            st.table(df_matches)
        else:
            st.info("No se realizaron matches.")
            
        # 2. Registros Rechazados (Agrupados)
        st.subheader("2. Registros Rechazados")
        rechazos_list = []
        
        # Usar estadísticas detalladas de match_placa si existen
        stats = s.stats_placa
        if stats and "detalle_rechazos" in stats:
            for motivo, count in stats["detalle_rechazos"].items():
                rechazos_list.append({"Razón": motivo, "Cantidad": count})
        else:
             # Fallback a s.pend_sin si no hay stats
             if not s.pend_sin.empty:
                 rechazos_list.append({"Razón": "Pendientes sin match (Id 8)", "Cantidad": len(s.pend_sin)})

        # Descartados CSV (Id 4, 6, 7, etc)
        if not s.descartados.empty:
            if "Clasificacion" in s.descartados.columns:
                motivos_desc = s.descartados["Clasificacion"].value_counts().reset_index()
                motivos_desc.columns = ["Razón", "Cantidad"]
                rechazos_list.extend(motivos_desc.to_dict('records'))
            else:
                rechazos_list.append({"Razón": "Descartados CSV (Id 4, 6, 7, etc)", "Cantidad": len(s.descartados)})

        # Duplicados AUSUR
        if not s.duplicados.empty:
             rechazos_list.append({"Razón": "Duplicados AUSUR (Tag repetido ≤10s)", "Cantidad": len(s.duplicados)})

        if rechazos_list:
            df_rechazos = pd.DataFrame(rechazos_list)
            # Agrupar por razón para asegurar unicidad
            df_rechazos = df_rechazos.groupby("Razón", as_index=False)["Cantidad"].sum()
            st.table(df_rechazos)
        else:
            st.success("No hubo rechazos significativos.")

        # 3. Resolución de Duplicados
        st.subheader("3. Resolución de Duplicados (Match Placa)")
        
        stats = s.stats_placa
        hay_duplicados = False
        
        if stats and stats.get("duplicados_detectados", 0) > 0:
            dup_data = [
                {"Método Resolución": "Prioridad Placa", "Cantidad": stats.get("resuelto_por_placa", 0)},
                {"Método Resolución": "Cercanía Temporal", "Cantidad": stats.get("resuelto_por_tiempo", 0)},
                {"Método Resolución": "Total Conflictos Resueltos", "Cantidad": stats.get("duplicados_detectados", 0)},
            ]
            st.table(pd.DataFrame(dup_data))
            hay_duplicados = True
        elif not s.nuevos.empty and "metodo_resolucion" in s.nuevos.columns:
             # Fallback si no tenemos stats pero sí columna (compatibilidad)
             resoluciones = s.nuevos["metodo_resolucion"].value_counts().reset_index()
             resoluciones.columns = ["Método Resolución", "Cantidad"]
             st.table(resoluciones)
             hay_duplicados = True
        
        if not hay_duplicados:
            st.info("No se requirió resolución de duplicados en el match por placa.")


    # Tab 9 — Log
    with t9:
        st.markdown('<p class="slbl">Log de ejecución</p>', unsafe_allow_html=True)
        html = "<br>".join(s.logs) if s.logs else "Sin logs."
        st.markdown(f'<div class="log">{html}</div>', unsafe_allow_html=True)
