import os
import re
import pandas as pd
import requests
import yfinance as yf

# ==============================================================================
# CONFIGURACIÓN GENERAL
# ==============================================================================
TELEGRAM_TOKEN = "8813853886:AAEh6iYqi7YnnXk_HzeTTuHMDOX6Q153Ero"
TELEGRAM_CHAT_ID = "928199102"

# Enlace de tu Google Sheet publicado como CSV
URL_GOOGLE_SHEETS_CSV = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRbLGRdor-TtNOtkqL0cbrTnUN0mg6-FLM-3yAxuZsznZRUJjeqoyWC7ZubG6kp1SEgYvcryTnb1eyE/pub?gid=0&single=true&output=csv"

UMBRAL_DESVIO_EMA200_PCT = 3.0
UMBRAL_PEG_ATRACTIVO = 1.0


# ==============================================================================
# ETAPA 1: LECTURA EN VIVO DESDE GOOGLE SHEETS
# ==============================================================================
def cargar_cartera_online(url_csv: str) -> pd.DataFrame:
    """Lee la hoja de cálculo directamente desde la web sin descargar archivos."""
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

    df_sub = df[list(cols_interes.keys())].rename(columns=cols_interes).copy()

    def limpiar_numero(val):
        if pd.isna(val) or val == "":
            return 0.0
        val_str = (
            str(val).replace("$", "").replace("%", "").replace(" ", "").strip()
        )
        if "," in val_str and "." in val_str:
            val_str = val_str.replace(".", "").replace(",", ".")
        elif "," in val_str:
            val_str = val_str.replace(",", ".")
        try:
            return float(val_str)
        except ValueError:
            return 0.0

    cols_numericas = [
        "Precio_USD_Excel",
        "Cantidad",
        "Inversion_Viva",
        "Monto_Actual",
        "Rend_Neto_USD",
        "Rendimiento_Pct",
    ]
    for col in cols_numericas:
        df_sub[col] = df_sub[col].apply(limpiar_numero)

    mask = df_sub["Categoria"].astype(str).str.contains(
        r"accion\s*us", flags=re.IGNORECASE, regex=True
    )
    df_filtrado = df_sub[mask].copy()

    df_filtrado = df_filtrado[
        df_filtrado["Ticker"].astype(str).str.strip() != ""
    ].copy()

    return df_filtrado


# ==============================================================================
# ETAPA 2: INDICADORES CON FIX DE TICKERS (BRK.B -> BRK-B)
# ==============================================================================
def calcular_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def obtener_metricas_completas(ticker_sym: str) -> dict:
    # NORMALIZACIÓN: Reemplaza puntos por guiones para Yahoo Finance (ej: BRK.B -> BRK-B)
    ticker_yf = ticker_sym.replace(".", "-")

    try:
        t = yf.Ticker(ticker_yf)
        hist = t.history(period="3y")

        if len(hist) < 200:
            return {}

        hist["EMA200"] = hist["Close"].ewm(span=200, adjust=False).mean()
        hist["EMA21"] = hist["Close"].ewm(span=21, adjust=False).mean()

        precio_actual = hist["Close"].iloc[-1]
        ema200_actual = hist["EMA200"].iloc[-1]
        ema21_actual = hist["EMA21"].iloc[-1]

        dist_ema200_pct = ((precio_actual - ema200_actual) / ema200_actual) * 100
        dist_ema21_pct = ((precio_actual - ema21_actual) / ema21_actual) * 100
        rsi = calcular_rsi(hist["Close"], 14)

        hist["Dist_EMA200_Hist"] = (
            (hist["Close"] - hist["EMA200"]) / hist["EMA200"]
        ) * 100
        desvio_superior_prom = hist[hist["Dist_EMA200_Hist"] > 0][
            "Dist_EMA200_Hist"
        ].mean()
        desvio_inferior_prom = hist[hist["Dist_EMA200_Hist"] < 0][
            "Dist_EMA200_Hist"
        ].mean()

        info = t.info
        pe_current = info.get("trailingPE", None)
        peg_ratio = info.get("pegRatio", None)
        target_price = info.get("targetMeanPrice", None)
        roe = info.get("returnOnEquity", None)
        revenue_growth = info.get("revenueGrowth", None)
        profit_margins = info.get("profitMargins", None)

        upside_target_pct = None
        if target_price and precio_actual > 0:
            upside_target_pct = (
                (target_price - precio_actual) / precio_actual
            ) * 100

        return {
            "Precio_Live": round(precio_actual, 2),
            "EMA200": round(ema200_actual, 2),
            "Dist_EMA200_%": round(dist_ema200_pct, 2),
            "Desvio_Inf_Prom_%": round(desvio_inferior_prom, 2)
            if pd.notna(desvio_inferior_prom)
            else 0.0,
            "Desvio_Sup_Prom_%": round(desvio_superior_prom, 2)
            if pd.notna(desvio_superior_prom)
            else 0.0,
            "Dist_EMA21_%": round(dist_ema21_pct, 2),
            "RSI": round(rsi, 2) if pd.notna(rsi) else None,
            "PER": round(pe_current, 2) if pe_current else "N/A",
            "PEG": round(peg_ratio, 2) if peg_ratio else "N/A",
            "ROE_%": round(roe * 100, 2) if roe else "N/A",
            "Ventas_Growth_%": round(revenue_growth * 100, 2)
            if revenue_growth
            else "N/A",
            "Margen_Neto_%": round(profit_margins * 100, 2)
            if profit_margins
            else "N/A",
            "Target_Analistas": round(target_price, 2)
            if target_price
            else "N/A",
            "Upside_Target_%": round(upside_target_pct, 2)
            if upside_target_pct
            else "N/A",
        }
    except Exception as e:
        print(f"Error procesando {ticker_sym}: {e}")
        return {}


# ==============================================================================
# ETAPA 3 & 4: ALERTAS Y EJECUCIÓN
# ==============================================================================
def enviar_telegram(mensaje: str):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Error Telegram: {e}")


def evaluar_condiciones_alerta(row: pd.Series) -> str:
    sugerencia = "MANTENER"
    ticker = row["Ticker"]
    precio = row["Precio_Live"]
    dist_ema200 = row["Dist_EMA200_%"]
    desvio_inf = row["Desvio_Inf_Prom_%"]
    desvio_sup = row["Desvio_Sup_Prom_%"]
    peg = row["PEG"]

    if (
        isinstance(dist_ema200, (int, float))
        and isinstance(desvio_inf, (int, float))
        and abs(dist_ema200 - desvio_inf) <= UMBRAL_DESVIO_EMA200_PCT
    ):
        sugerencia = "ZONA DE AGREGADO 🟢"
        msg = f"🟢 *OPORTUNIDAD DE COMPRA ({ticker})*\n• Precio Live: ${precio}\n• Distancia EMA200: {dist_ema200}%\n• Desvío Histórico Inferior: {desvio_inf}%"
        if isinstance(peg, (int, float)) and peg < UMBRAL_PEG_ATRACTIVO:
            msg += f"\n• PEG Ratio Atractivo: {peg}"
        enviar_telegram(msg)

    elif (
        isinstance(dist_ema200, (int, float))
        and isinstance(desvio_sup, (int, float))
        and dist_ema200 >= (desvio_sup - 2.0)
    ):
        sugerencia = "EVALUAR VENTA 🔴"
        msg = f"🔴 *ALERTA SOBRECOMPRA ({ticker})*\n• Precio Live: ${precio}\n• Distancia EMA200: +{dist_ema200}%\n• Límite Histórico Promedio: +{desvio_sup}%"
        enviar_telegram(msg)

    return sugerencia


def ejecutar_screener_cartera(url_csv: str):
    print("🔄 1. Conectando con Google Sheets...")
    df_cartera = cargar_cartera_online(url_csv)

    cant_activos = len(df_cartera)
    print(f"✅ {cant_activos} activos encontrados. Procesando...\n")

    if cant_activos == 0:
        return None

    resultados = []
    for _, fila in df_cartera.iterrows():
        ticker = str(fila["Ticker"]).strip()
        print(f"📊 Analizando: {ticker}...")

        metricas = obtener_metricas_completas(ticker)
        if not metricas:
            continue

        registro = {**fila.to_dict(), **metricas}
        registro["Sugerencia"] = evaluar_condiciones_alerta(registro)
        resultados.append(registro)

    df_final = pd.DataFrame(resultados)

    cols_orden = [
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

    print("\n" + "=" * 110)
    print("📈 TABLA CONSOLIDADA DE DECISIÓN Y SCREENER (AUTOMÁTICA)")
    print("=" * 110)
    print(df_reporte.to_string(index=False))

    return df_final


# --- EJECUCIÓN DIRECTA ---
df_resultado = ejecutar_screener_cartera(URL_GOOGLE_SHEETS_CSV)
