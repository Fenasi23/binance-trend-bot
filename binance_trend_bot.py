#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, logging, requests
from datetime import datetime, timezone, timedelta
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger('BinanceTrendBot')

ISTANBUL_TZ = timezone(timedelta(hours=3))

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
        'price_change_threshold_pct': float(os.environ.get('PRICE_CHANGE_THRESHOLD', '1.5')),
        'min_quote_volume_usd': int(os.environ.get('MIN_QUOTE_VOLUME', '500000')),
        'rsi_period': int(os.environ.get('RSI_PERIOD', '14')),
        'rsi_overbought': float(os.environ.get('RSI_OVERBOUGHT', '80')),
        'rsi_oversold': float(os.environ.get('RSI_OVERSOLD', '20')),
        'kline_interval': os.environ.get('KLINE_INTERVAL', '15m'),
        'kline_limit': int(os.environ.get('KLINE_LIMIT', '100')),
        'notify_window_min_minutes': int(os.environ.get('NOTIFY_WINDOW_MIN', '15')),
        'notify_window_max_minutes': int(os.environ.get('NOTIFY_WINDOW_MAX', '240')),
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

def rsi_interpretation(rsi):
    if rsi is None: return "Veri yok"
    if rsi >= 70: return "Asiri alim (dusus gelebilir)"
    elif rsi >= 60: return "Guclu, yukselis egilimi"
    elif rsi >= 45: return "Notr, yukselise yer var"
    elif rsi >= 30: return "Notr-asagi, dusus devam edebilir"
    else: return "Asiri satim (yukselis gelebilir)"

def volume_interpretation(vol):
    if vol >= 3.0: return "Cok guclu (cok guvenilir)"
    elif vol >= 2.0: return "Guclu (guvenilir sinyal)"
    elif vol >= 1.5: return "Orta guc"
    else: return "Normal hacim"

def estimate_trend_remaining(analysis):
    trend = analysis['trend']
    rsi = analysis.get('rsi')
    vol_ratio = analysis.get('volume_ratio', 1.0)
    if rsi is None: return "Tahmin yok"
    vol_note = ""
    if vol_ratio >= 2.5:
        vol_note = " (hacim yuksek, daha uzun)"
    elif vol_ratio < 1.5:
        vol_note = " (hacim zayif, daha kisa)"
    if trend == 'rising':
        if rsi >= 75:
            base = "0-2 saat daha (zirve yaklasiyor, donus gelebilir)"
        elif rsi >= 65:
            base = "1-3 saat daha (olgunlasiyor)"
        elif rsi >= 50:
            base = "2-4 saat daha (devam ediyor)"
        elif rsi >= 35:
            base = "4-8 saat daha (baslangic, yukari yer var)"
        else:
            base = "6-12 saat daha (yeni basladi)"
    else:
        if rsi <= 25:
            base = "0-2 saat daha (dip yaklasiyor, donus gelebilir)"
        elif rsi <= 35:
            base = "1-3 saat daha (olgunlasiyor)"
        elif rsi <= 50:
            base = "2-4 saat daha (devam ediyor)"
        elif rsi <= 65:
            base = "4-8 saat daha (baslangic, asagi yer var)"
        else:
            base = "6-12 saat daha (yeni basladi)"
    return base + vol_note

def get_position_recommendation(analysis):
    trend = analysis['trend']
    rsi = analysis.get('rsi')
    vol_ratio = analysis.get('volume_ratio', 1.0)
    if rsi is None: return "Veri yok"
    vol_note = ""
    if vol_ratio >= 2.5:
        vol_note = " (guclu hacim)"
    elif vol_ratio < 1.5:
        vol_note = " (zayif hacim - dikkat)"
    if trend == 'rising':
        if rsi < 50:
            base = "LONG (Alim) - yukselis yeni basladi"
        elif rsi < 65:
            base = "LONG (Alim) - yukselis devam ediyor"
        elif rsi < 75:
            base = "DIKKAT (yukselis olgun) - zirve yaklasiyor"
        else:
            base = "BEKLE (zirve, riskli) - alim icin gec olabilir"
    else:
        if rsi > 50:
            base = "SHORT (Satis) - dusus yeni basladi"
        elif rsi > 35:
            base = "SHORT (Satis) - dusus devam ediyor"
        elif rsi > 25:
            base = "DIKKAT (dusus olgun) - dip yaklasiyor"
        else:
            base = "BEKLE (dip, riskli) - satis icin gec olabilir"
    return base + vol_note

def get_position_emoji(analysis):
    """LONG yeşil, SHORT kırmızı emoji"""
    trend = analysis['trend']
    rsi = analysis.get('rsi')
    if trend == 'rising':
        if rsi is not None and rsi >= 75:
            return "🟡"  # BEKLE - sarı (nötr)
        if rsi is not None and rsi >= 65:
            return "🟡"  # DIKKAT - sarı
        return "🟢"  # LONG - yeşil
    else:
        if rsi is not None and rsi <= 25:
            return "🟡"  # BEKLE - sarı
        if rsi is not None and rsi <= 35:
            return "🟡"  # DIKKAT - sarı
        return "🔴"  # SHORT - kırmızı

def get_leverage_recommendation(analysis):
    rsi = analysis.get('rsi')
    vol_ratio = analysis.get('volume_ratio', 1.0)
    change_1h = abs(analysis.get('change_1h', 0))
    if rsi is None: return "2x-3x (dusuk)"
    strength = 0
    if vol_ratio >= 2.5: strength += 3
    elif vol_ratio >= 1.5: strength += 2
    elif vol_ratio >= 1.0: strength += 1
    if 35 <= rsi <= 65: strength += 3
    elif 25 <= rsi <= 75: strength += 2
    if change_1h >= 5: strength += 3
    elif change_1h >= 2: strength += 2
    elif change_1h >= 1: strength += 1
    if strength >= 8: return "10x-15x (guclu trend)"
    elif strength >= 6: return "5x-10x (orta guc)"
    elif strength >= 4: return "3x-5x (zayif trend)"
    else: return "2x-3x (dusuk - dikkat)"

def calculate_tp_sl(analysis):
    trend = analysis['trend']
    price = analysis['price']
    ema20 = analysis.get('ema20')
    if trend == 'rising':
        tp1 = price * 1.05
        tp2 = price * 1.10
        tp3 = price * 1.15
        sl = price * 0.97
        if ema20 and ema20 < sl and ema20 > 0:
            sl = ema20
    else:
        tp1 = price * 0.95
        tp2 = price * 0.90
        tp3 = price * 0.85
        sl = price * 1.03
        if ema20 and ema20 > sl and ema20 > 0:
            sl = ema20
    return tp1, tp2, tp3, sl

def calculate_trend_duration(closes, trend, threshold):
    candles_in_trend = 0
    min_cum_change = threshold * 0.25
    max_lookback = min(20, len(closes) - 1)
    for lookback in range(1, max_lookback + 1):
        past_idx = len(closes) - 1 - lookback
        if past_idx < 0: break
        past_price = closes[past_idx]
        if past_price == 0: break
        cum_change = ((closes[-1] - past_price) / past_price) * 100
        if trend == 'rising' and cum_change >= min_cum_change:
            candles_in_trend = lookback
        elif trend == 'falling' and cum_change <= -min_cum_change:
            candles_in_trend = lookback
        else:
            break
    return candles_in_trend * 15

def analyze_coin(symbol, klines, config):
    if not klines or len(klines) < 20: return None
    try:
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
    except (ValueError, IndexError): return None
    current_price = closes[-1]
    rsi = calculate_rsi(closes, config['rsi_period'])
    ema20 = calculate_ema(closes, 20)
    change_30m = safe_pct(closes, 2) or 0
    change_1h = safe_pct(closes, 4) or 0
    change_2h = safe_pct(closes, 8) or 0
    change_4h = safe_pct(closes, 16) or 0
    change_24h = safe_pct(closes, len(closes) - 1) or 0
    avg_vol = sum(volumes[-20:]) / max(len(volumes[-20:]), 1)
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
    threshold = config['price_change_threshold_pct']
    trend = None
    trend_score = 0
    if change_1h > threshold: trend_score += 2
    if change_2h > threshold * 0.5: trend_score += 1
    if change_30m > 0: trend_score += 1
    if rsi is not None and rsi < config['rsi_overbought']: trend_score += 1
    if ema20 is not None and current_price > ema20: trend_score += 2
    if trend_score >= 5:
        trend = 'rising'
    else:
        trend_score = 0
        if change_1h < -threshold: trend_score += 2
        if change_2h < -threshold * 0.5: trend_score += 1
        if change_30m < 0: trend_score += 1
        if rsi is not None and rsi > config['rsi_oversold']: trend_score += 1
        if ema20 is not None and current_price < ema20: trend_score += 2
        if trend_score >= 5:
            trend = 'falling'
    if not trend: return None
    trend_duration = calculate_trend_duration(closes, trend, threshold)
    return {
        'symbol': symbol, 'price': current_price,
        'change_1h': change_1h, 'change_4h': change_4h, 'change_24h': change_24h,
        'rsi': rsi, 'volume_ratio': vol_ratio, 'ema20': ema20,
        'trend': trend, 'trend_duration_minutes': trend_duration,
        'timestamp': int(time.time()),
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
    data = {'chat_id': config['telegram_chat_id'], 'text': message, 'disable_web_page_preview': True}
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
    now_str = datetime.now(ISTANBUL_TZ).strftime('%d.%m.%Y %H:%M (TSI)')
    msg = "━━━━━━━━━━━━━━━━━━\n"
    msg += "📊 BINANCE TREND RAPORU\n"
    msg += f"🕐 {now_str} | {len(rising) + len(falling)} yeni trend\n"
    msg += "🟢 LONG (Alim) | 🔴 SHORT (Satis) | 🟡 BEKLE\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    if rising:
        msg += "🟢🟢🟢 YUKSELIS TRENDI (LONG FIRSATLARI)\n"
        msg += "─────────────────────────────────────\n"
        for t in rising[:6]:
            pos_emoji = get_position_emoji(t)
            arrow = "🚀" if t['change_1h'] > 5 else "📈"
            msg += f"\n{pos_emoji} {arrow} {t['symbol']} ({t['trend_duration_minutes']} dk'dir devam ediyor)\n"
            msg += f"   💰 Fiyat: {format_price(t['price'])}\n"
            msg += f"   📊 1s: {format_change(t['change_1h'])} | 4s: {format_change(t['change_4h'])}\n"
            rsi_val = t['rsi'] if t['rsi'] is not None else 0
            rsi_interp = rsi_interpretation(t['rsi'])
            msg += f"   ⚡ RSI: {rsi_val:.1f} → {rsi_interp}\n"
            vol_interp = volume_interpretation(t['volume_ratio'])
            msg += f"   📦 Hacim: {t['volume_ratio']:.1f}x → {vol_interp}\n"
            est_remaining = estimate_trend_remaining(t)
            msg += f"   ⏱️ Tahmini kalan: {est_remaining}\n"
            position_rec = get_position_recommendation(t)
            msg += f"   🎯 Pozisyon: {pos_emoji} {position_rec}\n"
            leverage = get_leverage_recommendation(t)
            msg += f"   🎚️ Kaldirac: {leverage}\n"
            tp1, tp2, tp3, sl = calculate_tp_sl(t)
            msg += f"   💎 TP1 (Take Profit): {format_price(tp1)} (+5%)\n"
            msg += f"   💎 TP2 (Take Profit): {format_price(tp2)} (+10%)\n"
            msg += f"   💎 TP3 (Take Profit): {format_price(tp3)} (+15%)\n"
            sl_pct = ((sl - t['price']) / t['price']) * 100
            msg += f"   🛑 SL (Stop Loss): {format_price(sl)} ({sl_pct:+.1f}%)\n"
        msg += "\n"
    if falling:
        msg += "🔴🔴🔴 DUSUS TRENDI (SHORT FIRSATLARI)\n"
        msg += "─────────────────────────────────────\n"
        for t in falling[:6]:
            pos_emoji = get_position_emoji(t)
            arrow = "💥" if t['change_1h'] < -5 else "📉"
            msg += f"\n{pos_emoji} {arrow} {t['symbol']} ({t['trend_duration_minutes']} dk'dir devam ediyor)\n"
            msg += f"   💰 Fiyat: {format_price(t['price'])}\n"
            msg += f"   📊 1s: {format_change(t['change_1h'])} | 4s: {format_change(t['change_4h'])}\n"
            rsi_val = t['rsi'] if t['rsi'] is not None else 0
            rsi_interp = rsi_interpretation(t['rsi'])
            msg += f"   ⚡ RSI: {rsi_val:.1f} → {rsi_interp}\n"
            vol_interp = volume_interpretation(t['volume_ratio'])
            msg += f"   📦 Hacim: {t['volume_ratio']:.1f}x → {vol_interp}\n"
            est_remaining = estimate_trend_remaining(t)
            msg += f"   ⏱️ Tahmini kalan: {est_remaining}\n"
            position_rec = get_position_recommendation(t)
            msg += f"   🎯 Pozisyon: {pos_emoji} {position_rec}\n"
            leverage = get_leverage_recommendation(t)
            msg += f"   🎚️ Kaldirac: {leverage}\n"
            tp1, tp2, tp3, sl = calculate_tp_sl(t)
            msg += f"   💎 TP1 (Take Profit): {format_price(tp1)} (-5%)\n"
            msg += f"   💎 TP2 (Take Profit): {format_price(tp2)} (-10%)\n"
            msg += f"   💎 TP3 (Take Profit): {format_price(tp3)} (-15%)\n"
            sl_pct = ((sl - t['price']) / t['price']) * 100
            msg += f"   🛑 SL (Stop Loss): {format_price(sl)} ({sl_pct:+.1f}%)\n"
        msg += "\n"
    msg += "━━━━━━━━━━━━━━━━━━\n"
    msg += "📖 NASIL YORUMLANIR?\n\n"
    msg += "🟢 = LONG (Alim) - Yukselis trendi\n"
    msg += "🔴 = SHORT (Satis) - Dusus trendi\n"
    msg += "🟡 = BEKLE / DIKKAT - Riskli bolge\n\n"
    msg += "🎯 Pozisyon onerisi:\n"
    msg += "   🟢 LONG (Alim) → Yukselis trendinde alim\n"
    msg += "   🔴 SHORT (Satis) → Dusus trendinde satis\n"
    msg += "   🟡 DIKKAT/BEKLE → Trend olgun, donus gelebilir\n\n"
    msg += "🎚️ Kaldirac onerisi (gucune gore):\n"
    msg += "   2x-3x → Cok dusuk (zayif trend, dikkat)\n"
    msg += "   3x-5x → Dusuk-orta (zayif trend)\n"
    msg += "   5x-10x → Orta (orta guc)\n"
    msg += "   10x-15x → Yuksek (guclu trend)\n"
    msg += "   ⚠️ Kaldirac yukseldikce risk artar!\n\n"
    msg += "💎 TP (Take Profit) seviyeleri:\n"
    msg += "   TP1 → %5 kar al (ilk hedef)\n"
    msg += "   TP2 → %10 kar al (orta hedef)\n"
    msg += "   TP3 → %15 kar al (uzun hedef)\n\n"
    msg += "🛑 SL (Stop Loss):\n"
    msg += "   Fiyat %3 aleyhine gidince satis\n"
    msg += "   Zarar limiti - risk yonetimi\n\n"
    msg += "⚡ RSI:\n"
    msg += "   70+ → Asiri alim (dusus gelebilir)\n"
    msg += "   30-70 → Normal bolge\n"
    msg += "   30 alti → Asiri satim (yukselis gelebilir)\n\n"
    msg += "📦 Hacim:\n"
    msg += "   1x → Normal\n"
    msg += "   2x+ → Guclu (guvenilir sinyal)\n"
    msg += "   3x+ → Cok guclu\n\n"
    msg += "⏱️ Tahmini kalan sure:\n"
    msg += "   Zirve/dip yakinsa → donus gelebilir\n"
    msg += "   Baslangicta → trend daha uzun surer\n\n"
    msg += "━━━━━━━━━━━━━━━━━━\n"
    msg += "⚠️ Yatirim tavsiyesi DEGILDIR!\n"
    msg += "Tahminler teknik analize dayalidir, kesin degildir.\n"
    msg += "Kendi arastirmanizi yapin.\n"
    msg += "Yuksek kaldirac = yuksek risk!"
    return msg

def run_analysis_cycle(config):
    start_time = time.time()
    logger.info('Analiz dongusu basladi...')
    try:
        tickers = get_usdt_tickers(config, config['min_quote_volume_usd'])
        if not tickers:
            logger.error('Ticker verisi alinamadi')
            return
        logger.info(f"{len(tickers)} USDT cifti bulundu")
        tickers.sort(key=lambda t: float(t.get('quoteVolume', 0)), reverse=True)
        threshold = config['price_change_threshold_pct']
        candidates = [t for t in tickers if abs(float(t.get('priceChangePercent', 0))) >= threshold * 0.3]
        candidates = candidates[:config['max_coins_to_analyze']]
        logger.info(f"{len(candidates)} aday coin analiz edilecek")
        to_notify = []
        trends_found = 0
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
                    trends_found += 1
                    logger.info(f"  Trend: {symbol} {analysis['trend']} ({analysis['trend_duration_minutes']} dk, 1s: {analysis['change_1h']:.2f}%)")
                    if window_min <= analysis['trend_duration_minutes'] <= window_max:
                        to_notify.append(analysis)
                        logger.info(f"    -> BILDIRILECEK")
                    else:
                        logger.info(f"    -> Pencere disi (15-240 dk olmali)")
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
            logger.info(f'Bu dongude bildirim yok (trend: {trends_found}, pencerede: 0)')
        elapsed = time.time() - start_time
        logger.info(f"Dongu tamamlandi ({elapsed:.1f} sn) | Analiz: {analyzed_count} | Trend: {trends_found} | Hata: {error_count} | Bildirim: {len(to_notify)}")
    except Exception as e:
        logger.error(f'Dongu hatasi: {e}', exc_info=True)

def main():
    config = get_config()
    logger.info('Binance Trend Bot - GitHub Actions Surumu baslatilio')
    logger.info(f"Esik: %{config['price_change_threshold_pct']} | Min hacim: ${config['min_quote_volume_usd']:,}")
    logger.info(f"Bildirim penceresi: {config['notify_window_min_minutes']}-{config['notify_window_max_minutes']} dk")
    run_analysis_cycle(config)

if __name__ == '__main__':
    main()
