from binance.client import Client
import time
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import pandas as pd
import requests

rsi_overbought_threshold = 80  #Overbought RSI Point
rsi_oversold_threshold = 20  #OverSold RSI Point
bollinger_deviation = 2  #Standard Deviation for Bollinger Bands
telegram_bot_token = '7080889022:AAGA4nFFsPvrpMJ0aOU722bceJsuEevtSv8'
telegram_chat_id = '1026595920'
ideal_volumen = 50000000
variation = 5  # Variation in the last 30 minutes on percentage
variation_100 = 7  # Variation in the last 30 minutes on percentage while the currency has lower volumen 100k 
fast_variation = 2  # Variation in the last 2 minutes on percentage
last_signal_time = {}
timeframe_wait_times = {
    "5 Minutes": 300,
    "1 Hour": 3600,
    "4 Hours": 14400
}

client = Client('', '', tld='com')

def search_ticks():
    ticks = []
    try:
        list_ticks = client.futures_symbol_ticker()
    except Exception as e:
        print(f"Error while we get the ticks: {e}")
        return ticks

    for tick in list_ticks:
        if tick['symbol'][-4:] != 'USDT':
            continue
        if tick['symbol'] == "USDCUSDT":
            continue
        ticks.append(tick['symbol'])

    print('Number of currency found in the USDT Pair: #' + str(len(ticks)))

    return ticks

def get_klines(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_5MINUTE, limit=48, timeout=30)
        timeframe = "5 Minutes"
    except Exception as e:
        print(f"Error while getting data for {tick} 5 minutes klines: {e}")
        return None, None

    return klines, timeframe

def get_klines_one_hour(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_1HOUR, limit=48, timeout=30)
        timeframe = "1 Hour"
    except Exception as e:
        print(f"Error while getting data for {tick} 1-hour klines: {e}")
        return None, None

    return klines, timeframe

def get_klines_four_hour(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_4HOUR, limit=48, timeout=30)
        timeframe = "4 Hours"
    except Exception as e:
        print(f"Error while getting data for {tick} 4-hour klines: {e}")
        return None, None

    return klines, timeframe

def get_movement_alert_klines(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_1MINUTE, limit=30)
        timeframe = "1 Minute"
    except Exception as e:
        return None, None
    return klines, timeframe

def get_info_ticks(tick):
    try:
        info = client.futures_ticker(symbol=tick)
    except Exception as e:
        print(f"Error while we get the info for a ticker {tick}: {e}")
        return None

    return info

def human_format(volumen):
    magnitude = 0
    while abs(volumen) >= 1000:
        magnitude += 1
        volumen /= 1000.0
    return '%.2f%s' % (volumen, ['', 'K', 'M', 'G', 'T', 'P'][magnitude])

def get_powerful_patron_signals(tick, klines, timeframe):
    # Calcular el valor RSI
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df['close'] = df['close'].astype(float)

    rsi = RSIIndicator(df['close']).rsi().iloc[-1]
    if rsi == 100:
        return

    wait_time = timeframe_wait_times.get(timeframe, None)
    if wait_time is None:
        print(f"Timeframe not found: {timeframe}")
        return False

    if tick not in last_signal_time or (time.time() - last_signal_time[tick]) > wait_time:
        # Bandas de Bollinger
        bb = BollingerBands(df['close'], window=20, window_dev=bollinger_deviation)
        upper_band = bb.bollinger_hband()
        lower_band = bb.bollinger_lband()

        close_price = df['close'].iloc[-1]

        # Señal de LONG
        if close_price <= lower_band.iloc[-1] and rsi <= rsi_oversold_threshold:
            info = get_info_ticks(tick)
            if info:
                volumen = float(info['quoteVolume'])
                if volumen >= ideal_volumen:
                    send_telegram_message("⚠️ Powerful Patron", timeframe, "Possible Long", tick, human_format(volumen), rsi, info['lastPrice'], info['highPrice'], info['lowPrice'], False)
                    last_signal_time[tick] = time.time()
                    return True

        # Señal de SHORT
        elif close_price >= upper_band.iloc[-1] and rsi >= rsi_overbought_threshold:
            info = get_info_ticks(tick)
            if info:
                volumen = float(info['quoteVolume'])
                if volumen >= ideal_volumen:
                    send_telegram_message("⚠️ Powerful Patron", timeframe, "Possible Short", tick, human_format(volumen), rsi, info['lastPrice'], info['highPrice'], info['lowPrice'], False)
                    last_signal_time[tick] = time.time()
                    return True

    return False

def get_movement_alerts(tick, klines, knumber, timeframe):
    inicial = float(klines[0][4])
    final = float(klines[knumber][4])

    # LONG
    if inicial > final:
        result = round(((inicial - final) / inicial) * 100, 2)
        if result >= variation:
            info = get_info_ticks(tick)
            if info:
                volumen = float(info['quoteVolume'])
                if volumen > 100000000 or result >= variation_100:
                    send_telegram_message("⚠️ Movement Alert", timeframe, "Possible Long", tick, human_format(volumen), str(result) + '%', info['lastPrice'], info['highPrice'], info['lowPrice'], True)
                    return True

    # SHORT
    if final > inicial:
        result = round(((final - inicial) / inicial) * 100, 2)
        if result >= variation:
            info = get_info_ticks(tick)
            if info:
                volumen = float(info['quoteVolume'])
                if volumen > 100000000 or result >= variation_100:
                    send_telegram_message("⚠️ Movement Alert", timeframe, "Possible Short", tick, human_format(volumen), str(result) + '%', info['lastPrice'], info['highPrice'], info['lowPrice'], True)
                    return True

    # FAST
    if knumber >= 3:
        inicial = float(klines[knumber-2][4])
        final = float(klines[knumber][4])
        if inicial < final:
            result = round(((final - inicial) / inicial) * 100, 2)
            if result >= fast_variation:
                info = get_info_ticks(tick)
                if info:
                    volumen = float(info['quoteVolume'])
                    send_telegram_message("⚡️⚡️Fast Short", timeframe, "Possible Short", tick, human_format(volumen), str(result) + '%', info['lastPrice'], info['highPrice'], info['lowPrice'], True)
                    return True
    return False

def send_telegram_message(title, timeframe, order_type, currency_name, volume, rsi_variation, last_price, high_price, low_price, has_variation):
    url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    message = f"**{title}**\n\n"
    message += f"⌛️ TimeFrame: {timeframe}\n\n"
    message += f"🛍️ Order: {order_type}\n\n"
    message += f"🪙 Pair: {currency_name}\n\n"
    message += f"📊 Vol: {volume}\n\n"
    if has_variation:
        message += f"🔄 Var: {rsi_variation}\n\n"
    else:
        message += f"💹 RSI: {rsi_variation}\n\n"
    message += f"💰 Price: {last_price}\n\n"
    message += f"📈 High Price: {high_price}\n\n"
    message += f"📉 Low Price: {low_price}"
    payload = {
        "chat_id": telegram_chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print("Error while we send message to Telegram:", e)

while True:
    ticks = search_ticks()
    print('Scanning Currencies...')
    print('')
    for tick in ticks:
        klines_5m, time5m = get_klines(tick)
        if klines_5m is not None and time5m is not None:
            found_signal_powerful = get_powerful_patron_signals(tick, klines_5m, time5m)
            if found_signal_powerful:
                print("Found powerful patron signal for", tick, "on 5 minutes timeframe")
                print('**************************************************')
                print('')
        
        klines_1h, time1h = get_klines_one_hour(tick)
        if klines_1h is not None and time1h is not None:
            found_signal_bollinger = get_powerful_patron_signals(tick, klines_1h, time1h)
            if found_signal_bollinger:
                print("Found powerful patron signal for", tick, "on 1-hour timeframe")
                print('**************************************************')
                print('')
            
        klines_4h, time4h = get_klines_four_hour(tick)
        if klines_4h is not None and time4h is not None:
            found_signal_bollinger = get_powerful_patron_signals(tick, klines_4h, time4h)
            if found_signal_bollinger:
                print("Found powerful patron signal for", tick, "on 4-hour timeframe")
                print('**************************************************')
                print('')

        movement_klines, timeframe = get_movement_alert_klines(tick)
        if movement_klines is not None and timeframe is not None:
            knumber = len(movement_klines) - 1
            found_signal_movement = get_movement_alerts(tick, movement_klines, knumber, timeframe)
            if found_signal_movement:
                print("Found movement signal for", tick)
                print('**************************************************')
                print('')
    print('Waiting 30 seconds...')
    print('')
    time.sleep(30)