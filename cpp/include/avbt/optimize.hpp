#pragma once
#include <algorithm>
#include <cmath>
#include <functional>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "avbt/backtest.hpp"

namespace avbt {

// Sharpe of the equity sampled hourly, annualized over 24 * 365 hours, as in
// examples/state_trend.py. NaN when equity never moves.
inline double sharpe(const Result& r) {
    std::size_t step = std::max<int64_t>(1, 3600 / seconds(r.timeframe));
    std::vector<double> returns;
    for (std::size_t i = step; i < r.equity.size(); i += step) {
        returns.push_back(r.equity[i] / r.equity[i - step] - 1);
    }
    if (returns.size() < 2) return std::nan("");
    double mean = 0, var = 0;
    for (double x : returns) mean += x / returns.size();
    for (double x : returns) var += (x - mean) * (x - mean) / (returns.size() - 1);
    return var > 0 ? mean / std::sqrt(var) * std::sqrt(24.0 * 365) : std::nan("");
}

// One parameter the optimizer may change. choices[i] writes one value into
// the params; labels[i] names that value for printing.
template <class P>
struct Knob {
    std::string name;
    std::vector<std::string> labels;
    std::vector<std::function<void(P&)>> choices;
};

// One backtest the search ran. choice[k] indexes knob k's choices.
struct Run {
    int round = 0;
    std::vector<int> choice;
    double sharpe = 0.0;
};

template <class P>
struct Search {
    P best;
    std::vector<int> choice;
    double sharpe = 0.0;
    // The base timeframe every run stepped on.
    Timeframe timeframe = Timeframe::Min1;
    std::vector<Run> runs;
};

// Greedy search for the highest Sharpe. Every knob starts at its first
// choice; a parameter with no knob keeps its value in `start`. Round r tries
// every combination of one pair of knobs, the pairs in a fixed order (0,1),
// (0,2), ..., (1,2), ..., the other knobs at the best so far. Any higher
// Sharpe becomes the best; NaN never does. Each combination runs once. The
// search stops early after a full pass over the pairs brings no gain. ADR 0013.
template <Strategy S>
Search<typename S::Params> optimize(const typename S::Params& start,
                                    const std::vector<Knob<typename S::Params>>& knobs,
                                    const Markets& markets, const MarketCosts& costs,
                                    PortfolioSettings settings, int rounds) {
    using P = typename S::Params;
    std::vector<std::pair<int, int>> pairs;
    for (int a = 0; a < static_cast<int>(knobs.size()); ++a) {
        if (knobs[a].choices.empty()) throw std::invalid_argument(knobs[a].name + ": no choices");
        for (int b = a + 1; b < static_cast<int>(knobs.size()); ++b) pairs.push_back({a, b});
    }
    if (pairs.empty()) throw std::invalid_argument("optimize needs at least two knobs");

    auto params_of = [&](const std::vector<int>& choice) {
        P p = start;
        for (std::size_t k = 0; k < knobs.size(); ++k) knobs[k].choices[choice[k]](p);
        return p;
    };
    Search<P> out{.timeframe = markets.timeframe()};
    std::map<std::vector<int>, double> seen;
    auto score = [&](const std::vector<int>& choice, int round) {
        auto it = seen.find(choice);
        if (it != seen.end()) return it->second;
        S strategy;
        strategy.params = params_of(choice);
        double s = sharpe(backtest(strategy, markets, costs, settings));
        out.runs.push_back({round, choice, s});
        return seen[choice] = s;
    };
    auto beats = [](double s, double top) { return !std::isnan(s) && (std::isnan(top) || s > top); };

    std::vector<int> best(knobs.size(), 0);
    double top = score(best, 0);
    std::size_t since_gain = 0;
    for (int r = 1; r <= rounds && since_gain < pairs.size(); ++r) {
        auto [a, b] = pairs[(r - 1) % pairs.size()];
        std::vector<int> round_best = best;
        for (int i = 0; i < static_cast<int>(knobs[a].choices.size()); ++i) {
            for (int j = 0; j < static_cast<int>(knobs[b].choices.size()); ++j) {
                std::vector<int> c = best;
                c[a] = i;
                c[b] = j;
                double s = score(c, r);
                if (beats(s, top)) top = s, round_best = c;
            }
        }
        since_gain = round_best == best ? since_gain + 1 : 0;
        best = round_best;
    }
    out.best = params_of(best);
    out.choice = best;
    out.sharpe = top;
    return out;
}

}  // namespace avbt
