"""
SigSauceBot Telegram Signal Bot — MULTI-TIMEFRAME EDITION
==========================================================
✅ Scans every 15 minutes
✅ 7 timeframes per instrument: 1m · 5m · 15m · 30m · 1h · 4h
✅ Weighted confluence engine — higher TFs carry more weight
✅ 8 indicators: RSI · EMA · BB · MACD · ADX · Stoch · Candles · S&R
✅ 10 instruments watched
✅ Commands: /scan · /status · /pairs
"""

import os
import json
import requests
import schedule
import time
import pandas as pd
import numpy as np
import threading
from datetime import datetime, timezone
from flask import Flask

# ── CONFIG ─────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
PORT      = int(os.environ.get("PORT", 5000))

CHECK_INTERVAL_MINUTES = 10
MIN_CONFIDENCE         = 60   # weighted confidence threshold to fire

INSTRUMENTS = {
    "XAUUSD": {"yahoo": "GC=F",      "label": "🥇 Gold",       "pip": 0.01,   "type": "metal",  "unit": "oz"},
    "XAGUSD": {"yahoo": "SI=F",      "label": "🥈 Silver",     "pip": 0.001,  "type": "metal",  "unit": "oz"},
    "NAS100": {"yahoo": "NQ=F",      "label": "💻 NASDAQ 100", "pip": 0.25,   "type": "index",  "unit": "units"},
    "SPX500": {"yahoo": "ES=F",      "label": "📈 S&P 500",    "pip": 0.25,   "type": "index",  "unit": "units"},
    "EURUSD": {"yahoo": "EURUSD=X",  "label": "💶 EUR/USD",    "pip": 0.0001, "type": "forex",  "unit": "lots"},
    "GBPUSD": {"yahoo": "GBPUSD=X",  "label": "💷 GBP/USD",   "pip": 0.0001, "type": "forex",  "unit": "lots"},
    "USDJPY": {"yahoo": "JPY=X",     "label": "💴 USD/JPY",    "pip": 0.01,   "type": "forex",  "unit": "lots"},
    "GBPJPY": {"yahoo": "GBPJPY=X",  "label": "🇬🇧 GBP/JPY",  "pip": 0.01,   "type": "forex",  "unit": "lots"},
    "EURGBP": {"yahoo": "EURGBP=X",  "label": "🇪🇺 EUR/GBP",  "pip": 0.0001, "type": "forex",  "unit": "lots"},
    "NDXUSD": {"yahoo": "NQ=F",      "label": "📊 NDX/USD",    "pip": 0.25,   "type": "index",  "unit": "units"},
}

# Timeframe definitions — (yahoo_interval, yahoo_range, display_label, weight)
# Higher weight = more influence on the final signal
TIMEFRAMES = {
    "15m": ("15m", "5d",  "15 Min", 2),
    "30m": ("30m", "1mo", "30 Min", 2),
    "1h":  ("1h",  "1mo", "1 Hour", 3),
    "4h":  ("60m", "3mo", "4 Hour", 4),  # fetched as 60m, resampled to 4h
    "1d":  ("1d",  "1y",  "Daily",  5),
}

# ── Persistent risk config ─────────────────────────────────────────
CONFIG_FILE = "sigsaucebot/config.json"

def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {"balance": None, "risk_pct": 1.0}

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
    except Exception as e:
        print(f"[{now()}] Config save error: {e}")

_config = load_config()

# ── Signal history ──────────────────────────────────────────────────
HISTORY_FILE  = "sigsaucebot/history.json"
MAX_HISTORY   = 10

def load_history() -> list:
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def save_history(hist: list):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(hist[-MAX_HISTORY:], f)
    except Exception as e:
        print(f"[{now()}] History save error: {e}")

def append_history(result: dict):
    hist = load_history()
    hist.append({
        "ts":         datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
        "symbol":     result["symbol"],
        "label":      result["label"],
        "direction":  result["direction"],
        "confidence": result["confidence"],
        "entry":      result["entry"],
        "sl":         result["sl"],
        "tp1":        result["tp1"],
        "tp2":        result["tp2"],
        "tp3":        result["tp3"],
    })
    save_history(hist)

# ── Position size calculator ────────────────────────────────────────
def calc_position_size(entry: float, sl: float, meta: dict) -> str | None:
    """Returns a formatted position size string, or None if no balance set."""
    balance  = _config.get("balance")
    risk_pct = _config.get("risk_pct", 1.0)
    if not balance or balance <= 0:
        return None

    risk_dollars = balance * (risk_pct / 100)
    sl_distance  = abs(entry - sl)
    if sl_distance == 0:
        return None

    inst_type = meta.get("type", "forex")
    unit_label = meta["unit"]

    if inst_type == "forex":
        pip_size = meta["pip"]
        sl_pips  = sl_distance / pip_size
        pip_val  = 10.0
        lots     = risk_dollars / (sl_pips * pip_val)
        lots     = round(lots, 2)
        micro    = round(lots * 100)
        return (
            f"💰 <b>Position Size</b> ({risk_pct:.4g}% of ${balance:,.0f}):\n"
            f"<code>  Risk:  ${risk_dollars:,.2f}\n"
            f"  Size:  {lots:.2f} {unit_label}  ({micro:.0f} micro-lots)\n"
            f"  SL:    {sl_pips:.1f} pips</code>"
        )
    else:
        units = risk_dollars / sl_distance
        return (
            f"💰 <b>Position Size</b> ({risk_pct:.4g}% of ${balance:,.0f}):\n"
            f"<code>  Risk:  ${risk_dollars:,.2f}\n"
            f"  Size:  {units:.2f} {unit_label}\n"
            f"  SL:    {sl_distance:.4f} pts</code>"
        )

# ── 24/7 Uptime Web Server ─────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def home():
    return f"🤖 SigSauceBot is LIVE — {now()} UTC", 200

def run_server():
    app.run(host="0.0.0.0", port=PORT)

# ── Helpers ────────────────────────────────────────────────────────
def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

# ── Telegram ───────────────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        data = r.json()
        if data.get("ok"):
            print(f"[{now()}] ✅ Telegram sent → chat {CHAT_ID}")
        else:
            print(f"[{now()}] ❌ Telegram rejected: {data.get('description')} (code {data.get('error_code')}) → chat {CHAT_ID}")
    except Exception as e:
        print(f"[{now()}] ❌ Telegram error: {e}")

# ── Fetch candles from Yahoo Finance ──────────────────────────────
def get_candles(yahoo_symbol: str, interval: str, period: str) -> pd.DataFrame:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           f"?interval={interval}&range={period}")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        ohlcv = result["indicators"]["quote"][0]
        df = pd.DataFrame({
            "time":   pd.to_datetime(timestamps, unit="s", utc=True),
            "open":   ohlcv["open"],
            "high":   ohlcv["high"],
            "low":    ohlcv["low"],
            "close":  ohlcv["close"],
            "volume": ohlcv["volume"],
        }).dropna()
        return df
    except Exception as e:
        print(f"[{now()}] ❌ Price error ({yahoo_symbol} {interval}): {e}")
        return pd.DataFrame()

def get_candles_4h(yahoo_symbol: str) -> pd.DataFrame:
    df = get_candles(yahoo_symbol, "60m", "3mo")
    if df.empty:
        return df
    df = df.set_index("time")
    df4 = df.resample("4h").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna().reset_index()
    return df4

# ── Technical Indicators ───────────────────────────────────────────
def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_bb(series, period=20, dev=2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return mid + dev * std, mid, mid - dev * std

def calc_atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calc_macd(series):
    fast   = calc_ema(series, 12)
    slow   = calc_ema(series, 26)
    macd   = fast - slow
    signal = calc_ema(macd, 9)
    return macd, signal, macd - signal

def calc_adx(df, period=14):
    high, low = df["high"], df["low"]
    plus_dm  = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm]  = 0
    minus_dm[minus_dm < plus_dm] = 0
    atr      = calc_atr(df, period)
    plus_di  = 100 * plus_dm.rolling(period).mean()  / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(period).mean() / atr.replace(0, np.nan)
    dx       = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.rolling(period).mean(), plus_di, minus_di

def calc_stochastic(df, k=14, d=3):
    low_min  = df["low"].rolling(k).min()
    high_max = df["high"].rolling(k).max()
    k_pct    = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    return k_pct, k_pct.rolling(d).mean()

def detect_candle_pattern(df):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body        = (c - o).abs()
    bull_engulf = (c > o) & (o.shift(1) > c.shift(1)) & (c > o.shift(1)) & (o < c.shift(1))
    bear_engulf = (o > c) & (c.shift(1) > o.shift(1)) & (o > c.shift(1)) & (c < o.shift(1))
    lower_wick  = o.where(o < c, c) - l
    upper_wick  = h - c.where(c > o, o)
    hammer      = (lower_wick > 2 * body) & (upper_wick < body * 0.3) & (c > o)
    shooting    = (upper_wick > 2 * body) & (lower_wick < body * 0.3) & (o > c)
    return {
        "bull_engulf": bool(bull_engulf.iloc[-1]),
        "bear_engulf": bool(bear_engulf.iloc[-1]),
        "hammer":      bool(hammer.iloc[-1]),
        "shooting":    bool(shooting.iloc[-1]),
    }

def find_support_resistance(df, lookback=20):
    price = float(df["close"].iloc[-1])
    highs = float(df["high"].rolling(lookback).max().iloc[-1])
    lows  = float(df["low"].rolling(lookback).min().iloc[-1])
    return abs(price - lows) / price < 0.005, abs(price - highs) / price < 0.005

# ── Score a single dataframe ───────────────────────────────────────
def score_df(df: pd.DataFrame):
    if df.empty or len(df) < 30:
        return None

    close = df["close"]
    price = float(close.iloc[-1])
    prev  = float(close.iloc[-2])

    rsi                        = calc_rsi(close)
    ema20                      = calc_ema(close, 20)
    ema50                      = calc_ema(close, 50)
    ema200                     = calc_ema(close, min(200, len(close) - 1))
    bb_upper, bb_mid, bb_lower = calc_bb(close)
    atr                        = calc_atr(df)
    macd, macd_sig, macd_hist  = calc_macd(close)
    adx, plus_di, minus_di     = calc_adx(df)
    stoch_k, stoch_d           = calc_stochastic(df)
    patterns                   = detect_candle_pattern(df)
    near_support, near_resist  = find_support_resistance(df)

    rsi_now      = float(rsi.iloc[-1])
    rsi_prev     = float(rsi.iloc[-2])
    ema20_now    = float(ema20.iloc[-1])
    ema50_now    = float(ema50.iloc[-1])
    ema200_now   = float(ema200.iloc[-1])
    bb_up        = float(bb_upper.iloc[-1])
    bb_lo        = float(bb_lower.iloc[-1])
    bb_mid_now   = float(bb_mid.iloc[-1])
    atr_now      = float(atr.iloc[-1])
    macd_now     = float(macd.iloc[-1])
    macd_sig_now = float(macd_sig.iloc[-1])
    macd_h_now   = float(macd_hist.iloc[-1])
    macd_h_prev  = float(macd_hist.iloc[-2])
    adx_now      = float(adx.iloc[-1])
    plus_di_now  = float(plus_di.iloc[-1])
    minus_di_now = float(minus_di.iloc[-1])
    stoch_k_now  = float(stoch_k.iloc[-1])
    stoch_d_now  = float(stoch_d.iloc[-1])
    vol_avg      = float(df["volume"].rolling(20).mean().iloc[-1])
    vol_now      = float(df["volume"].iloc[-1])
    vol_surge    = vol_now > vol_avg * 1.2 if vol_avg > 0 else False

    buy_score, buy_hits = 0, []

    if rsi_now < 35:
        buy_score += 15; buy_hits.append(f"RSI oversold ({rsi_now:.0f})")
    elif rsi_now < 45 and rsi_now > rsi_prev:
        buy_score += 8;  buy_hits.append(f"RSI recovering ({rsi_now:.0f})")

    if price > ema20_now > ema50_now:
        buy_score += 15; buy_hits.append("EMA uptrend aligned")
    elif price > ema20_now:
        buy_score += 7

    if price > ema200_now:
        buy_score += 10; buy_hits.append("Above EMA200")

    if price <= bb_lo * 1.002:
        buy_score += 15; buy_hits.append("At lower Bollinger Band")
    elif price < bb_mid_now:
        buy_score += 5

    if macd_now > macd_sig_now and macd_h_now > macd_h_prev:
        buy_score += 15; buy_hits.append("MACD bullish crossover")
    elif macd_h_now > macd_h_prev:
        buy_score += 7

    if adx_now > 25 and plus_di_now > minus_di_now:
        buy_score += 10; buy_hits.append(f"Strong bullish trend (ADX {adx_now:.0f})")

    if stoch_k_now < 20 and stoch_k_now > stoch_d_now:
        buy_score += 10; buy_hits.append("Stochastic oversold crossup")

    if patterns["bull_engulf"]: buy_score += 10; buy_hits.append("Bullish engulfing")
    if patterns["hammer"]:      buy_score += 8;  buy_hits.append("Hammer pattern")
    if near_support:            buy_score += 7;  buy_hits.append("Near support")
    if vol_surge and price > prev: buy_score += 5; buy_hits.append("Volume surge up")

    sell_score, sell_hits = 0, []

    if rsi_now > 65:
        sell_score += 15; sell_hits.append(f"RSI overbought ({rsi_now:.0f})")
    elif rsi_now > 55 and rsi_now < rsi_prev:
        sell_score += 8;  sell_hits.append(f"RSI turning down ({rsi_now:.0f})")

    if price < ema20_now < ema50_now:
        sell_score += 15; sell_hits.append("EMA downtrend aligned")
    elif price < ema20_now:
        sell_score += 7

    if price < ema200_now:
        sell_score += 10; sell_hits.append("Below EMA200")

    if price >= bb_up * 0.998:
        sell_score += 15; sell_hits.append("At upper Bollinger Band")
    elif price > bb_mid_now:
        sell_score += 5

    if macd_now < macd_sig_now and macd_h_now < macd_h_prev:
        sell_score += 15; sell_hits.append("MACD bearish crossover")
    elif macd_h_now < macd_h_prev:
        sell_score += 7

    if adx_now > 25 and minus_di_now > plus_di_now:
        sell_score += 10; sell_hits.append(f"Strong bearish trend (ADX {adx_now:.0f})")

    if stoch_k_now > 80 and stoch_k_now < stoch_d_now:
        sell_score += 10; sell_hits.append("Stochastic overbought crossdown")

    if patterns["bear_engulf"]: sell_score += 10; sell_hits.append("Bearish engulfing")
    if patterns["shooting"]:    sell_score += 8;  sell_hits.append("Shooting star")
    if near_resist:             sell_score += 7;  sell_hits.append("Near resistance")
    if vol_surge and price < prev: sell_score += 5; sell_hits.append("Volume surge down")

    max_score = 120
    buy_conf  = min(int(buy_score  / max_score * 100), 100)
    sell_conf = min(int(sell_score / max_score * 100), 100)

    return buy_conf, sell_conf, atr_now, buy_hits[:3], sell_hits[:3]

# ── Multi-timeframe analysis ───────────────────────────────────────
def analyse_mtf(symbol: str, meta: dict):
    yahoo = meta["yahoo"]
    tf_results = {}

    for tf_key, (interval, period, label, weight) in TIMEFRAMES.items():
        if tf_key == "4h":
            df = get_candles_4h(yahoo)
        else:
            df = get_candles(yahoo, interval, period)

        result = score_df(df)
        if result:
            tf_results[tf_key] = result
        else:
            print(f"[{now()}] {symbol} {tf_key}: insufficient data")

    if len(tf_results) < 3:
        print(f"[{now()}] {symbol}: not enough timeframes scored")
        return None

    total_weight  = 0
    weighted_buy  = 0.0
    weighted_sell = 0.0
    atr_sum       = 0.0
    atr_count     = 0
    tf_summary    = {}

    for tf_key, (buy_conf, sell_conf, atr_now, buy_hits, sell_hits) in tf_results.items():
        _, _, label, weight = TIMEFRAMES[tf_key]
        weighted_buy  += buy_conf  * weight
        weighted_sell += sell_conf * weight
        total_weight  += weight
        atr_sum       += atr_now
        atr_count     += 1
        tf_summary[tf_key] = (label, buy_conf, sell_conf)

    avg_buy  = int(weighted_buy  / total_weight)
    avg_sell = int(weighted_sell / total_weight)
    avg_atr  = atr_sum / atr_count if atr_count else 0

    if avg_buy > avg_sell and avg_buy >= MIN_CONFIDENCE:
        direction  = "BUY"
        confidence = avg_buy
    elif avg_sell > avg_buy and avg_sell >= MIN_CONFIDENCE:
        direction  = "SELL"
        confidence = avg_sell
    else:
        print(f"[{now()}] {symbol}: No MTF signal (BUY {avg_buy}% / SELL {avg_sell}%)")
        return {"symbol": symbol, "label": meta["label"], "direction": "HOLD", "confidence": 0, "avg_buy": avg_buy, "avg_sell": avg_sell, "no_signal": True}


    top_reasons = []
    for tf_key in ["1d", "4h", "1h", "30m", "15m"]:
        if tf_key not in tf_results:
            continue
        buy_conf, sell_conf, _, buy_hits, sell_hits = tf_results[tf_key]
        _, _, label, _ = TIMEFRAMES[tf_key]
        hits = buy_hits if direction == "BUY" else sell_hits
        for h in hits:
            entry = f"{h} ({label})"
            if entry not in top_reasons:
                top_reasons.append(entry)
        if len(top_reasons) >= 4:
            break

    price = 0.0
    for tf_key in ["1h", "4h", "30m", "15m", "5m", "1m", "1d"]:
        if tf_key in tf_results:
            if tf_key == "4h":
                df_ref = get_candles_4h(yahoo)
            else:
                iv, pr, _, _ = TIMEFRAMES[tf_key]
                df_ref = get_candles(yahoo, iv, pr)
            if not df_ref.empty:
                price = float(df_ref["close"].iloc[-1])
            break

    sl_dist  = avg_atr * 1.5
    tp1_dist = sl_dist * 1.0
    tp2_dist = sl_dist * 2.0
    tp3_dist = sl_dist * 3.0

    def r(val): return round(val, 4)

    if direction == "BUY":
        sl  = r(price - sl_dist)
        tp1 = r(price + tp1_dist)
        tp2 = r(price + tp2_dist)
        tp3 = r(price + tp3_dist)
    else:
        sl  = r(price + sl_dist)
        tp1 = r(price - tp1_dist)
        tp2 = r(price - tp2_dist)
        tp3 = r(price - tp3_dist)

    risk = "LOW" if confidence >= 85 else "MEDIUM" if confidence >= 75 else "HIGH"

    return {
        "symbol":     symbol,
        "label":      meta["label"],
        "direction":  direction,
        "confidence": confidence,
        "entry":      r(price),
        "sl":         sl,
        "tp1":        tp1,
        "tp2":        tp2,
        "tp3":        tp3,
        "sl_dist":    r(sl_dist),
        "risk":       risk,
        "reasons":    top_reasons[:4],
        "tf_summary": tf_summary,
        "avg_buy":    avg_buy,
        "avg_sell":   avg_sell,
    }

# ── Format Telegram message ────────────────────────────────────────
def format_message(s: dict, meta: dict) -> str:
    arrow     = "🟢 BUY  ▲" if s["direction"] == "BUY" else "🔴 SELL ▼"
    risk_icon = "🟢" if s["risk"] == "LOW" else "🟡" if s["risk"] == "MEDIUM" else "🔴"
    conf_bar  = "█" * (s["confidence"] // 10) + "░" * (10 - s["confidence"] // 10)
    reasons   = "\n".join([f"  ✔ {r}" for r in s["reasons"]])

    tf_lines = []
    for tf_key in ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]:
        if tf_key not in s["tf_summary"]:
            continue
        label, buy_c, sell_c = s["tf_summary"][tf_key]
        if buy_c > sell_c:
            icon, pct = "🟢", buy_c
        elif sell_c > buy_c:
            icon, pct = "🔴", sell_c
        else:
            icon, pct = "⚪", buy_c
        tf_lines.append(f"  {label:<8} {icon} {pct}%")

    tf_table = "\n".join(tf_lines)
    pos_block = calc_position_size(s["entry"], s["sl"], meta)
    pos_section = f"\n{pos_block}\n" if pos_block else ""

    return f"""<b>━━━━━━━━━━━━━━━━━━━━━</b>
<b>{s['label']}</b>  |  {arrow}
<b>━━━━━━━━━━━━━━━━━━━━━</b>

📊 <b>Timeframe Alignment:</b>
<code>{tf_table}</code>

🎯 <b>Weighted Confidence: {s['confidence']}%</b>
<code>{conf_bar}</code>

📌 <b>Entry:</b>        <code>{s['entry']}</code>
🛑 <b>Stop Loss:</b>    <code>{s['sl']}</code>

🎯 <b>TP1:</b> <code>{s['tp1']}</code>  <i>1:1 — close ⅓, move SL to entry</i>
🎯 <b>TP2:</b> <code>{s['tp2']}</code>  <i>1:2 — close ⅓, move SL to TP1</i>
🎯 <b>TP3:</b> <code>{s['tp3']}</code>  <i>1:3 — close final ⅓</i>
{pos_section}
{risk_icon} <b>Risk Level:</b>  {s['risk']}

⚠️ <b>Risk Management:</b>
<code>  • Risk 1–2% of account per trade
  • Breakeven after TP1  |  Lock profit after TP2
  • Trail SL on final ⅓ if momentum holds</code>

💡 <b>Key signals:</b>
{reasons}

⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC · %d %b %Y')}
<b>━━━━━━━━━━━━━━━━━━━━━</b>""".strip()

# ── Main scan ──────────────────────────────────────────────────────
def run_scan():
    print(f"\n[{now()}] ── Starting MTF scan ──")
    sent = 0
    score_summary = []
    for symbol, meta in INSTRUMENTS.items():
        print("[" + now() + "] Analysing " + symbol + "...")
        result = analyse_mtf(symbol, meta)
        if result and not result.get("no_signal"):
            msg = format_message(result, meta)
            send_telegram(msg)
            append_history(result)
            sent += 1
            score_summary.append(meta["label"] + ": " + result["direction"] + " " + str(result["confidence"]) + "% SIGNAL")
            time.sleep(1.5)
        elif result:
            score_summary.append(meta["label"] + ": BUY " + str(result.get("avg_buy", 0)) + "% / SELL " + str(result.get("avg_sell", 0)) + "%")
        else:
            score_summary.append(meta["label"] + ": no data")

    send_telegram(
        "Scan complete\n"
        "========================\n" +
        "\n".join(score_summary) +
        "\n========================\n"
        "Signals: " + str(sent) + " | Threshold: " + str(MIN_CONFIDENCE) + "%\n"
        "Next scan in " + str(CHECK_INTERVAL_MINUTES) + " mins"
    )
    print("[" + now() + "] Scan complete.\n")

# ── Telegram command listener (long-polling) ──────────────────────
_scan_lock      = threading.Lock()
_last_update_id = 0

def poll_commands():
    global _last_update_id
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    print(f"[{now()}] Command listener ready. Commands: /scan /status /pairs")

    while True:
        try:
            r = requests.get(url, params={
                "offset":          _last_update_id + 1,
                "timeout":         30,
                "allowed_updates": ["message", "channel_post"],
            }, timeout=40)
            data = r.json()

            for update in data.get("result", []):
                _last_update_id = update["update_id"]
                msg  = update.get("message") or update.get("channel_post") or {}
                text = msg.get("text", "").strip().lower()
                chat = str(msg.get("chat", {}).get("id", ""))

                if chat != CHAT_ID:
                    continue

                if text in ("/scan", "/scan@sigsaucebot"):
                    print(f"[{now()}] /scan received")
                    send_telegram("🔍 <b>Manual scan triggered…</b> Analysing 7 timeframes per pair — give me a moment.")
                    threading.Thread(target=_safe_scan, daemon=True).start()

                elif text in ("/status", "/status@sigsaucebot"):
                    send_telegram(
                        f"✅ <b>SigSauceBot is running</b>\n\n"
                        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC · %d %b %Y')}\n"
                        f"🎯 Min confidence: <b>{MIN_CONFIDENCE}%</b>\n"
                        f"📊 Timeframes: 1m · 5m · 15m · 30m · 1h · 4h · 1d\n"
                        f"⏱ Auto-scan every <b>{CHECK_INTERVAL_MINUTES} mins</b>\n"
                        f"💰 Risk settings: <b>/risk</b>"
                    )

                elif text in ("/pairs", "/pairs@sigsaucebot"):
                    lines = "\n".join(
                        f"  {m['label']}  <code>{sym}</code>"
                        for sym, m in INSTRUMENTS.items()
                    )
                    send_telegram(
                        f"📡 <b>Watching {len(INSTRUMENTS)} instruments:</b>\n\n"
                        f"{lines}\n\n"
                        f"🎯 Signal fires at <b>{MIN_CONFIDENCE}%+</b> weighted confidence"
                    )

                elif text.startswith("/risk"):
                    _handle_risk_command(text)

                elif text in ("/history", "/history@sigsaucebot"):
                    hist = load_history()
                    if not hist:
                        send_telegram(
                            "📭 <b>No signal history yet.</b>\n\n"
                            "History builds up as signals are fired. "
                            "Use /scan to trigger a manual scan."
                        )
                    else:
                        lines = []
                        for i, h in enumerate(reversed(hist), 1):
                            arrow = "🟢 BUY" if h["direction"] == "BUY" else "🔴 SELL"
                            lines.append(
                                f"<b>{i}. {h['label']}</b>  {arrow}  {h['confidence']}%\n"
                                f"   📌 Entry: <code>{h['entry']}</code>  "
                                f"🛑 SL: <code>{h['sl']}</code>\n"
                                f"   🎯 TP1: <code>{h['tp1']}</code>  "
                                f"TP2: <code>{h['tp2']}</code>  "
                                f"TP3: <code>{h['tp3']}</code>\n"
                                f"   ⏰ {h['ts']}"
                            )
                        body = "\n\n".join(lines)
                        send_telegram(
                            f"📋 <b>Last {len(hist)} Signal{'s' if len(hist) > 1 else ''}</b> "
                            f"(newest first)\n"
                            f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                            f"{body}"
                        )

                elif text in ("/help", "/help@sigsaucebot"):
                    send_telegram(
                        "🤖 <b>SigSauceBot — Command Guide</b>\n\n"
                        "🔍 <b>/scan</b>\n"
                        "Trigger an instant scan right now across all 10 instruments and 7 timeframes.\n\n"
                        "📡 <b>/pairs</b>\n"
                        "List all instruments the bot is watching.\n\n"
                        "📊 <b>/status</b>\n"
                        "Check the bot is alive and see current settings.\n\n"
                        "💰 <b>/risk</b>\n"
                        "View your current risk settings.\n\n"
                        "💰 <b>/risk &lt;balance&gt;</b>\n"
                        "Set your account balance. Example: <code>/risk 10000</code>\n\n"
                        "💰 <b>/risk &lt;balance&gt; &lt;pct&gt;</b>\n"
                        "Set balance + risk %. Example: <code>/risk 10000 1.5</code>\n\n"
                        "💰 <b>/risk &lt;pct&gt;%</b>\n"
                        "Change risk % only. Example: <code>/risk 2%</code>\n\n"
                        "📋 <b>/history</b>\n"
                        "Show the last 10 signals fired — newest first.\n\n"
                        "❓ <b>/help</b>\n"
                        "Show this message.\n\n"
                        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
                        "📐 <b>How signals work:</b>\n"
                        "Each pair is scored across <b>7 timeframes</b> (1m → Daily) using "
                        "<b>8 technical indicators</b>. Higher timeframes carry more weight. "
                        f"Signal fires at <b>{MIN_CONFIDENCE}%+</b> weighted confidence."
                    )

        except Exception as e:
            print(f"[{now()}] Poll error: {e}")
            time.sleep(5)

def _handle_risk_command(text: str):
    global _config
    parts = text.strip().split()

    if len(parts) == 1:
        bal  = _config.get("balance")
        pct  = _config.get("risk_pct", 1.0)
        bal_str  = f"${bal:,.0f}" if bal else "not set"
        risk_amt = f"${bal * pct / 100:,.2f}" if bal else "—"
        send_telegram(
            f"💰 <b>Risk Settings</b>\n\n"
            f"  Balance:   <code>{bal_str}</code>\n"
            f"  Risk %:    <code>{pct:.4g}%</code>\n"
            f"  Per trade: <code>{risk_amt}</code>\n\n"
            f"To update:\n"
            f"  <code>/risk 10000</code>  — set balance\n"
            f"  <code>/risk 10000 1.5</code>  — balance + risk %\n"
            f"  <code>/risk 2%</code>  — risk % only"
        )
        return

    try:
        if len(parts) == 2 and parts[1].endswith("%"):
            pct = float(parts[1].rstrip("%"))
            _config["risk_pct"] = pct
            save_config(_config)
            bal = _config.get("balance")
            risk_amt = f"${bal * pct / 100:,.2f}" if bal else "—"
            send_telegram(
                f"✅ Risk % updated to <b>{pct:.4g}%</b>\n"
                f"Per-trade risk: <code>{risk_amt}</code>"
            )
            return

        balance = float(parts[1].replace(",", ""))
        pct     = float(parts[2]) if len(parts) >= 3 else _config.get("risk_pct", 1.0)
        _config["balance"]  = balance
        _config["risk_pct"] = pct
        save_config(_config)
        risk_amt = balance * pct / 100
        send_telegram(
            f"✅ <b>Risk settings saved</b>\n\n"
            f"  Balance:   <code>${balance:,.0f}</code>\n"
            f"  Risk %:    <code>{pct:.4g}%</code>\n"
            f"  Per trade: <code>${risk_amt:,.2f}</code>\n\n"
            f"Position sizes will now appear in every signal. 📊"
        )

    except (ValueError, IndexError):
        send_telegram(
            "⚠️ <b>Invalid format.</b> Try:\n\n"
            "  <code>/risk 10000</code>\n"
            "  <code>/risk 10000 1.5</code>\n"
            "  <code>/risk 2%</code>"
        )

def _safe_scan():
    if _scan_lock.acquire(blocking=False):
        try:
            run_scan()
        finally:
            _scan_lock.release()
    else:
        send_telegram("⚠️ A scan is already running — please wait.")
def self_ping():
    while True:
        try:
            requests.get("https://sigsaucebot.onrender.com")
        except:
            pass
        time.sleep(270)

# ── Startup ────────────────────────────────────────────────────────
def startup():
    send_telegram(f"""🤖 <b>SigSauceBot — Multi-Timeframe Edition</b>

📡 <b>Watching 10 instruments:</b>
XAUUSD · XAGUSD · NAS100 · SPX500
EURUSD · GBPUSD · USDJPY · GBPJPY · EURGBP · NDXUSD

📊 <b>Timeframes:</b> 1m · 5m · 15m · 30m · 1h · 4h · 1d
🎯 Min weighted confidence: <b>{MIN_CONFIDENCE}%</b>
🔍 8 Indicators per timeframe
⏱ Auto-scan every <b>{CHECK_INTERVAL_MINUTES} minutes</b>
💬 Commands: /scan · /status · /pairs · /risk · /history · /help

First scan starting now…
⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC · %d %b %Y')}""".strip())
    run_scan()

# ── Entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  SigSauceBot — Multi-Timeframe Edition")
    print("=" * 50)

    threading.Thread(target=run_server,    daemon=True).start()
    print(f"[{now()}] Web server started on port {PORT}")

    threading.Thread(target=poll_commands, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()

    startup()

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_scan)

    print(f"[{now()}] Bot running. Scanning every {CHECK_INTERVAL_MINUTES} mins.")
    while True:
        schedule.run_pending()
        time.sleep(10)
