#pragma once
#include <vector>
#include <cstdint>

namespace avbt {

struct Bars {
    int bar_seconds = 0;
    std::vector<int64_t> ts;
    std::vector<double> open, high, low, close;
    std::vector<int> minutes;
};

std::vector<double> sma(const std::vector<double>& series, int period);

std::vector<double> prior_max(const std::vector<double>& series, int period);

std::vector<double> prior_min(const std::vector<double>& series, int period);

std::vector<double> pct_change(const std::vector<double>& series, int lag);

std::vector<double> true_range(const Bars& bars);

std::vector<double> atr(const Bars& bars, int n);


}



