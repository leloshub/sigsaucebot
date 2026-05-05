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

# CONFIG

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
PORT      = int(os.environ.get("PORT", 5000))

CHECK_INTERVAL_MINUTES = 15
MIN_CONFIDENCE         = 75

INSTRUMENTS = {
“XAUUSD”: {“yahoo”: “GC=F”,      “label”: “Gold”,       “pip”: 0.01,   “type”: “metal”,  “unit”: “oz”},
“XAGUSD”: {“yahoo”: “SI=F”,      “label”: “Silver”,     “pip”: 0.001,  “type”: “metal”,  “unit”: “oz”},
“NAS100”: {“yahoo”: “NQ=F”,      “label”: “NASDAQ 100”, “pip”: 0.25,   “type”: “index”,  “unit”: “units”},
“SPX500”: {“yahoo”: “ES=F”,      “label”: “S&P 500”,    “pip”: 0.25,   “type”: “index”,  “unit”: “units”},
“EURUSD”: {“yahoo”: “EURUSD=X”,  “label”: “EUR/USD”,    “pip”: 0.0001, “type”: “forex”,  “unit”: “lots”},
“GBPUSD”: {“yahoo”: “GBPUSD=X”,  “label”: “GBP/USD”,    “pip”: 0.0001, “type”: “forex”,  “unit”: “lots”},
“USDJPY”: {“yahoo”: “JPY=X”,     “label”: “USD/JPY”,    “pip”: 0.01,   “type”: “forex”,  “unit”: “lots”},
“GBPJPY”: {“yahoo”: “GBPJPY=X”,  “label”: “GBP/JPY”,    “pip”: 0.01,   “type”: “forex”,  “unit”: “lots”},
“EURGBP”: {“yahoo”: “EURGBP=X”,  “label”: “EUR/GBP”,    “pip”: 0.0001, “type”: “forex”,  “unit”: “lots”},
“EURJPY”: {“yahoo”: “EURJPY=X”,  “label”: “EUR/JPY”,    “pip”: 0.01,   “type”: “forex”,  “unit”: “lots”},
}

TIMEFRAMES = {
“1m”:  (“1m”,  “1d”,  “1 Min”,  1),
“5m”:  (“5m”,  “5d”,  “5 Min”,  1),
“15m”: (“15m”, “5d”,  “15 Min”, 2),
“30m”: (“30m”, “1mo”, “30 Min”, 2),
“1h”:  (“1h”,  “1mo”, “1 Hour”, 3),
“4h”:  (“60m”, “3mo”, “4 Hour”, 4),
“1d”:  (“1d”,  “1y”,  “Daily”,  5),
}

CONFIG_FILE  = “config.json”
HISTORY_FILE = “history.json”
MAX_HISTORY  = 10

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {“balance”: None, “risk_pct”: 1.0}

def save_config(cfg):
try:
with open(CONFIG_FILE, “w”) as f:
json.dump(cfg, f)
except Exception as e:
print(”[” + now() + “] Config save error: “ + str(e))

def load_history():
try:
with open(HISTORY_FILE) as f:
return json.load(f)
except Exception:
return []

def save_history(hist):
try:
with open(HISTORY_FILE, “w”) as f:
json.dump(hist[-MAX_HISTORY:], f)
except Exception as e:
print(”[” + now() + “] History save error: “ + str(e))

def append_history(result):
hist = load_history()
hist.append({
“ts”:         datetime.now(timezone.utc).strftime(”%d %b %Y %H:%M UTC”),
“symbol”:     result[“symbol”],
“label”:      result[“label”],
“direction”:  result[“direction”],
“confidence”: result[“confidence”],
“entry”:      result[“entry”],
“sl”:         result[“sl”],
“tp1”:        result[“tp1”],
“tp2”:        result[“tp2”],
“tp3”:        result[“tp3”],
})
save_history(hist)

_config = load_config()

def calc_position_size(entry, sl, meta):
balance  = _config.get(“balance”)
risk_pct = _config.get(“risk_pct”, 1.0)
if not balance or balance <= 0:
return None
risk_dollars = balance * (risk_pct / 100)
sl_distance  = abs(entry - sl)
if sl_distance == 0:
return None
inst_type  = meta.get(“type”, “forex”)
unit_label = meta[“unit”]
if inst_type == “forex”:
pip_size = meta[“pip”]
sl_pips  = sl_distance / pip_size
pip_val  = 10.0
lots     = risk_dollars / (sl_pips * pip_val)
lots     = round(lots, 2)
micro    = round(lots * 100)
return (
“<b>Position Size</b> (” + str(risk_pct) + “% of $” + str(int(balance)) + “):\n”
“<code>  Risk:  $” + str(round(risk_dollars, 2)) + “\n”
“  Size:  “ + str(lots) + “ “ + unit_label + “ (” + str(int(micro)) + “ micro-lots)\n”
“  SL:    “ + str(round(sl_pips, 1)) + “ pips</code>”
)
else:
units = risk_dollars / sl_distance
return (
“<b>Position Size</b> (” + str(risk_pct) + “% of $” + str(int(balance)) + “):\n”
“<code>  Risk:  $” + str(round(risk_dollars, 2)) + “\n”
“  Size:  “ + str(round(units, 2)) + “ “ + unit_label + “\n”
“  SL:    “ + str(round(sl_distance, 4)) + “ pts</code>”
)

# Web server for uptime

app = Flask(**name**)

@app.route(”/”)
def home():
return “SigSauceBot is LIVE - “ + now() + “ UTC”, 200

def run_server():
app.run(host=“0.0.0.0”, port=PORT)

def now():
return datetime.now(timezone.utc).strftime(”%H:%M:%S”)

def send_telegram(message):
url = “https://api.telegram.org/bot” + BOT_TOKEN + “/sendMessage”
try:
r = requests.post(url, json={
“chat_id”:    CHAT_ID,
“text”:       message,
“parse_mode”: “HTML”
}, timeout=10)
data = r.json()
if data.get(“ok”):
print(”[” + now() + “] Telegram sent”)
else:
print(”[” + now() + “] Telegram error: “ + str(data.get(“description”)))
except Exception as e:
print(”[” + now() + “] Telegram error: “ + str(e))

def get_candles(yahoo_symbol, interval, period):
url = (“https://query1.finance.yahoo.com/v8/finance/chart/” + yahoo_symbol +
“?interval=” + interval + “&range=” + period)
headers = {“User-Agent”: “Mozilla/5.0”}
try:
r = requests.get(url, headers=headers, timeout=15)
data   = r.json()
result = data[“chart”][“result”][0]
ohlcv  = result[“indicators”][“quote”][0]
df = pd.DataFrame({
“time”:   pd.to_datetime(result[“timestamp”], unit=“s”, utc=True),
“open”:   ohlcv[“open”],
“high”:   ohlcv[“high”],
“low”:    ohlcv[“low”],
“close”:  ohlcv[“close”],
“volume”: ohlcv[“volume”],
}).dropna()
return df
except Exception as e:
print(”[” + now() + “] Price error (” + yahoo_symbol + “ “ + interval + “): “ + str(e))
return pd.DataFrame()

def get_candles_4h(yahoo_symbol):
df = get_candles(yahoo_symbol, “60m”, “3mo”)
if df.empty:
return df
df = df.set_index(“time”)
df4 = df.resample(“4h”).agg({
“open”:   “first”,
“high”:   “max”,
“low”:    “min”,
“close”:  “last”,
“volume”: “sum”,
}).dropna().reset_index()
return df4

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
hl = df[“high”] - df[“low”]
hc = (df[“high”] - df[“close”].shift()).abs()
lc = (df[“low”]  - df[“close”].shift()).abs()
tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
return tr.rolling(period).mean()

def calc_macd(series):
fast   = calc_ema(series, 12)
slow   = calc_ema(series, 26)
macd   = fast - slow
signal = calc_ema(macd, 9)
return macd, signal, macd - signal

def calc_adx(df, period=14):
high     = df[“high”]
low      = df[“low”]
plus_dm  = high.diff().clip(lower=0)
minus_dm = (-low.diff()).clip(lower=0)
plus_dm[plus_dm < minus_dm]  = 0
minus_dm[minus_dm < plus_dm] = 0
atr      = calc_atr(df, period)
plus_di  = 100 * plus_dm.rolling(period).mean()  / atr.replace(0, np.nan)
minus_di = 100 * minus_dm.rolling(period).mean() / atr.replace(0, np.nan)
dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
return dx.rolling(period).mean(), plus_di, minus_di

def calc_stochastic(df, k=14, d=3):
low_min  = df[“low”].rolling(k).min()
high_max = df[“high”].rolling(k).max()
k_pct    = 100 * (df[“close”] - low_min) / (high_max - low_min).replace(0, np.nan)
return k_pct, k_pct.rolling(d).mean()

def detect_candle_pattern(df):
o = df[“open”]
h = df[“high”]
l = df[“low”]
c = df[“close”]
body        = (c - o).abs()
bull_engulf = (c > o) & (o.shift(1) > c.shift(1)) & (c > o.shift(1)) & (o < c.shift(1))
bear_engulf = (o > c) & (c.shift(1) > o.shift(1)) & (o > c.shift(1)) & (c < o.shift(1))
lower_wick  = o.where(o < c, c) - l
upper_wick  = h - c.where(c > o, o)
hammer      = (lower_wick > 2 * body) & (upper_wick < body * 0.3) & (c > o)
shooting    = (upper_wick > 2 * body) & (lower_wick < body * 0.3) & (o > c)
return {
“bull_engulf”: bool(bull_engulf.iloc[-1]),
“bear_engulf”: bool(bear_engulf.iloc[-1]),
“hammer”:      bool(hammer.iloc[-1]),
“shooting”:    bool(shooting.iloc[-1]),
}

def find_support_resistance(df, lookback=20):
price = float(df[“close”].iloc[-1])
highs = float(df[“high”].rolling(lookback).max().iloc[-1])
lows  = float(df[“low”].rolling(lookback).min().iloc[-1])
return abs(price - lows) / price < 0.005, abs(price - highs) / price < 0.005

def score_df(df):
if df.empty or len(df) < 30:
return None

```
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

buy_score = 0
buy_hits  = []

if rsi_now < 35:
    buy_score += 15
    buy_hits.append("RSI oversold (" + str(int(rsi_now)) + ")")
elif rsi_now < 45 and rsi_now > rsi_prev:
    buy_score += 8
    buy_hits.append("RSI recovering (" + str(int(rsi_now)) + ")")
if price > ema20_now > ema50_now:
    buy_score += 15
    buy_hits.append("EMA uptrend aligned")
elif price > ema20_now:
    buy_score += 7
if price > ema200_now:
    buy_score += 10
    buy_hits.append("Above EMA200")
if price <= bb_lo * 1.002:
    buy_score += 15
    buy_hits.append("At lower Bollinger Band")
elif price < bb_mid_now:
    buy_score += 5
if macd_now > macd_sig_now and macd_h_now > macd_h_prev:
    buy_score += 15
    buy_hits.append("MACD bullish crossover")
elif macd_h_now > macd_h_prev:
    buy_score += 7
if adx_now > 25 and plus_di_now > minus_di_now:
    buy_score += 10
    buy_hits.append("Strong bullish trend (ADX " + str(int(adx_now)) + ")")
if stoch_k_now < 20 and stoch_k_now > stoch_d_now:
    buy_score += 10
    buy_hits.append("Stochastic oversold crossup")
if patterns["bull_engulf"]:
    buy_score += 10
    buy_hits.append("Bullish engulfing")
if patterns["hammer"]:
    buy_score += 8
    buy_hits.append("Hammer pattern")
if near_support:
    buy_score += 7
    buy_hits.append("Near support")
if vol_surge and price > prev:
    buy_score += 5
    buy_hits.append("Volume surge up")

sell_score = 0
sell_hits  = []

if rsi_now > 65:
    sell_score += 15
    sell_hits.append("RSI overbought (" + str(int(rsi_now)) + ")")
elif rsi_now > 55 and rsi_now < rsi_prev:
    sell_score += 8
    sell_hits.append("RSI turning down (" + str(int(rsi_now)) + ")")
if price < ema20_now < ema50_now:
    sell_score += 15
    sell_hits.append("EMA downtrend aligned")
elif price < ema20_now:
    sell_score += 7
if price < ema200_now:
    sell_score += 10
    sell_hits.append("Below EMA200")
if price >= bb_up * 0.998:
    sell_score += 15
    sell_hits.append("At upper Bollinger Band")
elif price > bb_mid_now:
    sell_score += 5
if macd_now < macd_sig_now and macd_h_now < macd_h_prev:
    sell_score += 15
    sell_hits.append("MACD bearish crossover")
elif macd_h_now < macd_h_prev:
    sell_score += 7
if adx_now > 25 and minus_di_now > plus_di_now:
    sell_score += 10
    sell_hits.append("Strong bearish trend (ADX " + str(int(adx_now)) + ")")
if stoch_k_now > 80 and stoch_k_now < stoch_d_now:
    sell_score += 10
    sell_hits.append("Stochastic overbought crossdown")
if patterns["bear_engulf"]:
    sell_score += 10
    sell_hits.append("Bearish engulfing")
if patterns["shooting"]:
    sell_score += 8
    sell_hits.append("Shooting star")
if near_resist:
    sell_score += 7
    sell_hits.append("Near resistance")
if vol_surge and price < prev:
    sell_score += 5
    sell_hits.append("Volume surge down")

max_score = 120
buy_conf  = min(int(buy_score  / max_score * 100), 100)
sell_conf = min(int(sell_score / max_score * 100), 100)

return buy_conf, sell_conf, atr_now, buy_hits[:3], sell_hits[:3]
```

def analyse_mtf(symbol, meta):
yahoo      = meta[“yahoo”]
tf_results = {}

```
for tf_key, (interval, period, label, weight) in TIMEFRAMES.items():
    if tf_key == "4h":
        df = get_candles_4h(yahoo)
    else:
        df = get_candles(yahoo, interval, period)
    result = score_df(df)
    if result:
        tf_results[tf_key] = result
    else:
        print("[" + now() + "] " + symbol + " " + tf_key + ": insufficient data")

if len(tf_results) < 3:
    print("[" + now() + "] " + symbol + ": not enough timeframes")
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
    print("[" + now() + "] " + symbol + ": No signal (BUY " + str(avg_buy) + "% / SELL " + str(avg_sell) + "%)")
    return None

top_reasons = []
for tf_key in ["1d", "4h", "1h", "30m", "15m"]:
    if tf_key not in tf_results:
        continue
    buy_conf, sell_conf, _, buy_hits, sell_hits = tf_results[tf_key]
    _, _, label, _ = TIMEFRAMES[tf_key]
    hits = buy_hits if direction == "BUY" else sell_hits
    for h in hits:
        entry = h + " (" + label + ")"
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

def r(val):
    return round(val, 4)

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
```

def format_message(s, meta):
arrow     = “BUY” if s[“direction”] == “BUY” else “SELL”
arrow_sym = “UP” if s[“direction”] == “BUY” else “DOWN”
risk_icon = “LOW” if s[“risk”] == “LOW” else “MEDIUM” if s[“risk”] == “MEDIUM” else “HIGH”
conf_bar  = “#” * (s[“confidence”] // 10) + “.” * (10 - s[“confidence”] // 10)
reasons   = “\n”.join([”  + “ + r for r in s[“reasons”]])

```
tf_lines = []
for tf_key in ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]:
    if tf_key not in s["tf_summary"]:
        continue
    label, buy_c, sell_c = s["tf_summary"][tf_key]
    if buy_c > sell_c:
        icon = "BUY"
        pct  = buy_c
    elif sell_c > buy_c:
        icon = "SELL"
        pct  = sell_c
    else:
        icon = "FLAT"
        pct  = buy_c
    tf_lines.append("  " + label + " " + icon + " " + str(pct) + "%")

tf_table    = "\n".join(tf_lines)
pos_block   = calc_position_size(s["entry"], s["sl"], meta)
pos_section = "\n" + pos_block + "\n" if pos_block else ""

msg = (
    "<b>========================</b>\n"
    "<b>" + s["label"] + " | " + arrow + " " + arrow_sym + "</b>\n"
    "<b>========================</b>\n\n"
    "<b>Timeframe Alignment:</b>\n"
    "<code>" + tf_table + "</code>\n\n"
    "<b>Confidence: " + str(s["confidence"]) + "%</b>\n"
    "<code>" + conf_bar + "</code>\n\n"
    "<b>Entry:</b>      <code>" + str(s["entry"]) + "</code>\n"
    "<b>Stop Loss:</b>  <code>" + str(s["sl"]) + "</code>\n\n"
    "<b>TP1:</b> <code>" + str(s["tp1"]) + "</code>  1:1 - close 1/3, move SL to entry\n"
    "<b>TP2:</b> <code>" + str(s["tp2"]) + "</code>  1:2 - close 1/3, move SL to TP1\n"
    "<b>TP3:</b> <code>" + str(s["tp3"]) + "</code>  1:3 - close final 1/3\n"
    + pos_section +
    "\n<b>Risk Level:</b> " + risk_icon + "\n\n"
    "<b>Risk Management:</b>\n"
    "<code>  - Risk 1-2% of account per trade\n"
    "  - Breakeven after TP1\n"
    "  - Lock profit after TP2\n"
    "  - Trail SL on final 1/3</code>\n\n"
    "<b>Key signals:</b>\n" + reasons + "\n\n"
    "<i>" + datetime.now(timezone.utc).strftime("%H:%M UTC - %d %b %Y") + "</i>\n"
    "<b>========================</b>"
)
return msg
```

def run_scan():
print(”\n[” + now() + “] Starting MTF scan…”)
sent = 0
for symbol, meta in INSTRUMENTS.items():
print(”[” + now() + “] Analysing “ + symbol + “ across 7 timeframes…”)
result = analyse_mtf(symbol, meta)
if result:
msg = format_message(result, meta)
send_telegram(msg)
append_history(result)
sent += 1
time.sleep(1.5)
if sent == 0:
print(”[” + now() + “] No signals above “ + str(MIN_CONFIDENCE) + “% this scan.”)
else:
send_telegram(
“Scan done - <b>” + str(sent) + “ signal” + (“s” if sent > 1 else “”) + “</b> sent. “
“Next scan in “ + str(CHECK_INTERVAL_MINUTES) + “ mins.”
)
print(”[” + now() + “] Scan complete.\n”)

_scan_lock      = threading.Lock()
_last_update_id = 0

def poll_commands():
global _last_update_id
url = “https://api.telegram.org/bot” + BOT_TOKEN + “/getUpdates”
print(”[” + now() + “] Command listener ready”)

```
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

            if "/scan" in text:
                send_telegram("Manual scan triggered - analysing all pairs now...")
                threading.Thread(target=_safe_scan, daemon=True).start()

            elif "/status" in text:
                send_telegram(
                    "SigSauceBot is running\n\n"
                    "Time: " + datetime.now(timezone.utc).strftime("%H:%M UTC - %d %b %Y") + "\n"
                    "Min confidence: <b>" + str(MIN_CONFIDENCE) + "%</b>\n"
                    "Timeframes: 1m, 5m, 15m, 30m, 1h, 4h, 1d\n"
                    "Auto-scan every <b>" + str(CHECK_INTERVAL_MINUTES) + " mins</b>"
                )

            elif "/pairs" in text:
                lines = "\n".join(
                    "  " + m["label"] + " - " + sym
                    for sym, m in INSTRUMENTS.items()
                )
                send_telegram(
                    "Watching <b>" + str(len(INSTRUMENTS)) + " instruments:</b>\n\n" +
                    lines + "\n\nSignal fires at <b>" + str(MIN_CONFIDENCE) + "%+</b> confidence"
                )

            elif "/risk" in text:
                _handle_risk_command(text)

            elif "/history" in text:
                hist = load_history()
                if not hist:
                    send_telegram("No signal history yet. Use /scan to trigger a scan.")
                else:
                    lines = []
                    for i, h in enumerate(reversed(hist), 1):
                        lines.append(
                            "<b>" + str(i) + ". " + h["label"] + "</b>  " + h["direction"] + "  " + str(h["confidence"]) + "%\n"
                            "   Entry: <code>" + str(h["entry"]) + "</code>  "
                            "SL: <code>" + str(h["sl"]) + "</code>\n"
                            "   TP1: <code>" + str(h["tp1"]) + "</code>  "
                            "TP2: <code>" + str(h["tp2"]) + "</code>  "
                            "TP3: <code>" + str(h["tp3"]) + "</code>\n"
                            "   " + h["ts"]
                        )
                    send_telegram(
                        "<b>Last " + str(len(hist)) + " Signals</b>\n\n" +
                        "\n\n".join(lines)
                    )

            elif "/help" in text:
                send_telegram(
                    "<b>SigSauceBot Commands</b>\n\n"
                    "/scan - trigger instant scan\n"
                    "/pairs - list all instruments\n"
                    "/status - check bot is alive\n"
                    "/risk - view risk settings\n"
                    "/risk 10000 - set balance\n"
                    "/risk 10000 1.5 - set balance and risk %\n"
                    "/risk 2% - set risk % only\n"
                    "/history - last 10 signals\n"
                    "/help - show this message"
                )

    except Exception as e:
        print("[" + now() + "] Poll error: " + str(e))
        time.sleep(5)
```

def _handle_risk_command(text):
global _config
parts = text.strip().split()

```
if len(parts) == 1:
    bal     = _config.get("balance")
    pct     = _config.get("risk_pct", 1.0)
    bal_str = "$" + str(int(bal)) if bal else "not set"
    risk_amt = "$" + str(round(bal * pct / 100, 2)) if bal else "not set"
    send_telegram(
        "<b>Risk Settings</b>\n\n"
        "  Balance:   <code>" + bal_str + "</code>\n"
        "  Risk %:    <code>" + str(pct) + "%</code>\n"
        "  Per trade: <code>" + risk_amt + "</code>\n\n"
        "To update:\n"
        "  /risk 10000\n"
        "  /risk 10000 1.5\n"
        "  /risk 2%"
    )
    return

try:
    if len(parts) == 2 and parts[1].endswith("%"):
        pct = float(parts[1].rstrip("%"))
        _config["risk_pct"] = pct
        save_config(_config)
        bal      = _config.get("balance")
        risk_amt = "$" + str(round(bal * pct / 100, 2)) if bal else "not set"
        send_telegram("Risk % updated to <b>" + str(pct) + "%</b>\nPer-trade risk: <code>" + risk_amt + "</code>")
        return

    balance = float(parts[1].replace(",", ""))
    pct     = float(parts[2]) if len(parts) >= 3 else _config.get("risk_pct", 1.0)
    _config["balance"]  = balance
    _config["risk_pct"] = pct
    save_config(_config)
    risk_amt = balance * pct / 100
    send_telegram(
        "<b>Risk settings saved</b>\n\n"
        "  Balance:   <code>$" + str(int(balance)) + "</code>\n"
        "  Risk %:    <code>" + str(pct) + "%</code>\n"
        "  Per trade: <code>$" + str(round(risk_amt, 2)) + "</code>"
    )

except Exception:
    send_telegram(
        "Invalid format. Try:\n"
        "  /risk 10000\n"
        "  /risk 10000 1.5\n"
        "  /risk 2%"
    )
```

def _safe_scan():
if _scan_lock.acquire(blocking=False):
try:
run_scan()
finally:
_scan_lock.release()
else:
send_telegram(“A scan is already running - please wait.”)

def startup():
send_telegram(
“<b>SigSauceBot is LIVE</b>\n\n”
“Watching 10 instruments:\n”
“XAUUSD, XAGUSD, NAS100, SPX500\n”
“EURUSD, GBPUSD, USDJPY, GBPJPY, EURGBP, EURJPY\n\n”
“Timeframes: 1m, 5m, 15m, 30m, 1h, 4h, 1d\n”
“Min confidence: <b>” + str(MIN_CONFIDENCE) + “%</b>\n”
“Auto-scan every <b>” + str(CHECK_INTERVAL_MINUTES) + “ minutes</b>\n”
“Commands: /scan /status /pairs /risk /history /help\n\n”
“First scan starting now…\n”
+ datetime.now(timezone.utc).strftime(”%H:%M UTC - %d %b %Y”)
)
run_scan()

if **name** == “**main**”:
print(“SigSauceBot starting…”)

```
if not BOT_TOKEN or not CHAT_ID:
    print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables required!")
    exit(1)

threading.Thread(target=run_server,    daemon=True).start()
print("[" + now() + "] Web server started on port " + str(PORT))

threading.Thread(target=poll_commands, daemon=True).start()

startup()

schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_scan)

print("[" + now() + "] Bot running. Scanning every " + str(CHECK_INTERVAL_MINUTES) + " mins.")
while True:
    schedule.run_pending()
    time.sleep(10)
```
