import os
import time
import math
import pandas as pd

from binance.client import Client
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from dotenv import load_dotenv

# =========================
# CONFIGURACIONES GLOBALES
# =========================
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET_KEY")

client = Client(API_KEY, API_SECRET, tld='com')

# Bollinger Bands, RSI, y volumen mínimo
bollinger_deviation = 2
rsi_overbought_threshold = 80
rsi_oversold_threshold = 20
ideal_volumen = 50_000_000

# Arriesgar 1% del balance de Futuros en cada trade
RISK_PERCENTAGE = 0.01  # 1% (ajusta a tu gusto)

# Distancia de cada recompras (2%)
DCA_DISTANCE = 0.02

# Ratios de las 4 entradas (suman 8)
DCA_RATIOS = [1, 1, 2, 4]  # 1 + 1 + 2 + 4 = 8

# Para evitar señales muy seguidas en el mismo símbolo
last_signal_time = {}
WAIT_TIME_SECONDS = 300  # 5 minutos

# Diccionario para trackear posiciones abiertas
open_positions = {}

# =========================
# FUNCIONES AUXILIARES
# =========================

def get_futures_usdt_balance():
    """
    Retorna el balance disponible en USDT en tu cuenta de Futuros.
    """
    try:
        futures_balance = client.futures_account_balance()
        for asset_info in futures_balance:
            if asset_info["asset"] == "USDT":
                return float(asset_info["balance"])
    except Exception as e:
        print(f"Error al obtener balance de Futuros: {e}")
    return 0.0

def get_step_size(symbol):
    """
    Retorna el stepSize del par en Futuros para ajustar la quantity.
    """
    try:
        exchange_info = client.futures_exchange_info()
        for s in exchange_info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        return float(f["stepSize"])
    except:
        pass
    return 0.0

def adjust_quantity_to_step_size(qty, step_size):
    """
    Redondea qty al múltiplo de step_size apropiado.
    """
    if step_size <= 0:
        return round(qty, 3)  # fallback
    decimals = int(round(-math.log(step_size, 10), 0))
    return round(qty, decimals)

def get_price_tick_size(symbol):
    """
    Retorna el tickSize (precio mínimo) del par en Futuros para ajustar el precio.
    """
    try:
        exchange_info = client.futures_exchange_info()
        for s in exchange_info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "PRICE_FILTER":
                        return float(f["tickSize"])
    except:
        pass
    return 0.0

def adjust_price_to_tick_size(price, tick_size):
    """
    Redondea price al múltiplo de tick_size.
    """
    if tick_size <= 0:
        return round(price, 6)  # fallback
    decimals = int(round(-math.log(tick_size, 10), 0))
    return round(price, decimals)

def get_current_funding_rate(symbol):
    """
    Obtiene el funding rate más reciente de un símbolo de Futuros.
    Retorna None si ocurre un error.
    """
    try:
        response = client.futures_funding_rate(symbol=symbol, limit=1)
        if response and len(response) > 0:
            return float(response[0]['fundingRate'])
    except Exception as e:
        print(f"Error al obtener funding rate de {symbol}: {e}")
    return None

def is_position_open_in_binance(symbol):
    """
    Retorna True si hay una posición abierta (positionAmt != 0) en Binance Futuros para 'symbol'.
    """
    try:
        positions = client.futures_position_information(symbol=symbol)
        for pos in positions:
            if float(pos['positionAmt']) != 0.0:
                return True
        return False
    except Exception as e:
        print(f"Error consultando posición en {symbol}: {e}")
        return False

def cancel_order(symbol, order_id):
    """ Cancela una orden dada su ID. """
    try:
        client.futures_cancel_order(symbol=symbol, orderId=order_id)
        print(f"[{symbol}] Orden {order_id} cancelada.")
    except Exception as e:
        print(f"No se pudo cancelar la orden {order_id} en {symbol}: {e}")

# =========================
# NUEVA FUNCIÓN MULTI-ENTRADA (DCA + COBERTURA)
# =========================

def place_futures_dca_order(symbol, side):
    """
    Crea:
      - Entrada inicial (Market)
      - 3 recompras (orden limit) cada 2% de distancia
      - 1 Stop de cobertura (STOP_MARKET) para que la pérdida sea RISK_PERCENTAGE.
    
    Usamos el ejemplo de 4 entradas: ratios [1,1,2,4] (sum=8).
    Si se llenan todas, y salta la cobertura, la pérdida total = 1% (o RISK_PERCENTAGE).
    """

    # 1) Calcular precios para las 4 "entradas" + precio de cobertura
    # ----------------------------------------------------------
    #   p0 = precio actual (se compra con MARKET)
    #   p1 = p0 * (1 - DCA_DISTANCE)   (si LONG)
    #   p2 = p1 * (1 - DCA_DISTANCE)
    #   p3 = p2 * (1 - DCA_DISTANCE)
    #   coverage = STOP_MARKET un poco más abajo de p3 que garantice la pérdida = RISK%
    #
    #   (Para SHORT es inverso: p1 = p0*(1+ DCA_DISTANCE), etc.)
    #
    #   Weighted cost = p0*(1) + p1*(1) + p2*(2) + p3*(4) = sumCost
    #   Weighted qty  = 1 + 1 + 2 + 4 = 8
    #
    #   averagePrice = sumCost / 8
    #   coverage se calcula para que la diferencia (averagePrice - coverage)* (8 * baseQty) = R (usd)
    #   => coverage = averagePrice - R/(8 * baseQty), en LONG.
    #
    #   Pero usaremos la idea de "coverage = p3 * (1 - algo)" o un cómputo directo,
    #   y resolvemos baseQty = R / [ (avgPrice - coverage)* 8 ].
    #
    #   Para LONG, coverage < p3 < p2 < p1 < p0.
    #   Para SHORT, coverage > p3 > p2 > p1 > p0.
    #
    # ----------------------------------------------------------

    info = client.futures_ticker(symbol=symbol)
    if not info:
        print(f"No se pudo obtener 'ticker' para {symbol}.")
        return

    current_price = float(info['lastPrice'])
    tick_size = get_price_tick_size(symbol)
    # Para no liarnos, asumimos que si side=LONG, bajamos 2% cada vez. Si side=SHORT, subimos 2%.

    if side == "LONG":
        p0 = current_price  # Market
        p1 = p0 * (1 - DCA_DISTANCE)
        p2 = p1 * (1 - DCA_DISTANCE)
        p3 = p2 * (1 - DCA_DISTANCE)
    else:  # side == "SHORT"
        p0 = current_price
        p1 = p0 * (1 + DCA_DISTANCE)
        p2 = p1 * (1 + DCA_DISTANCE)
        p3 = p2 * (1 + DCA_DISTANCE)

    p0 = adjust_price_to_tick_size(p0, tick_size)
    p1 = adjust_price_to_tick_size(p1, tick_size)
    p2 = adjust_price_to_tick_size(p2, tick_size)
    p3 = adjust_price_to_tick_size(p3, tick_size)

    # Cálculo de Weighted Cost + Weighted Avg
    # (Ratios: 1,1,2,4 => sum=8)
    sumCost = (p0 * 1) + (p1 * 1) + (p2 * 2) + (p3 * 4)
    sumRatios = sum(DCA_RATIOS)  # 8
    avgPrice = sumCost / sumRatios

    # Definimos coverage para LONG o SHORT
    # Por ejemplo, en LONG: coverage < p3, si p3 = 0.94, coverage un % más abajo.
    # Podríamos fijar coverage = p3 * (1 - 0.02) => 2% adicional. O resolver exacto.
    # Si quieres EXACTAMENTE 1% de riesgo cuando se llenan todas, la fórmula es:
    #
    # coverage = avgPrice - riskUsd/( sumRatios * baseQty )  (LONG)
    # coverage = avgPrice + riskUsd/( sumRatios * baseQty )  (SHORT)
    #
    # pero baseQty lo tenemos que calcular. Hacemos un mini-sistema de ecuaciones.

    usdt_balance = get_futures_usdt_balance()
    if usdt_balance <= 0:
        print("No hay balance USDT en Futuros, no se puede abrir posición.")
        return

    riskUsd = usdt_balance * RISK_PERCENTAGE  # p.e. 14 usd si 1400 de balance

    # Para simplificar, definimos coverage a un 2% más allá de p3 (o p3*(1-0.02) si LONG).
    # Luego calculamos "baseQty" para que la pérdida final sea riskUsd.
    # (Si coverage termina quedando arriba del p3, ajusta la distancia.)
    if side == "LONG":
        coverage_price = p3 * (1 - DCA_DISTANCE)  # 2% debajo de p3
        coverage_price = adjust_price_to_tick_size(coverage_price, tick_size)
        diff = avgPrice - coverage_price  # > 0 en LONG
        if diff <= 0:
            print("No se puede calcular coverage correctamente (avgPrice <= coverage).")
            return
        # baseQty = riskUsd / ( diff * sumRatios )
        #  sumRatios = 8 => total qty = 8*baseQty
        baseQty = riskUsd / (diff * sumRatios)

    else:  # SHORT
        coverage_price = p3 * (1 + DCA_DISTANCE)  # 2% por encima de p3
        coverage_price = adjust_price_to_tick_size(coverage_price, tick_size)
        diff = coverage_price - avgPrice  # > 0 en SHORT
        if diff <= 0:
            print("No se puede calcular coverage correctamente (coverage <= avgPrice).")
            return
        baseQty = riskUsd / (diff * sumRatios)

    if baseQty <= 0:
        print("baseQty calculada <=0, no abrimos operación.")
        return

    # Ahora tenemos baseQty. Cada tramo es baseQty, baseQty, 2*baseQty, 4*baseQty.
    # Ajustamos a stepSize.
    step_size = get_step_size(symbol)

    # Cantidades finales
    q0 = adjust_quantity_to_step_size(baseQty * 1, step_size)
    q1 = adjust_quantity_to_step_size(baseQty * 1, step_size)
    q2 = adjust_quantity_to_step_size(baseQty * 2, step_size)
    q3 = adjust_quantity_to_step_size(baseQty * 4, step_size)
    total_qty = q0 + q1 + q2 + q3  # si se llenan todas

    # ================
    # 1) Orden MARKET (entrada inicial)
    # ================
    try:
        if side == "LONG":
            order = client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                positionSide="LONG",
                quantity=q0
            )
            print(f"[{symbol}] Market BUY inicial: qty={q0} (precio aprox {p0})")
        else:
            order = client.futures_create_order(
                symbol=symbol,
                side="SELL",
                type="MARKET",
                positionSide="SHORT",
                quantity=q0
            )
            print(f"[{symbol}] Market SELL inicial: qty={q0} (precio aprox {p0})")
    except Exception as e:
        print(f"Error creando orden MARKET en {symbol}: {e}")
        return

    # ================
    # 2) Órdenes LIMIT (las recompras)
    # ================
    limit_order_ids = []
    try:
        if side == "LONG":
            # Recompra #1
            if q1 > 0:
                o1 = client.futures_create_order(
                    symbol=symbol,
                    side="BUY",
                    positionSide="LONG",
                    type="LIMIT",
                    price=str(p1),
                    quantity=q1,
                    timeInForce="GTC"
                )
                limit_order_ids.append(o1["orderId"])
                print(f"[{symbol}] Limit BUY #1 en {p1}, qty={q1}")

            # Recompra #2
            if q2 > 0:
                o2 = client.futures_create_order(
                    symbol=symbol,
                    side="BUY",
                    positionSide="LONG",
                    type="LIMIT",
                    price=str(p2),
                    quantity=q2,
                    timeInForce="GTC"
                )
                limit_order_ids.append(o2["orderId"])
                print(f"[{symbol}] Limit BUY #2 en {p2}, qty={q2}")

            # Recompra #3
            if q3 > 0:
                o3 = client.futures_create_order(
                    symbol=symbol,
                    side="BUY",
                    positionSide="LONG",
                    type="LIMIT",
                    price=str(p3),
                    quantity=q3,
                    timeInForce="GTC"
                )
                limit_order_ids.append(o3["orderId"])
                print(f"[{symbol}] Limit BUY #3 en {p3}, qty={q3}")

        else:  # SHORT
            # Reentrada #1
            if q1 > 0:
                o1 = client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    positionSide="SHORT",
                    type="LIMIT",
                    price=str(p1),
                    quantity=q1,
                    timeInForce="GTC"
                )
                limit_order_ids.append(o1["orderId"])
                print(f"[{symbol}] Limit SELL #1 en {p1}, qty={q1}")

            # Reentrada #2
            if q2 > 0:
                o2 = client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    positionSide="SHORT",
                    type="LIMIT",
                    price=str(p2),
                    quantity=q2,
                    timeInForce="GTC"
                )
                limit_order_ids.append(o2["orderId"])
                print(f"[{symbol}] Limit SELL #2 en {p2}, qty={q2}")

            # Reentrada #3
            if q3 > 0:
                o3 = client.futures_create_order(
                    symbol=symbol,
                    side="SELL",
                    positionSide="SHORT",
                    type="LIMIT",
                    price=str(p3),
                    quantity=q3,
                    timeInForce="GTC"
                )
                limit_order_ids.append(o3["orderId"])
                print(f"[{symbol}] Limit SELL #3 en {p3}, qty={q3}")

    except Exception as e:
        print(f"Error creando órdenes LIMIT en {symbol}: {e}")

    # ================
    # 3) Stop de cobertura (STOP_MARKET)
    # ================
    coverage_order_id = None
    try:
        coverage_str = str(coverage_price)
        if side == "LONG":
            # Stop Market SELL
            coverage_order = client.futures_create_order(
                symbol=symbol,
                side="SELL",
                positionSide="SHORT",
                type="STOP_MARKET",
                stopPrice=coverage_str,
                quantity=total_qty
            )
            coverage_order_id = coverage_order["orderId"]
            print(f"[{symbol}] Cobertura STOP_MARKET en {coverage_price}, qty={total_qty} (LONG)")
        else:
            # Stop Market BUY
            coverage_order = client.futures_create_order(
                symbol=symbol,
                side="BUY",
                positionSide="LONG",
                type="STOP_MARKET",
                stopPrice=coverage_str,
                quantity=total_qty
            )
            coverage_order_id = coverage_order["orderId"]
            print(f"[{symbol}] Cobertura STOP_MARKET en {coverage_price}, qty={total_qty} (SHORT)")

    except Exception as e:
        print(f"Error colocando la cobertura STOP_MARKET en {symbol}: {e}")

    # Retornamos info para trackear
    return {
        "side": side,
        "init_qty": q0,
        "limit_order_ids": limit_order_ids,
        "coverage_order_id": coverage_order_id,
        "total_qty": total_qty,
        "prices": [p0, p1, p2, p3, coverage_price]
    }

# =========================
# CHECKEAR POSICIONES (Coverage)
# =========================

def check_open_positions():
    """
    Verifica si la cobertura se ha llenado.
    Si se llenó, cancela las órdenes limit pendientes (si las hubiera) y elimina la posición del dict.
    """
    symbols_in_positions = list(open_positions.keys())

    for symbol in symbols_in_positions:
        data = open_positions[symbol]
        coverage_order_id = data.get("coverage_order_id")
        limit_ids = data.get("limit_order_ids", [])

        coverage_filled = False

        # Revisar orden de cobertura
        if coverage_order_id:
            try:
                cov_info = client.futures_get_order(symbol=symbol, orderId=coverage_order_id)
                if cov_info["status"] == "FILLED":
                    coverage_filled = True
            except Exception as e:
                print(f"Error consultando cobertura {coverage_order_id} de {symbol}: {e}")

        if coverage_filled:
            print(f"[{symbol}] Cobertura ejecutada. Cerramos posición y cancelamos límites.")
            # Cancelar las órdenes limit que queden abiertas
            for lid in limit_ids:
                cancel_order(symbol, lid)
            open_positions.pop(symbol, None)
            continue

# =========================
# FUNCIÓN DE SEÑALES (5m)
# =========================

def get_klines_5m(symbol, limit=200):
    try:
        klines = client.futures_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_5MINUTE,
            limit=limit
        )
        return klines
    except Exception as e:
        print(f"Error al obtener klines 5m de {symbol}: {e}")
        return None

def apply_bollinger_rsi_5m(symbol, klines):
    """
    Aplica la lógica de Bollinger y RSI (umbral 80/20),
    abre posición LONG o SHORT si se cumplen las condiciones (con la estrategia DCA).
    """
    if not klines:
        return False

    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df['close'] = df['close'].astype(float)
    df['high']  = df['high'].astype(float)
    df['low']   = df['low'].astype(float)

    # RSI
    rsi = RSIIndicator(df['close']).rsi().iloc[-1]
    if rsi == 100:
        return
    
    # Bollinger
    bb = BollingerBands(df['close'], window=20, window_dev=bollinger_deviation)
    upper_band = bb.bollinger_hband()
    lower_band = bb.bollinger_lband()

    close_price = df['close'].iloc[-1]
    last_upper  = upper_band.iloc[-1]
    last_lower  = lower_band.iloc[-1]

    # Condición LONG: Precio rompe banda inferior + RSI < 20
    if close_price <= last_lower and rsi <= rsi_oversold_threshold:
        info = client.futures_ticker(symbol=symbol)
        volume = float(info['quoteVolume']) if info else 0.0
        if volume >= ideal_volumen:
            print(f"[{symbol}] Señal LONG. close={close_price}, RSI={rsi}, Vol={volume}")

            # Check funding rate (opcional)
            funding_rate = get_current_funding_rate(symbol)
            if funding_rate is not None and funding_rate < 0:
                print(f"[{symbol}] Funding Rate negativo ({funding_rate}), no abrimos operación.")
                return False

            if is_position_open_in_binance(symbol):
                print(f"Ya existe posición abierta en {symbol}, no abrimos otra.")
                return False
            if symbol in open_positions:
                print(f"{symbol} ya en open_positions, no abrimos otra.")
                return False

            # Creamos la posición multi-entrada (DCA)
            result = place_futures_dca_order(symbol, side="LONG")
            if result:
                open_positions[symbol] = result
            return True

    # Condición SHORT: Precio rompe banda superior + RSI > 80
    elif close_price >= last_upper and rsi >= rsi_overbought_threshold:
        info = client.futures_ticker(symbol=symbol)
        volume = float(info['quoteVolume']) if info else 0.0
        if volume >= ideal_volumen:
            print(f"[{symbol}] Señal SHORT. close={close_price}, RSI={rsi}, Vol={volume}")

            # Check funding rate (opcional)
            funding_rate = get_current_funding_rate(symbol)
            if funding_rate is not None and funding_rate < 0:
                print(f"[{symbol}] Funding Rate negativo ({funding_rate}), no abrimos operación.")
                return False

            if is_position_open_in_binance(symbol):
                print(f"Ya existe posición abierta en {symbol}, no abrimos otra.")
                return False
            if symbol in open_positions:
                print(f"{symbol} ya en open_positions, no abrimos otra.")
                return False

            # Creamos la posición multi-entrada (DCA)
            result = place_futures_dca_order(symbol, side="SHORT")
            if result:
                open_positions[symbol] = result
            return True

    return False

# =========================
# BUCLE PRINCIPAL
# =========================

def search_ticks():
    """
    Retorna la lista de símbolos USDT de Futuros (ej: ETHUSDT, BNBUSDT, etc.).
    """
    try:
        list_ticks = client.futures_symbol_ticker()
        ticks = []
        for tick in list_ticks:
            if tick['symbol'].endswith('USDT'):
                # Excluye pares si lo deseas
                if tick['symbol'] not in ("USDCUSDT",):
                    ticks.append(tick['symbol'])
        print(f"Símbolos en USDT encontrados: {len(ticks)}")
        return ticks
    except Exception as e:
        print(f"Error al obtener la lista de símbolos: {e}")
        return []

def main_loop():
    # 1) Obtener lista de símbolos una sola vez
    tickers = search_ticks()

    while True:
        print("Iniciando ciclo de análisis en 5m...\n")
        for symbol in tickers:
            # Verificamos cooldown de 5min para no spamear órdenes
            if symbol not in last_signal_time or (time.time() - last_signal_time[symbol]) > WAIT_TIME_SECONDS:
                klines_5m = get_klines_5m(symbol)
                if klines_5m:
                    signal_found = apply_bollinger_rsi_5m(symbol, klines_5m)
                    if signal_found:
                        last_signal_time[symbol] = time.time()

        # Verificar cobertura de posiciones abiertas
        check_open_positions()

        print("Ciclo completado, espero 60 seg...\n")
        time.sleep(60)

if __name__ == "__main__":
    main_loop()
