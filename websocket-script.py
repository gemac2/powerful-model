import asyncio
import websockets
import json
import pandas as pd
import numpy as np
import pandas_ta as ta
from collections import deque

# Configuración de WebSocket y streams
API_URL = "wss://stream.binance.com:9443/ws/"
streams = [
    'filusdt@kline_5m', 'aceusdt@kline_5m', 'thetausdt@kline_5m', 'magicusdt@kline_5m',
    'bondusdt@kline_5m', 'enausdt@kline_5m', 'synusdt@kline_5m', 'snxusdt@kline_5m', 
    # Agrega más streams aquí
]

# Configuración de la longitud de las colas de precios
PRICE_QUEUE_LIMIT = 100
price_queues = {stream: deque(maxlen=PRICE_QUEUE_LIMIT) for stream in streams}

# Función para calcular las bandas de Bollinger y RSI con pandas_ta
def calculate_indicators(prices):
    close_prices = pd.Series(prices)
    if len(close_prices) < 30:
        return None, None  # Espera a tener suficientes datos para el cálculo

    # Bandas de Bollinger
    bollinger = ta.bbands(close_prices, length=20, std=2)
    upper_band = bollinger['BBL_20_2.0'][-1]
    lower_band = bollinger['BBU_20_2.0'][-1]

    # RSI
    rsi = ta.rsi(close_prices, length=14)[-1]

    return upper_band, lower_band, rsi

# Función para manejar los mensajes WebSocket y procesar los datos
async def process_stream(stream):
    uri = API_URL + stream
    async with websockets.connect(uri) as websocket:
        while True:
            try:
                message = await websocket.recv()
                data = json.loads(message)

                # Obtención de precios de cierre
                close_price = float(data['k']['c'])
                price_queues[stream].append(close_price)

                # Calcula indicadores cuando la cola tiene suficientes datos
                if len(price_queues[stream]) == PRICE_QUEUE_LIMIT:
                    upper_band, lower_band, rsi = calculate_indicators(price_queues[stream])
                    
                    if upper_band is None or lower_band is None or rsi is None:
                        continue  # No hay suficientes datos para los indicadores

                    # Lógica de trading
                    if close_price > upper_band and rsi > 80:
                        print(f"Short Signal on {stream}: Price {close_price}, RSI {rsi}")
                    elif close_price < lower_band and rsi < 20:
                        print(f"Long Signal on {stream}: Price {close_price}, RSI {rsi}")

            except websockets.exceptions.ConnectionClosedError as e:
                print(f"Connection to {stream} closed with error: {e}. Reconnecting...")
                break
            except Exception as e:
                print(f"Error in processing stream {stream}: {e}")

# Función para gestionar múltiples streams
async def main():
    tasks = [process_stream(stream) for stream in streams]
    await asyncio.gather(*tasks)

# Ejecutar la aplicación
if __name__ == "__main__":
    asyncio.run(main())
