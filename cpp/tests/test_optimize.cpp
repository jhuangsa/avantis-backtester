// Tests for sharpe and optimize in avbt/optimize.hpp, with a toy strategy on
// rising minute bars. Returns 0 when every check passes.

#include "avbt/optimize.hpp"

#include <cmath>
#include <cstdio>
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

// Opens once at the start: long when a is 2 and b is 3, short when only a
// is 2, otherwise nothing. c changes nothing.
struct Toy {
    struct Params {
        int a = 0, b = 0, c = 0;
    } params;
    bool opened = false;
    void prepare(const Markets&) {}
    std::vector<Order> decide(int64_t, const Report&, const std::vector<Position>&) {
        if (opened || params.a != 2) return {};
        opened = true;
        return {Order{.instrument = "X", .side = params.b == 3 ? Side::Long : Side::Short, .stop_distance = 0.5}};
    }
};

// 600 minutes rising with a wiggle, so hourly returns vary.
Markets rising() {
    Bars b;
    for (int i = 0; i < 600; ++i) {
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

void test_sharpe_flat_is_nan() {
    Result r{.equity = std::vector<double>(600, 100.0)};
    check_value("flat equity nan", std::isnan(sharpe(r)), 1);
}

void test_optimize() {
    std::vector<Knob<Toy::Params>> knobs = {knob("a", &Toy::Params::a, {0, 1, 2}),
                                            knob("b", &Toy::Params::b, {0, 1, 2, 3}),
                                            knob("c", &Toy::Params::c, {0, 1})};
    Markets m = rising();
    auto s = optimize<Toy>({}, knobs, m, PortfolioSettings{}, 10);
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
    check_value("one round", optimize<Toy>({}, knobs, m, PortfolioSettings{}, 1).runs.size(), 12);
}

}  // namespace

int main() {
    test_sharpe_flat_is_nan();
    test_optimize();
    if (failures == 0) std::printf("all optimize checks passed\n");
    return failures == 0 ? 0 : 1;
}
