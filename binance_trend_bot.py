#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, logging, requests
from datetime import datetime
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger('BinanceTrendBot')

BINANCE_ENDPOINTS = [
    'https://data-api.binance.vision',
    'https://api.binance.com',
    'https://api1.binance.com',
    'https://api2.binance.com',
    'https://api3.binance.com',
    'https://api4.binance.com',
]

def get_config():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        logger.error('HATA: TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID gerekli!')
        sys.exit(1)
    return {
        'telegram_bot_token': token,
        'telegram_chat_id': chat_id,
        'telegram_api_url': 'https://api.telegram.org',
        'price_change_threshold_pct': float(os.environ.get('PRICE_CHANGE_THRESHOLD', '2.0')),
        'min_quote_volume_usd': int(os.environ.get('MIN_QUOTE_VOLUME', '500000')),
        'rsi_period': int(os.environ.get('RSI_PERIOD', '14')),
        'rsi_overbought': float(os.environ.get('RSI_OVERBOUGHT', '75')),
        'rsi_oversold': float(os.environ.get('RSI_OVERSOLD', '25')),
        'kline_interval': os.environ.get('KLINE_INTERVAL', '15m'),
        'kline_limit': int(os.environ.get('KLINE_LIMIT', '100')),
        'notify_window_min_minutes': int(os.environ.get('NOTIFY_WINDOW_MIN', '30')),
        'notify_window_max_minutes': int(os.environ.get('NOTIFY_WINDOW_MAX', '40')),
        'max_notifications_per_cycle': int(os.environ.get('MAX_NOTIFICATIONS', '15')),
        'request_delay_seconds': float(os.environ.get('REQUEST_DELAY', '0.05')),
        'max_coins_to_analyze': int(os.environ.get('MAX_COINS', '100')),
    }

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return None
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
    gains = [max(0, d) for d in deltas]
    losses = [max(0, -d) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

def calculate_ema(prices, period):
    if len(prices) < period: return None
    m = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for i in range(period, len(prices)):
        ema = (prices[i] - ema) * m + ema
    return ema

def safe_pct(closes, lookback):
    if len(closes) < lookback + 1 or closes[-(lookback+1)] == 0: return None
    return ((closes[-1] - closes[-(lookback+1)]) / closes[-(lookback+1)]) * 100

def format_price(price):
    if price >= 1000: return f"${price:,.2f}"
    elif price >= 1: return f"${price:.4f}"
    elif price >= 0.01: return f"${price:.6f}"
    else: return f"${price:.8f}"

def format_change(pct):
    return f"+{pct:.2f}%" if pct > 0 else f"{pct:.2f}%"

def analyze_coin(symbol, klines, config):
    if not klines or len(klines) < 20: return None
    try:
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
    except (ValueError, IndexError): return None
    current_price = closes[-1]
    rsi = calculate_rsi(closes, config['rsi_period'])
    ema20 = calculate_ema(closes, 20)
    change_1h = safe_pct(closes, 4) or 0
    change_4h = safe_pct(closes, 16) or 0
    change_24h = safe_pct(closes, len(closes) - 1) or 0
    avg_vol = sum(volumes[-20:]) / max(len(volumes[-20:]), 1)
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
    threshold = config['price_change_threshold_pct']
    trend = None
    if (change_1h > threshold and rsi is not None and rsi < config['rsi_overbought']
            and ema20 is not None and current_price > ema20):
        trend = 'rising'
    elif (change_1h < -threshold and rsi is not None and rsi > config['rsi_oversold']
          and ema20 is not None and current_price < ema20):
        trend = 'falling'
    if not trend: return None
    half_threshold = threshold * 0.5
    candles_in_trend = 0
    for i in range(len(closes) - 1, 0, -1):
        prev_idx = i - 1
        if prev_idx < 0 or closes[prev_idx] == 0: break
        chg = ((closes[i] - closes[prev_idx]) / closes[prev_idx]) * 100
        if trend == 'rising' and chg >= half_threshold: candles_in_trend += 1
        elif trend == 'falling' and chg <= -half_threshold: candles_in_trend += 1
        else: break
        if candles_in_trend >= 20: break
    return {
        'symbol': symbol, 'price': current_price,
        'change_1h': change_1h, 'change_4h': change_4h, 'change_24h': change_24h,
        'rsi': rsi, 'volume_ratio': vol_ratio, 'ema20': ema20,
        'trend': trend, 'trend_duration_minutes': candles_in_trend * 15,
        'candles_in_trend': candles_in_trend, 'timestamp': int(time.time()),
    }

def api_request(path, params=None, timeout=15):
    for base in BINANCE_ENDPOINTS:
        url = f"{base}{path}"
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={'User-Agent': 'BinanceTrendBot-GH/1.0'})
            if resp.status_code == 429:
                logger.warning('Rate limit!'); time.sleep(10); continue
            if resp.status_code != 200:
                logger.warning(f'{base} HTTP {resp.status_code}')
                continue
            return resp.json()
        except Exception as e:
            logger.warning(f'{base} hatasi: {e}'); continue
    logger.error(f'Tum Binance endpointleri basarisiz: {path}')
    return None

def get_usdt_tickers(config, min_volume):
    data = api_request('/api/v3/ticker/24hr', timeout=30)
    if not data: return []
    return [t for t in data if t.get('symbol', '').endswith('USDT')
            and float(t.get('quoteVolume', 0)) >= min_volume]

def get_klines(symbol, config):
    params = {'symbol': symbol, 'interval': config['kline_interval'], 'limit': config['kline_limit']}
    return api_request('/api/v3/klines', params=params)

def send_telegram_message(config, message):
    url = f"{config['telegram_api_url']}/bot{config['telegram_bot_token']}/sendMessage"
    data = {'chat_id': config['telegram_chat_id'], 'text': message, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
    try:
        resp = requests.post(url, json=data, timeout=15)
        if resp.status_code != 200:
            logger.error(f'Telegram hatasi: {resp.text[:200]}'); return False
        return True
    except Exception as e:
        logger.error(f'Telegram gonderim hatasi: {e}'); return False

def build_notification_message(config, trends):
    rising = [t for t in trends if t['trend'] == 'rising']
    falling = [t for t in trends if t['trend'] == 'falling']
    rising.sort(key=lambda x: x['change_1h'], reverse=True)
    falling.sort(key=lambda x: x['change_1h'])
    now_str = datetime.utcnow().strftime('%d.%m.%Y %H:%M UTC')
    msg = "--------------------\n"
    msg += "BINANCE TREND RAPORU\n"
    msg += f"{now_str} | {len(rising) + len(falling)} yeni trend\n"
    msg += "--------------------\n\n"
    if rising:
        msg += "YUKSELIS TRENDI\n----------\n"
        for t in rising[:8]:
            arrow = "Y" if t['change_1h'] > 5 else "I"
            msg += f"\n{arrow} {t['symbol']} ({t['trend_duration_minutes']} dk)\n"
            msg += f"   Fiyat: {format_price(t['price'])}\n"
            msg += f"   1s: {format_change(t['change_1h'])} | 4s: {format_change(t['change_4h'])}\n"
            rsi_val = t['rsi'] if t['rsi'] is not None else 0
            msg += f"   RSI: {rsi_val:.1f} | Hacim: {t['volume_ratio']:.1f}x\n"
        msg += "\n"
    if falling:
        msg += "DUSUS TRENDI\n----------\n"
        for t in falling[:8]:
            arrow = "D" if t['change_1h'] < -5 else "L"
            msg += f"\n{arrow} {t['symbol']} ({t['trend_duration_minutes']} dk)\n"
            msg += f"   Fiyat: {format_price(t['price'])}\n"
            msg += f"   1s: {format_change(t['change_1h'])} | 4s: {format_change(t['change_4h'])}\n"
            rsi_val = t['rsi'] if t['rsi'] is not None else 0
            msg += f"   RSI: {rsi_val:.1f} | Hacim: {t['volume_ratio']:.1f}x\n"
        msg += "\n"
    msg += "--------------------\nOtomatik GitHub Actions bildirimi\nYatirim tavsiyesi degildir."
    return msg

def run_analysis_cycle(config):
    start_time = time.time()
    logger.info('Analiz dongusu basladi...')
    try:
        tickers = get_usdt_tickers(config, config['min_quote_volume_usd'])
        if not tickers:
            logger.error('Ticker verisi alinamadi - tum Binance endpointleri basarisiz')
            return
        logger.info(f"{len(tickers)} USDT cifti bulundu")
        tickers.sort(key=lambda t: float(t.get('quoteVolume', 0)), reverse=True)
        threshold = config['price_change_threshold_pct']
        candidates = [t for t in tickers if abs(float(t.get('priceChangePercent', 0))) >= threshold * 0.5]
        candidates = candidates[:config['max_coins_to_analyze']]
        logger.info(f"{len(candidates)} aday coin analiz edilecek")
        to_notify = []
        analyzed_count = 0
        error_count = 0
        window_min = config['notify_window_min_minutes']
        window_max = config['notify_window_max_minutes']
        for i, ticker in enumerate(candidates):
            symbol = ticker['symbol']
            try:
                klines = get_klines(symbol, config)
                if not klines: continue
                analysis = analyze_coin(symbol, klines, config)
                analyzed_count += 1
                if analysis and analysis['trend']:
                    if window_min <= analysis['trend_duration_minutes'] <= window_max:
                        to_notify.append(analysis)
                        logger.info(f"  BILDIR: {symbol} {analysis['trend']} ({analysis['trend_duration_minutes']} dk)")
                time.sleep(config['request_delay_seconds'])
            except Exception as e:
                error_count += 1
                logger.debug(f'{symbol} analiz hatasi: {e}'); continue
            if (i + 1) % 20 == 0:
                logger.info(f"  Ilerleme: {i+1}/{len(candidates)}")
        if to_notify:
            to_notify = to_notify[:config['max_notifications_per_cycle']]
            msg = build_notification_message(config, to_notify)
            success = send_telegram_message(config, msg)
            if success:
                logger.info(f"Telegram'a {len(to_notify)} coin bildirimi gonderildi!")
            else:
                logger.error('Telegram bildirimi basarisiz!')
        else:
            logger.info('Bu dongude bildirim yok (yeni 30+ dk trend yok)')
        elapsed = time.time() - start_time
        logger.info(f"Dongu tamamlandi ({elapsed:.1f} sn) | Analiz: {analyzed_count} | Hata: {error_count} | Bildirim: {len(to_notify)}")
    except Exception as e:
        logger.error(f'Dongu hatasi: {e}', exc_info=True)

def main():
    config = get_config()
    logger.info('Binance Trend Bot - GitHub Actions Surumu baslatiliyor')
    logger.info(f"Esik: %{config['price_change_threshold_pct']} | Min hacim: ${config['min_quote_volume_usd']:,}")
    logger.info(f"Bildirim penceresi: {config['notify_window_min_minutes']}-{config['notify_window_max_minutes']} dk")
    run_analysis_cycle(config)

if __name__ == '__main__':
    main()
