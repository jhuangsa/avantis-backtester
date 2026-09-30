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

    if (failures == 0) {
        std::printf("All checks passed.\n");
        return 0;
    }
    std::printf("%d check(s) failed.\n", failures);
    return 1;
}
