import asyncio
asyncio.set_event_loop(asyncio.new_event_loop())
from flask import Flask, jsonify
import json, time, threading, os
from datetime import datetime
import pytz
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()  # reads ALPACA_API_KEY / ALPACA_SECRET_KEY from a local .env file

app = Flask(__name__)

# ── Alpaca API ──
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    raise SystemExit(
        "Missing Alpaca credentials. Copy .env.example to .env and fill in "
        "ALPACA_API_KEY and ALPACA_SECRET_KEY."
    )

ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type":        "application/json"
}

# ── Config ──
TRADE_AMOUNT  = 10.0
POLL_INTERVAL = 30
STOP_LOSS_PCT = 0.15

# ── Indicator Settings ──
EMA_FAST_LEN = 9
EMA_SLOW_LEN = 21
RSI_LEN      = 14
MACD_FAST    = 12
MACD_SLOW    = 26
MACD_SIGNAL  = 9
BAR_LIMIT    = 100

# ── Sell Signal Guards ──
MIN_HOLD_SECONDS = 300
MIN_LOSS_PCT     = -0.005
MIN_GAIN_PCT     =  0.010

# ── Market Hours (kept here — DO NOT remove) ──
def is_market_open():
    ny_tz = pytz.timezone("America/New_York")
    now = datetime.now(ny_tz)
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now <= market_close

# ── Manual Indicator Calculations ──
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss  = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast    = calc_ema(series, fast)
    ema_slow    = calc_ema(series, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    return macd_line, signal_line

# ── Live Data from Alpaca IEX ──
def get_live_data(symbol):
    try:
        response = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars",
            headers=HEADERS,
            params={
                "timeframe": "1Min",
                "limit":     BAR_LIMIT,
                "feed":      "iex",
                "sort":      "asc"
            }
        )

        if response.status_code != 200:
            print(f"Data fetch error for {symbol}: {response.text}")
            return None

        bars = response.json().get("bars", [])
        if not bars or len(bars) < MACD_SLOW + MACD_SIGNAL:
            print(f"Not enough bars for {symbol}")
            return None

        df = pd.DataFrame(bars)
        df.rename(columns={"c": "close"}, inplace=True)
        df["close"] = df["close"].astype(float)

        close = df["close"]

        ema_fast               = calc_ema(close, EMA_FAST_LEN).iloc[-1]
        ema_slow               = calc_ema(close, EMA_SLOW_LEN).iloc[-1]
        rsi                    = calc_rsi(close, RSI_LEN).iloc[-1]
        macd_line, signal_line = calc_macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        macd                   = macd_line.iloc[-1]
        macd_sig               = signal_line.iloc[-1]
        price                  = close.iloc[-1]

        return {
            "price":    price,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "rsi":      rsi,
            "macd":     macd,
            "macd_sig": macd_sig
        }

    except Exception as e:
        print(f"get_live_data error for {symbol}: {e}")
        return None

# ── Order Placement ──
def place_order(side, symbol):
    try:
        if side.upper() == "BUY":
            payload = {
                "symbol":        symbol,
                "notional":      str(TRADE_AMOUNT),
                "side":          "buy",
                "type":          "market",
                "time_in_force": "day"
            }
            print(f"BUY order placed for {symbol} | ${TRADE_AMOUNT}")
        else:
            pos_resp = requests.get(
                f"{ALPACA_BASE_URL}/v2/positions/{symbol}",
                headers=HEADERS
            )
            if pos_resp.status_code != 200:
                print(f"No position found for {symbol}")
                return None
            qty = pos_resp.json().get("qty", "0")
            payload = {
                "symbol":        symbol,
                "qty":           qty,
                "side":          "sell",
                "type":          "market",
                "time_in_force": "day"
            }
            print(f"SELL order placed for {symbol} | {qty} shares")

        response = requests.post(
            f"{ALPACA_BASE_URL}/v2/orders",
            headers=HEADERS,
            json=payload
        )
        if response.status_code in [200, 201]:
            print(f"Order confirmed: {response.json().get('id')}")
        else:
            print(f"Order error: {response.text}")
        return response

    except Exception as e:
        print(f"Order error: {e}")
        return None

# ── Watchlist Loader ──
def load_watchlist():
    watchlist_path = r"E:\trading-agent\Trading\agent\watchlist.json"
    try:
        if os.path.exists(watchlist_path):
            with open(watchlist_path, "r") as f:
                data      = json.load(f)
                watchlist = data.get("watchlist", [])
                updated   = data.get("updated", "unknown")
                if watchlist:
                    print(f"Loaded watchlist (updated {updated}): {watchlist}")
                    return watchlist
    except Exception as e:
        print(f"Could not load watchlist: {e}")
    print("Using default watchlist")
    return ["PLTR", "AMD", "F", "SOFI", "BAC"]

# ── Account Info ──
def get_account():
    try:
        response = requests.get(f"{ALPACA_BASE_URL}/v2/account", headers=HEADERS)
        return response.json()
    except:
        return {}

# ── Main Poll Loop ──
def poll_loop():
    watchlist = load_watchlist()
    print(f"Scanning {watchlist} every {POLL_INTERVAL}s using Alpaca live IEX data...")

    in_position    = False
    current_symbol = None
    entry_price    = None
    entry_time     = None

    while True:
        # ── Market hours check DISABLED for testing ──
        # Uncomment the block below before going live
        # if not is_market_open():
        #     ny_tz = pytz.timezone("America/New_York")
        #     now = datetime.now(ny_tz)
        #     print(f"Market closed | NY Time: {now.strftime('%H:%M')} | Waiting...")
        #     time.sleep(60)
        #     continue

        for symbol in watchlist:
            data = get_live_data(symbol)

            if not data:
                print(f"Skipping {symbol} — could not fetch live data")
                continue

            price    = data["price"]
            ema_fast = data["ema_fast"]
            ema_slow = data["ema_slow"]
            rsi      = data["rsi"]
            macd     = data["macd"]
            macd_sig = data["macd_sig"]

            time_held = (time.time() - entry_time) if entry_time else 0
            pnl_pct   = ((price - entry_price) / entry_price) if entry_price else 0

            print(
                f"{symbol} | Price:{price:.2f} | EMA_F:{ema_fast:.2f} EMA_S:{ema_slow:.2f} | "
                f"RSI:{rsi:.1f} | MACD:{macd:.4f} Sig:{macd_sig:.4f} | "
                f"InPos:{in_position} ({current_symbol}) | "
                f"HeldSecs:{int(time_held)} | PnL:{pnl_pct*100:.2f}%"
            )

            # ── Hard Stop Loss ──
            if in_position and current_symbol == symbol and entry_price:
                drop_pct = (entry_price - price) / entry_price
                if drop_pct >= STOP_LOSS_PCT:
                    print(f"HARD STOP triggered on {symbol} — {drop_pct*100:.1f}% drop from entry {entry_price:.2f}")
                    place_order("sell", symbol)
                    in_position    = False
                    current_symbol = None
                    entry_price    = None
                    entry_time     = None
                    break

            # ── Buy Signal ──
            buy_signal = (
                not in_position and
                price > ema_fast > ema_slow and
                45 < rsi < 65 and
                macd > macd_sig
            )

            # ── Sell Signal (all 3 fixes applied) ──
            indicators_say_sell = (price < ema_fast and macd < macd_sig) or rsi > 70
            pnl_threshold_met   = (pnl_pct <= MIN_LOSS_PCT or pnl_pct >= MIN_GAIN_PCT)

            sell_signal = (
                in_position and
                current_symbol == symbol and
                time_held >= MIN_HOLD_SECONDS and
                pnl_threshold_met and
                indicators_say_sell
            )

            if buy_signal:
                place_order("buy", symbol)
                in_position    = True
                current_symbol = symbol
                entry_price    = price
                entry_time     = time.time()
                print(
                    f"Entered {symbol} at {price:.2f} | "
                    f"Hard stop at {price * (1 - STOP_LOSS_PCT):.2f} | "
                    f"Sell signal unlocks after {MIN_HOLD_SECONDS}s"
                )
                break

            elif sell_signal:
                place_order("sell", symbol)
                in_position    = False
                current_symbol = None
                entry_price    = None
                entry_time     = None
                print(f"Exited {symbol} | PnL: {pnl_pct*100:.2f}% | Held: {int(time_held)}s")

        time.sleep(POLL_INTERVAL)

# ── Flask Status Route ──
@app.route('/status')
def status():
    account = get_account()
    return jsonify({
        "status":          "running",
        "net_liquidation": account.get("portfolio_value", "N/A"),
        "cash_balance":    account.get("cash", "N/A"),
        "watchlist":       load_watchlist()
    })

if __name__ == "__main__":
    print("=== Trading Bot Ready (Alpaca IEX + Manual Indicators) ===")
    print(
        f"Trade Amount: ${TRADE_AMOUNT} | "
        f"Interval: {POLL_INTERVAL}s | "
        f"Hard Stop: {STOP_LOSS_PCT*100}% | "
        f"Min Hold: {MIN_HOLD_SECONDS}s | "
        f"Min Loss Exit: {MIN_LOSS_PCT*100}% | "
        f"Min Gain Exit: {MIN_GAIN_PCT*100}%"
    )
    threading.Thread(target=poll_loop, daemon=True).start()
    app.run(host='127.0.0.1', port=5000)  # local only: /status shows account value
