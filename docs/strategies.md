# Strategies

Ten strategies in the C++ strategy library. Each trades Avantis crypto markets
(BTC, ETH, SOL, DOGE, AVAX); five trade one coin and five trade every coin
passed in, in one account. Each was tuned with `optimize` on 2026 data.

Code: `cpp/include/avbt/library/<name>.hpp`. Test: `cpp/tests/test_<name>.cpp`.
Run: `python3 examples/sharpe_hunt/run_<name>.py`. Tuned params and scores:
`examples/sharpe_hunt/results/<name>.json`.

### `rsi_snap`

- **Coins:** BTC
- **What it does:** On 15-minute bars, buy when the close is above its 100-bar average and the 7-bar RSI is below 30; sell short in the mirror case. Out when the close crosses back over its 10-bar average, at 2 ATRs profit, at a 6-ATR stop, or after 12 bars.
- **Tuned params:** instrument BTC, timeframe 15 minutes, rsi_period 7, trend_period 100, exit_period 10, low 30.0, tp_atrs 2.0, atr_stops 6.0, atr_period 14, hold 12, shorts True, leverage 10.0

### `weekend_breakout_fade`

- **Coins:** DOGE
- **What it does:** On 4-hour bars, two fades share one position. On Monday at 08:00 UTC, fade the weekend's move. Otherwise fade a close that broke the prior high or low and came back inside within 4 bars. Small take profit, ATR stop, time exit.
- **Tuned params:** instrument DOGE, b_hold 8, b_leverage 5.0, b_stops 3.0, tp_atrs 1.2, tp_share 0.3, w_leverage 5.0, window 36

### `weekend_basket`

- **Coins:** BTC, ETH, SOL, DOGE, AVAX
- **What it does:** The weekend fade and the failed-breakout fade on every coin, in one account, on 4-hour bars, at most 5 positions at once.
- **Tuned params:** b_stops 3.0, leverage 2.0, max_open 5, tp_atrs 1.2, tp_share 0.3, w_stops 1.5, window 24

### `range_basket`

- **Coins:** BTC, ETH, SOL, DOGE, AVAX
- **What it does:** On 1-hour bars, while a coin's market label is a range label, fade a shallow overshoot of the prior 100-bar range once the close is back inside. Take profit at the range middle, wide stop, at most 3 positions.
- **Tuned params:** timeframe 1 hour, window 100, atr_period 14, max_bars 120, max_open 3, take 1.0, depth 0.4, atr_stops 6.0, max_stop 0.06, leverage 3.0

### `failed_breakout_fade`

- **Coins:** BTC, ETH, SOL, DOGE, AVAX
- **What it does:** On 4-hour bars on every coin: a close beyond the prior 24-bar high or low that comes back inside within 4 bars is faded. Take profit 1.2 ATRs, stop 3 ATRs, out after 8 bars.
- **Tuned params:** timeframe 4 hours, window 24, within 4, tp_atrs 1.2, atr_stops 3.0, hold 8, leverage 2.0

### `false_break_1h`

- **Coins:** BTC, ETH, SOL, DOGE, AVAX
- **What it does:** On 1-hour bars on every coin, with a range market label: fade a break of the prior 120-bar high or low that is back inside within 9 bars. Out at the range middle, at a take profit, at a wide stop or by time. 10x leverage.
- **Tuned params:** atr_stops 7.0, hold 96, leverage 10.0, max_open 3, regime 2, revert True, tp_atrs 3.0, window 120, within 9, atr_period 14

### `dip_buyer`

- **Coins:** SOL
- **What it does:** On SOL, while the 1-hour close is above its average, buy a 15-minute close more than 2.1 ATRs below its 10-bar average; the mirror short in a downtrend. Out at the average, at a small take profit, at a wide stop or by time.
- **Tuned params:** instrument SOL, timeframe 15 minutes, trend_timeframe 1 hour, trend_period 30, avg_period 10, atr_period 28, k 2.1, tp_atrs 1.75, atr_stops 4.0, hold 20, shorts True, revert True, leverage 8.0

### `band_scalper`

- **Coins:** AVAX
- **What it does:** On AVAX 15-minute bars, with a calm volatility label and with the trend label, fade a close that comes back inside a band 3 ATRs from its average. Tiny take profit, wide stop, 20x leverage.
- **Tuned params:** instrument AVAX, timeframe 15 minutes, window 23, k 3.0, tp_atrs 0.45, atr_stops 3.75, min_stop 0.01, trend 2, confirm True, max_bars 40, leverage 20.0

### `dip_basket`

- **Coins:** BTC, ETH, SOL, DOGE, AVAX
- **What it does:** On every coin, buy a fall of 4 ATRs over 4 bars while at least one in five coins is above its 1-hour average; the mirror short. At most 3 positions at once.
- **Tuned params:** timeframe 15 minutes, trend_timeframe 1 hour, trend_period 50, lookback 4, atr_period 28, drop 4.0, breadth 0.2, tp_atrs 2.0, atr_stops 5.0, hold 32, max_open 3, shorts True, revert False, leverage 10.0

### `range_seller`

- **Coins:** ETH
- **What it does:** On ETH 1-hour bars, while the market label is a range label, sell a close back inside after the prior bar closed above the 100-bar range, and buy the mirror. Take profit at the range middle, 8-ATR stop, 10x leverage.
- **Tuned params:** timeframe 1 hour, back_inside True, edge 0.5, window 100, atr_stops 8.0, leverage 10.0, max_bars 96, take 1.0, atr_period 14, flat False

## Rebuild

```bash
python3 examples/sharpe_hunt/data.py
python3 examples/sharpe_hunt/record.py
```
