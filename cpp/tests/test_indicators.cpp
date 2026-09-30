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
    test_prior_max_rejects_bad_n(0);
    test_prior_max_rejects_bad_n(-1);

    test_prior_min_basic();
    test_prior_min_old_min_leaves();
    test_prior_min_excludes_current_bar();
    test_prior_min_window_one();
    test_prior_min_empty();
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

    if (failures == 0) {
        std::printf("All checks passed.\n");
        return 0;
    }
    std::printf("%d check(s) failed.\n", failures);
    return 1;
}
