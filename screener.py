# ==============================================================================
# SCREENER VALUE & MONITOR CON SYSTEM SCORE COMPLETO + FILTROS + DESVÍOS EMA
# ==============================================================================

import datetime
import json
import os
import re
import pandas as pd
import requests
import yfinance as yf

# ==============================================================================
# CONFIGURACIÓN GENERAL Y ENLACES
# ==============================================================================
URL_CARTERA_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRbLGRdor-TtNOtkqL0cbrTnUN0mg6-FLM-3yAxuZsznZRUJjeqoyWC7ZubG6kp1SEgYvcryTnb1eyE/pub?gid=0&single=true&output=csv"
URL_WATCHLIST_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRbLGRdor-TtNOtkqL0cbrTnUN0mg6-FLM-3yAxuZsznZRUJjeqoyWC7ZubG6kp1SEgYvcryTnb1eyE/pub?gid=440350475&single=true&output=csv"

TELEGRAM_TOKEN = "8813853886:AAEh6iYqi7YnnXk_HzeTTuHMDOX6Q153Ero"
TELEGRAM_CHAT_ID = "928199102"

ARCHIVO_HISTORIAL = "alertas_enviadas.json"
DIAS_ENFRIAMIENTO = 7


# ==============================================================================
# ETAPA 1: LECTURA DE GOOGLE SHEETS
# ==============================================================================
def aplicar_filtro_categoria(df: pd.DataFrame) -> pd.DataFrame:
    """Filtra filas inactivas o ignoradas si existe una columna de estado/categoría/filtro."""
    if df.empty:
        return df

    cols_posibles = [col for col in df.columns if str(col).strip().upper() in [
        "FILTRO", "CATEGORIA", "ESTADO", "ANALIZAR", "ACTIVO", "INCLUIR", "STATUS"
    ]]

    if not cols_posibles:
        return df

    col_filtro = cols_posibles[0]
    val_filtro = df[col_filtro].astype(str).str.strip().str.upper()

    valores_excluir = ["NO", "IGNORAR", "INACTIVO", "FALSE", "0", "OFF", "DESACTIVADO", "N"]
    df_filtrado = df[~val_filtro.isin(valores_excluir)].copy()

    return df_filtrado


def cargar_cartera_online(url_csv: str) -> pd.DataFrame:
    if not url_csv or "PEGA_AQUI" in url_csv:
        return pd.DataFrame()

    try:
        df = pd.read_csv(url_csv)
        if "Ticker" not in df.columns:
            return pd.DataFrame()

        df = aplicar_filtro_categoria(df)

        df_sub = df[["Ticker"]].copy()
        df_sub = df_sub[df_sub["Ticker"].astype(str).str.strip() != ""].copy()
        df_sub["Origen"] = "CARTERA"
        return df_sub
    except Exception as e:
        print(f"Error cargando Cartera: {e}")
        return pd.DataFrame()


def cargar_watchlist_online(url_csv: str) -> pd.DataFrame:
    if not url_csv or "PEGA_AQUI" in url_csv:
        return pd.DataFrame()

    try:
        df = pd.read_csv(url_csv)
        if "Ticker" not in df.columns:
            return pd.DataFrame()

        df = aplicar_filtro_categoria(df)

        df_sub = df[["Ticker"]].copy()
        df_sub = df_sub[df_sub["Ticker"].astype(str).str.strip() != ""].copy()
        df_sub["Origen"] = "WATCHLIST"
        return df_sub
    except Exception as e:
        print(f"Error cargando Watchlist: {e}")
        return pd.DataFrame()


# ==============================================================================
# ETAPA 2: CÁLCULOS DE MÉTRICAS, DESVÍOS Y SCORE
# ==============================================================================
def calcular_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def obtener_metricas_completas(ticker_sym: str) -> dict:
    ticker_yf = ticker_sym.replace(".", "-")

    try:
        t = yf.Ticker(ticker_yf)
        hist = t.history(period="3y")

        if len(hist) < 200:
            return {}

        # 1. Indicadores Técnicos y Desvíos EMA200
        hist["EMA200"] = hist["Close"].ewm(span=200, adjust=False).mean()
        precio_actual = hist["Close"].iloc[-1]
        ema200_actual = hist["EMA200"].iloc[-1]

        dist_ema200_pct = ((precio_actual - ema200_actual) / ema200_actual) * 100
        rsi = calcular_rsi(hist["Close"], 14)

        hist["Dist_EMA200_Hist"] = ((hist["Close"] - hist["EMA200"]) / hist["EMA200"]) * 100
        
        desvio_pos_prom = hist[hist["Dist_EMA200_Hist"] > 0]["Dist_EMA200_Hist"].mean()
        desvio_neg_prom = hist[hist["Dist_EMA200_Hist"] < 0]["Dist_EMA200_Hist"].mean()

        low_52 = hist["Low"].tail(252).min()
        high_52 = hist["High"].tail(252).max()
        pos_52_pct = ((precio_actual - low_52) / (high_52 - low_52)) * 100 if high_52 > low_52 else 50.0

        # 2. Datos Fundamentales
        info = t.info
        market_cap = info.get("marketCap", 0)
        free_cashflow = info.get("freeCashflow", None)
        fcf_yield = (free_cashflow / market_cap * 100) if (free_cashflow and market_cap) else None

        peg_ratio = info.get("pegRatio", None)
        roe = info.get("returnOnEquity", None)
        roe_pct = roe * 100 if roe else None

        debt_to_equity = info.get("debtToEquity", None)
        current_ratio = info.get("currentRatio", None)

        revenue_growth = info.get("revenueGrowth", None)
        rev_growth_pct = revenue_growth * 100 if revenue_growth else None

        earnings_growth = info.get("earningsGrowth", None)
        earn_growth_pct = earnings_growth * 100 if earnings_growth else None

        profit_margins = info.get("profitMargins", None)
        target_price = info.get("targetMeanPrice", None)

        upside_target_pct = None
        if target_price and precio_actual > 0:
            upside_target_pct = ((target_price - precio_actual) / precio_actual) * 100

        # CÁLCULO DEL SCORE SYSTEM
        score_fund = 0
        score_tec = 0
        score_riesgo = 0

        # Valuación
        if fcf_yield is not None:
            if fcf_yield > 5.0: score_fund += 2
            elif 3.0 <= fcf_yield <= 5.0: score_fund += 1

        # Calidad / Solvencia
        if roe_pct is not None and debt_to_equity is not None:
            if roe_pct > 15.0 and debt_to_equity < 100.0: score_fund += 2
            elif debt_to_equity > 200.0: score_fund -= 2

        if current_ratio and current_ratio > 1.5: score_fund += 1

        # Crecimiento
        if rev_growth_pct is not None and earn_growth_pct is not None:
            if rev_growth_pct > 10.0 and earn_growth_pct > 10.0: score_fund += 2
            elif rev_growth_pct > 10.0 or earn_growth_pct > 10.0: score_fund += 1
            elif rev_growth_pct < 0.0 and earn_growth_pct < 0.0: score_fund -= 2

        # Precio / AT
        if pd.notna(desvio_neg_prom) and dist_ema200_pct <= desvio_neg_prom and rsi < 45:
            score_tec += 2
        
        if rsi > 75:
            score_tec -= 1

        # Riesgo
        if rev_growth_pct is not None and rev_growth_pct < 0.0 and (profit_margins and profit_margins < 0.05):
            score_riesgo -= 3

        score_total = score_fund + score_tec + score_riesgo

        if score_total >= 6: tier = "🟢 PRIORITARIO"
        elif 2 <= score_total <= 5: tier = "🟡 VIGILAR"
        elif -1 <= score_total <= 1: tier = "⚪ NEUTRAL"
        else: tier = "🔴 TRAMPA"

        return {
            "Ticker": ticker_sym,
            "Precio_Live": round(precio_actual, 2),
            "Score_Total": score_total,
            "Score_Fund": score_fund,
            "Score_Tec": score_tec,
            "Tier": tier,
            "FCF_Yield_%": round(fcf_yield, 2) if fcf_yield is not None else None,
            "Dist_EMA200_%": round(dist_ema200_pct, 2),
            "Prom_Desvio_Sup_%": round(desvio_pos_prom, 2) if pd.notna(desvio_pos_prom) else None,
            "Prom_Desvio_Inf_%": round(desvio_neg_prom, 2) if pd.notna(desvio_neg_prom) else None,
            "RSI": round(rsi, 2) if pd.notna(rsi) else None,
            "Pos_52W_%": round(pos_52_pct, 1),
            "PEG": round(peg_ratio, 2) if peg_ratio else None,
            "ROE_%": round(roe_pct, 2) if roe_pct else None,
            "D/E": round(debt_to_equity, 1) if debt_to_equity else None,
            "Current_Ratio": round(current_ratio, 2) if current_ratio else None,
            "Ventas_Growth_%": round(rev_growth_pct, 2) if rev_growth_pct else None,
            "Upside_Target_%": round(upside_target_pct, 2) if upside_target_pct else None,
        }
    except Exception as e:
        print(f"Error procesando {ticker_sym}: {e}")
        return {}


# ==============================================================================
# ETAPA 3: ALERTAS Y TELEGRAM
# ==============================================================================
def enviar_telegram(mensaje: str):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Error enviando mensaje: {e}")


def enviar_documento_telegram(ruta_archivo: str, caption: str = ""):
    if not TELEGRAM_TOKEN or not os.path.exists(ruta_archivo): return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        with open(ruta_archivo, "rb") as doc:
            files = {"document": doc}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
            requests.post(url, data=data, files=files, timeout=10)
            print("  📄 Reporte HTML enviado a Telegram.")
    except Exception as e:
        print(f"Error enviando documento: {e}")


def cargar_historial_alertas() -> dict:
    if os.path.exists(ARCHIVO_HISTORIAL):
        try:
            with open(ARCHIVO_HISTORIAL, "r") as f: return json.load(f)
        except Exception: return {}
    return {}


def guardar_historial_alertas(historial: dict):
    try:
        with open(ARCHIVO_HISTORIAL, "w") as f: json.dump(historial, f, indent=4)
    except Exception as e:
        print(f"Error guardando historial: {e}")


def evaluar_condiciones_alerta(row: pd.Series, historial: dict) -> str:
    ticker = row["Ticker"]
    origen = row.get("Origen", "CARTERA")
    precio = row["Precio_Live"]
    score_total = row.get("Score_Total", 0)
    tier = row.get("Tier", "⚪ NEUTRAL")

    prefijo = "👀 WATCHLIST" if origen == "WATCHLIST" else "💼 CARTERA"
    fecha_actual = datetime.date.today()

    def procesar_alerta(tipo_alerta: str, mensaje: str):
        clave_unica = f"{ticker}_{tipo_alerta}"
        debe_enviar = True
        if clave_unica in historial:
            try:
                fecha_ultima_envio = datetime.datetime.strptime(historial[clave_unica], "%Y-%m-%d").date()
                if (fecha_actual - fecha_ultima_envio).days < DIAS_ENFRIAMIENTO:
                    debe_enviar = False
            except Exception: debe_enviar = True

        if debe_enviar:
            enviar_telegram(mensaje)
            historial[clave_unica] = fecha_actual.strftime("%Y-%m-%d")

    if score_total >= 6:
        msg = f"🌟 *{prefijo}: OPORTUNIDAD TOP ({ticker})*\n• Score: {score_total}\n• Estado: {tier}\n• Precio: ${precio}"
        procesar_alerta("OPORTUNIDAD TOP 🌟", msg)
    elif score_total <= -2:
        msg = f"⚠️ *{prefijo}: RIESGO / TRAMPA ({ticker})*\n• Score: {score_total}\n• Estado: {tier}\n• Precio: ${precio}"
        procesar_alerta("ALERTA TRAMPA ⚠️", msg)

    return tier


# ==============================================================================
# ETAPA 4: HTML CON DOS TABLAS (CARTERA REDUCIDA Y WATCHLIST COMPLETA)
# ==============================================================================
def obtener_estilo_columna(col: str, val):
    if pd.isna(val) or val is None or val == "N/A":
        return "", "N/A"

    try:
        val_num = float(val)
        val_fmt = f"{val_num:.2f}"
    except ValueError:
        return "", str(val)

    # Reglas de color por indicador
    if col == "Score_Total":
        if val_num >= 6: return "class='bg-verde'", val_fmt
        if val_num <= -2: return "class='bg-rojo'", val_fmt

    elif col == "FCF_Yield_%":
        if val_num > 5.0: return "class='txt-verde'", val_fmt
        if val_num < 0: return "class='txt-rojo'", val_fmt

    elif col == "RSI":
        if val_num < 35: return "class='bg-verde'", val_fmt
        if val_num > 70: return "class='bg-rojo'", val_fmt

    elif col == "Dist_EMA200_%":
        if val_num < -10: return "class='txt-verde'", val_fmt
        if val_num > 30: return "class='txt-rojo'", val_fmt

    elif col == "ROE_%":
        if val_num > 15: return "class='txt-verde'", val_fmt

    elif col == "D/E":
        if val_num > 200: return "class='txt-rojo'", val_fmt
        if val_num < 100: return "class='txt-verde'", val_fmt

    elif col == "Current_Ratio":
        if val_num > 1.5: return "class='txt-verde'", val_fmt

    return "", val_fmt


def renderizar_tabla_html(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p style='color: #94a3b8; text-align: center;'>No hay datos para mostrar.</p>"

    cols = df.columns.tolist()
    filas_html = []

    for _, row in df.iterrows():
        celdas = []
        for col in cols:
            val = row[col]

            if col == "Tier":
                t_str = str(val)
                cls = "badge-compra" if "PRIORITARIO" in t_str else ("badge-cartera" if "VIGILAR" in t_str else ("badge-venta" if "TRAMPA" in t_str else "badge-neutral"))
                celdas.append(f"<td><span class='badge {cls}'>{t_str}</span></td>")

            elif col == "Origen":
                o_str = str(val)
                cls = "badge-cartera" if o_str == "CARTERA" else "badge-watchlist"
                celdas.append(f"<td><span class='badge {cls}'>{o_str}</span></td>")

            else:
                css_class, val_fmt = obtener_estilo_columna(col, val)
                celdas.append(f"<td {css_class}>{val_fmt}</td>")

        filas_html.append(f"<tr>{''.join(celdas)}</tr>")

    headers_html = "".join([f"<th>{col}</th>" for col in cols])
    body_html = "".join(filas_html)

    return f"""
    <div class="table-container">
        <table>
            <thead><tr>{headers_html}</tr></thead>
            <tbody>{body_html}</tbody>
        </table>
    </div>
    """


def generar_reporte_html_dos_tablas(df_total: pd.DataFrame) -> str:
    # 1. TABLA CARTERA REDUCIDA
    cols_cartera_deseadas = [
        "Score_Total", "Score_Fund", "Score_Tec", "Ticker", "Precio_Live", 
        "Dist_EMA200_%", "Prom_Desvio_Sup_%", "Prom_Desvio_Inf_%", "RSI", 
        "ROE_%", "Current_Ratio", "Ventas_Growth_%", "Upside_Target_%"
    ]
    
    df_cartera = df_total[df_total["Origen"] == "CARTERA"].copy()
    cols_cartera_existentes = [c for c in cols_cartera_deseadas if c in df_cartera.columns]
    df_cartera_filtrada = df_cartera[cols_cartera_existentes]

    # 2. TABLA WATCHLIST COMPLETA (Toda la matriz)
    cols_watchlist_orden = [
        "Score_Total", "Score_Fund", "Score_Tec", "Tier", "Origen",
        "Ticker", "Precio_Live", "FCF_Yield_%", 
        "Dist_EMA200_%", "Prom_Desvio_Sup_%", "Prom_Desvio_Inf_%", "RSI",
        "Pos_52W_%", "PEG", "ROE_%", "D/E", "Current_Ratio", "Ventas_Growth_%", "Upside_Target_%"
    ]
    cols_watchlist_existentes = [c for c in cols_watchlist_orden if c in df_total.columns]
    df_watchlist_completa = df_total[cols_watchlist_existentes]

    # Generar fragmentos HTML de cada tabla
    html_tabla_cartera = renderizar_tabla_html(df_cartera_filtrada)
    html_tabla_watchlist = renderizar_tabla_html(df_watchlist_completa)

    # Documento HTML Final Concatenado
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Monitor Value Score System</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background-color: #0f172a; color: #e2e8f0; padding: 20px; margin: 0; }}
            h2 {{ color: #38bdf8; text-align: left; margin-top: 30px; margin-bottom: 10px; border-bottom: 2px solid #334155; padding-bottom: 8px; }}
            p.subtitle {{ color: #94a3b8; font-size: 13px; margin-bottom: 15px; }}
            .table-container {{ overflow-x: auto; background: #1e293b; border-radius: 8px; border: 1px solid #334155; margin-bottom: 30px; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 12px; text-align: center; }}
            th {{ background-color: #0f172a; color: #94a3b8; padding: 10px; border-bottom: 2px solid #334155; }}
            td {{ padding: 8px 6px; border-bottom: 1px solid #334155; white-space: nowrap; }}
            tr:hover {{ background-color: #334155; }}
            .bg-verde {{ background-color: rgba(34, 197, 94, 0.25) !important; color: #4ade80 !important; font-weight: bold; }}
            .bg-rojo {{ background-color: rgba(239, 68, 68, 0.25) !important; color: #f87171 !important; font-weight: bold; }}
            .txt-verde {{ color: #4ade80 !important; font-weight: bold; }}
            .txt-rojo {{ color: #f87171 !important; font-weight: bold; }}
            .badge {{ padding: 3px 8px; border-radius: 12px; font-size: 10px; font-weight: bold; }}
            .badge-cartera {{ background: #0284c7; color: white; }}
            .badge-watchlist {{ background: #6366f1; color: white; }}
            .badge-compra {{ background: #16a34a; color: white; }}
            .badge-venta {{ background: #dc2626; color: white; }}
            .badge-neutral {{ background: #475569; color: #cbd5e1; }}
        </style>
    </head>
    <body>
        <h1 style="color: #f8fafc; text-align: center; margin-bottom: 5px;">📈 Monitor Value Score System</h1>
        <p style="text-align: center; color: #64748b; font-size: 12px; margin-bottom: 25px;">Generado automáticamente para Telegram</p>

        <h2>💼 MI CARTERA (RESUMEN EJECUTIVO)</h2>
        <p class="subtitle">Métricas esenciales de tus posiciones actuales</p>
        {html_tabla_cartera}

        <h2>👀 WATCHLIST COMPLETA & MERCADO</h2>
        <p class="subtitle">Análisis integral ordenado por Score Total</p>
        {html_tabla_watchlist}
    </body>
    </html>
    """

    ruta_html = "reporte_cartera.html"
    with open(ruta_html, "w", encoding="utf-8") as f:
        f.write(html_content)
    return ruta_html


# ==============================================================================
# ETAPA 5: EJECUCIÓN PRINCIPAL
# ==============================================================================
def ejecutar_screener_cartera():
    print("🔄 Conectando con Google Sheets...")
    df_cartera = cargar_cartera_online(URL_CARTERA_CSV)
    df_watchlist = cargar_watchlist_online(URL_WATCHLIST_CSV)

    df_total = pd.concat([df_cartera, df_watchlist], ignore_index=True)

    if df_total.empty:
        print("⚠️ No se encontraron activos válidos.")
        return

    # Eliminar duplicados si un ticker está en ambas listas (prioriza Cartera)
    df_total = df_total.drop_duplicates(subset=["Ticker"], keep="first")

    historial_alertas = cargar_historial_alertas()

    resultados = []
    for _, fila in df_total.iterrows():
        ticker = str(fila["Ticker"]).strip()
        origen = fila.get("Origen", "CARTERA")
        print(f"📊 Evaluando Score: {ticker}...")

        metricas = obtener_metricas_completas(ticker)
        if not metricas:
            continue

        metricas["Origen"] = origen
        metricas["Estado_Alerta"] = evaluar_condiciones_alerta(metricas, historial_alertas)
        resultados.append(metricas)

    guardar_historial_alertas(historial_alertas)

    df_final = pd.DataFrame(resultados)
    df_final = df_final.sort_values(by="Score_Total", ascending=False).reset_index(drop=True)

    ruta_html = generar_reporte_html_dos_tablas(df_final)
    enviar_documento_telegram(ruta_html, caption="📊 Reporte consolidado (Cartera + Watchlist) adjunto.")


if __name__ == "__main__":
    ejecutar_screener_cartera()
