import concurrent.futures
import time
from binance.client import Client 
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import pandas as pd
import requests
from cachetools import TTLCache

# Constants
RSI_OVERBOUGHT = 80
RSI_OVERSOLD = 20
BOLLINGER_DEVIATION = 2
IDEAL_VOLUME = 50_000_000
VARIATION_THRESHOLD = 5
VARIATION_100_THRESHOLD = 7
FAST_VARIATION_THRESHOLD = 2

# Telegram settings
TELEGRAM_BOT_TOKEN = '7080889022:AAGA4nFFsPvrpMJ0aOU722bceJsuEevtSv8'
TELEGRAM_CHAT_ID = '1026595920'

# Last signal time tracking
last_signal_time = {}

# Timeframe wait times
TIMEFRAME_WAIT_TIMES = {
    "5m": 300,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400
}

# Cache para almacenar resultados de llamadas API
cache = TTLCache(maxsize=500, ttl=300)  # Máximo 500 entradas con un TTL de 5 minutos

# Binance API client
client = Client('', '', tld='com')

# Límites de concurrencia
MAX_CONCURRENT_REQUESTS = 5

# Functions
def search_ticks():
    return [tick['symbol'] for tick in client.futures_symbol_ticker() if tick['symbol'][-4:] == 'USDT' and tick['symbol'] != 'USDCUSDT']

def get_klines_cached(tick, interval, limit=48):
    key = f"{tick}_{interval}"
    if key in cache:
        return cache[key]
    
    try:
        klines = client.futures_klines(symbol=tick, interval=interval, limit=limit)
        cache[key] = klines  # Guardar en caché
        return klines
    except Exception as e:
        print(f"Error getting klines for {tick}: {e}")
        return None

def get_info_ticks(tick):
    key = f"info_{tick}"
    if key in cache:
        return cache[key]
    
    try:
        info = client.futures_ticker(symbol=tick)
        cache[key] = info  # Guardar en caché
        return info
    except Exception as e:
        print(f"Error getting info for {tick}: {e}")
        return None

def human_format(volume):
    for unit in ['', 'K', 'M', 'G', 'T', 'P']:
        if abs(volume) < 1000:
            return f"{volume:.2f}{unit}"
        volume /= 1000

def process_signals_optimized(tick):
    signals = []

    for interval in [Client.KLINE_INTERVAL_5MINUTE, Client.KLINE_INTERVAL_1HOUR, Client.KLINE_INTERVAL_4HOUR]:
        klines = get_klines_cached(tick, interval)
        if klines is not None:
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            df['close'] = df['close'].astype(float)

            rsi = RSIIndicator(df['close']).rsi().iloc[-1]
            if rsi == 100:
                continue

            wait_time = TIMEFRAME_WAIT_TIMES.get(interval, None)
            if wait_time is None:
                print(f"Timeframe not found: {interval}")
                continue

            if tick not in last_signal_time or (time.time() - last_signal_time[tick]) > wait_time:
                bb = BollingerBands(df['close'], window=20, window_dev=BOLLINGER_DEVIATION)
                upper_band, lower_band = bb.bollinger_hband(), bb.bollinger_lband()
                close_price = df['close'].iloc[-1]

                # Long signal
                if close_price <= lower_band.iloc[-1] and rsi <= RSI_OVERSOLD:
                    info = get_info_ticks(tick)
                    if info and float(info['quoteVolume']) >= IDEAL_VOLUME:
                        signals.append(("⚠️ Powerful Patron", interval, "Possible Long", tick, info))
                        last_signal_time[tick] = time.time()

                # Short signal
                elif close_price >= upper_band.iloc[-1] and rsi >= RSI_OVERBOUGHT:
                    info = get_info_ticks(tick)
                    if info and float(info['quoteVolume']) >= IDEAL_VOLUME:
                        signals.append(("⚠️ Powerful Patron", interval, "Possible Short", tick, info))
                        last_signal_time[tick] = time.time()

    # Movement alerts (1-minute interval)
    movement_klines = get_klines_cached(tick, Client.KLINE_INTERVAL_1MINUTE, 30)
    if movement_klines is not None:
        initial, final = float(movement_klines[0][4]), float(movement_klines[-1][4])
        variation = round(((final - initial) / initial) * 100, 2)

        # Long signal
        if initial > final and variation >= VARIATION_THRESHOLD:
            info = get_info_ticks(tick)
            if info and (float(info['quoteVolume']) > IDEAL_VOLUME or variation >= VARIATION_100_THRESHOLD):
                signals.append(("⚠️ Movement Alert", "1 Minute", "Possible Long", tick, info))

        # Short signal
        if final > initial and variation >= VARIATION_THRESHOLD:
            info = get_info_ticks(tick)
            if info and (float(info['quoteVolume']) > IDEAL_VOLUME or variation >= VARIATION_100_THRESHOLD):
                signals.append(("⚠️ Movement Alert", "1 Minute", "Possible Short", tick, info))

        # Fast signal
        if len(movement_klines) >= 3:
            initial, final = float(movement_klines[-2][4]), float(movement_klines[-1][4])
            variation = round(((final - initial) / initial) * 100, 2)
            if initial < final and variation >= FAST_VARIATION_THRESHOLD:
                info = get_info_ticks(tick)
                if info:
                    signals.append(("⚡️⚡️ Fast Short", "1 Minute", "Possible Short", tick, info))

    return signals


def send_telegram_message(title, timeframe, order_type, currency_name, info):
    message = f"**{title}**\n\n⌛️ TimeFrame: {timeframe}\n\n🛍️ Order: {order_type}\n\n🪙 Pair: {currency_name}\n\n📊 Vol: {human_format(float(info['quoteVolume']))}\n\n💹 Price: {info['lastPrice']}\n\n📈 High Price: {info['highPrice']}\n\n📉 Low Price: {info['lowPrice']}"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    )

# Main loop
while True:
    ticks = search_ticks()
    print('Scanning currencies...')

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
        for signals in executor.map(process_signals_optimized, ticks):
            for signal in signals:
                send_telegram_message(*signal)

    print('Waiting 30 seconds...')
    time.sleep(30)
