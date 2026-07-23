# ==============================================================================
# SCREENER & MONITOR DE CARTERA VALUE CON WATCHLIST, HTML PRO Y ANTI-SPAM (7 DÍAS)
# ==============================================================================

import datetime
import json
import os
import re
import pandas as pd
import requests
import yfinance as yf

# ==============================================================================
# CONFIGURACIÓN GENERAL Y ENLACES DE GOOGLE SHEETS
# ==============================================================================
# Enlace publicado como CSV de tu pestaña CARTERA
URL_CARTERA_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRbLGRdor-TtNOtkqL0cbrTnUN0mg6-FLM-3yAxuZsznZRUJjeqoyWC7ZubG6kp1SEgYvcryTnb1eyE/pub?gid=0&single=true&output=csv"

# Enlace publicado como CSV de tu pestaña WATCHLIST (deja vacío "" si aún no la publicas)
URL_WATCHLIST_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRbLGRdor-TtNOtkqL0cbrTnUN0mg6-FLM-3yAxuZsznZRUJjeqoyWC7ZubG6kp1SEgYvcryTnb1eyE/pub?gid=440350475&single=true&output=csv"

# Credenciales de Telegram
TELEGRAM_TOKEN = "8813853886:AAEh6iYqi7YnnXk_HzeTTuHMDOX6Q153Ero"
TELEGRAM_CHAT_ID = "928199102"

# Configuración Anti-Spam
ARCHIVO_HISTORIAL = "alertas_enviadas.json"
DIAS_ENFRIAMIENTO = 7  # Días que el bot "guardará silencio" para la misma alerta


# ==============================================================================
# ETAPA 1: LECTURA DE CARTERA Y WATCHLIST DESDE GOOGLE SHEETS
# ==============================================================================
def cargar_cartera_online(url_csv: str) -> pd.DataFrame:
    """Lee la hoja de cartera desde la web."""
    if not url_csv or "PEGA_AQUI" in url_csv:
        return pd.DataFrame()

    try:
        df = pd.read_csv(url_csv)
        cols_interes = {
            "Ticker": "Ticker",
            "Precio USD": "Precio_USD_Excel",
            "Cantidad en el mercado": "Cantidad",
            "total remanente": "Inversion_Viva",
            "monto actual": "Monto_Actual",
            "Rend. Neto": "Rend_Neto_USD",
            "rendimiento": "Rendimiento_Pct",
            "Categoria": "Categoria",
        }

        cols_existentes = {k: v for k, v in cols_interes.items() if k in df.columns}
        df_sub = df[list(cols_existentes.keys())].rename(columns=cols_existentes).copy()

        def limpiar_numero(val):
            if pd.isna(val) or val == "":
                return 0.0
            val_str = str(val).replace("$", "").replace("%", "").replace(" ", "").strip()
            if "," in val_str and "." in val_str:
                val_str = val_str.replace(".", "").replace(",", ".")
            elif "," in val_str:
                val_str = val_str.replace(",", ".")
            try:
                return float(val_str)
            except ValueError:
                return 0.0

        for col in df_sub.columns:
            if col != "Ticker" and col != "Categoria":
                df_sub[col] = df_sub[col].apply(limpiar_numero)

        if "Categoria" in df_sub.columns:
            mask = df_sub["Categoria"].astype(str).str.contains(r"accion\s*us", flags=re.IGNORECASE, regex=True)
            df_sub = df_sub[mask].copy()

        df_sub = df_sub[df_sub["Ticker"].astype(str).str.strip() != ""].copy()
        df_sub["Origen"] = "CARTERA"
        return df_sub
    except Exception as e:
        print(f"Error cargando Cartera: {e}")
        return pd.DataFrame()


def cargar_watchlist_online(url_csv: str) -> pd.DataFrame:
    """Lee los Tickers de la pestaña Watchlist."""
    if not url_csv or "PEGA_AQUI" in url_csv:
        return pd.DataFrame()

    try:
        df = pd.read_csv(url_csv)
        if "Ticker" not in df.columns:
            return pd.DataFrame()

        df_sub = df[["Ticker"]].copy()
        df_sub = df_sub[df_sub["Ticker"].astype(str).str.strip() != ""].copy()
        df_sub["Origen"] = "WATCHLIST"
        return df_sub
    except Exception as e:
        print(f"Error cargando Watchlist: {e}")
        return pd.DataFrame()


# ==============================================================================
# ETAPA 2: CÁLCULOS TÉCNICOS Y FUNDAMENTALES (yfinance)
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

        hist["EMA200"] = hist["Close"].ewm(span=200, adjust=False).mean()
        precio_actual = hist["Close"].iloc[-1]
        ema200_actual = hist["EMA200"].iloc[-1]

        dist_ema200_pct = ((precio_actual - ema200_actual) / ema200_actual) * 100
        rsi = calcular_rsi(hist["Close"], 14)

        hist["Dist_EMA200_Hist"] = ((hist["Close"] - hist["EMA200"]) / hist["EMA200"]) * 100
        desvio_superior_prom = hist[hist["Dist_EMA200_Hist"] > 0]["Dist_EMA200_Hist"].mean()
        desvio_inferior_prom = hist[hist["Dist_EMA200_Hist"] < 0]["Dist_EMA200_Hist"].mean()

        info = t.info
        pe_current = info.get("trailingPE", None)
        peg_ratio = info.get("pegRatio", None)
        target_price = info.get("targetMeanPrice", None)
        roe = info.get("returnOnEquity", None)
        revenue_growth = info.get("revenueGrowth", None)
        profit_margins = info.get("profitMargins", None)

        upside_target_pct = None
        if target_price and precio_actual > 0:
            upside_target_pct = ((target_price - precio_actual) / precio_actual) * 100

        return {
            "Precio_Live": round(precio_actual, 2),
            "EMA200": round(ema200_actual, 2),
            "Dist_EMA200_%": round(dist_ema200_pct, 2),
            "Desvio_Inf_Prom_%": round(desvio_inferior_prom, 2) if pd.notna(desvio_inferior_prom) else 0.0,
            "Desvio_Sup_Prom_%": round(desvio_superior_prom, 2) if pd.notna(desvio_superior_prom) else 0.0,
            "RSI": round(rsi, 2) if pd.notna(rsi) else None,
            "PER": round(pe_current, 2) if pe_current else "N/A",
            "PEG": round(peg_ratio, 2) if peg_ratio else "N/A",
            "ROE_%": round(roe * 100, 2) if roe else "N/A",
            "Ventas_Growth_%": round(revenue_growth * 100, 2) if revenue_growth else "N/A",
            "Margen_Neto_%": round(profit_margins * 100, 2) if profit_margins else "N/A",
            "Target_Analistas": round(target_price, 2) if target_price else "N/A",
            "Upside_Target_%": round(upside_target_pct, 2) if upside_target_pct else "N/A",
        }
    except Exception as e:
        print(f"Error procesando {ticker_sym}: {e}")
        return {}


# ==============================================================================
# ETAPA 3: REGLAS DE ALERTAS VALUE Y CONTROL ANTI-SPAM (HISTORIAL JSON)
# ==============================================================================
def enviar_telegram(mensaje: str):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Error enviando mensaje: {e}")


def enviar_documento_telegram(ruta_archivo: str, caption: str = ""):
    if not TELEGRAM_TOKEN or not os.path.exists(ruta_archivo):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        with open(ruta_archivo, "rb") as doc:
            files = {"document": doc}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
            requests.post(url, data=data, files=files, timeout=10)
            print("  📄 Reporte HTML adjuntado enviado con éxito a Telegram.")
    except Exception as e:
        print(f"Error enviando documento a Telegram: {e}")


def cargar_historial_alertas() -> dict:
    if os.path.exists(ARCHIVO_HISTORIAL):
        try:
            with open(ARCHIVO_HISTORIAL, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def guardar_historial_alertas(historial: dict):
    try:
        with open(ARCHIVO_HISTORIAL, "w") as f:
            json.dump(historial, f, indent=4)
    except Exception as e:
        print(f"Error guardando historial: {e}")


def evaluar_condiciones_alerta(row: pd.Series, historial: dict) -> str:
    sugerencias = []
    ticker = row["Ticker"]
    origen = row.get("Origen", "CARTERA")
    precio = row["Precio_Live"]
    dist_ema200 = row.get("Dist_EMA200_%", 0)
    desvio_inf = row.get("Desvio_Inf_Prom_%", 0)
    peg = row.get("PEG", "N/A")
    roe = row.get("ROE_%", "N/A")
    upside = row.get("Upside_Target_%", "N/A")
    rsi = row.get("RSI", 50)
    ventas_growth = row.get("Ventas_Growth_%", "N/A")

    prefijo = "👀 WATCHLIST" if origen == "WATCHLIST" else "💼 CARTERA"
    fecha_actual = datetime.date.today()

    def procesar_alerta(tipo_alerta: str, mensaje: str):
        clave_unica = f"{ticker}_{tipo_alerta}"
        sugerencias.append(tipo_alerta)

        debe_enviar = True
        if clave_unica in historial:
            try:
                fecha_ultima_envio = datetime.datetime.strptime(historial[clave_unica], "%Y-%m-%d").date()
                dias_transcurridos = (fecha_actual - fecha_ultima_envio).days

                if dias_transcurridos < DIAS_ENFRIAMIENTO:
                    debe_enviar = False
                    print(f"  🔕 Alerta omitida (Silenciado {dias_transcurridos}/{DIAS_ENFRIAMIENTO} días) para {ticker}: {tipo_alerta}")
            except Exception:
                debe_enviar = True

        if debe_enviar:
            enviar_telegram(mensaje)
            historial[clave_unica] = fecha_actual.strftime("%Y-%m-%d")
            print(f"  🔔 Alerta NUEVA enviada para {ticker}: {tipo_alerta}")

    # REGLA 1: Descuento Histórico respecto a EMA 200
    if isinstance(dist_ema200, (int, float)) and isinstance(desvio_inf, (int, float)):
        if dist_ema200 <= desvio_inf:
            msg = f"🟢 *{prefijo}: PISO HISTÓRICO ({ticker})*\n• Precio: ${precio}\n• Dist. EMA200: {dist_ema200}%\n• Piso Promedio: {desvio_inf}%"
            procesar_alerta("PISO HISTÓRICO 🟢", msg)

    # REGLA 2: Oportunidad Value (PEG < 1.0 y ROE > 15%)
    if isinstance(peg, (int, float)) and isinstance(roe, (int, float)):
        if peg < 1.0 and roe > 15.0:
            msg = f"⭐ *{prefijo}: OPORTUNIDAD VALUE ({ticker})*\n• PEG: {peg} (Atractivo)\n• ROE: {roe}% (Negocio Excelente)"
            procesar_alerta("VALUE / ALTA CALIDAD ⭐", msg)

    # REGLA 3: Descuento Extremo de Analistas + Sobrevendido
    if isinstance(upside, (int, float)) and isinstance(rsi, (int, float)):
        if upside >= 30.0 and rsi <= 40.0:
            msg = f"🎯 *{prefijo}: OPORTUNIDAD OVERSOLD ({ticker})*\n• Upside Estimado: +{upside}%\n• RSI: {rsi}"
            procesar_alerta("DESCUENTO ANALISTAS 🎯", msg)

    # REGLA 4: Alerta de Riesgo (Trampa de Valor)
    if isinstance(dist_ema200, (int, float)) and isinstance(ventas_growth, (int, float)):
        if dist_ema200 < -15.0 and ventas_growth < 0.0:
            msg = f"⚠️ *{prefijo}: ALERTA DE RIESGO ({ticker})*\n• La acción cae ({dist_ema200}%) y sus Ventas se contraen ({ventas_growth}%)"
            procesar_alerta("TRAMPA DE VALOR ⚠️", msg)

    return " | ".join(sugerencias) if sugerencias else "MANTENER"


# ==============================================================================
# ETAPA 4: GENERACIÓN DE REPORTE HTML CON ESTILOS DARK MODE & COLORES
# ==============================================================================
def aplicar_estilos_celda(row):
    estilos = {}

    dist = row.get("Dist_EMA200_%", 0)
    piso = row.get("Desvio_Inf_Prom_%", 0)
    if isinstance(dist, (int, float)) and isinstance(piso, (int, float)):
        if dist <= piso:
            estilos["Dist_EMA200_%"] = "class='bg-verde'"
        elif dist > 15:
            estilos["Dist_EMA200_%"] = "class='bg-rojo'"

    rsi = row.get("RSI", None)
    if isinstance(rsi, (int, float)):
        if rsi <= 35:
            estilos["RSI"] = "class='bg-verde'"
        elif rsi >= 70:
            estilos["RSI"] = "class='bg-rojo'"

    peg = row.get("PEG", None)
    if isinstance(peg, (int, float)):
        if peg < 1.0:
            estilos["PEG"] = "class='bg-verde'"
        elif peg > 2.5:
            estilos["PEG"] = "class='bg-rojo'"

    roe = row.get("ROE_%", None)
    if isinstance(roe, (int, float)) and roe >= 15.0:
        estilos["ROE_%"] = "class='bg-verde'"

    vg = row.get("Ventas_Growth_%", None)
    if isinstance(vg, (int, float)):
        if vg > 10.0:
            estilos["Ventas_Growth_%"] = "class='bg-verde'"
        elif vg < 0.0:
            estilos["Ventas_Growth_%"] = "class='bg-rojo'"

    upside = row.get("Upside_Target_%", None)
    if isinstance(upside, (int, float)) and upside >= 25.0:
        estilos["Upside_Target_%"] = "class='bg-verde'"

    origen = str(row.get("Origen", ""))
    estilos["Origen"] = "class='badge badge-cartera'" if origen == "CARTERA" else "class='badge badge-watchlist'"

    sug = str(row.get("Sugerencia", ""))
    if "PISO" in sug or "VALUE" in sug or "OVERSOLD" in sug:
        estilos["Sugerencia"] = "class='badge badge-compra'"
    elif "TRAMPA" in sug or "VENTA" in sug:
        estilos["Sugerencia"] = "class='badge badge-venta'"
    else:
        estilos["Sugerencia"] = "class='badge badge-neutral'"

    return estilos


def generar_reporte_html(df: pd.DataFrame) -> str:
    filas_html = []
    cols = df.columns.tolist()

    for _, row in df.iterrows():
        estilos = aplicar_estilos_celda(row)
        celdas = []
        for col in cols:
            val = row[col]
            val_fmt = f"{val:.2f}" if isinstance(val, float) else str(val)
            css_class = estilos.get(col, "")
            celdas.append(f"<td {css_class}>{val_fmt}</td>")

        filas_html.append(f"<tr>{''.join(celdas)}</tr>")

    headers_html = "".join([f"<th>{col}</th>" for col in cols])
    body_html = "".join(filas_html)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #e2e8f0; padding: 20px; margin: 0; }}
            .header-container {{ text-align: center; margin-bottom: 25px; }}
            h2 {{ color: #38bdf8; font-size: 24px; margin-bottom: 5px; }}
            p.subtitle {{ color: #94a3b8; font-size: 13px; }}
            .table-container {{ overflow-x: auto; background: #1e293b; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: center; }}
            th {{ background-color: #0f172a; color: #94a3b8; padding: 14px 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #334155; }}
            td {{ padding: 12px 10px; border-bottom: 1px solid #334155; white-space: nowrap; }}
            tr:hover {{ background-color: #334155; }}
            .bg-verde {{ background-color: rgba(34, 197, 94, 0.2) !important; color: #4ade80 !important; font-weight: bold; }}
            .bg-rojo {{ background-color: rgba(239, 68, 68, 0.2) !important; color: #f87171 !important; font-weight: bold; }}
            .badge {{ padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: bold; display: inline-block; }}
            .badge-cartera {{ background: #0284c7; color: white; }}
            .badge-watchlist {{ background: #6366f1; color: white; }}
            .badge-compra {{ background: #16a34a; color: white; }}
            .badge-venta {{ background: #dc2626; color: white; }}
            .badge-neutral {{ background: #475569; color: #cbd5e1; }}
        </style>
    </head>
    <body>
        <div class="header-container">
            <h2>📈 Monitor de Cartera & Watchlist Value</h2>
            <p class="subtitle">Resumen ejecutivo generado automáticamente</p>
        </div>
        <div class="table-container">
            <table>
                <thead>
                    <tr>{headers_html}</tr>
                </thead>
                <tbody>
                    {body_html}
                </tbody>
            </table>
        </div>
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
        print("⚠️ No se encontraron activos para procesar.")
        return None

    cant_activos = len(df_total)
    print(f"✅ {cant_activos} activos listados para analizar. Procesando métricas...\n")

    historial_alertas = cargar_historial_alertas()

    resultados = []
    for _, fila in df_total.iterrows():
        ticker = str(fila["Ticker"]).strip()
        print(f"📊 Analizando: {ticker}...")

        metricas = obtener_metricas_completas(ticker)
        if not metricas:
            continue

        registro = {**fila.to_dict(), **metricas}
        registro["Sugerencia"] = evaluar_condiciones_alerta(registro, historial_alertas)
        resultados.append(registro)

    guardar_historial_alertas(historial_alertas)

    df_final = pd.DataFrame(resultados)

    cols_orden = [
        "Origen",
        "Ticker",
        "Precio_Live",
        "Dist_EMA200_%",
        "Desvio_Inf_Prom_%",
        "RSI",
        "PER",
        "PEG",
        "ROE_%",
        "Ventas_Growth_%",
        "Margen_Neto_%",
        "Upside_Target_%",
        "Sugerencia",
    ]

    df_reporte = df_final[cols_orden].copy()

    # Consola
    print("\n" + "=" * 110)
    print("📈 TABLA CONSOLIDADA SCREENER")
    print("=" * 110)
    print(df_reporte.to_string(index=False))

    # Adjuntar HTML a Telegram
    ruta_html = generar_reporte_html(df_reporte)
    enviar_documento_telegram(ruta_html, caption="📊 Reporte consolidado adjunto del Screener.")

    return df_final


# --- EJECUCIÓN DIRECTA ---
if __name__ == "__main__":
    ejecutar_screener_cartera()
