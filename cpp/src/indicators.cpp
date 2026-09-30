#include "avbt/indicators.hpp"
#include <cmath>
#include <stdexcept>
#include <cstdio>
#include <utility>
#include <queue>

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
    std::priority_queue<std::pair<double, int>> max_queue;

    for (int i = 0; i < series.size(); i++) {

        if (i >= n) {
            // remove elements that are outside the current window
            while (max_queue.top().second < i - n) {
                max_queue.pop(); 
            }
            prior_max[i] = max_queue.top().first;
        } else {
            prior_max[i] = std::nan("");
        }

        max_queue.push(std::pair<double, int>(series[i], i));
    }

    return prior_max;
}
} // namespace avbt