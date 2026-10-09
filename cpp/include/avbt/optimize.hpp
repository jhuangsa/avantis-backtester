#pragma once
#include <algorithm>
#include <cmath>
#include <functional>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "avbt/backtest.hpp"
#include "avbt/run.hpp"

namespace avbt {

// Daily Sharpe from clock time. Step i closes at clock[i] + seconds(timeframe).
// At each UTC midnight from the first close to the last, the day's value is
// the equity of the last step closed at or before it, so a day with no bars
// is a 0% return; the part-days at either end are dropped. -inf when any
// equity is at or below 0; NaN with fewer than 30 daily returns or none that
// vary; else mean / sample sd * sqrt(365). Throws std::invalid_argument
// when equity and clock differ in length. ADR 0013.
inline double sharpe(const Result& r) {
    if (r.equity.size() != r.clock.size()) {
        throw std::invalid_argument("Result has " + std::to_string(r.equity.size()) + " equity values and " +
                                    std::to_string(r.clock.size()) + " clock steps");
    }
    for (double e : r.equity) {
        if (e <= 0) return -std::numeric_limits<double>::infinity();
    }
    if (r.equity.empty()) return std::nan("");
    const int64_t day = 86400, len = seconds(r.timeframe);
    int64_t first = r.clock.front() + len, last = r.clock.back() + len;
    std::vector<double> days;
    std::size_t i = 0;
    for (int64_t midnight = (first + day - 1) / day * day; midnight <= last; midnight += day) {
        while (i + 1 < r.clock.size() && r.clock[i + 1] + len <= midnight) ++i;
        days.push_back(r.equity[i]);
    }
    std::vector<double> returns;
    for (std::size_t k = 1; k < days.size(); ++k) returns.push_back(days[k] / days[k - 1] - 1);
    if (returns.size() < 30) return std::nan("");
    double mean = 0, var = 0;
    for (double x : returns) mean += x / returns.size();
    for (double x : returns) var += (x - mean) * (x - mean) / (returns.size() - 1);
    return var > 0 ? mean / std::sqrt(var) * std::sqrt(365.0) : std::nan("");
}

// A result in four numbers. total_return is ending / starting equity - 1;
// max_drawdown is the largest fall from an equity peak, as a positive fraction.
struct Summary {
    double sharpe = 0.0, total_return = 0.0, max_drawdown = 0.0;
    int trades = 0;
};

inline Summary summary(const Result& r) {
    Summary s{.sharpe = sharpe(r), .trades = static_cast<int>(r.trades.size())};
    if (r.equity.empty()) return s;
    s.total_return = r.equity.back() / r.equity.front() - 1;
    double peak = r.equity.front();
    for (double e : r.equity) {
        peak = std::max(peak, e);
        s.max_drawdown = std::max(s.max_drawdown, 1 - e / peak);
    }
    return s;
}

// One param the search may change: its name and the values it may take,
// as run takes them. Combined strategies prefix the name with a. or b.
struct Knob {
    std::string name;
    std::vector<Value> values;
};

// What the search maximizes. With no max_drawdown (NaN), Sharpe. With one,
// total return over the runs whose max drawdown is at most max_drawdown (a
// positive fraction: 0.1 is a 10% fall); a run over the limit scores NaN, so
// it never wins. A rise in the best score of min_gain or less still becomes
// the best, but does not count as a gain for stopping.
struct Goal {
    double max_drawdown = std::nan("");
    double min_gain = 0.0;
};

inline double score(const Summary& s, const Goal& g) {
    if (std::isnan(g.max_drawdown)) return s.sharpe;
    return s.max_drawdown <= g.max_drawdown ? s.total_return : std::nan("");
}

// One backtest the search ran.
struct Run {
    int round = 0;
    Params params;
    Summary summary;
    double score = 0.0;
};

// score is NaN when no run met the drawdown limit; best is then the start.
struct Search {
    Params best;
    double score = 0.0;
    Summary summary;
    // The base timeframe every run stepped on.
    Timeframe timeframe = Timeframe::Min1;
    std::vector<Run> runs;
};

// Called after each round with (round, rounds, best score, best params);
// returning false stops the search after that round.
using Progress = std::function<bool(int, int, double, const Params&)>;

// Greedy search for the highest score of `run_of` under `goal`. Every knob
// starts at its first value; a param with no knob keeps its value in `start`.
// Round r tries every combination of one pair of knobs, the pairs in a fixed
// order (0,1), (0,2), ..., (1,2), ..., the other knobs at the best so far.
// Any higher score becomes the best; NaN never does. Each combination runs
// once. The search stops early after a full pass over the pairs brings no
// gain above goal.min_gain. ADR 0013, 0018.
inline Search search(const Params& start, const std::vector<Knob>& knobs, int rounds,
                     const std::function<Summary(const Params&)>& run_of, const Progress& progress = {},
                     const Goal& goal = {}) {
    std::vector<std::pair<int, int>> pairs;
    for (int a = 0; a < static_cast<int>(knobs.size()); ++a) {
        if (knobs[a].values.empty()) throw std::invalid_argument(knobs[a].name + ": no values");
        for (int b = a + 1; b < static_cast<int>(knobs.size()); ++b) pairs.push_back({a, b});
    }
    if (pairs.empty()) throw std::invalid_argument("optimize needs at least two knobs");
    if (rounds < 1) throw std::invalid_argument("rounds must be at least 1, got " + std::to_string(rounds));
    if (goal.max_drawdown < 0) throw std::invalid_argument("max_drawdown must be at least 0");

    auto params_of = [&](const std::vector<int>& choice) {
        Params p = start;
        for (std::size_t k = 0; k < knobs.size(); ++k) p[knobs[k].name] = knobs[k].values[choice[k]];
        return p;
    };
    Search out;
    std::map<std::vector<int>, std::size_t> seen;  // choice -> index in out.runs
    auto run = [&](const std::vector<int>& choice, int round) -> const Run& {
        auto it = seen.find(choice);
        if (it != seen.end()) return out.runs[it->second];
        Params p = params_of(choice);
        Summary s = run_of(p);
        out.runs.push_back({round, p, s, score(s, goal)});
        seen[choice] = out.runs.size() - 1;
        return out.runs.back();
    };
    auto beats = [](double s, double top) { return !std::isnan(s) && (std::isnan(top) || s > top); };

    std::vector<int> best(knobs.size(), 0);
    Run top = run(best, 0);
    std::size_t since_gain = 0;
    for (int r = 1; r <= rounds && since_gain < pairs.size(); ++r) {
        auto [a, b] = pairs[(r - 1) % pairs.size()];
        double before = top.score;
        for (int i = 0; i < static_cast<int>(knobs[a].values.size()); ++i) {
            for (int j = 0; j < static_cast<int>(knobs[b].values.size()); ++j) {
                std::vector<int> c = best;
                c[a] = i;
                c[b] = j;
                const Run& x = run(c, r);
                if (beats(x.score, top.score)) top = x, best = c;
            }
        }
        bool gain = beats(top.score, before) && (std::isnan(before) || top.score - before > goal.min_gain);
        since_gain = gain ? 0 : since_gain + 1;
        if (progress && !progress(r, rounds, top.score, top.params)) break;
    }
    out.best = top.params;
    out.score = top.score;
    out.summary = top.summary;
    return out;
}

// search over the table strategy `name`, each run scored under `goal`.
// Every knob value is checked first: a bad name, type, or range throws
// std::invalid_argument naming the knob.
inline Search optimize(const std::string& name, const Params& start, const std::vector<Knob>& knobs,
                       const Markets& markets, const MarketCosts& costs, PortfolioSettings settings,
                       int rounds, const Progress& progress = {}, const Goal& goal = {}) {
    const StrategyInfo& info = strategy(name);
    struct NoLabels {} none;
    use_settings(none, settings);  // a bad state_delay is named as such, not as a knob
    for (const Knob& k : knobs) {
        for (const Value& v : k.values) {
            try {
                info.live({{k.name, v}}, settings);
            } catch (const std::invalid_argument& e) {
                throw std::invalid_argument("knob " + k.name + ": " + e.what());
            }
        }
    }
    auto run_of = [&](const Params& p) { return summary(info.run(p, markets, costs, settings)); };
    Search out = search(start, knobs, rounds, run_of, progress, goal);
    out.timeframe = markets.timeframe();
    return out;
}

// One walk-forward fold: the caller cuts the windows. test should start
// where train ends.
struct Fold {
    const Markets* train;
    MarketCosts train_costs;
    const Markets* test;
    MarketCosts test_costs;
};

// A fold's winner and how it did. overfit is train Sharpe minus test
// Sharpe: near 0, the winner held up on days the search never saw; large,
// the search fit noise.
struct FoldResult {
    Params best;
    Summary train, test;
    double overfit = 0.0;
};

// Called after each round with the fold's index, then Progress's arguments.
using FoldProgress = std::function<bool(int, int, int, double, const Params&)>;

// Per fold: optimize on train, then run the winner on train and on test.
// A false from progress stops that fold's search, not the next fold's.
inline std::vector<FoldResult> walk_forward(const std::string& name, const Params& start,
                                            const std::vector<Knob>& knobs, const std::vector<Fold>& folds,
                                            PortfolioSettings settings, int rounds,
                                            const FoldProgress& progress = {}, const Goal& goal = {}) {
    if (folds.empty()) throw std::invalid_argument("walk_forward needs at least one fold");
    for (const Fold& fold : folds) {
        if (!fold.train || !fold.test) throw std::invalid_argument("each fold needs train and test Markets");
    }
    std::vector<FoldResult> out;
    for (int f = 0; f < static_cast<int>(folds.size()); ++f) {
        const Fold& fold = folds[f];
        Progress each;
        if (progress) each = [&](int r, int n, double s, const Params& p) { return progress(f, r, n, s, p); };
        Search s = optimize(name, start, knobs, *fold.train, fold.train_costs, settings, rounds, each, goal);
        FoldResult r{.best = s.best};
        r.train = summary(run(name, s.best, *fold.train, fold.train_costs, settings));
        r.test = summary(run(name, s.best, *fold.test, fold.test_costs, settings));
        r.overfit = r.train.sharpe - r.test.sharpe;
        out.push_back(r);
    }
    return out;
}

}  // namespace avbt
