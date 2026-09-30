#pragma once
#include <vector>
#include <cstdint>

namespace avbt {

struct Bars {
    // Length of every bar, in seconds. The same for the whole series:
    // 3600 for hourly bars, 900 for 15-minute bars.
    int bar_size_seconds = 0;
    std::vector<int64_t> ts;
    std::vector<double> open, high, low, close;
    // One number per bar: how many 1-minute candles with real prices
    // went into it, from 0 to bar_size_seconds / 60. Zero means the bar
    // had no data and was filled flat at the previous close.
    std::vector<int> minutes_with_data;
};

std::vector<double> sma(const std::vector<double>& series, int period);

std::vector<double> prior_max(const std::vector<double>& series, int period);

std::vector<double> prior_min(const std::vector<double>& series, int period);

std::vector<double> pct_change(const std::vector<double>& series, int lag);

std::vector<double> true_range(const Bars& bars);

std::vector<double> atr(const Bars& bars, int n);

// The UTC hour, 0 to 23, at which each bar opens.
std::vector<double> hour_of_day(const Bars& bars);

enum class Field { Open, High, Low, Close };

// now_field[i] / then_field[i - lag] - 1.
std::vector<double> bar_change(const Bars& bars, Field now_field, Field then_field, int lag);

enum class Side { Long, Short };

// Long: prior_max(high, n) - k * atr(n). Short: prior_min(low, n) + k * atr(n).
std::vector<double> chandelier(const Bars& bars, int n, double k, Side side);


}



