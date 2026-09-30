#include "avbt/indicators.hpp"
#include <cmath>
#include <stdexcept>
#include <cstdio>
#include <utility>
#include <deque>
#include <algorithm>

namespace avbt {

std::vector<double> sma(const std::vector<double>& series, int period) {

    if (period < 1) {
        throw std::invalid_argument("Period must be greater than 0");
    }

    std::vector<double> sma(series.size());
    double sum = 0;

    for (int i = 0; i < series.size(); i++) {

        sum += series[i];
        if (i < period - 1) {
            sma[i] = std::nan("");
        } else {
            sma[i] = sum / period;
            sum -= series[i - period + 1];
        }
    }

    return sma;
}


std::vector<double> prior_max(const std::vector<double>& series, int n) {

    if (n < 1) {
        throw std::invalid_argument("n must be greater than 0");
    }

    std::vector<double> prior_max(series.size());
    // indices whose values decrease from front to back; the front is the max
    std::deque<int> window;

    for (int i = 0; i < series.size(); i++) {

        // drop the front once it is outside the window [i - n, i - 1]
        while (!window.empty() && window.front() < i - n) {
            window.pop_front();
        }

        if (i >= n) {
            prior_max[i] = series[window.front()];
        } else {
            prior_max[i] = std::nan("");
        }

        // an older value that is no bigger than series[i] can never be the max again
        while (!window.empty() && series[window.back()] <= series[i]) {
            window.pop_back();
        }
        window.push_back(i);
    }

    return prior_max;
}


std::vector<double> prior_min(const std::vector<double>& series, int n) {

    if (n < 1) {
        throw std::invalid_argument("n must be greater than 0");
    }

    std::vector<double> prior_min(series.size());
    // indices whose values increase from front to back; the front is the min
    std::deque<int> window;

    for (int i = 0; i < series.size(); i++) {

        // drop the front once it is outside the window [i - n, i - 1]
        while (!window.empty() && window.front() < i - n) {
            window.pop_front();
        }

        if (i >= n) {
            prior_min[i] = series[window.front()];
        } else {
            prior_min[i] = std::nan("");
        }

        // an older value that is no smaller than series[i] can never be the min again
        while (!window.empty() && series[window.back()] >= series[i]) {
            window.pop_back();
        }
        window.push_back(i);
    }

    return prior_min;
}


std::vector<double> pct_change(const std::vector<double>& series, int lag) {

    if (lag < 1) {
        throw std::invalid_argument("lag must be greater than 0");
    }

    std::vector<double> pct_change(series.size());

    for (int i = 0; i < series.size(); i++) {

        if (i < lag) {
            pct_change[i] = std::nan("");
        } else {
            pct_change[i] = (series[i] - series[i - lag]) / series[i - lag];
        }
    }

    return pct_change;
}


std::vector<double> true_range(const Bars& bars) {

    if (bars.low.size() != bars.high.size() || bars.close.size() != bars.high.size()) {
        throw std::invalid_argument("high, low, and close must be the same size");
    }

    std::vector<double> true_range(bars.high.size());

    for (int i = 0; i < bars.high.size(); i++) {

        double high = bars.high[i];
        double low = bars.low[i];

        // a missing high or low leaves the bar undefined
        if (!std::isfinite(high) || !std::isfinite(low)) {
            true_range[i] = std::nan("");
            continue;
        }

        // bar 0 has no previous close
        if (i == 0) {
            true_range[i] = high - low;
            continue;
        }

        double prev_close = bars.close[i - 1];
        if (!std::isfinite(prev_close)) {
            true_range[i] = std::nan("");
            continue;
        }

        true_range[i] = std::max({high - low,
                                  std::abs(high - prev_close),
                                  std::abs(low - prev_close)});
    }

    return true_range;
}


std::vector<double> atr(const Bars& bars, int n) {

    if (n < 1) {
        throw std::invalid_argument("n must be greater than 0");
    }

    std::vector<double> tr = true_range(bars);
    std::vector<double> atr(tr.size(), std::nan(""));

    if (tr.size() <= n) {
        return atr;
    }

    // seed: mean of TR[1..n], skipping TR[0] which has no previous close
    double sum = 0;
    for (int i = 1; i <= n; i++) {
        sum += tr[i];
    }
    atr[n] = sum / n;

    // Wilder smoothing; a NaN carries forward, so a gap stays undefined
    for (int i = n + 1; i < tr.size(); i++) {
        atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n;
    }

    return atr;
}
} // namespace avbt