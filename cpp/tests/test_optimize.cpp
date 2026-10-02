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

Knob<Toy::Params> knob(const std::string& name, int Toy::Params::*field, std::vector<int> values) {
    Knob<Toy::Params> k{.name = name};
    for (int v : values) {
        k.labels.push_back(std::to_string(v));
        k.choices.push_back([=](Toy::Params& p) { p.*field = v; });
    }
    return k;
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

void test_optimize() {
    std::vector<Knob<Toy::Params>> knobs = {knob("a", &Toy::Params::a, {0, 1, 2}),
                                            knob("b", &Toy::Params::b, {0, 1, 2, 3}),
                                            knob("c", &Toy::Params::c, {0, 1})};
    Markets m = rising();
    auto s = optimize<Toy>({}, knobs, m, flat_costs(m), PortfolioSettings{}, 10);
    check_value("best a", s.best.a, 2);
    check_value("best b", s.best.b, 3);
    check_value("best c stays first", s.best.c, 0);
    check_value("positive sharpe", s.sharpe > 0, 1);
    // Start 1; pair (a,b) 11 new; (a,c) at b=3 and (b,c) at a=2 each 3 new,
    // the rest already run. Then a full pass with no gain ends the search.
    check_value("runs", s.runs.size(), 18);
    check_value("last round", s.runs.back().round, 3);
    std::set<std::vector<int>> unique;
    for (const Run& r : s.runs) unique.insert(r.choice);
    check_value("each combination once", unique.size(), s.runs.size());
    check_value("one round", optimize<Toy>({}, knobs, m, flat_costs(m), PortfolioSettings{}, 1).runs.size(), 12);
}

}  // namespace

int main() {
    test_sharpe_gap_days_are_flat();
    test_sharpe_wipe_out_is_minus_infinity();
    test_sharpe_short_run_is_nan();
    test_sharpe_flat_is_nan();
    test_sharpe_same_on_minute_and_hour_bars();
    test_optimize();
    if (failures == 0) std::printf("all optimize checks passed\n");
    return failures == 0 ? 0 : 1;
}
