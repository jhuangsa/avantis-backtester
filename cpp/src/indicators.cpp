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

Sma::Sma(int n) : n_(n) {
    if (n < 1) {
        throw std::invalid_argument("Period must be greater than 0");
    }
}

double Sma::update(double x) {
    sum_ += x;
    window_.push_back(x);
    if (static_cast<int>(window_.size()) < n_) {
        return std::nan("");
    }
    double out = sum_ / n_;
    sum_ -= window_.front();
    window_.pop_front();
    return out;
}

PriorMax::PriorMax(int n) : n_(n) {
    if (n < 1) {
        throw std::invalid_argument("n must be greater than 0");
    }
}

double PriorMax::update(double x) {
    long long i = count_++;
    // drop the front once it is outside the window [i - n, i - 1]
    while (!window_.empty() && window_.front().first < i - n_) {
        window_.pop_front();
    }
    double out = i >= n_ ? window_.front().second : std::nan("");
    // an older value that is no bigger than x can never be the max again
    while (!window_.empty() && window_.back().second <= x) {
        window_.pop_back();
    }
    window_.push_back({i, x});
    return out;
}

PriorMin::PriorMin(int n) : n_(n) {
    if (n < 1) {
        throw std::invalid_argument("n must be greater than 0");
    }
}

double PriorMin::update(double x) {
    long long i = count_++;
    while (!window_.empty() && window_.front().first < i - n_) {
        window_.pop_front();
    }
    double out = i >= n_ ? window_.front().second : std::nan("");
    // an older value that is no smaller than x can never be the min again
    while (!window_.empty() && window_.back().second >= x) {
        window_.pop_back();
    }
    window_.push_back({i, x});
    return out;
}

PctChange::PctChange(int lag) : lag_(lag) {
    if (lag < 1) {
        throw std::invalid_argument("lag must be greater than 0");
    }
}

double PctChange::update(double x) {
    double out = std::nan("");
    if (static_cast<int>(window_.size()) == lag_) {
        double old = window_.front();
        window_.pop_front();
        out = (x - old) / old;
    }
    window_.push_back(x);
    return out;
}

BarChange::BarChange(int lag) : lag_(lag) {
    if (lag < 1) {
        throw std::invalid_argument("lag must be greater than 0");
    }
}

double BarChange::update(double now, double then) {
    double out = std::nan("");
    if (static_cast<int>(then_.size()) == lag_) {
        double old = then_.front();
        then_.pop_front();
        // a missing value leaves the bar undefined
        if (std::isfinite(now) && std::isfinite(old)) {
            out = now / old - 1;
        }
    }
    then_.push_back(then);
    return out;
}

double TrueRange::update(double high, double low, double close) {
    bool first = first_;
    double prev_close = prev_close_;
    first_ = false;
    prev_close_ = close;
    // a missing high or low leaves the bar undefined
    if (!std::isfinite(high) || !std::isfinite(low)) {
        return std::nan("");
    }
    // bar 0 has no previous close
    if (first) {
        return high - low;
    }
    if (!std::isfinite(prev_close)) {
        return std::nan("");
    }
    return std::max({high - low, std::abs(high - prev_close), std::abs(low - prev_close)});
}

Atr::Atr(int n) : n_(n) {
    if (n < 1) {
        throw std::invalid_argument("n must be greater than 0");
    }
}

double Atr::update(double high, double low, double close) {
    double tr = tr_.update(high, low, close);
    long long i = count_++;
    // seed: mean of TR[1..n], skipping TR[0] which has no previous close
    if (i == 0) {
        return std::nan("");
    }
    if (i < n_) {
        sum_ += tr;
        return std::nan("");
    }
    if (i == n_) {
        sum_ += tr;
        prev_ = sum_ / n_;
        return prev_;
    }
    // Wilder smoothing; a NaN carries forward, so a gap stays undefined
    prev_ = (prev_ * (n_ - 1) + tr) / n_;
    return prev_;
}

// The UTC hour at which a bar opens, from ts (UTC seconds at the bar's start).
// Under next-open fill, a rule on hour_of_day == 16 is decided at the close of
// the 16:00 bar (17:00) and fills at the open of the 17:00 bar.
// UTC only: the caller shifts ts for another zone.
HourOfDay::HourOfDay(Timeframe tf) {
    // on bars coarser than an hour, the opening hour means nothing
    if (tf > Timeframe::Hour1) {
        throw std::invalid_argument("hour_of_day needs bars of an hour or less");
    }
}

double HourOfDay::update(int64_t ts) const {
    // floor division, so a time before 1970 still lands in 0..23
    int64_t seconds_into_day = ts % 86400;
    if (seconds_into_day < 0) {
        seconds_into_day += 86400;
    }
    return static_cast<double>(seconds_into_day / 3600);
}

// The fixed-window line only. The extreme covers [i - n, i - 1], so a new high
// enters the extreme one bar later (its own range still widens that bar's ATR),
// and an old high leaving the window lowers the line.
// The ratchet while a trade is open depends on the entry bar; it belongs in the engine.
Chandelier::Chandelier(int n, double k, Side side) : atr_(n), max_(n), min_(n), k_(k), side_(side) {
    if (!(k > 0)) {
        throw std::invalid_argument("k must be greater than 0");
    }
}

double Chandelier::update(double high, double low, double close) {
    double range = atr_.update(high, low, close);
    double extreme = side_ == Side::Long ? max_.update(high) : min_.update(low);
    double sign = side_ == Side::Long ? -1.0 : 1.0;
    // undefined until both the extreme and the ATR are
    if (!std::isfinite(extreme) || !std::isfinite(range)) {
        return std::nan("");
    }
    return extreme + sign * k_ * range;
}


template <class Indicator>
static std::vector<double> each(Indicator ind, const std::vector<double>& series) {
    std::vector<double> out(series.size());
    for (size_t i = 0; i < series.size(); i++) {
        out[i] = ind.update(series[i]);
    }
    return out;
}

std::vector<double> sma(const std::vector<double>& series, int period) {
    return each(Sma(period), series);
}

std::vector<double> prior_max(const std::vector<double>& series, int n) {
    return each(PriorMax(n), series);
}

std::vector<double> prior_min(const std::vector<double>& series, int n) {
    return each(PriorMin(n), series);
}

std::vector<double> pct_change(const std::vector<double>& series, int lag) {
    return each(PctChange(lag), series);
}

static void check_hlc(const Bars& bars) {
    if (bars.low.size() != bars.high.size() || bars.close.size() != bars.high.size()) {
        throw std::invalid_argument("high, low, and close must be the same size");
    }
}

template <class Indicator>
static std::vector<double> each_hlc(Indicator ind, const Bars& bars) {
    check_hlc(bars);
    std::vector<double> out(bars.high.size());
    for (size_t i = 0; i < bars.high.size(); i++) {
        out[i] = ind.update(bars.high[i], bars.low[i], bars.close[i]);
    }
    return out;
}

std::vector<double> true_range(const Bars& bars) {
    return each_hlc(TrueRange(), bars);
}

std::vector<double> atr(const Bars& bars, int n) {
    Atr ind(n);
    return each_hlc(ind, bars);
}

std::vector<double> hour_of_day(const Bars& bars) {
    HourOfDay ind(bars.timeframe);
    std::vector<double> out(bars.ts.size());
    for (size_t i = 0; i < bars.ts.size(); i++) {
        out[i] = ind.update(bars.ts[i]);
    }
    return out;
}

std::vector<double> chandelier(const Bars& bars, int n, double k, Side side) {
    Chandelier ind(n, k, side);
    return each_hlc(ind, bars);
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
    BarChange ind(lag);
    const std::vector<double>& now = field_of(bars, now_field);
    const std::vector<double>& then = field_of(bars, then_field);
    if (now.size() != then.size()) {
        throw std::invalid_argument("fields must be the same size");
    }
    std::vector<double> out(now.size());
    for (size_t i = 0; i < now.size(); i++) {
        out[i] = ind.update(now[i], then[i]);
    }
    return out;
}
} // namespace avbt
