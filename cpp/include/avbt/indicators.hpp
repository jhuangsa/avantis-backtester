#pragma once
#include <vector>
#include <cstdint>

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
};

// The index of the last bar that has fully closed at time `now` (UTC
// seconds), or -1 when none has. Bar i has closed once bar i+1 has opened;
// the last bar, once seconds(timeframe) have passed. A strategy reads an
// hourly bar through this, so at 10:30 it sees the 09:00 bar, not the 10:00.
int last_closed(const Bars& bars, int64_t now);

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

enum class Side { Long, Short };

// Long: prior_max(high, n) - k * atr(n). Short: prior_min(low, n) + k * atr(n).
std::vector<double> chandelier(const Bars& bars, int n, double k, Side side);


}



