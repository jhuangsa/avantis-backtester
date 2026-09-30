// Tests for the indicators in avbt/indicators.hpp.
// The program returns 0 when every check passes and 1 when any check fails.

#include "avbt/indicators.hpp"

#include <cmath>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

int failures = 0;

constexpr double kTolerance = 1e-9;
const double NaN = std::numeric_limits<double>::quiet_NaN();

void fail(const std::string& label, const std::string& message) {
    ++failures;
    std::printf("FAIL %s: %s\n", label.c_str(), message.c_str());
}

// Checks one value. An expected NaN matches only an actual NaN.
void check_value(const std::string& label, double actual, double expected) {
    if (std::isnan(expected)) {
        if (!std::isnan(actual)) {
            fail(label, "expected NaN, got " + std::to_string(actual));
        }
        return;
    }
    if (std::isnan(actual)) {
        fail(label, "expected " + std::to_string(expected) + ", got NaN");
        return;
    }
    if (std::abs(actual - expected) >= kTolerance) {
        fail(label, "expected " + std::to_string(expected) + ", got " +
                        std::to_string(actual));
    }
}

// Checks the size first, then each value.
void check_series(const std::string& label, const std::vector<double>& actual,
                  const std::vector<double>& expected) {
    if (actual.size() != expected.size()) {
        fail(label, "expected size " + std::to_string(expected.size()) +
                        ", got " + std::to_string(actual.size()));
        return;
    }
    for (std::size_t i = 0; i < expected.size(); ++i) {
        check_value(label + " [" + std::to_string(i) + "]", actual[i],
                    expected[i]);
    }
}

// ---- sma ----

void test_sma_basic() {
    check_series("sma basic", avbt::sma({1, 2, 3, 4, 5}, 3),
                 {NaN, NaN, 2, 3, 4});
}

void test_sma_period_one() {
    check_series("sma period 1", avbt::sma({1, 2, 3}, 1), {1, 2, 3});
}

void test_sma_period_longer_than_series() {
    check_series("sma period > size", avbt::sma({1, 2}, 3), {NaN, NaN});
}

void test_sma_empty() {
    check_series("sma empty", avbt::sma({}, 3), {});
}

void test_sma_decimals() {
    check_series("sma decimals", avbt::sma({0.1, 0.2, 0.3}, 3),
                 {NaN, NaN, 0.2});
}

void test_sma_rejects_bad_period(int period) {
    const std::string label = "sma period " + std::to_string(period);
    try {
        avbt::sma({1, 2, 3}, period);
        fail(label, "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

// ---- prior_max ----

void test_prior_max_basic() {
    check_series("prior_max basic", avbt::prior_max({3, 1, 4, 1, 5, 9, 2}, 3),
                 {NaN, NaN, NaN, 4, 4, 5, 9});
}

// The old maximum (9 at index 0) reaches the top after it has left the window.
void test_prior_max_old_max_leaves() {
    check_series("prior_max old max leaves", avbt::prior_max({9, 1, 1, 1}, 2),
                 {NaN, NaN, 9, 1});
}

// With a window of 1, each output is the bar just before it.
void test_prior_max_window_one() {
    check_series("prior_max n 1", avbt::prior_max({5, 3, 8}, 1),
                 {NaN, 5, 3});
}

void test_prior_max_window_equals_size() {
    check_series("prior_max n = size", avbt::prior_max({1, 2, 3}, 3),
                 {NaN, NaN, NaN});
}

void test_prior_max_empty() {
    check_series("prior_max empty", avbt::prior_max({}, 3), {});
}

void test_prior_max_rejects_bad_n(int n) {
    const std::string label = "prior_max n " + std::to_string(n);
    try {
        avbt::prior_max({1, 2, 3}, n);
        fail(label, "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

// Falling series: the max is always the oldest bar, so the front expires every bar.
void test_prior_max_falling() {
    check_series("prior_max falling", avbt::prior_max({5, 4, 3, 2, 1}, 2),
                 {NaN, NaN, 5, 4, 3});
}

// Equal values: popping the older equal value must not lose the max.
void test_prior_max_ties() {
    check_series("prior_max ties", avbt::prior_max({5, 5, 1, 1, 1}, 2),
                 {NaN, NaN, 5, 5, 1});
}

// ---- prior_min ----

void test_prior_min_basic() {
    check_series("prior_min basic", avbt::prior_min({3, 1, 4, 1, 5, 9, 2}, 3),
                 {NaN, NaN, NaN, 1, 1, 1, 1});
}

// The old minimum (1 at index 0) reaches the top after it has left the window.
void test_prior_min_old_min_leaves() {
    check_series("prior_min old min leaves", avbt::prior_min({1, 9, 9, 9}, 2),
                 {NaN, NaN, 1, 9});
}

// Bar t is excluded: a new low at t does not show until t + 1.
void test_prior_min_excludes_current_bar() {
    check_series("prior_min excludes bar t", avbt::prior_min({5, 4, 0, 6}, 2),
                 {NaN, NaN, 4, 0});
}

void test_prior_min_window_one() {
    check_series("prior_min n 1", avbt::prior_min({5, 3, 8}, 1),
                 {NaN, 5, 3});
}

void test_prior_min_empty() {
    check_series("prior_min empty", avbt::prior_min({}, 3), {});
}

void test_prior_min_rejects_bad_n(int n) {
    const std::string label = "prior_min n " + std::to_string(n);
    try {
        avbt::prior_min({1, 2, 3}, n);
        fail(label, "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

// Rising series: the min is always the oldest bar, so the front expires every bar.
void test_prior_min_rising() {
    check_series("prior_min rising", avbt::prior_min({1, 2, 3, 4, 5}, 2),
                 {NaN, NaN, 1, 2, 3});
}

void test_prior_min_ties() {
    check_series("prior_min ties", avbt::prior_min({1, 1, 5, 5, 5}, 2),
                 {NaN, NaN, 1, 1, 5});
}

// ---- pct_change ----

void test_pct_change_lag_one() {
    check_series("pct_change lag 1", avbt::pct_change({100, 110, 99}, 1),
                 {NaN, 0.1, -0.1});
}

void test_pct_change_lag_two() {
    check_series("pct_change lag 2", avbt::pct_change({100, 50, 150, 25}, 2),
                 {NaN, NaN, 0.5, -0.5});
}

void test_pct_change_lag_longer_than_series() {
    check_series("pct_change lag > size", avbt::pct_change({1, 2}, 24),
                 {NaN, NaN});
}

void test_pct_change_empty() {
    check_series("pct_change empty", avbt::pct_change({}, 1), {});
}

void test_pct_change_rejects_bad_lag(int lag) {
    const std::string label = "pct_change lag " + std::to_string(lag);
    try {
        avbt::pct_change({1, 2, 3}, lag);
        fail(label, "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

// ---- true_range and atr ----

avbt::Bars make_bars(const std::vector<double>& high,
                     const std::vector<double>& low,
                     const std::vector<double>& close) {
    avbt::Bars bars;
    bars.high = high;
    bars.low = low;
    bars.close = close;
    return bars;
}

// True ranges, worked by hand:
//   bar 0: no previous close        -> 10 - 8             = 2
//   bar 1: inside range wins (tie)  -> max(3, 3, 0)       = 3
//   bar 2: inside range wins (tie)  -> max(1, 0.5, 1)     = 1
//   bar 3: gap up from 10.5         -> max(1, 4.5, 3.5)   = 4.5
//   bar 4: gap down from 14         -> max(1, 1, 2)       = 2
avbt::Bars sample_bars() {
    return make_bars({10, 12, 11, 15, 13},
                     {8, 9, 10, 14, 12},
                     {9, 11, 10.5, 14, 12});
}

void test_true_range_basic() {
    check_series("true_range basic", avbt::true_range(sample_bars()),
                 {2, 3, 1, 4.5, 2});
}

// A missing previous close leaves only that bar undefined.
void test_true_range_missing_close() {
    avbt::Bars bars = sample_bars();
    bars.close[2] = NaN;
    check_series("true_range missing close", avbt::true_range(bars),
                 {2, 3, 1, NaN, 2});
}

void test_true_range_empty() {
    check_series("true_range empty", avbt::true_range(make_bars({}, {}, {})), {});
}

void test_true_range_rejects_mismatched_sizes() {
    try {
        avbt::true_range(make_bars({1, 2}, {1}, {1, 2}));
        fail("true_range sizes", "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

// Seed at index 2 is mean(TR[1], TR[2]) = (3 + 1) / 2 = 2, not (2 + 3) / 2.
// Then (2 * 1 + 4.5) / 2 = 3.25, then (3.25 * 1 + 2) / 2 = 2.625.
void test_atr_basic() {
    check_series("atr n 2", avbt::atr(sample_bars(), 2),
                 {NaN, NaN, 2, 3.25, 2.625});
}

// Seed at index 3 is (3 + 1 + 4.5) / 3 = 8.5 / 3.
// Then (8.5 / 3 * 2 + 2) / 3 = 23 / 9.
void test_atr_n_three() {
    check_series("atr n 3", avbt::atr(sample_bars(), 3),
                 {NaN, NaN, NaN, 8.5 / 3, 23.0 / 9});
}

// With n = 1 the seed is TR[1] and each later value is just TR[t].
void test_atr_n_one() {
    check_series("atr n 1", avbt::atr(sample_bars(), 1),
                 {NaN, 3, 1, 4.5, 2});
}

// n = size - 1 is the last n with a value; n = size has none.
void test_atr_n_near_size() {
    check_series("atr n 4", avbt::atr(sample_bars(), 4),
                 {NaN, NaN, NaN, NaN, (3 + 1 + 4.5 + 2) / 4.0});
    check_series("atr n 5", avbt::atr(sample_bars(), 5),
                 {NaN, NaN, NaN, NaN, NaN});
}

// Once a true range is undefined, every later ATR stays undefined.
void test_atr_gap_carries_forward() {
    avbt::Bars bars = sample_bars();
    bars.close[2] = NaN;
    check_series("atr gap", avbt::atr(bars, 2), {NaN, NaN, 2, NaN, NaN});
}

void test_atr_rejects_bad_n(int n) {
    const std::string label = "atr n " + std::to_string(n);
    try {
        avbt::atr(sample_bars(), n);
        fail(label, "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

// ---- hour_of_day ----

avbt::Bars make_ts_bars(const std::vector<int64_t>& ts, avbt::Timeframe tf) {
    avbt::Bars bars;
    bars.timeframe = tf;
    bars.ts = ts;
    return bars;
}

// 0 is 00:00, 3600 is 01:00, 16 * 3600 + 59 is 16:00:59, 86400 + 5 * 3600 is day 2 05:00.
void test_hour_of_day_basic() {
    check_series("hour_of_day basic",
                 avbt::hour_of_day(make_ts_bars({0, 3600, 16 * 3600 + 59, 86400 + 5 * 3600}, avbt::Timeframe::Hour1)),
                 {0, 1, 16, 5});
}

// Exactly midnight is hour 0; 23:59:59 is still hour 23.
void test_hour_of_day_day_edges() {
    check_series("hour_of_day day edges",
                 avbt::hour_of_day(make_ts_bars({86400, 86400 - 1, 1699920000}, avbt::Timeframe::Min1)),
                 {0, 23, 0});
}

// Before 1970: -1 is 23:59:59, -3600 is 23:00, -3601 is 22:59:59.
void test_hour_of_day_before_1970() {
    check_series("hour_of_day before 1970",
                 avbt::hour_of_day(make_ts_bars({-1, -3600, -3601, -86400}, avbt::Timeframe::Hour1)),
                 {23, 23, 22, 0});
}

void test_hour_of_day_hourly_bars_ok() {
    check_series("hour_of_day hourly bars",
                 avbt::hour_of_day(make_ts_bars({7200}, avbt::Timeframe::Hour1)), {2});
}

void test_hour_of_day_rejects_coarse_bars() {
    try {
        avbt::hour_of_day(make_ts_bars({0}, avbt::Timeframe::Hour4));
        fail("hour_of_day 4-hour bars", "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

void test_hour_of_day_empty() {
    check_series("hour_of_day empty", avbt::hour_of_day(make_ts_bars({}, avbt::Timeframe::Hour1)), {});
}

// ---- bar_change ----

avbt::Bars make_ohlc_bars(const std::vector<double>& open,
                          const std::vector<double>& high,
                          const std::vector<double>& low,
                          const std::vector<double>& close) {
    avbt::Bars bars = make_bars(high, low, close);
    bars.open = open;
    return bars;
}

// open, high, low, close by bar:
//   bar 0: 100, 110,  90, 100
//   bar 1: 105, 120, 100, 110
//   bar 2:  99, 121,  88,  99
avbt::Bars change_bars() {
    return make_ohlc_bars({100, 105, 99}, {110, 120, 121}, {90, 100, 88},
                          {100, 110, 99});
}

// 120 / 100 - 1 = 0.2, 121 / 110 - 1 = 0.1.
void test_bar_change_high_close() {
    check_series("bar_change high/close",
                 avbt::bar_change(change_bars(), avbt::Field::High, avbt::Field::Close, 1),
                 {NaN, 0.2, 0.1});
}

// The overnight gap: 105 / 100 - 1 = 0.05, 99 / 110 - 1 = -0.1.
void test_bar_change_open_close_gap() {
    check_series("bar_change open/close",
                 avbt::bar_change(change_bars(), avbt::Field::Open, avbt::Field::Close, 1),
                 {NaN, 0.05, -0.1});
}

// 100 / 100 - 1 = 0, 88 / 110 - 1 = -0.2.
void test_bar_change_low_close() {
    check_series("bar_change low/close",
                 avbt::bar_change(change_bars(), avbt::Field::Low, avbt::Field::Close, 1),
                 {NaN, 0, -0.2});
}

// 99 / 100 - 1 = -0.01.
void test_bar_change_lag_two() {
    check_series("bar_change lag 2",
                 avbt::bar_change(change_bars(), avbt::Field::Close, avbt::Field::Close, 2),
                 {NaN, NaN, -0.01});
}

void test_bar_change_lag_longer_than_series() {
    check_series("bar_change lag > size",
                 avbt::bar_change(change_bars(), avbt::Field::High, avbt::Field::Close, 5),
                 {NaN, NaN, NaN});
}

// A missing value on either side leaves only the bars that read it undefined.
void test_bar_change_missing_value() {
    avbt::Bars now_missing = change_bars();
    now_missing.high[1] = NaN;
    check_series("bar_change missing now",
                 avbt::bar_change(now_missing, avbt::Field::High, avbt::Field::Close, 1),
                 {NaN, NaN, 0.1});

    avbt::Bars then_missing = change_bars();
    then_missing.close[1] = NaN;
    check_series("bar_change missing then",
                 avbt::bar_change(then_missing, avbt::Field::High, avbt::Field::Close, 1),
                 {NaN, 0.2, NaN});
}

void test_bar_change_rejects_bad_lag(int lag) {
    const std::string label = "bar_change lag " + std::to_string(lag);
    try {
        avbt::bar_change(change_bars(), avbt::Field::High, avbt::Field::Close, lag);
        fail(label, "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

// ---- chandelier ----

// On sample_bars with n = 2: atr is {NaN, NaN, 2, 3.25, 2.625},
// prior_max(high) is {NaN, NaN, 12, 12, 15}, prior_min(low) is {NaN, NaN, 8, 9, 10}.
// Long, k = 1: 12 - 2 = 10, 12 - 3.25 = 8.75, 15 - 2.625 = 12.375.
// The new high of 15 at bar 3 raises the line only at bar 4.
void test_chandelier_long() {
    check_series("chandelier long",
                 avbt::chandelier(sample_bars(), 2, 1, avbt::Side::Long),
                 {NaN, NaN, 10, 8.75, 12.375});
}

// Short, k = 2: 8 + 4 = 12, 9 + 6.5 = 15.5, 10 + 5.25 = 15.25.
void test_chandelier_short() {
    check_series("chandelier short",
                 avbt::chandelier(sample_bars(), 2, 2, avbt::Side::Short),
                 {NaN, NaN, 12, 15.5, 15.25});
}

// n = 3: atr is {NaN, NaN, NaN, 8.5 / 3, 23 / 9}, prior_max(high) is {.., 12, 15}.
void test_chandelier_n_three() {
    check_series("chandelier n 3",
                 avbt::chandelier(sample_bars(), 3, 1, avbt::Side::Long),
                 {NaN, NaN, NaN, 12 - 8.5 / 3, 15 - 23.0 / 9});
}

// A gap in the ATR leaves the line undefined though the extreme is defined.
void test_chandelier_undefined_atr() {
    avbt::Bars bars = sample_bars();
    bars.close[2] = NaN;
    check_series("chandelier atr gap",
                 avbt::chandelier(bars, 2, 1, avbt::Side::Long),
                 {NaN, NaN, 10, NaN, NaN});
}

// True ranges: 1, max(2, 10, 8) = 10, max(1, 7, 8) = 8, 1, 1.
// atr n 2: (10 + 8) / 2 = 9, (9 + 1) / 2 = 5, (5 + 1) / 2 = 3.
// prior_max(high, 2): 20, 20, then 12 once the 20 at bar 1 leaves.
// Long, k = 1: 11, 15, 9.
void test_chandelier_old_high_leaves() {
    avbt::Bars bars = make_bars({10, 20, 12, 12, 12}, {9, 18, 11, 11, 11},
                                {10, 19, 11.5, 11.5, 11.5});
    check_series("chandelier old high leaves",
                 avbt::chandelier(bars, 2, 1, avbt::Side::Long),
                 {NaN, NaN, 11, 15, 9});
}

void test_chandelier_rejects_bad_n(int n) {
    const std::string label = "chandelier n " + std::to_string(n);
    try {
        avbt::chandelier(sample_bars(), n, 1, avbt::Side::Long);
        fail(label, "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

void test_chandelier_rejects_bad_k(double k) {
    const std::string label = "chandelier k " + std::to_string(k);
    try {
        avbt::chandelier(sample_bars(), 2, k, avbt::Side::Long);
        fail(label, "expected std::invalid_argument, nothing was thrown");
    } catch (const std::invalid_argument&) {
        // Expected.
    }
}

}  // namespace

int main() {
    test_sma_basic();
    test_sma_period_one();
    test_sma_period_longer_than_series();
    test_sma_empty();
    test_sma_decimals();
    test_sma_rejects_bad_period(0);
    test_sma_rejects_bad_period(-1);

    test_prior_max_basic();
    test_prior_max_old_max_leaves();
    test_prior_max_window_one();
    test_prior_max_window_equals_size();
    test_prior_max_empty();
    test_prior_max_falling();
    test_prior_max_ties();
    test_prior_max_rejects_bad_n(0);
    test_prior_max_rejects_bad_n(-1);

    test_prior_min_basic();
    test_prior_min_old_min_leaves();
    test_prior_min_excludes_current_bar();
    test_prior_min_window_one();
    test_prior_min_empty();
    test_prior_min_rising();
    test_prior_min_ties();
    test_prior_min_rejects_bad_n(0);
    test_prior_min_rejects_bad_n(-1);

    test_pct_change_lag_one();
    test_pct_change_lag_two();
    test_pct_change_lag_longer_than_series();
    test_pct_change_empty();
    test_pct_change_rejects_bad_lag(0);
    test_pct_change_rejects_bad_lag(-1);

    test_true_range_basic();
    test_true_range_missing_close();
    test_true_range_empty();
    test_true_range_rejects_mismatched_sizes();

    test_atr_basic();
    test_atr_n_three();
    test_atr_n_one();
    test_atr_n_near_size();
    test_atr_gap_carries_forward();
    test_atr_rejects_bad_n(0);
    test_atr_rejects_bad_n(-1);

    test_hour_of_day_basic();
    test_hour_of_day_day_edges();
    test_hour_of_day_before_1970();
    test_hour_of_day_hourly_bars_ok();
    test_hour_of_day_rejects_coarse_bars();
    test_hour_of_day_empty();

    test_bar_change_high_close();
    test_bar_change_open_close_gap();
    test_bar_change_low_close();
    test_bar_change_lag_two();
    test_bar_change_lag_longer_than_series();
    test_bar_change_missing_value();
    test_bar_change_rejects_bad_lag(0);
    test_bar_change_rejects_bad_lag(-1);

    test_chandelier_long();
    test_chandelier_short();
    test_chandelier_n_three();
    test_chandelier_undefined_atr();
    test_chandelier_old_high_leaves();
    test_chandelier_rejects_bad_n(0);
    test_chandelier_rejects_bad_k(0);
    test_chandelier_rejects_bad_k(-1);
    test_chandelier_rejects_bad_k(NaN);

    if (failures == 0) {
        std::printf("All checks passed.\n");
        return 0;
    }
    std::printf("%d check(s) failed.\n", failures);
    return 1;
}
