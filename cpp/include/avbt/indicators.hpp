#pragma once
#include <vector>
#include <cstdint>
#include <deque>
#include <utility>

namespace avbt {

// The bar lengths a trader can pick. Nothing in between: no 2.5-minute bars.
// Listed finest first, so a < b means a is the finer timeframe.
enum class Timeframe {
    Min1, Min3, Min5, Min15, Min30,
    Hour1, Hour4, Hour8, Hour12,
    Day1, Week1, Month1,
};

// Length of one bar, in seconds. A month is 28 to 31 days, so Month1 returns
// 31 days, the longest: last_closed uses it only for a series' last bar, and
// a bar taken as longer than it is can close late but never early.
int64_t seconds(Timeframe tf);

// Plain text for results and messages: "1 minute", "4 hours", "1 month".
const char* name(Timeframe tf);

// One instrument's bars on one timeframe. Python builds every timeframe
// from the same 1-minute candles and fills empty bars flat, so there are no
// gaps: bar i ends when bar i+1 opens.
struct Bars {
    Timeframe timeframe = Timeframe::Min1;
    // UTC seconds at which each bar opens, rising.
    std::vector<int64_t> ts;
    std::vector<double> open, high, low, close;
    // One number per bar: how many 1-minute candles with real prices went
    // into it. Zero means the bar had no data and was filled flat at the
    // previous close.
    std::vector<int> minutes_with_data;
    // Optional: empty, or one value per bar. No strategy reads it yet.
    std::vector<double> volume;
};

// The index of the last bar that has fully closed at time `now` (UTC
// seconds), or -1 when none has. Bar i has closed once bar i+1 has opened;
// the last bar, once seconds(timeframe) have passed. A strategy reads an
// hourly bar through this, so at 10:30 it sees the 09:00 bar, not the 10:00.
int last_closed(const Bars& bars, int64_t now);

// Rolling indicators: each takes one bar per update and returns that bar's
// value, NaN while undefined. The full-series functions below are loops over
// them, so live and backtest give bit-identical numbers. Constructors check
// their arguments and throw std::invalid_argument.

// Mean of the last n values. Keeps the running sum, never avg * n.
class Sma {
public:
    explicit Sma(int n);
    double update(double x);
private:
    int n_;
    double sum_ = 0;
    std::deque<double> window_;
};

// Max (min) of the n values before this one; the current value is excluded.
class PriorMax {
public:
    explicit PriorMax(int n);
    double update(double x);
private:
    int n_;
    long long count_ = 0;
    // (bar count, value), values decreasing front to back; the front is the max
    std::deque<std::pair<long long, double>> window_;
};

class PriorMin {
public:
    explicit PriorMin(int n);
    double update(double x);
private:
    int n_;
    long long count_ = 0;
    // (bar count, value), values increasing front to back; the front is the min
    std::deque<std::pair<long long, double>> window_;
};

// (x - x[lag ago]) / x[lag ago].
class PctChange {
public:
    explicit PctChange(int lag);
    double update(double x);
private:
    int lag_;
    std::deque<double> window_;
};

// now / then[lag ago] - 1; NaN if either is not finite.
class BarChange {
public:
    explicit BarChange(int lag);
    double update(double now, double then);
private:
    int lag_;
    std::deque<double> then_;
};

class TrueRange {
public:
    double update(double high, double low, double close);
private:
    bool first_ = true;
    double prev_close_ = 0;
};

// Seed: mean of TR[1..n] at bar n; then Wilder smoothing.
class Atr {
public:
    explicit Atr(int n);
    double update(double high, double low, double close);
private:
    int n_;
    TrueRange tr_;
    long long count_ = 0;
    double sum_ = 0;
    double prev_ = 0;
};

// Throws on bars coarser than an hour.
class HourOfDay {
public:
    explicit HourOfDay(Timeframe tf);
    double update(int64_t ts) const;
};

enum class Side { Long, Short };

class Chandelier {
public:
    Chandelier(int n, double k, Side side);
    double update(double high, double low, double close);
private:
    Atr atr_;
    PriorMax max_;
    PriorMin min_;
    double k_;
    Side side_;
};

std::vector<double> sma(const std::vector<double>& series, int period);

std::vector<double> prior_max(const std::vector<double>& series, int period);

std::vector<double> prior_min(const std::vector<double>& series, int period);

std::vector<double> pct_change(const std::vector<double>& series, int lag);

std::vector<double> true_range(const Bars& bars);

std::vector<double> atr(const Bars& bars, int n);

// The UTC hour, 0 to 23, at which each bar opens. Needs bars of an hour or less.
std::vector<double> hour_of_day(const Bars& bars);

enum class Field { Open, High, Low, Close };

// now_field[i] / then_field[i - lag] - 1.
std::vector<double> bar_change(const Bars& bars, Field now_field, Field then_field, int lag);

// Long: prior_max(high, n) - k * atr(n). Short: prior_min(low, n) + k * atr(n).
std::vector<double> chandelier(const Bars& bars, int n, double k, Side side);


}



