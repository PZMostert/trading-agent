# Trading Agent: Momentum Stock Screener and Paper-Trading Bot

A hands-on project that screens US stocks with rule-based filters, ranks them, and trades the top picks on an **Alpaca paper-trading account** (no real money). It also includes an agent that turns plain-English strategy ideas into TradingView Pine Script using a local LLM.

> **Transparency: this project was built with AI assistance.** I described what I wanted and how it should work in plain English, and the code was written by **Claude** (Anthropic's AI assistant). I did not write the code myself. My part was defining the requirements and rules, running and testing the system, reporting problems, and directing the fixes. See [My role](#my-role).
>
> **Status: work in progress.** There is no backtest and there are no performance results yet, so this repo makes no claim that the strategy makes money. See [Status and results](#status-and-results).

## The problem

Choosing which stocks to watch is a filtering and ranking problem. I wanted a repeatable, rule-based way to:

1. Narrow roughly 500 stocks down to a short watchlist using explicit, explainable criteria.
2. Apply a fixed set of entry and exit rules to that watchlist, with no discretionary decisions.
3. Test the whole thing safely, on paper money only.

## My role

- **Defined the requirements:** the goal, the filter criteria and thresholds, the entry and exit rules, and what "safe" meant (paper trading only, small fixed trade size, a stop loss).
- **Iterated on the rules:** for example, the sell logic gained a minimum hold time and a profit/loss threshold.
- **Set up and ran the stack:** Alpaca paper account, Python environment, TradingView Desktop and a local Ollama model.
- **Tested and reported problems:** including a screener run that sat on "Scanning..." with no output.
- **Had the code reviewed (with Claude):** the review found real bugs and weaknesses, all listed under [Known limitations](#known-limitations). One example is a screener bug that meant it could never select a stock, which has been fixed.
- **Prepared the project for publishing:** removed credentials from the code, moved them to a `.env` file, and published a clean repository.

## Data pipeline (screener)

```
S&P 500 list (Wikipedia) -> 6 months of daily prices and volume (yfinance, batched)
  -> filters -> score -> rank -> Alpaca IEX availability check -> watchlist.json
```

**Data sources:** Wikipedia (index constituents), Yahoo Finance via `yfinance` (daily prices and volume), Alpaca Market Data API (live-data availability check).

**Filters.** A stock must pass all of them:

| Rule | Threshold |
|---|---|
| Price | at or below $100 |
| Average daily volume | at least 5 million shares |
| Trend | price above 50-day EMA |
| Momentum strength | RSI(14) between 45 and 65 |
| Short-term momentum | positive 5-day price change |
| Short-term trend | price > EMA(9) > EMA(21) |

**Scoring:** `5-day momentum % x average volume (millions)`. The top 5 candidates that also have live data on Alpaca's free IEX feed become the watchlist.

**Transparency in the output:** the screener prints why each stock was dropped (a filter funnel), and if it has to fall back to a default list it records `"source": "fallback"` in `watchlist.json` instead of hiding it.

## Trading rules (bot)

`bot.py` checks each watchlist stock every 30 seconds using live 1-minute bars and calculates EMA(9), EMA(21), RSI(14) and MACD(12, 26, 9) in plain pandas.

| | Rule |
|---|---|
| **Buy** | No open position, and price > EMA(9) > EMA(21), and RSI between 45 and 65, and MACD above its signal line |
| **Sell** | Held at least 5 minutes, and profit/loss is at least +1.0% or at most -0.5%, and either (price below EMA(9) and MACD below signal) or RSI above 70 |
| **Hard stop** | Close the position at a 15% drop from entry |
| **Sizing** | Fixed $10 per trade, one position at a time |
| **Account** | Alpaca **paper** endpoint only |

A local status page runs at `http://127.0.0.1:5000/status`.

## Pine Script agent

`agent.py` takes a strategy described in plain English, asks a local model (`qwen2.5-coder:3b` via Ollama) to write Pine Script v5, shows it to me, and on approval pushes it into TradingView using the [tradingview-mcp](https://github.com/tradesdontlie/tradingview-mcp) command-line tool by tradesdontlie (not included in this repo).

## Status and results

- **No backtest yet and no recorded trade results.** I can't say whether these rules make or lose money, and nothing here should be read as evidence that they do.
- Everything runs on **paper money** with a $10 trade size.
- The next step is to evaluate the screener properly: compare how its picks perform over the following days and weeks against the S&P 500, and report the finding honestly, including if there is no edge.

## What this project shows

- Turning a business idea into explicit, testable rules and thresholds.
- Understanding a data pipeline end to end: collect, filter, score, rank, validate, output.
- Working with market data APIs and handling missing or failed data.
- Testing a system, spotting where it fails, and asking for the right fixes.
- Working responsibly with AI coding tools: reviewing the output, questioning it, and being open about what was AI-written.
- Basic project hygiene: keeping secrets out of code, using `.env` and `.gitignore`, documenting limitations.

## Tech

Python, pandas, Flask, requests, yfinance, Alpaca Markets API (paper trading), Ollama, TradingView Desktop.

## Setup

Developed on Windows with Python 3.14.

```bash
pip install -r requirements.txt
```

1. Create a free [Alpaca](https://alpaca.markets) account and generate **paper trading** API keys.
2. Copy `.env.example` to `.env` and add your keys. Never commit `.env`; it is already in `.gitignore`.
   ```
   ALPACA_API_KEY=your_paper_api_key_here
   ALPACA_SECRET_KEY=your_paper_secret_key_here
   ```
3. Run the screener, then the bot:
   ```bash
   python screener.py
   python bot.py
   ```
   Or on Windows, run `Start.bat` to do it in one go: it checks for `.env`, runs the screener, opens the bot in its own window, and asks whether to also start the Pine Script agent.
4. Optional, for the Pine Script agent: install [Ollama](https://ollama.com), run `ollama pull qwen2.5-coder:3b`, set up tradingview-mcp with TradingView Desktop on debug port 9222, then run `python agent.py`.

**Before the first run, check the hardcoded paths** (see below). `bot.py` and `agent.py` currently point to `E:\trading-agent\...` and need editing for your machine.

## Known limitations

Found in a code review and not yet fixed:

- **Bars may be stale (suspected, not yet verified).** `bot.py` requests 100 one-minute bars sorted ascending with no start time. This may return the first bars of the day rather than the latest, which would make signals stale after the first couple of hours of trading.
- **Order results are not checked.** The bot treats a trade as open as soon as it sends the order, even if Alpaca rejects it, and it records the last bar's close as the entry price, not the real fill price.
- **Hardcoded Windows paths.** `bot.py` reads the watchlist from a fixed `E:\` path, and `agent.py` has fixed paths for the MCP tool and the `.pine` file. It also writes and reads the `.pine` file at two different paths, so injection can fail.
- **Market-hours check is disabled** in the main loop (commented out for testing).
- **RSI is a simple-average version**, not Wilder's smoothing, so values differ slightly from TradingView's RSI.
- **The 15% stop is very wide** for a 1-minute strategy, and a position between -0.5% and +1.0% can only exit through that stop.
- **Generated Pine Script is often invalid.** A 3B-parameter local model regularly produces code that doesn't compile (for example, non-existent functions or a sell rule that opens a short). The agent does not yet validate the compile result.
- **No trade log and no backtest.**
- Single position at a time. Yahoo Finance data comes through an unofficial library that can change or throttle.

## Roadmap

- [ ] Evaluate the screener's picks against the S&P 500 and report the result honestly
- [ ] Fix the stale-bars request and check order results before updating state
- [ ] Log every trade to CSV
- [ ] Replace hardcoded paths with relative paths and a config file
- [ ] Re-enable the market-hours check
- [ ] Backtest the buy/sell rules on historical data before drawing conclusions
- [ ] Check Pine Script compile output and retry on errors

## Disclaimer

This project is for learning. It is not financial advice and comes with no guarantee of any kind. It is built for paper trading only. Do not use it with real money.
