# Day-trading indicator catalog

Scope: this is a source catalog for rule hypotheses such as "buy when MACD crosses its signal" or "buy when price crosses resistance." It lists the indicators day traders actually use, the hyperparameters a library exposes, what each call emits, and the comparison that call can support. It is not a backtester design and it does not pick a strategy. Defaults below are what the library does when the caller omits the argument. Where TA-Lib and pandas-ta disagree, both numbers are kept; nothing in the requested families was dropped.

Sources, in the order used: [TA-Lib function pages](https://ta-lib.org/functions/) (formula, inputs, parameters) and the public `TA_*_Lookback` functions in [TA-Lib/ta-lib](https://github.com/TA-Lib/ta-lib) `src/ta_func/`; pandas-ta **0.4.71b0** from the [PyPI sdist](https://files.pythonhosted.org/packages/95/6d/60b88a0334a8c6a5be114ed2c46c8f3e164127d0eccd9ff99b50773f2b20/pandas_ta-0.4.71b0.tar.gz) (`src/pandas_ta/...`). The GitHub tree named by that package, `https://github.com/twopirllc/pandas-ta`, returned 404 on 2026-09-22, so the sdist is the copy that was read. Pine names and the crossover sentence are from the [Pine Script v5 reference](https://www.tradingview.com/pine-script-reference/v5/) (the HTML shell is client-rendered; the sentences quoted here are the reference text, also printed in the [v6 `ta.*` transcription](https://github.com/codenamedevan/pinescriptv6/blob/main/reference/functions/ta.md)). Floor pivots are the [StockCharts ChartSchool standard formula](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/pivot-points). CME's education page blocked this fetch. Pine `ta.*` calls take the length as a required argument; they do not ship a hidden default the way the two Python libraries do. Chart studies that traders type (MACD 12/26/9, RSI 14, Supertrend 3 and 10, SAR 0.02/0.02/0.2) are the examples in that reference, not extra defaults invented here.

`pta` below means pandas-ta 0.4.71b0. `talib=True` is that package's default when TA-Lib is installed, and then the numeric result is TA-Lib's, not the native branch.

## Crossover

TradingView `ta.crossover(source1, source2)` is true only when **source1 is greater than source2 on this bar and source1 was less than or equal to source2 on the previous bar**. `ta.crossunder` is the mirror: source1 is less now, and was greater than or equal on the previous bar. Equality on the previous bar counts; equality on this bar does not. A constant is a legal `source2` (RSI crossing 70 is the same call as a fast average crossing a slow one). See the v5 reference anchor [`fun_ta.crossover`](https://www.tradingview.com/pine-script-reference/v5/#fun_ta.crossover) and the same sentence in the v6 transcription.

TA-Lib has **no** crossover function. The [function index](https://ta-lib.org/functions/) has no `CROSS` / `CROSSOVER` entry. A cross has to be written on the outputs.

pandas-ta `cross(x, y, above=True, equal=True)` (defaults) is a different predicate, in `utils/_signals.py`: previous bar is strict `x < y`, current bar is `x >= y` when `equal=True`. So equality is tested on the **current** bar, not the previous one. `equal=False` is previous `x < y` and current `x > y`, which still misses the TradingView case "previous bar was equal." Do not treat pandas-ta `cross` as TradingView `ta.crossover`.

## Warmup

TA-Lib's rule, from the [C/C++ API](https://ta-lib.org/api/): lookback is how many input elements are consumed before the first output exists. An SMA of period 10 has lookback 9, so the first defined value sits on the 10th bar (index 9). Aligned to the input, the first `lookback` outputs are missing. Bars required for one value = lookback + 1. The formulas below are the `TA_*_Lookback` return with the [unstable period left at its default of 0](https://ta-lib.org/api/unstable-period/), which discards nothing. Setting `TA_SetUnstablePeriod` adds that many more leading bars on top of lookback for the functions that have one (EMA, KAMA, ATR, NATR, RSI, CMO, and anything that calls them: MACD, DEMA, TEMA, TSI, KC, Supertrend). The library does not publish a "bars until converged" count; 0 means "return every value the math can compute."

| Call | Lookback (unstable period 0) |
| --- | --- |
| SMA, WMA, VWMA, CCI, WILLR, CMF, DONCHIAN | `period - 1` |
| EMA | `period - 1` |
| DEMA | `2 * (period - 1)` |
| TEMA | `3 * (period - 1)` |
| HMA | `(period - 1) + (floor(sqrt(period)) - 1)` |
| KAMA | `period` |
| MACD | `EMA_Lookback(slow) + EMA_Lookback(signal)` = `slow + signal - 2` |
| RSI, CMO | `period` |
| MOM, ROC, MFI | `period` |
| ATR, NATR, SUPERTREND | `period` (Supertrend is `ATR_Lookback`) |
| AO | `SMA_Lookback(max(fast, slow))` = `max(fast, slow) - 1` |
| APO, PPO | `MA_Lookback` of the longer period (EMA: `max(fast, slow) - 1`) |
| STOCH, SMA smoothing | `(fastK - 1) + (slowK - 1) + (slowD - 1)` |
| STOCHRSI, SMA smoothing | `RSI_Lookback(period) + (fastK - 1) + (fastD - 1)` |
| ULTOSC | `max(p1, p2, p3)` |
| TSI | `1 + (first - 1) + (second - 1)` |
| BBANDS, SMA middle | `period - 1` (max of the MA lookback and `STDDEV` lookback) |
| KC | `max(EMA_Lookback(period), ATR_Lookback(atrPeriod))` |
| SAR | `1` |
| OBV, AD, VWAP | `0` |
| FRACTAL | `leftBars + rightBars` (the pivot bar is `rightBars` before the bar that first reports it) |

pandas-ta does not document a warmup table. Two code facts are not a substitute for one: `v_series(..., n)` returns nothing if the input is shorter than `n`, and `macd` sets that floor to `slow + signal - 1` (one bar more than TA-Lib's lookback, which is exactly one output). With `talib=True` the NaN prefix is TA-Lib's lookback. Native `rolling` / `ewm` paths are NaN until their window is filled; this note does not invent those counts.

## Repaint

Closed-bar Supertrend does not read future bars. [TA-Lib SUPERTREND](https://ta-lib.org/functions/supertrend.html) decides the trend against the current bar's band and seeds the first trend up; the seed is path-dependent, so a later start index can differ, but a finished bar does not change when more bars arrive. TradingView `ta.supertrend` uses the opposite sign (reference example: `1` down, `-1` up). TA-Lib uses `+1` up and `-1` down.

A confirmed swing does not repaint after the confirming bar. [TA-Lib FRACTAL](https://ta-lib.org/functions/fractal.html) emits the verdict `optInRightBars` bars after the pivot, and only if that bar strictly beats every other high (or low) in `[pivot - left, pivot + right]`. Pine `ta.pivothigh` / `ta.pivotlow` is non-strict on the left and strict on the right, so a flat top Pine calls a pivot is not one in TA-Lib. An unconfirmed extreme (the highest high of a window that still includes "now," or a pivot with no right-side bars) will move if a later bar exceeds it. That object is not FRACTAL.

Ichimoku spans are displaced, they are not a same-bar line. pandas-ta writes Senkou A/B with `shift(kijun - 1)` (25 bars when `kijun` is 26) and Chikou with `close.shift(-kijun + 1)`, which places a later close on an earlier timestamp. The docstring tells the caller to set `lookahead=False` to drop Chikou and avoid that leak. The cloud drawn past the last bar is a projection of the latest spans; bars already displaced by a full kijun window do not change. TA-Lib has no Ichimoku.

Donchian is not a repaint, but the current bar's upper band contains the current high, so `high[t] > upper[t]` cannot fire. [TA-Lib DONCHIAN](https://ta-lib.org/functions/donchian.html) says the breakout test is `high[t] > upper[t - 1]`.

OBV, AD, and an unanchored VWAP are path-dependent running sums ([stability notes](https://ta-lib.org/functions/stability.html)). Their level depends on where the series starts. They do not use future bars. pandas-ta VWAP resets on an anchor (default `"D"`); TA-Lib VWAP does not, because no function there takes a session boundary.

## Overlap / trend

One price-scale line, unless the row says otherwise. Comparison that fits: price crosses the line, or a fast line crosses a slow line of the same function. Source for the line is close unless noted.

| Name | TA-Lib | pandas-ta | Defaults to tune | Emits |
| --- | --- | --- | --- | --- |
| SMA | `SMA` `optInTimePeriod=30` | `sma(length=10)` | window; source | 1 line |
| EMA | `EMA` `30`, `k=2/(period+1)`, seed = SMA of the first `period` bars | `ema(length=10, presma=True)` | window; source | 1 line |
| WMA | `WMA` `30`, weights `1..N` | `wma(length=10, asc=True)` | window; source | 1 line |
| DEMA | `DEMA` `30` = `2*EMA - EMA(EMA)` | `dema(length=10)` | window; source | 1 line |
| TEMA | `TEMA` `30` = `3*EMA1 - 3*EMA2 + EMA3` | `tema(length=10)` | window; source | 1 line |
| KAMA | `KAMA` `30`. Fast/slow constants are fixed: `2/(2+1)` and `2/(30+1)` | `kama(length=10, fast=2, slow=30)` | ER window, and in pandas-ta the fast/slow SC periods | 1 line |
| HMA | `HMA` `20` (Hull's default). `WMA(2*WMA(n/2) - WMA(n), floor(sqrt(n)))`, integer truncation | `hma(length=10, mamode="wma")` | window; inner average (pandas-ta only) | 1 line |
| VWMA | `VWMA` `30` on price × volume | `vwma(length=10)` | window; source price | 1 line |
| VWAP | `VWAP`, no period. Typical price `(H+L+C)/3`, cumulative from the first bar passed in | `vwap(anchor="D")` on `hlc3` | anchor (pandas-ta). No window | 1 line (a level that resets only if the caller resets it) |
| Supertrend | `SUPERTREND` ATR `10`, multiplier `3`. Median `(H+L)/2` | `supertrend(length=7, multiplier=3.0, atr_mamode="rma")`. `atr_length` defaults to `length` | ATR length, multiplier | line + direction. pandas-ta also emits the long and short bands (`SUPERT`, `SUPERTd`, `SUPERTl`, `SUPERTs`) |
| Ichimoku | none | `ichimoku(tenkan=9, kijun=26, senkou=52)` | tenkan, kijun, senkou | tenkan, kijun, senkou A, senkou B, chikou. A/B are shifted (see Repaint) |
| PSAR | `SAR` acceleration `0.02`, maximum `0.2` | `psar(af=0.02, max_af=0.2)` (`af0` defaults to `af`) | step, max step | 1 level. Price crossing it flips the side |

Pine names, arguments required, no hidden length: `ta.sma`, `ta.ema`, `ta.wma`, `ta.hma`, `ta.vwma(source, length)`, `ta.vwap`, `ta.sar(start, inc, max)`, `ta.supertrend(factor, atrPeriod)`. The reference examples traders copy are `ta.sar(0.02, 0.02, 0.2)` and `ta.supertrend(3, 10)`. There is no `ta.ichimoku` in the v6 `ta.*` list that was read. DEMA and TEMA are not separate `ta.*` names there; they are the usual EMA stacks.

KAMA is the sharp default split: TA-Lib's only knob is the efficiency-ratio window, default 30, with the 2-and-30 smoothing constants written into the [formula](https://ta-lib.org/functions/kama.html). pandas-ta's ER window defaults to 10 and exposes `fast` and `slow`.

## Momentum / oscillators

These are not on the price scale (TA-Lib marks them independent Y-axis). The fit is line-crosses-line or line-crosses-constant. Price crossing the oscillator is not a meaningful comparison.

| Name | TA-Lib | pandas-ta | Defaults to tune | Emits | Comparison |
| --- | --- | --- | --- | --- | --- |
| MACD | `MACD` fast `12`, slow `26`, signal `9`, all EMA. Slow and fast are swapped if slow < fast | `macd(fast=12, slow=26, signal=9)` | fast, slow, signal | MACD, signal, histogram | MACD crosses signal; histogram crosses 0 |
| RSI | `RSI` `14`, Wilder smooth after an SMA seed | `rsi(length=14, mamode="rma", scalar=100)` | window; source | 1 line, 0–100 | crosses 70 / 30 / 50 |
| Stochastic, slow | `STOCH` fastK `5`, slowK `3` SMA, slowD `3` SMA | `stoch(k=14, smooth_k=3, d=3, mamode="sma")` | window, %K smooth, %D smooth | slow %K and slow %D | %K crosses %D; either crosses 80 / 20 |
| StochRSI | `STOCHRSI` RSI `14`, fastK `5`, fastD `3` SMA. Outputs are raw %K and smoothed %D | `stochrsi(length=14, rsi_length=14, k=3, d=3)` | RSI length, stochastic window, %K, %D | %K and %D of RSI | same as stochastic, on 0–100 |
| CCI | `CCI` `14`, constant `0.015`, typical price | `cci(length=14, c=0.015)` | window | 1 line | crosses +100 / −100 / 0 |
| Williams %R | `WILLR` `14`, range −100..0 | `willr(length=14)` | window | 1 line | crosses −20 / −80 |
| ROC | `ROC` `10`, `((price / price[n]) - 1) * 100` | `roc(length=10, scalar=100)` | window; source | 1 line | crosses 0 |
| MOM | `MOM` `10`, `price - price[n]` | `mom(length=10)` | window; source | 1 line | crosses 0 |
| MFI | `MFI` `14` (listed under momentum) | `mfi(length=14)` (listed under volume) | window | 1 line, 0–100 | crosses 80 / 20 |
| Awesome Oscillator | `AO` fast `5`, slow `34`, SMA of `(H+L)/2` | `ao(fast=5, slow=34)` | fast, slow | 1 line, zero-centered | crosses 0 |
| TSI | `TSI` first `25`, second `13`. `100 * EMA(EMA(m)) / EMA(EMA(\|m\|))`. No signal | `tsi(fast=13, slow=25, signal=13, scalar=100)` | long smooth, short smooth, signal | TA-Lib: 1 line in −100..100. pandas-ta: TSI and a signal | line crosses 0; pandas-ta also TSI crosses signal |
| Ultimate Oscillator | `ULTOSC` `7`, `14`, `28`, weights 4/2/1 on the shortest/middle/longest (periods are sorted) | `uo(fast=7, medium=14, slow=28, fast_w=4, medium_w=2, slow_w=1)` | three windows and three weights | 1 line, 0–100 | crosses 70 / 30 |
| PPO | `PPO` fast `12`, slow `26`, MA type **EMA**. One percentage line, no signal | `ppo(fast=12, slow=26, signal=9, mamode="sma", scalar=100)` | fast, slow, signal, MA type | TA-Lib: 1 line. pandas-ta: PPO, signal, histogram | crosses 0, or crosses its signal when one exists |
| APO | `APO` fast `12`, slow `26`, MA type **EMA**. Price-unit MACD line, no signal | `apo(fast=12, slow=26, mamode="sma")` | fast, slow, MA type | 1 line | crosses 0 |
| CMO | `CMO` `14`. Wilder smoothing, not Chande's raw sums | `cmo(length=14, scalar=100)`. `talib=False` uses an EMA | window | 1 line, about −100..100 | crosses 0, or +50 / −50 |

Pine: `ta.macd(source, fastlen, slowlen, siglen)` returns `[macd, signal, hist]`, example 12/26/9. `ta.rsi(source, length)`, `ta.mom` is `source - source[length]`, `ta.cci`, `ta.cmo(source, length)`, `ta.mfi(source, length)`, `ta.wpr(length)`, `ta.atr` is separate. `ta.stoch` in that reference is the unsmoothed ratio `100 * (close - lowest(low, length)) / (highest(high, length) - lowest(low, length))`, not TA-Lib's slowed `STOCH`. `ta.tsi` is documented as a value in **[−1, 1]**, not the −100..100 scaling TA-Lib and pandas-ta apply. There is no built-in Awesome Oscillator or Ultimate Oscillator name in the `ta.*` list that was read.

The slow-stochastic window is a real split: TA-Lib's fastK default is 5; pandas-ta's `k` default is 14, which is the usual Lane setting. Both then smooth by 3 and 3.

## Volatility / bands

| Name | TA-Lib | pandas-ta | Defaults to tune | Emits | Comparison |
| --- | --- | --- | --- | --- | --- |
| ATR | `ATR` `14`, Wilder. Seed = SMA of the first `period` true ranges | `atr(length=14, mamode="rma")` | window | 1 line, price units, not a level to cross | used as a distance (Supertrend, stops). Crossing ATR itself is not the usual rule |
| NATR | `NATR` `14` = `100 * ATR / close` | `natr(length=14, scalar=100, mamode="ema")` | window | 1 line, percent | crosses a constant threshold. Not a price band |
| Bollinger | `BBANDS` `20`, `nbDevUp=2`, `nbDevDn=2`, middle **SMA** | `bbands(length=5, lower_std=2, upper_std=2, mamode="sma")`. Native `ddof` falls through to 1 | window, upper mult, lower mult, middle MA | upper, middle, lower. pandas-ta also bandwidth and %B | close crosses upper or lower; close crosses the middle |
| Keltner | `KC` center period `20` = EMA of **typical price**, ATR period `10`, multiplier `2` | `kc(length=20, scalar=2, mamode="ema", tr=True)`. Center is EMA of **close**. Band is the same MA of true range, same length | center length, multiplier, and (TA-Lib only) a separate ATR length | upper, middle, lower | close crosses upper or lower |
| Donchian | `DONCHIAN` `20`. Upper = highest high, lower = lowest low, middle = midpoint, window **includes** the current bar | `donchian(lower_length=20, upper_length=20)` | window (pandas-ta: separate upper and lower lengths) | upper, middle, lower | close/high crosses the **previous** bar's upper or lower |

Pine: `ta.atr(length)`, `ta.bb(source, length, mult)` (example in the manual is `5, 4`, which is not Bollinger's 20 and 2), `ta.kc` as EMA of the source plus/minus multiplier times EMA of true range. `ta.highest` / `ta.lowest` are the Donchian edges.

pandas-ta Bollinger length **5** is the code default (`v_pos_default(length, 5)`), matching its docstring, and it is not TA-Lib's 20 or Bollinger's published 20 and 2 ([TA-Lib BBANDS](https://ta-lib.org/functions/bbands.html) says the 20/2/SMA defaults reproduce that definition). The Keltner center also disagrees: TA-Lib uses typical price and an ATR period of 10; pandas-ta and the Pine `ta.kc` sketch use the close and one shared length. NATR's pandas-ta docstring says length 20; the code default is 14. The code is what runs.

## Volume

| Name | TA-Lib | pandas-ta | Defaults to tune | Emits | Comparison |
| --- | --- | --- | --- | --- | --- |
| OBV | `OBV`, no period. Seed is the first bar's volume | `obv` | none (source is close) | 1 cumulative line | slope, or the line crosses its own moving average. The absolute level is an artifact of the start date |
| ADL | `AD`, Chaikin A/D, no period. Money-flow multiplier × volume, running sum from 0 | `ad` | none | 1 cumulative line | same as OBV: crosses a smoothed copy of itself, not a fixed constant copied from another symbol |
| CMF | `CMF` `20` | `cmf(length=20)` | window | 1 line in [−1, +1] | crosses 0 |
| VWAP | see Overlap | see Overlap | anchor | 1 line | price crosses VWAP |

`ADOSC` exists (fast 3, slow 10) but is the oscillator of the A/D line, not the line itself, so it is not given a row. MFI is under Momentum.

## Levels

These are not indicators in the moving-average sense. A floor pivot is a constant for the whole next session. A swing level does not exist until it is confirmed.

**Floor pivots (PP, R1, S1, R2, S2).** StockCharts standard, prior period high/low/close, fixed once the period closes:

```
P  = (H + L + C) / 3
S1 = (P * 2) - H
R1 = (P * 2) - L
S2 = P - (H - L)
R2 = P + (H - L)
```

Intraday charts use the prior day; 30/60/120-minute charts use the prior week; daily charts use the prior month. They do not move during the period they apply to. No lookback inside the session: the inputs are three numbers from the previous period. Price crosses P, R1, S1, R2, or S2.

pandas-ta `pivots(method="traditional", anchor="D")` and `method="classic"` both match those four offsets (`pivot_traditional` and `pivot_classic` in `overlap/pivots.py`). They also emit S3/S4/R3/R4. Past R2/S2 the two methods disagree, and `traditional` sets R4 = R3 and S4 = S3. StockCharts' standard formula stops at R2/S2; do not take the extra levels from that file as the floor-trader definition. TA-Lib has no pivot function. Pine `ta.pivot_point_levels(type, anchor)` returns 11 slots and names `"Traditional"` and `"Classic"` as different types; this note does not copy its R3 formula because the reference page that was read does not print it.

**Donchian high / low.** The channel extremes from the Donchian row. They are a rolling level, not a floor pivot: they change every bar, they include the current bar, and a breakout is a cross of the previous bar's extreme.

**Swing high / swing low.** Confirmed by N bars on each side. TA-Lib `FRACTAL(leftBars=2, rightBars=2)` returns 100 or 0 for a strict swing, reported `rightBars` bars late. Pine `ta.pivothigh(leftbars, rightbars)` and `ta.pivotlow(source, leftbars, rightbars)` return the price or `na`, and the reference example plots them with `offset=-rightbars` so the mark sits on the pivot bar only after the right side exists. pandas-ta has no `pivothigh`. The hyperparameters are the two widths. The comparison is price crossing that confirmed price, and the signal cannot be known earlier than `rightBars` bars after the swing. An unconfirmed swing (no right-side test) is a different object and it repaints.

## Rule shapes

Five comparisons cover every row above. ATR and NATR are the exception: they are distances, not something price crosses.

1. **Series crosses series.** Fast MA crosses slow MA, MACD crosses signal, %K crosses %D, tenkan crosses kijun, price crosses an overlay line (MA, VWAP, PSAR, Supertrend, Ichimoku line). Same test as `ta.crossover(a, b)`: `a[1] <= b[1]` and `a > b`.
2. **Series crosses a constant.** RSI 70/30/50, CCI ±100, Williams %R −20/−80, ROC/MOM/AO/PPO/APO/TSI/CMF/histogram through 0, MFI 80/20, Ultimate 70/30. Same test with `b` constant.
3. **Price crosses a band edge.** Bollinger or Keltner upper/lower (and the middle, which is shape 1). Donchian only against the previous bar's high or low.
4. **Price crosses a session level.** Floor pivot P, R1, S1, R2, S2. The level is constant for the session.
5. **Price crosses a confirmed swing.** Swing high or swing low after N bars on the right. Not available on the pivot bar itself. Not the same object as shape 4.

## Kept despite disagreement

No requested indicator was dropped. These are the places a direct port will not match if the other library's default is used:

- Moving-average length: TA-Lib 30, pandas-ta 10, for SMA, EMA, WMA, DEMA, TEMA. HMA is 20 vs 10. KAMA's efficiency window is 30 vs 10.
- Bollinger length 20 (TA-Lib) vs 5 (pandas-ta).
- Keltner: typical-price center and ATR length 10 (TA-Lib) vs close center and one length of 20 (pandas-ta, and the Pine `ta.kc` sketch).
- Supertrend ATR length 10 (TA-Lib and the Pine example) vs 7 (pandas-ta), and the direction sign.
- Stochastic fast window 5 (TA-Lib) vs 14 (pandas-ta).
- PPO and APO moving-average type EMA (TA-Lib) vs SMA (pandas-ta). pandas-ta's APO docstring says "two EMAs"; the code default is `"sma"`.
- TSI scale: TA-Lib and pandas-ta use 100; the Pine reference says [−1, 1]. Parameter order is long-then-short in TA-Lib (25, 13) and the reverse names in pandas-ta (`fast=13`, `slow=25`).
- CMO smoothing: Wilder inside TA-Lib, simple sums in the Pine `ta.cmo` example.
- Crossover equality: previous bar (TradingView) vs current bar (pandas-ta `equal=True`).
- Ichimoku displacement: pandas-ta shifts by `kijun - 1`, not by `kijun`.
- VWAP has no window in either library. Only pandas-ta resets it (default daily).
- pandas-ta NATR docstring says 20; `v_pos_default(length, 14)` is what runs, and it matches TA-Lib.
