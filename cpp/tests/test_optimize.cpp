// Tests for sharpe and optimize in avbt/optimize.hpp, with a toy strategy on
// rising minute bars. Returns 0 when every check passes.

#include "avbt/optimize.hpp"

#include <cmath>
#include <cstdio>
#include <limits>
#include <set>
#include <string>
#include <vector>

namespace {

int failures = 0;

void check_value(const std::string& label, double actual, double expected) {
    if (std::abs(actual - expected) >= 1e-9) {
        ++failures;
        std::printf("FAIL %s: expected %f, got %f\n", label.c_str(), expected, actual);
    }
}

using namespace avbt;

// Every market at the given fee, no holding costs.
MarketCosts flat_costs(const Markets& m, double fee = 0.0) {
    MarketCosts c;
    for (const Market& x : m.all()) c[x.instrument] = Costs{.open_fee = fee, .close_fee = fee};
    return c;
}

// The old three-argument call: every market free.
template <class S>
Result backtest(S& s, const Markets& m, PortfolioSettings settings) {
    return avbt::backtest(s, m, flat_costs(m), settings);
}

// Opens once at the start: long when a is 2 and b is 3, short when only a
// is 2, otherwise nothing. c changes nothing.
struct Toy {
    struct Params {
        int a = 0, b = 0, c = 0;
    } params;
    bool opened = false;
    void prepare(const Markets&) {}
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t, const Report&, const std::vector<Position>&) {
        if (opened || params.a != 2) return {};
        opened = true;
        return {Order{.instrument = "X", .side = params.b == 3 ? Side::Long : Side::Short, .stop_distance = 0.5}};
    }
};

// 32 days of minutes rising with a wiggle, so daily returns vary.
Markets rising() {
    Bars b;
    for (int i = 0; i < 32 * 1440; ++i) {
        double c = 100 + 0.01 * i + std::sin(i);
        b.ts.push_back(60 * i);
        for (auto* v : {&b.open, &b.high, &b.low, &b.close}) v->push_back(c);
        b.minutes_with_data.push_back(1);
    }
    return Markets::make({{"X", {b}}});
}

Knob knob(const std::string& name, std::vector<int> values) {
    return {name, std::vector<Value>(values.begin(), values.end())};
}

// Sharpe of Toy with a, b, c read from params.
double toy_sharpe(const Params& p, const Markets& m) {
    Toy t;
    t.params = {std::get<int>(p.at("a")), std::get<int>(p.at("b")), std::get<int>(p.at("c"))};
    return sharpe(backtest(t, m, PortfolioSettings{}));
}

// Daily bars on days 0-15, rising 10% a day, then no bars until day 30.
// The 31 midnights give 15 returns of 0.1 and 15 of 0. With k of n returns
// equal to x and the rest 0, mean / sd = sqrt(k (n - 1) / (n (n - k))),
// here sqrt(29 / 30).
void test_sharpe_gap_days_are_flat() {
    Result r{.timeframe = Timeframe::Day1};
    double e = 100;
    for (int d = 0; d <= 15; ++d, e *= 1.1) {
        r.clock.push_back(86400 * d);
        r.equity.push_back(e);
    }
    r.clock.push_back(86400 * 30);
    r.equity.push_back(r.equity.back());
    check_value("gap sharpe", sharpe(r), std::sqrt(29.0 / 30 * 365));
}

// Steps of the given timeframe over len(days) days from 0; every step on day
// d has equity days[d].
Result path(Timeframe tf, const std::vector<double>& days) {
    Result r{.timeframe = tf};
    for (int64_t t = 0; t < 86400 * static_cast<int64_t>(days.size()); t += seconds(tf)) {
        r.clock.push_back(t);
        r.equity.push_back(days[t / 86400]);
    }
    return r;
}

// 16 days rising 10% a day, then 16 flat days: 15 returns of 0.1, 16 of 0.
std::vector<double> rise_then_flat() {
    std::vector<double> days = {100};
    for (int d = 1; d < 32; ++d) days.push_back(d <= 15 ? days.back() * 1.1 : days.back());
    return days;
}

// Equity at 0 for one hour on day 10 counts, though it is back by midnight.
void test_sharpe_wipe_out_is_minus_infinity() {
    Result r = path(Timeframe::Hour1, rise_then_flat());
    r.equity[24 * 10 + 5] = 0;
    check_value("wipe-out", sharpe(r) == -std::numeric_limits<double>::infinity(), 1);
}

// 30 days of hourly bars give midnights 1 to 30: 29 returns, too few.
void test_sharpe_short_run_is_nan() {
    std::vector<double> days = rise_then_flat();
    days.resize(30);
    check_value("short run nan", std::isnan(sharpe(path(Timeframe::Hour1, days))), 1);
}

void test_sharpe_flat_is_nan() {
    check_value("flat nan", std::isnan(sharpe(path(Timeframe::Hour1, std::vector<double>(40, 100.0)))), 1);
}

// 32 days give midnights 1 to 32: 31 returns, 15 of 0.1 and 16 of 0, so
// mean / sd = sqrt(15 * 30 / (31 * 16)) on any bar length.
void test_sharpe_same_on_minute_and_hour_bars() {
    double expected = std::sqrt(15.0 * 30 / (31 * 16) * 365);
    check_value("hour sharpe", sharpe(path(Timeframe::Hour1, rise_then_flat())), expected);
    check_value("minute sharpe", sharpe(path(Timeframe::Min1, rise_then_flat())), expected);
}

void test_search() {
    std::vector<Knob> knobs = {knob("a", {0, 1, 2}), knob("b", {0, 1, 2, 3}), knob("c", {0, 1})};
    Markets m = rising();
    auto score = [&](const Params& p) { return toy_sharpe(p, m); };
    Search s = search({}, knobs, 10, score);
    check_value("best a", std::get<int>(s.best.at("a")), 2);
    check_value("best b", std::get<int>(s.best.at("b")), 3);
    check_value("best c stays first", std::get<int>(s.best.at("c")), 0);
    check_value("positive sharpe", s.sharpe > 0, 1);
    // Start 1; pair (a,b) 11 new; (a,c) at b=3 and (b,c) at a=2 each 3 new,
    // the rest already run. Then a full pass with no gain ends the search.
    check_value("runs", s.runs.size(), 18);
    check_value("last round", s.runs.back().round, 3);
    std::set<Params> unique;
    for (const Run& r : s.runs) unique.insert(r.params);
    check_value("each combination once", unique.size(), s.runs.size());
    check_value("one round", search({}, knobs, 1, score).runs.size(), 12);
    // The same inputs give the same search.
    Search again = search({}, knobs, 10, score);
    check_value("same runs", again.runs.size(), s.runs.size());
    for (std::size_t i = 0; i < s.runs.size(); ++i) {
        check_value("same params", again.runs[i].params == s.runs[i].params, 1);
        check_value("same sharpe", again.runs[i].sharpe, s.runs[i].sharpe);
    }
    // Progress sees each round; returning false stops after that round.
    std::vector<int> seen;
    auto stop_at_2 = [&](int r, int total, double, const Params&) {
        seen.push_back(r * 100 + total);
        return r < 2;
    };
    Search cut = search({}, knobs, 10, score, stop_at_2);
    check_value("progress calls", seen.size(), 2);
    check_value("progress args", seen[0] == 110 && seen[1] == 210, 1);
    check_value("stopped after round 2", cut.runs.back().round, 2);
    bool threw = false;
    try {
        search({}, knobs, 0, score);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    check_value("zero rounds throws", threw, 1);
}

// A Result whose equity and clock differ in length throws, not crashes.
void test_sharpe_length_mismatch_throws() {
    Result r{.equity = std::vector<double>(40, 100.0)};
    bool threw = false;
    try {
        sharpe(r);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    check_value("mismatch throws", threw, 1);
}

// Bad settings throw before any bar is read.
void test_bad_settings_throw() {
    Markets m = rising();
    for (PortfolioSettings bad : {PortfolioSettings{.starting_balance = 0}, PortfolioSettings{.risk_per_trade = -0.1},
                                  PortfolioSettings{.hard_stop = 1.5}}) {
        bool threw = false;
        try {
            run("state_trend", {}, m, flat_costs(m), bad);
        } catch (const std::invalid_argument&) {
            threw = true;
        }
        check_value("bad settings throw", threw, 1);
    }
}

// One row per fold; progress sees each fold's index.
void test_walk_forward() {
    Markets a = rising(), b = rising();
    std::vector<Fold> folds = {{&a, flat_costs(a), &b, flat_costs(b)}, {&b, flat_costs(b), &a, flat_costs(a)}};
    std::vector<Knob> knobs = {knob("average", {50, 20}), knob("breakout", {30, 15})};
    std::set<int> seen;
    auto progress = [&](int f, int, int, double, const Params&) { return seen.insert(f), true; };
    std::vector<FoldResult> rows = walk_forward("state_trend", {}, knobs, folds, {}, 2, progress);
    check_value("rows", rows.size(), 2);
    check_value("folds seen", seen == std::set<int>{0, 1}, 1);
    check_value("best has knobs", rows[0].best.count("average") && rows[0].best.count("breakout"), 1);
    bool threw = false;
    try {
        walk_forward("state_trend", {}, knobs, {}, {}, 2);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    check_value("no folds throws", threw, 1);
    for (Fold bad : {Fold{nullptr, flat_costs(a), &b, flat_costs(b)}, Fold{&a, flat_costs(a), nullptr, flat_costs(b)}}) {
        threw = false;
        try {
            walk_forward("state_trend", {}, knobs, {folds[0], bad}, {}, 2);
        } catch (const std::invalid_argument&) {
            threw = true;
        }
        check_value("null fold throws", threw, 1);
    }
}

}  // namespace

int main() {
    test_sharpe_length_mismatch_throws();
    test_bad_settings_throw();
    test_walk_forward();
    test_sharpe_gap_days_are_flat();
    test_sharpe_wipe_out_is_minus_infinity();
    test_sharpe_short_run_is_nan();
    test_sharpe_flat_is_nan();
    test_sharpe_same_on_minute_and_hour_bars();
    test_search();
    if (failures == 0) std::printf("all optimize checks passed\n");
    return failures == 0 ? 0 : 1;
}
