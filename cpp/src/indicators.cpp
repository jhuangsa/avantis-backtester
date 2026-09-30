#include "avbt/indicators.hpp"
#include <cmath>
#include <stdexcept>
#include <cstdio>
#include <utility>
#include <queue>
#include <functional>

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


std::vector<double> prior_min(const std::vector<double>& series, int n) {

    if (n < 1) {
        throw std::invalid_argument("n must be greater than 0");
    }

    std::vector<double> prior_min(series.size());
    // std::greater turns the priority queue into a min-heap
    std::priority_queue<std::pair<double, int>,
                        std::vector<std::pair<double, int>>,
                        std::greater<std::pair<double, int>>> min_queue;

    for (int i = 0; i < series.size(); i++) {

        if (i >= n) {
            // remove elements that are outside the current window
            while (min_queue.top().second < i - n) {
                min_queue.pop();
            }
            prior_min[i] = min_queue.top().first;
        } else {
            prior_min[i] = std::nan("");
        }

        min_queue.push(std::pair<double, int>(series[i], i));
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
} // namespace avbt