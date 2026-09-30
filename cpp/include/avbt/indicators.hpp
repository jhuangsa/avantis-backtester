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


}



