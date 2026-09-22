"""One backtest seam per indicator kind.

Expected bars and prices are hand arithmetic from the locked formulas.
They are not copied from values().
"""

from backtest import (
    All,
    Above,
    Bar,
    Below,
    Cross,
    Hypothesis,
    Indicator,
    Price,
    Threshold,
    backtest,
)


def _bars(rows):
    built = []
    for row in rows:
        if len(row) == 4:
            open_, high, low, close = row
            volume = 1
        else:
            open_, high, low, close, volume = row
        built.append(Bar(open_, high, low, close, volume))
    return built


def _closes(closes):
    return _bars((close, close, close, close) for close in closes)


def _run(entry, rows):
    hypothesis = Hypothesis(
        name="seam",
        side="long",
        long_entry=entry,
        short_entry=None,
        long_rule_exit=None,
        short_rule_exit=None,
        time_exit=None,
        distance_kind="percent",
        take_profit_size=1 / 2,
        stop_loss_size=1 / 2,
    )
    return backtest(hypothesis, rows, fee=0.0, bar_size="1h")


def _marked(result, entry_bar, price):
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_bar == entry_bar
    assert trade.exit_bar == entry_bar
    assert trade.side == "long"
    assert trade.entry_price == price
    assert trade.exit_price == price
    assert trade.cause == "still open"
    # Fee 0, and the mark equals the open, so the stake stays 1.
    assert result.ending_stake == 1


def _cross(left, right, direction="above"):
    return Cross(left=left, right=right, direction=direction)


def _between(line, low, high, level, direction="above"):
    return All(
        parts=(
            _cross(line, Threshold(value=level, name="level"), direction),
            Above(left=line, right=Threshold(value=low, name="low")),
            Below(left=line, right=Threshold(value=high, name="high")),
        )
    )


def test_ema_seed_then_step():
    # Window 2, k = 2/3. Seed at index 1 is (2 + 4) / 2 = 3.
    # Index 2 is (2/3)*6 + (1/3)*3 = 5. That average crosses above 4
    # and lies in (4.5, 5.5) only if the step is 5. Fill is bar 3's open, 7.
    ema = Indicator("ema", "ema", window=2)
    result = _run(
        _between(ema, 4.5, 5.5, 4),
        _closes((2, 4, 6, 7)),
    )
    _marked(result, 3, 7)


def test_wma_newest_gets_the_largest_weight():
    # Window 3. Weights 1, 2, 3 and denominator 6.
    # Index 2: (1*1 + 2*2 + 3*3) / 6 = 14/6. Index 3: (1*2 + 2*3 + 3*6) / 6 = 26/6.
    # 14/6 <= 4 < 26/6, so the average crosses above 4 on bar 3. Fill open is 5.
    wma = Indicator("wma", "average", window=3)
    result = _run(_cross(wma, Threshold(value=4, name="level")), _closes((1, 2, 3, 6, 5)))
    _marked(result, 4, 5)


def test_dema_subtracts_the_second_ema():
    # Window 2. EMA seed 1, then 1, then 5. EMA of that seeds at 1 and steps to 11/3.
    # DEMA at index 3 is 2*5 - 11/3 = 19/3, which crosses above 6 and stays under 6.5.
    # Fill open is 4.
    dema = Indicator("dema", "dema", window=2)
    result = _run(_between(dema, 6, 6.5, 6), _closes((1, 1, 1, 7, 4)))
    _marked(result, 4, 4)


def test_tema_third_ema():
    # Same window-2 stack as DEMA, one bar further. Prices 1, 1, 1, 7, 7.
    # TEMA at index 3 is 19/3. At index 4 it is 191/27, inside (7, 7.15),
    # so it crosses above 6.5 there. The second-EMA stack is outside that band.
    # Fill open is 8.
    tema = Indicator("tema", "tema", window=2)
    result = _run(_between(tema, 7, 7.15, 6.5), _closes((1, 1, 1, 7, 7, 8)))
    _marked(result, 5, 8)


def test_kama_squares_the_smoothing_constant():
    # Window 2. Seed at index 2 is the price 10.
    # ER at index 3 is 1, so SC = (2/3) ** 2 and the step is 10 + 4 = 14,
    # inside (13, 15) even after the binary rounding of 2/3. Fill open is 12.
    kama = Indicator("kama", "kama", window=2)
    result = _run(_between(kama, 13, 15, 12), _closes((10, 10, 10, 19, 12)))
    _marked(result, 4, 12)


def test_hma_uses_the_catalog_wma_stack():
    # n = 4, half = 2, sqrt = 2. WMA(2*WMA(2) - WMA(4), 2) on 1..6
    # is 5 at index 4 and 6 at index 5. It crosses above 5.5 and sits in (5.9, 6.1).
    # Fill open is 6.
    hma = Indicator("hma", "hma", window=4)
    result = _run(
        _between(hma, 5.9, 6.1, 5.5),
        _closes((1, 2, 3, 4, 5, 6, 6)),
    )
    _marked(result, 6, 6)


def test_vwma_weights_by_volume():
    # Window 2. Index 1: (1*1 + 3*3) / 4 = 2.5. Index 2: (3*3 + 2*1) / 4 = 2.75.
    # 2.5 <= 13/5 < 2.75. Fill open is 3.
    vwma = Indicator("vwma", "vwma", window=2)
    result = _run(
        _cross(vwma, Threshold(value=13 / 5, name="level")),
        _bars(((1, 1, 1, 1, 1), (3, 3, 3, 3, 3), (2, 2, 2, 2, 1), (3, 3, 3, 3, 1))),
    )
    _marked(result, 3, 3)


def test_vwap_anchor_uses_only_the_current_bucket():
    # Anchor 2. Typical price equals the close. Bucket bars 2 and 3 are 5 then 9,
    # so VWAP is 5 then 7. Close 9 crosses above 7. A cumulative sum would be 4
    # on that bar, and the previous close 5 would already be above it. Fill open is 8.
    vwap = Indicator("vwap", "vwap", params=(("anchor", 2),))
    result = _run(
        _cross(Price("close"), vwap),
        _closes((1, 1, 5, 9, 8)),
    )
    _marked(result, 4, 8)


def test_supertrend_default_bands():
    # Length 10 and multiplier 3. Bar 0's true range is 100 and is not in the seed.
    # ATR[10] is the mean of ten 2s, so 2. Median 10 gives final lower 4 while the
    # seeded trend is up. Bar 11 has TR 6, ATR 2.4, and final lower 4.8.
    # 4 <= 4.5 < 4.8, and 4.8 is inside (4.6, 5). Fill open is 10.
    line = Indicator("supertrend", "supertrend")
    rows = [(10, 100, 0, 10)] + [(10, 11, 9, 10)] * 10 + [(14, 15, 9, 14), (10, 10, 10, 10)]
    result = _run(_between(line, 4.6, 5, 4.5), _bars(rows))
    _marked(result, 12, 10)


def test_sar_default_step():
    # high[1] > high[0], so bar 1 publishes low[0] = 1, extreme 12, step 0.02.
    # Bar 2's advance is clamped back to the prior low 1. Bar 3 advances
    # 1 + 0.02 * (12 - 1) = 1.22, which is inside (1.2, 1.25).
    # Close 1.3 crosses above it. Fill open is 2.
    sar = Indicator("sar", "sar")
    rows = [(1, 10, 1, 1), (1, 12, 2, 1), (1, 12, 3, 1), (1.3, 12, 3, 1.3), (2, 2, 2, 2)]
    result = _run(
        All(
            parts=(
                _cross(Price("close"), sar),
                Above(left=sar, right=Threshold(value=1.2, name="low")),
                Below(left=sar, right=Threshold(value=1.25, name="high")),
            )
        ),
        _bars(rows),
    )
    _marked(result, 4, 2)


def test_ichimoku_tenkan_default_is_nine():
    # Window omitted, so tenkan is 9. At index 8 the range is 100 and 2, midpoint 51.
    # At index 9 the range is 20 and 2, midpoint 11, inside (10, 12).
    # Close 12 crosses above 11. Fill open is 12.
    tenkan = Indicator("ichimoku", "tenkan", output="tenkan")
    rows = [(2, 100, 2, 2), (2, 20, 2, 2)] + [(2, 8, 2, 2)] * 7 + [(2, 12, 2, 12), (12, 12, 12, 12)]
    result = _run(
        All(
            parts=(
                _cross(Price("close"), tenkan),
                Above(left=tenkan, right=Threshold(value=10, name="low")),
                Below(left=tenkan, right=Threshold(value=12, name="high")),
            )
        ),
        _bars(rows),
    )
    _marked(result, 10, 12)


def test_macd_default_histogram_crosses_below_zero():
    # 12, 26, and 9, all exponential. Twenty-six closes of 1 seed both averages at 1,
    # so the line at index 25 is 0. The next close is 2, and the line is
    # 15/13 - 29/27 = 28/351. The signal exists from index 33.
    # Histogram is still positive at index 39 and negative at index 40.
    # Fill open is 2.
    histogram = Indicator("macd", "histogram", output="histogram")
    closes = [1] * 26 + [2] * 16
    result = _run(_cross(histogram, Threshold(value=0, name="level"), "below"), _closes(closes))
    _marked(result, 41, 2)


def test_rsi_default_wilder_seed():
    # Sixteen closes. The first fourteen changes are six flats, four drops of 1,
    # and four rises of 2. Sum of ups is 8 and sum of downs is 4, so Wilder RSI(14)
    # at index 14 is 100 * (8/14) / (12/14) = 200/3, inside (66.6, 66.7).
    # Close crosses above 12 on that same bar. Window is omitted. Fill open is 15.
    rsi = Indicator("rsi", "rsi")
    closes = [10] * 7 + [9, 8, 7, 6, 8, 10, 12, 14, 15]
    result = _run(
        All(
            parts=(
                _cross(Price("close"), Threshold(value=12, name="level")),
                Above(left=rsi, right=Threshold(value=66.6, name="low")),
                Below(left=rsi, right=Threshold(value=66.7, name="high")),
            )
        ),
        _closes(closes),
    )
    _marked(result, 15, 15)


def test_stoch_default_slow_d():
    # 14, 3, and 3. Raw %K is 0 while the close sits on the low, then 100.
    # Slow K at index 17 is 100/3 and at 18 is 200/3.
    # Slow D is 100/9 then 100/3, so it crosses above 20 on bar 18. Fill open is 5.
    slow_d = Indicator("stoch", "slow_d", output="slow_d")
    rows = [(0, 10, 0, 0)] * 17 + [(10, 10, 0, 10), (10, 10, 0, 10), (5, 5, 5, 5)]
    result = _run(_cross(slow_d, Threshold(value=20, name="level")), _bars(rows))
    _marked(result, 19, 5)


def test_stochrsi_default_smooths_raw_k_by_three():
    # RSI length and stochastic length are both the pinned 14. D is an SMA of 3.
    # Close starts at 50, rises by 1 twenty times (RSI seed 100), falls by 1 twelve
    # times, then rises by 3. D is below 50 at index 35 and above it at index 36.
    # Fill open is 10.
    smooth = Indicator("stochrsi", "d", output="d")
    closes = [50.0]
    for _ in range(20):
        closes.append(closes[-1] + 1)
    for _ in range(12):
        closes.append(closes[-1] - 1)
    for _ in range(4):
        closes.append(closes[-1] + 3)
    closes.append(10)
    result = _run(_cross(smooth, Threshold(value=50, name="level")), _closes(closes))
    _marked(result, 37, 10)


def test_cci_default_mean_absolute_deviation():
    # Fourteen typical prices at index 13: thirteen 0s and one 14.
    # Mean 1, mean absolute deviation 13/7, CCI = 7 / 0.015 = 1400/3.
    # The next window is twelve 0s and two 14s, and CCI is 700/3.
    # 1400/3 >= 400 > 700/3, so CCI crosses below 400 on bar 14. Fill open is 10.
    cci = Indicator("cci", "cci")
    result = _run(
        _cross(cci, Threshold(value=400, name="level"), "below"),
        _closes([0] * 13 + [14, 14, 10]),
    )
    _marked(result, 15, 10)


def test_willr_default():
    # At index 13 the close is the high, so Williams %R is 0.
    # At index 14 the close is the low, so it is -100. It crosses below -50.
    # Fill open is 5.
    willr = Indicator("willr", "willr")
    rows = [(10, 10, 0, 10)] * 14 + [(0, 10, 0, 0), (5, 5, 5, 5)]
    result = _run(_cross(willr, Threshold(value=-50, name="level"), "below"), _bars(rows))
    _marked(result, 15, 5)


def test_roc_default_is_ten():
    # (10 / 10 - 1) * 100 = 0 at index 10. (10 / 5 - 1) * 100 = 100 at index 11.
    # That crosses above 0. The 5 is ten bars before index 11. Fill open is 8.
    roc = Indicator("roc", "roc")
    result = _run(_cross(roc, Threshold(value=0, name="level")), _closes([10, 5] + [10] * 10 + [8]))
    _marked(result, 12, 8)


def test_mom_default_is_ten():
    # Same closes as ROC. Momentum is 0 at index 10 and 10 - 5 = 5 at index 11.
    # It crosses above 0. Fill open is 8.
    mom = Indicator("mom", "mom")
    result = _run(_cross(mom, Threshold(value=0, name="level")), _closes([10, 5] + [10] * 10 + [8]))
    _marked(result, 12, 8)


def test_mfi_default_drops_when_typical_price_falls():
    # Fourteen rises into a flat 11 put every flow in the positive sum, so MFI is 100
    # at index 14. The next window has only the drop to 9, a negative sum, and no
    # positive sum, so MFI is 0. It crosses below 50. Fill open is 11.
    mfi = Indicator("mfi", "mfi")
    rows = [(10, 10, 10, 10)] + [(11, 11, 11, 11)] * 14 + [(9, 9, 9, 9), (11, 11, 11, 11)]
    result = _run(_cross(mfi, Threshold(value=50, name="level"), "below"), _bars(rows))
    _marked(result, 16, 11)


def test_ao_default_is_fast_minus_slow():
    # Medians are 0 for 34 bars, then 2. Both SMAs are 0 at index 33.
    # At index 34 the fast SMA is 2/5 and the slow SMA is 2/34, so AO is positive
    # and crosses above 0. Fill open is 2.
    ao = Indicator("ao", "ao")
    rows = [(0, 0, 0, 0)] * 34 + [(2, 2, 2, 2), (2, 2, 2, 2)]
    result = _run(_cross(ao, Threshold(value=0, name="level")), _bars(rows))
    _marked(result, 35, 2)


def test_tsi_default_crosses_above_zero_after_the_turn():
    # Long 25, short 13. Price falls by 1 from 100 through index 40, then rises by 1.
    # The first double-smoothed ratio, at index 37, is -100. It is still negative
    # at index 55 and positive at index 56. Fill open is 70.
    tsi = Indicator("tsi", "tsi")
    prices = [100 - i for i in range(41)]
    for step in range(1, 17):
        prices.append(60 + step)
    prices.append(70)
    result = _run(_cross(tsi, Threshold(value=0, name="level")), _closes(prices))
    _marked(result, 57, 70)


def test_ultosc_default_weights_are_four_two_one():
    # Lengths 7, 14, and 28. Buying pressure is 0 and the true range is 1 through
    # index 28, so the oscillator is 0. Bar 29 adds one unit of pressure.
    # 100 * (4/7 + 2/14 + 1/28) / 7 = 75/7, which crosses above 0. Fill open is 5.
    ultosc = Indicator("ultosc", "ultosc")
    rows = [(0, 1, 0, 0)] * 29 + [(1, 1, 0, 1), (5, 5, 5, 5)]
    result = _run(_cross(ultosc, Threshold(value=0, name="level")), _bars(rows))
    _marked(result, 30, 5)


def test_ppo_default_is_the_percentage_of_the_slow_ema():
    # Fast 12 and slow 26, both exponential. Both are 1 at index 25, so PPO is 0.
    # At index 26 the fast EMA is 15/13 and the slow EMA is 29/27.
    # 100 * (28/351) / (29/27) = 2800/377, which crosses above 0. Fill open is 2.
    ppo = Indicator("ppo", "ppo")
    result = _run(_cross(ppo, Threshold(value=0, name="level")), _closes([1] * 26 + [2, 2]))
    _marked(result, 27, 2)


def test_apo_default_is_the_fast_ema_minus_the_slow_ema():
    # Same closes and lengths as PPO, without the percentage. The difference at
    # index 26 is 28/351, which crosses above 0. Fill open is 2.
    apo = Indicator("apo", "apo")
    result = _run(_cross(apo, Threshold(value=0, name="level")), _closes([1] * 26 + [2, 2]))
    _marked(result, 27, 2)


def test_cmo_default_wilder_seed():
    # Fourteen rises of 1 seed the up average at 1 and the down average at 0,
    # so CMO at index 14 is 100. The next change is -14.
    # Up becomes 13/14 and down becomes 1, so CMO is 100 * -1/27, below 0.
    # Fill open is 10.
    cmo = Indicator("cmo", "cmo")
    result = _run(
        _cross(cmo, Threshold(value=0, name="level"), "below"),
        _closes(list(range(15)) + [0, 10]),
    )
    _marked(result, 16, 10)


def test_bbands_default_uses_the_population_deviation():
    # Length 20, deviations 2 and 2. Twenty closes of 10 put the upper band on 10.
    # The next window is nineteen 10s and one 12. Mean is 10.1 and the population
    # deviation is sqrt(0.19), so the upper band is about 10.972, inside (10.96, 10.98).
    # Close 12 crosses above it. The sample divisor would sit above 10.98. Fill open is 11.
    upper = Indicator("bbands", "upper", output="upper")
    rows = _closes([10] * 20 + [12, 11])
    result = _run(
        All(
            parts=(
                _cross(Price("close"), upper),
                Above(left=upper, right=Threshold(value=10.96, name="low")),
                Below(left=upper, right=Threshold(value=10.98, name="high")),
            )
        ),
        rows,
    )
    _marked(result, 21, 11)


def test_keltner_default_is_ema_plus_wilder_atr():
    # EMA 20 of the close, multiplier 2, ATR 20. Bar 0's true range is 100 and is
    # not in the ATR seed, so ATR[20] is 4 and the upper band is 18.
    # Bar 21 has ATR 4.8 and EMA 250/21, so the upper band is 250/21 + 9.6,
    # inside (21, 22). Close 30 crosses above it. Fill open is 20.
    upper = Indicator("keltner", "upper", output="upper")
    rows = [(10, 100, 0, 10)] + [(10, 12, 8, 10)] * 20 + [(30, 30, 30, 30), (20, 20, 20, 20)]
    result = _run(
        All(
            parts=(
                _cross(Price("close"), upper),
                Above(left=upper, right=Threshold(value=21, name="low")),
                Below(left=upper, right=Threshold(value=22, name="high")),
            )
        ),
        _bars(rows),
    )
    _marked(result, 22, 20)


def test_donchian_excludes_the_current_bar():
    # Adversarial case K. Window 2. Upper at bar 2 is max(5, 6) = 6.
    # Upper at bar 3 is max(6, 7) = 7. Close 8 crosses above 7.
    # Fill is bar 4's open, 8. Target 12 and stop 4 are outside the bar.
    upper = Indicator("donchian", "upper", window=2, output="upper")
    rows = [(5, 5, 4, 5), (5, 6, 5, 6), (6, 7, 6, 6), (6, 9, 6, 8), (8, 8, 7, 8)]
    result = _run(_cross(Price("close"), upper), _bars(rows))
    _marked(result, 4, 8)


def test_donchian_default_window_is_the_finished_twenty():
    # Window omitted, so the channel is 20 and still excludes this bar.
    # Upper at index 21 is 5. Close 6 is above it. Including this bar's high of 6
    # would make the upper 6, and 6 would not cross. Fill open is 7.
    upper = Indicator("donchian", "upper", output="upper")
    rows = [(5, 5, 4, 5)] * 21 + [(5, 6, 5, 6), (7, 7, 7, 7)]
    result = _run(_cross(Price("close"), upper), _bars(rows))
    _marked(result, 22, 7)


def test_obv_starts_at_zero():
    # Bar 0 is 0, not the volume. Down volume 1 makes -1. Up volume 2 makes 1.
    #  -1 <= 0 < 1, so OBV crosses above 0 on bar 2. Fill open is 6.
    obv = Indicator("obv", "obv")
    rows = [(10, 10, 10, 10, 5), (9, 9, 9, 9, 1), (11, 11, 11, 11, 2), (6, 6, 6, 6, 1)]
    result = _run(_cross(obv, Threshold(value=0, name="level")), _bars(rows))
    _marked(result, 3, 6)


def test_ad_adds_the_money_flow_volume():
    # Multipliers are 1, -1, 0, 1. Volumes 2, 2, 4, 3. The line is 2, 0, 0, 3.
    # 0 <= 0 < 3, so it crosses above 0 on bar 3. Fill open is 5.
    ad = Indicator("ad", "ad")
    rows = [
        (3, 3, 1, 3, 2),
        (1, 3, 1, 1, 2),
        (2, 3, 1, 2, 4),
        (4, 4, 2, 4, 3),
        (5, 5, 5, 5, 1),
    ]
    result = _run(_cross(ad, Threshold(value=0, name="level")), _bars(rows))
    _marked(result, 4, 5)


def test_cmf_default_is_twenty():
    # Multiplier is -1 on bars 0-19 and +1 from bar 20. At index 29 the window
    # is ten of each, so CMF is 0. At index 30 it is 2/20. It crosses above 0.
    # Fill open is 5.
    cmf = Indicator("cmf", "cmf")
    rows = [(0, 2, 0, 0)] * 20 + [(2, 2, 0, 2)] * 11 + [(5, 5, 5, 5)]
    result = _run(_cross(cmf, Threshold(value=0, name="level")), _bars(rows))
    _marked(result, 31, 5)


def test_floor_pivot_publishes_the_prior_session():
    # Session length 2. Bars 0 and 1 are the prior session: high 6, low 2, close 4.
    # P = (6 + 2 + 4) / 3 = 4 and R1 = 2 * 4 - 2 = 6, published from bar 2 onward.
    # A cross needs R1 on the previous bar too, so bar 2 cannot cross.
    # Bar 2's close is 6, which is not strictly above R1. Bar 3's close is 7.
    # 6 <= 6 < 7, so the cross is on bar 3. Fill is bar 4's open, 8.
    # Target 12 and stop 4 sit outside that flat bar, so the cause stays open.
    r1 = Indicator("pivot", "r1", output="r1", params=(("session", 2),))
    rows = [
        (4, 6, 2, 4),
        (4, 6, 2, 4),
        (6, 6, 6, 6),
        (7, 7, 7, 7),
        (8, 8, 8, 8),
    ]
    result = _run(_cross(Price("close"), r1), _bars(rows))
    _marked(result, 4, 8)


def test_swing_is_absent_until_confirmation_then_replaced():
    # Adversarial case L. One bar on each side. High 8 confirms on bar 2.
    # High 9 confirms on bar 5 and replaces it. Close 10 crosses above 9 on bar 6.
    # Fill is bar 7's open, 10. Target 15 and stop 5 are outside the bar.
    swing = Indicator("swing", "swing", output="high", params=(("left", 1), ("right", 1)))
    rows = [
        (5, 5, 4, 5),
        (5, 8, 5, 6),
        (6, 6, 5, 6),
        (6, 7, 6, 7),
        (7, 9, 7, 8),
        (8, 8, 7, 7),
        (7, 10, 7, 10),
        (10, 10, 9, 10),
    ]
    result = _run(_cross(Price("close"), swing), _bars(rows))
    _marked(result, 7, 10)


def test_natr_is_one_hundred_times_atr_over_close():
    # Window 1. TR[1] = max(2, 2, 0) = 2, so ATR[1] = 2 and NATR[1] = 200/11.
    # 200/11 is above 15, so the gate passes and bar 2's open 10 is the fill.
    natr = Indicator("natr", "natr", window=1)
    entry = All(
        parts=(
            _cross(Price("close"), Threshold(10, "level")),
            Above(left=natr, right=Threshold(15, "gate")),
        )
    )
    rows = [(10, 10, 10, 10), (10, 12, 10, 11), (10, 10, 10, 10)]
    result = _run(entry, _bars(rows))
    _marked(result, 2, 10)
