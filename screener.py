"""
Stock screener: scans the S&P 500 for momentum candidates and writes the top
few (verified as available on Alpaca's free IEX feed) to watchlist.json,
which bot.py reads.

Pipeline:
  S&P 500 list (Wikipedia) -> daily history (yfinance, batched)
  -> filters (price, volume, EMA trend, RSI, momentum) -> score -> rank
  -> Alpaca IEX availability check -> watchlist.json
"""
import json
import os
import time
from collections import Counter
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# ── Alpaca API (only used to verify a symbol has live IEX data) ──
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_DATA_URL   = "https://data.alpaca.markets"

# ── Screener settings ──
MAX_PRICE      = 100          # skip stocks above this price
MIN_AVG_VOLUME = 5_000_000    # average daily volume
RSI_MIN, RSI_MAX = 45, 65
HISTORY_PERIOD = "6mo"        # NOTE: "60d" is only ~42 trading days, too few for an EMA50
MIN_ROWS       = 50           # need at least this many daily bars (EMA50)
CHUNK_SIZE     = 50           # symbols per Yahoo request
TOP_N          = 5            # watchlist size
DEFAULT_WATCHLIST = ["PLTR", "AMD", "F", "SOFI", "BAC"]

# watchlist.json is written next to this file, which is where bot.py expects it
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


# ── Symbols ──
def get_sp500_symbols():
    try:
        tables = pd.read_html(SP500_URL, storage_options={"User-Agent": "Mozilla/5.0"})
        return tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception as e:
        print(f"Could not fetch S&P 500 list: {e}")
        print("Using built-in fallback symbol list instead.")
        return [
            "F", "SOFI", "PLTR", "AAL", "CCL", "VALE", "GOLD", "BAC", "C",
            "AMD", "SNAP", "UBER", "HOOD", "RIVN", "NIO", "IONQ", "T",
            "INTC", "WBD", "NOK", "GRAB", "SIRI", "AGNC", "MFA", "ERIC",
            "BB", "LUMN", "HIMX", "KOSS", "CTRM", "SENS", "MVIS", "IDEX",
            "XPEV", "LI", "NKLA", "WKHS", "ZEV", "GOEV", "RIDE", "FSR",
        ]


# ── Indicators ──
def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain  = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss  = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


# ── Download (batched, with progress) ──
def _extract_symbol_frame(data, symbol):
    """Pull one symbol's OHLCV frame out of a yfinance batch download."""
    if isinstance(data.columns, pd.MultiIndex):
        if symbol not in data.columns.get_level_values(0):
            return None
        df = data[symbol]
    else:
        df = data  # single-symbol download comes back flat
    df = df.dropna(subset=["Close"])
    return df if not df.empty else None


def download_history(symbols):
    frames = {}
    total = len(symbols)
    for i in range(0, total, CHUNK_SIZE):
        chunk = symbols[i:i + CHUNK_SIZE]
        try:
            data = yf.download(
                chunk, period=HISTORY_PERIOD, interval="1d",
                group_by="ticker", auto_adjust=True,
                threads=True, progress=False,
            )
        except Exception as e:
            print(f"  Download failed for symbols {i + 1}-{i + len(chunk)}: {e}")
            continue

        for sym in chunk:
            df = _extract_symbol_frame(data, sym)
            if df is not None:
                frames[sym] = df

        print(f"  Downloaded {min(i + CHUNK_SIZE, total)}/{total} symbols "
              f"({len(frames)} with data)")
        time.sleep(1)  # be gentle with Yahoo
    return frames


# ── Per-symbol filter ──
def evaluate_symbol(symbol, hist):
    """Return (candidate_dict, None) if it passes every filter, else (None, reason)."""
    if hist is None or len(hist) < MIN_ROWS:
        return None, "not enough history"

    close  = hist["Close"]
    volume = hist["Volume"]
    price  = float(close.iloc[-1])

    if price <= 0 or price > MAX_PRICE:
        return None, f"price over ${MAX_PRICE}"

    avg_volume = float(volume.mean())
    if avg_volume < MIN_AVG_VOLUME:
        return None, "low volume"

    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    if price < ema50:
        return None, "below EMA50"

    rsi = float(calculate_rsi(close).iloc[-1])
    if not (RSI_MIN < rsi < RSI_MAX):
        return None, "RSI outside range"

    momentum_5d = (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100
    if momentum_5d <= 0:
        return None, "no 5-day momentum"

    ema9  = close.ewm(span=9,  adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    if not (price > ema9 > ema21):
        return None, "EMA9/21 not stacked"

    return {
        "symbol":       symbol,
        "price":        round(price, 2),
        "rsi":          round(rsi, 1),
        "momentum_5d":  round(float(momentum_5d), 2),
        "avg_volume_m": round(avg_volume / 1_000_000, 1),
        "score":        round(float(momentum_5d) * (avg_volume / 1_000_000), 2),
    }, None


# ── Alpaca IEX availability ──
def has_alpaca_iex_data(symbol):
    """True if Alpaca returns a latest 1-min bar for this symbol on the IEX feed."""
    headers = {
        "APCA-API-KEY-ID":     ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    try:
        r = requests.get(
            f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars/latest",
            headers=headers, params={"feed": "iex"}, timeout=10,
        )
        return r.status_code == 200 and bool(r.json().get("bar"))
    except Exception:
        return False


def save_watchlist(symbols, details, source):
    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "updated":   datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source":    source,   # "screener" or "fallback"
            "watchlist": symbols,
            "details":   details,
        }, f, indent=2)
    print(f"\nWatchlist saved to {OUTPUT_PATH}")


# ── Main ──
def screen_stocks():
    print("=== Stock Screener Starting ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("Fetching S&P 500 symbols...")
    symbols = get_sp500_symbols()
    print(f"Downloading {HISTORY_PERIOD} of daily history for {len(symbols)} symbols...")

    frames = download_history(symbols)
    if not frames:
        print("\nERROR: Yahoo Finance returned no data for any symbol.")
        print("Check your internet connection, and try:  pip install -U yfinance")
        print("Using the default watchlist.")
        save_watchlist(DEFAULT_WATCHLIST, [{"symbol": s} for s in DEFAULT_WATCHLIST], "fallback")
        return DEFAULT_WATCHLIST

    median_rows = int(pd.Series([len(df) for df in frames.values()]).median())
    print(f"\nGot data for {len(frames)}/{len(symbols)} symbols "
          f"(median {median_rows} daily bars each). Applying filters...")

    candidates, rejected = [], Counter()
    for sym in symbols:
        cand, reason = evaluate_symbol(sym, frames.get(sym))
        if cand:
            candidates.append(cand)
        else:
            rejected[reason if sym in frames else "no data from Yahoo"] += 1

    print("\nFilter results (why symbols were dropped):")
    for reason, n in rejected.most_common():
        print(f"  {n:4d}  {reason}")
    print(f"  {len(candidates):4d}  passed all filters")

    candidates.sort(key=lambda c: c["score"], reverse=True)

    if ALPACA_API_KEY and ALPACA_SECRET_KEY:
        print("\nVerifying Alpaca IEX availability...")
        verified = []
        for c in candidates:
            if has_alpaca_iex_data(c["symbol"]):
                c["alpaca_iex"] = True
                verified.append(c)
                print(f"  ✓ {c['symbol']} - available on Alpaca IEX")
            else:
                print(f"  ✗ {c['symbol']} - NOT available on Alpaca IEX, skipping")
            if len(verified) == TOP_N:
                break
    else:
        print("\nWARNING: no Alpaca keys in .env - skipping IEX availability check.")
        verified = candidates[:TOP_N]

    if not verified:
        print("\nNo candidates passed - using the default watchlist.")
        save_watchlist(DEFAULT_WATCHLIST, [{"symbol": s} for s in DEFAULT_WATCHLIST], "fallback")
        return DEFAULT_WATCHLIST

    print("\n=== Final Watchlist ===")
    for i, s in enumerate(verified, 1):
        print(f"{i}. {s['symbol']} | Price: ${s['price']} | RSI: {s['rsi']} | "
              f"5d Momentum: {s['momentum_5d']}% | Volume: {s['avg_volume_m']}M")

    save_watchlist([s["symbol"] for s in verified], verified, "screener")
    return [s["symbol"] for s in verified]


if __name__ == "__main__":
    screen_stocks()
