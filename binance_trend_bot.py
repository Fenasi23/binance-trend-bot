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
    if rsi >= 70: return "Asiri alim"
    elif rsi >= 60: return "Guclu yukselis"
    elif rsi >= 45: return "Notr (yer var)"
    elif rsi >= 30: return "Notr-asagi"
    else: return "Asiri satim"

def volume_interpretation(vol):
    if vol >= 3.0: return "Cok guclu"
    elif vol >= 2.0: return "Guclu"
    elif vol >= 1.5: return "Orta"
    elif vol >= 0.5: return "Normal"
    else: return "Cok zayif"

def estimate_trend_remaining(analysis):
    trend = analysis['trend']
    rsi = analysis.get('rsi')
    vol_ratio = analysis.get('volume_ratio', 1.0)
    if rsi is None: return "Tahmin yok"
    vol_note = ""
    if vol_ratio >= 2.5: vol_note = " (yuksek hacim)"
    elif vol_ratio < 1.5: vol_note = " (zayif hacim)"
    if trend == 'rising':
        if rsi >= 75: base = "0-2 saat (zirve yakin)"
        elif rsi >= 65: base = "1-3 saat (olgun)"
        elif rsi >= 50: base = "2-4 saat (devam)"
        elif rsi >= 35: base = "4-8 saat (baslangic)"
        else: base = "6-12 saat (yeni)"
    else:
        if rsi <= 25: base = "0-2 saat (dip yakin)"
        elif rsi <= 35: base = "1-3 saat (olgun)"
        elif rsi <= 50: base = "2-4 saat (devam)"
        elif rsi <= 65: base = "4-8 saat (baslangic)"
        else: base = "6-12 saat (yeni)"
    return base + vol_note

def get_position_recommendation(analysis):
    trend = analysis['trend']
    rsi = analysis.get('rsi')
    vol_ratio = analysis.get('volume_ratio', 1.0)
    if rsi is None: return "Veri yok"
    vol_note = ""
    if vol_ratio >= 2.5: vol_note = " (guclu hacim)"
    elif vol_ratio < 1.5: vol_note = " (zayif hacim)"
    if trend == 'rising':
        if rsi < 50: return "LONG - yeni basladi" + vol_note
        elif rsi < 65: return "LONG - devam ediyor" + vol_note
        elif rsi < 75: return "DIKKAT - zirve yakin"
        else: return "BEKLE - zirve, riskli"
    else:
        if rsi > 50: return "SHORT - yeni basladi" + vol_note
        elif rsi > 35: return "SHORT - devam ediyor" + vol_note
        elif rsi > 25: return "DIKKAT - dip yakin"
        else: return "BEKLE - dip, riskli"

def get_position_emoji(analysis):
    trend = analysis['trend']
    rsi = analysis.get('rsi')
    if trend == 'rising':
        if rsi is not None and rsi >= 65: return "🟡"
        return "🟢"
    else:
        if rsi is not None and rsi <= 35: return "🟡"
        return "🔴"

def get_leverage_recommendation(analysis):
    rsi = analysis.get('rsi')
    vol_ratio = analysis.get('volume_ratio', 1.0)
    change_1h = abs(analysis.get('change_1h', 0))
    if rsi is None: return "2x-3x"
    strength = 0
    if vol_ratio >= 2.5: strength += 3
    elif vol_ratio >= 1.5: strength += 2
    elif vol_ratio >= 1.0: strength += 1
    if 35 <= rsi <= 65: strength += 3
    elif 25 <= rsi <= 75: strength += 2
    if change_1h >= 5: strength += 3
    elif change_1h >= 2: strength += 2
    elif change_1h >= 1: strength += 1
    if strength >= 8: return "10x-15x"
    elif strength >= 6: return "5x-10x"
    elif strength >= 4: return "3x-5x"
    else: return "2x-3x"

def calculate_tp_sl_smart(analysis, leverage_str):
    trend = analysis['trend']
    price = analysis['price']
    if "10x-15x" in leverage_str:
        sl_pct, tp1_pct, tp2_pct, tp3_pct, lev = 0.02, 0.03, 0.06, 0.10, 12
    elif "5x-10x" in leverage_str:
        sl_pct, tp1_pct, tp2_pct, tp3_pct, lev = 0.03, 0.045, 0.09, 0.15, 7
    elif "3x-5x" in leverage_str:
        sl_pct, tp1_pct, tp2_pct, tp3_pct, lev = 0.04, 0.06, 0.12, 0.20, 4
    else:
        sl_pct, tp1_pct, tp2_pct, tp3_pct, lev = 0.05, 0.075, 0.15, 0.25, 2
    if trend == 'rising':
        tp1 = price * (1 + tp1_pct); tp2 = price * (1 + tp2_pct); tp3 = price * (1 + tp3_pct)
        sl = price * (1 - sl_pct); liq_price = price * (1 - 1/lev)
        sl_disp, liq_disp, tp1_disp, tp2_disp, tp3_disp = -sl_pct*100, -(1/lev)*100, tp1_pct*100, tp2_pct*100, tp3_pct*100
    else:
        tp1 = price * (1 - tp1_pct); tp2 = price * (1 - tp2_pct); tp3 = price * (1 - tp3_pct)
        sl = price * (1 + sl_pct); liq_price = price * (1 + 1/lev)
        sl_disp, liq_disp, tp1_disp, tp2_disp, tp3_disp = sl_pct*100, (1/lev)*100, -tp1_pct*100, -tp2_pct*100, -tp3_pct*100
    return {'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'sl': sl, 'liq': liq_price,
            'sl_pct': sl_disp, 'liq_pct': liq_disp, 'tp1_pct': tp1_disp, 'tp2_pct': tp2_disp, 'tp3_pct': tp3_disp}

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
        if trend == 'rising' and cum_change >= min_cum_change: candles_in_trend = lookback
        elif trend == 'falling' and cum_change <= -min_cum_change: candles_in_trend = lookback
        else: break
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
    if trend_score >= 5: trend = 'rising'
    else:
        trend_score = 0
        if change_1h < -threshold: trend_score += 2
        if change_2h < -threshold * 0.5: trend_score += 1
        if change_30m < 0: trend_score += 1
        if rsi is not None and rsi > config['rsi_oversold']: trend_score += 1
        if ema20 is not None and current_price < ema20: trend_score += 2
        if trend_score >= 5: trend = 'falling'
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

def build_trend_block(t):
    """Tek trend için şık formatlı blok"""
    pos_emoji = get_position_emoji(t)
    if t['trend'] == 'rising':
        arrow = "🚀" if t['change_1h'] > 5 else "📈"
    else:
        arrow = "💥" if t['change_1h'] < -5 else "📉"
    rsi_val = t['rsi'] if t['rsi'] is not None else 0
    leverage = get_leverage_recommendation(t)
    tpsl = calculate_tp_sl_smart(t, leverage)
    position = get_position_recommendation(t)
    rsi_interp = rsi_interpretation(t['rsi'])
    vol_interp = volume_interpretation(t['volume_ratio'])
    est = estimate_trend_remaining(t)
    
    block = ""
    block += f"\n{pos_emoji}  {arrow}  {t['symbol']}\n"
    block += f"     {t['trend_duration_minutes']} dk'dir devam\n"
    block += f"\n"
    block += f"   💰 Fiyat      :  {format_price(t['price'])}\n"
    block += f"   📊 1 saat     :  {format_change(t['change_1h'])}\n"
    block += f"   📊 4 saat     :  {format_change(t['change_4h'])}\n"
    block += f"   ⚡ RSI        :  {rsi_val:.1f}  ({rsi_interp})\n"
    block += f"   📦 Hacim      :  {t['volume_ratio']:.2f}x  ({vol_interp})\n"
    block += f"   ⏱️ Kalan süre :  {est}\n"
    block += f"\n"
    block += f"   🎯 Pozisyon  :  {pos_emoji} {position}\n"
    block += f"   🎚️ Kaldıraç  :  {leverage}\n"
    block += f"\n"
    block += f"   💎 TP1 : {format_price(tpsl['tp1'])}  ({tpsl['tp1_pct']:+.1f}%)\n"
    block += f"   💎 TP2 : {format_price(tpsl['tp2'])}  ({tpsl['tp2_pct']:+.1f}%)\n"
    block += f"   💎 TP3 : {format_price(tpsl['tp3'])}  ({tpsl['tp3_pct']:+.1f}%)\n"
    block += f"   🛑 SL  : {format_price(tpsl['sl'])}  ({tpsl['sl_pct']:+.1f}%)\n"
    block += f"\n"
    block += f"   ⚠️ Likitasyon : {format_price(tpsl['liq'])}  ({tpsl['liq_pct']:+.1f}%)\n"
    block += f"   ✅ SL likitasyondan önce çalışır\n"
    block += f"\n"
    block += f"   ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    return block

def build_notification_message(config, trends):
    rising = [t for t in trends if t['trend'] == 'rising']
    falling = [t for t in trends if t['trend'] == 'falling']
    rising.sort(key=lambda x: x['change_1h'], reverse=True)
    falling.sort(key=lambda x: x['change_1h'])
    now_str = datetime.now(ISTANBUL_TZ).strftime('%d.%m.%Y %H:%M')
    total = len(rising) + len(falling)
    
    msg = ""
    # Başlık - şık çerçeve
    msg += "╔══════════════════════════╗\n"
    msg += "║                          ║\n"
    msg += "║   📊 BINANCE TREND       ║\n"
    msg += "║      RAPORU              ║\n"
    msg += "║                          ║\n"
    msg += "╚══════════════════════════╝\n"
    msg += "\n"
    msg += f"🕐 Tarih/Saat : {now_str} (TSI)\n"
    msg += f"📈 Toplam    : {total} yeni trend\n"
    msg += "\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += "🟢 LONG (Alım)  🔴 SHORT (Satış)  🟡 BEKLE\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += "\n"
    
    if rising:
        msg += "╭──────────────────────────╮\n"
        msg += "│ 🟢 YÜKSELİŞ TRENDİ       │\n"
        msg += "│    LONG FIRSATLARI       │\n"
        msg += "╰──────────────────────────╯\n"
        msg += "\n"
        for t in rising[:6]:
            msg += build_trend_block(t)
        msg += "\n"
    
    if falling:
        msg += "╭──────────────────────────╮\n"
        msg += "│ 🔴 DÜŞÜŞ TRENDİ           │\n"
        msg += "│    SHORT FIRSATLARI      │\n"
        msg += "╰──────────────────────────╯\n"
        msg += "\n"
        for t in falling[:6]:
            msg += build_trend_block(t)
        msg += "\n"
    
    # Kısa rehber - kompakt
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += "📖 KISA REHBER\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += "\n"
    msg += "🟢 LONG  → Yükseliş trendinde ALIM\n"
    msg += "🔴 SHORT → Düşüş trendinde SATIŞ\n"
    msg += "🟡 BEKLE → Riskli bölge, bekle\n"
    msg += "\n"
    msg += "🎚️ Kaldıraç:\n"
    msg += "   2x-3x  → Zayıf trend\n"
    msg += "   5x-10x → Orta güç\n"
    msg += "   10x-15x → Güçlü trend\n"
    msg += "\n"
    msg += "💎 TP = Kar alma hedefleri\n"
    msg += "🛑 SL = Stop Loss (zarar limiti)\n"
    msg += "⚠️ Likitasyon = Hesap kapanma fiyatı\n"
    msg += "✅ SL, likitasyondan ÖNCE çalışır\n"
    msg += "\n"
    msg += "⚡ RSI:\n"
    msg += "   70+ → Aşırı alım (düşüş gelebilir)\n"
    msg += "   30-70 → Normal bölge\n"
    msg += "   <30 → Aşırı satım (yükseliş gelebilir)\n"
    msg += "\n"
    msg += "📦 Hacim:\n"
    msg += "   1x → Normal  |  2x+ → Güçlü\n"
    msg += "   3x+ → Çok güçlü (güvenilir)\n"
    msg += "\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += "⚠️ Yatırım tavsiyesi DEĞİLDİR!\n"
    msg += "Tahminler teknik analize dayalıdır.\n"
    msg += "Yüksek kaldıraç = yüksek risk!\n"
    msg += "Kendi araştırmanızı yapın.\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
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
    logger.info('Binance Trend Bot - GitHub Actions Surumu baslatiliyor')
    logger.info(f"Esik: %{config['price_change_threshold_pct']} | Min hacim: ${config['min_quote_volume_usd']:,}")
    logger.info(f"Bildirim penceresi: {config['notify_window_min_minutes']}-{config['notify_window_max_minutes']} dk")
    run_analysis_cycle(config)

if __name__ == '__main__':
    main()
