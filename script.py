from binance.client import Client
import time
import pygame
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import pandas as pd

rsi_overbought_threshold = 80  # Nivel de sobrecompra para RSI
rsi_oversold_threshold = 20  # Nivel de sobreventa para RSI
bollinger_deviation = 2  # Desviación estándar para las bandas de Bollinger
bollinger_deviation_three = 3  # Desviación estándar para las bandas de Bollinger

client = Client('', '', tld='com')  # Reemplaza con tus credenciales
pygame.init()
alert_sound = pygame.mixer.Sound('alerta.wav')


def buscarticks():
    ticks = []
    try:
        lista_ticks = client.futures_symbol_ticker()
    except Exception as e:
        print(f"Error al obtener la lista de ticks: {e}")
        return ticks

    print('Numero de monedas encontradas #' + str(len(lista_ticks)))

    for tick in lista_ticks:
        if tick['symbol'][-4:] != 'USDT':
            continue
        ticks.append(tick['symbol'])

    print('Numero de monedas encontradas en el par USDT: #' + str(len(ticks)))

    return ticks


def get_klines(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_5MINUTE, limit=30)
    except Exception as e:
        print(f"Error al obtener los datos de klines para {tick}: {e}")
        return None

    return klines


def infoticks(tick):
    try:
        info = client.futures_ticker(symbol=tick)
    except Exception as e:
        print(f"Error al obtener la información de ticker para {tick}: {e}")
        return None

    return info


def human_format(volumen):
    magnitude = 0
    while abs(volumen) >= 1000:
        magnitude += 1
        volumen /= 1000.0
    return '%.2f%s' % (volumen, ['', 'K', 'M', 'G', 'T', 'P'][magnitude])


def porcentaje_klines(tick, klines, knumber):
    # Calcula el RSI
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df['close'] = df['close'].astype(float)

    rsi = RSIIndicator(df['close']).rsi().iloc[-1]
    if rsi == 100:
        return

    # Bollinger Bands
    bb = BollingerBands(df['close'], window=20, window_dev=bollinger_deviation)
    upper_band = bb.bollinger_hband()
    lower_band = bb.bollinger_lband()

    # Verifica si el precio de cierre está fuera de las bandas de Bollinger
    close_price = df['close'].iloc[-1]

    # LONG si el precio está por debajo de la banda inferior y RSI es bajista
    if close_price <= lower_band.iloc[-1] and rsi <= rsi_oversold_threshold:
        info = infoticks(tick)
        volumen = float(info['quoteVolume'])
        alert_sound.play()
        print('LONG (Bollinger Bands & RSI): ' + tick)
        print('RSI: ' + str(rsi))
        print('Volumen: ' + human_format(volumen))
        print('Precio max: ' + info['highPrice'])
        print('Precio min: ' + info['lowPrice'])
        print('')

    # SHORT si el precio está por encima de la banda superior y RSI es alcista
    elif close_price >= upper_band.iloc[-1] and rsi >= rsi_overbought_threshold:
        info = infoticks(tick)
        volumen = float(info['quoteVolume'])
        alert_sound.play()
        print('SHORT (Bollinger Bands & RSI): ' + tick)
        print('RSI: ' + str(rsi))
        print('Volumen: ' + human_format(volumen))
        print('Precio max: ' + info['highPrice'])
        print('Precio min: ' + info['lowPrice'])
        print('')
    
    bb2 = BollingerBands(df['close'], window=20, window_dev=bollinger_deviation_three)
    upper_band2 = bb2.bollinger_hband()
    lower_band2 = bb2.bollinger_lband()

    # LONG si el precio está por debajo de la banda inferior
    if close_price <= lower_band2.iloc[-1]:
        info = infoticks(tick)
        volumen = float(info['quoteVolume'])
        alert_sound.play()
        print('LONG (Bollinger Bands): ' + tick)
        print('RSI: ' + str(rsi))
        print('Volumen: ' + human_format(volumen))
        print('Precio max: ' + info['highPrice'])
        print('Precio min: ' + info['lowPrice'])
        print('')

    # SHORT si el precio está por encima de la banda superior
    elif close_price >= upper_band2.iloc[-1]:
        info = infoticks(tick)
        volumen = float(info['quoteVolume'])
        alert_sound.play()
        print('SHORT (Bollinger Bands): ' + tick)
        print('RSI: ' + str(rsi))
        print('Volumen: ' + human_format(volumen))
        print('Precio max: ' + info['highPrice'])
        print('Precio min: ' + info['lowPrice'])
        print('')


while True:
    ticks = buscarticks()
    print('Escaneando monedas...')
    print('')
    for tick in ticks:
        klines = get_klines(tick)
        knumber = len(klines)
        if knumber > 0:
            knumber = knumber - 1
            porcentaje_klines(tick, klines, knumber)
    print('Esperando 30 segundos...')
    print('')
    time.sleep(30)
