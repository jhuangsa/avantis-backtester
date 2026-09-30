#include "avbt/indicators.hpp"
#include <cmath>
#include <stdexcept>
#include <cstdio>
#include <utility>
#include <deque>
#include <algorithm>

namespace avbt {

int64_t seconds(Timeframe tf) {
    constexpr int64_t minute = 60, hour = 3600, day = 86400;
    switch (tf) {
        case Timeframe::Min1: return minute;
        case Timeframe::Min3: return 3 * minute;
        case Timeframe::Min5: return 5 * minute;
        case Timeframe::Min15: return 15 * minute;
        case Timeframe::Min30: return 30 * minute;
        case Timeframe::Hour1: return hour;
        case Timeframe::Hour4: return 4 * hour;
        case Timeframe::Hour8: return 8 * hour;
        case Timeframe::Hour12: return 12 * hour;
        case Timeframe::Day1: return day;
        case Timeframe::Week1: return 7 * day;
        case Timeframe::Month1: return 31 * day;
    }
    throw std::invalid_argument("unknown timeframe");
}

const char* name(Timeframe tf) {
    switch (tf) {
        case Timeframe::Min1: return "1 minute";
        case Timeframe::Min3: return "3 minutes";
        case Timeframe::Min5: return "5 minutes";
        case Timeframe::Min15: return "15 minutes";
        case Timeframe::Min30: return "30 minutes";
        case Timeframe::Hour1: return "1 hour";
        case Timeframe::Hour4: return "4 hours";
        case Timeframe::Hour8: return "8 hours";
        case Timeframe::Hour12: return "12 hours";
        case Timeframe::Day1: return "1 day";
        case Timeframe::Week1: return "1 week";
        case Timeframe::Month1: return "1 month";
    }
    throw std::invalid_argument("unknown timeframe");
}

int last_closed(const Bars& bars, int64_t now) {
    // k is the last bar that has opened by now. Every bar before it has
    // closed, because the bar after it has opened.
    auto after = std::upper_bound(bars.ts.begin(), bars.ts.end(), now);
    int k = static_cast<int>(after - bars.ts.begin()) - 1;
    if (k < 0) return -1;
    // Bar k itself has closed only if it is the last bar and its length has passed.
    bool last = k + 1 == static_cast<int>(bars.ts.size());
    if (last && bars.ts[k] + seconds(bars.timeframe) <= now) return k;
    return k - 1;
}

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


// The UTC hour at which each bar opens, from ts (UTC seconds at the bar's start).
// Under next-open fill, a rule on hour_of_day == 16 is decided at the close of
// the 16:00 bar (17:00) and fills at the open of the 17:00 bar.
// UTC only: the caller shifts ts for another zone.
std::vector<double> hour_of_day(const Bars& bars) {

    // on bars coarser than an hour, the opening hour means nothing
    if (bars.timeframe > Timeframe::Hour1) {
        throw std::invalid_argument("hour_of_day needs bars of an hour or less");
    }

    std::vector<double> hour_of_day(bars.ts.size());

    for (int i = 0; i < bars.ts.size(); i++) {

        // floor division, so a time before 1970 still lands in 0..23
        int64_t seconds_into_day = bars.ts[i] % 86400;
        if (seconds_into_day < 0) {
            seconds_into_day += 86400;
        }
        hour_of_day[i] = static_cast<double>(seconds_into_day / 3600);
    }

    return hour_of_day;
}


static const std::vector<double>& field_of(const Bars& bars, Field field) {
    switch (field) {
        case Field::Open: return bars.open;
        case Field::High: return bars.high;
        case Field::Low: return bars.low;
        case Field::Close: return bars.close;
    }
    throw std::invalid_argument("unknown field");
}


std::vector<double> bar_change(const Bars& bars, Field now_field, Field then_field, int lag) {

    if (lag < 1) {
        throw std::invalid_argument("lag must be greater than 0");
    }

    const std::vector<double>& now = field_of(bars, now_field);
    const std::vector<double>& then = field_of(bars, then_field);
    if (now.size() != then.size()) {
        throw std::invalid_argument("fields must be the same size");
    }

    std::vector<double> bar_change(now.size());

    for (int i = 0; i < now.size(); i++) {

        // bar i - lag does not exist yet, or a value is missing
        if (i < lag || !std::isfinite(now[i]) || !std::isfinite(then[i - lag])) {
            bar_change[i] = std::nan("");
            continue;
        }

        bar_change[i] = now[i] / then[i - lag] - 1;
    }

    return bar_change;
}


// The fixed-window line only. The extreme covers [i - n, i - 1], so a new high
// enters the extreme one bar later (its own range still widens that bar's ATR),
// and an old high leaving the window lowers the line.
// The ratchet while a trade is open depends on the entry bar; it belongs in the engine.
std::vector<double> chandelier(const Bars& bars, int n, double k, Side side) {

    if (n < 1) {
        throw std::invalid_argument("n must be greater than 0");
    }
    if (!(k > 0)) {
        throw std::invalid_argument("k must be greater than 0");
    }

    std::vector<double> range = atr(bars, n);
    std::vector<double> extreme = side == Side::Long ? prior_max(bars.high, n)
                                                     : prior_min(bars.low, n);
    double sign = side == Side::Long ? -1.0 : 1.0;

    std::vector<double> chandelier(extreme.size());

    for (int i = 0; i < extreme.size(); i++) {

        // undefined until both the extreme and the ATR are
        if (!std::isfinite(extreme[i]) || !std::isfinite(range[i])) {
            chandelier[i] = std::nan("");
            continue;
        }

        chandelier[i] = extreme[i] + sign * k * range[i];
    }

    return chandelier;
}
} // namespace avbt