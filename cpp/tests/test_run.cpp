// Tests for run() and the strategy table in avbt/run.hpp: run by name
// matches the struct run directly, and bad names and params throw.
// Returns 0 when every check passes.

#include "avbt/optimize.hpp"
#include "avbt/run.hpp"

#include <cmath>
#include <cstdio>
#include <functional>
#include <string>
#include <vector>

namespace {

int failures = 0;

void check_true(const std::string& label, bool condition) {
    if (!condition) {
        ++failures;
        std::printf("FAIL %s\n", label.c_str());
    }
}

using namespace avbt;

// Bars that swing up and down, with a spike every 17th bar, so the strategies trade.
Bars wave(Timeframe tf, std::size_t n, double period) {
    Bars b;
    b.timeframe = tf;
    for (std::size_t i = 0; i < n; ++i) {
        double c = 100 + 10 * std::sin(i / period) + 0.05 * i;
        b.ts.push_back(static_cast<int64_t>(i) * seconds(tf));
        b.open.push_back(c);
        b.high.push_back(i % 17 == 0 ? c * 1.08 : c + 1);
        b.low.push_back(c - 1);
        b.close.push_back(c);
        b.minutes_with_data.push_back(static_cast<int>(seconds(tf) / 60));
    }
    return b;
}

Markets veranta() {
    Bars h = wave(Timeframe::Hour1, 400, 3.0);
    return Markets::make({{"ZORA", {h}}, {"AVNT", {h}}, {"DYM", {h}}, {"GOLD", {h}}});
}

// BTC minutes rising with a wiggle, labelled trending up, and hourly bars
// sampled from them, so the trend check and the breakout both pass.
Markets trend() {
    std::size_t n = 6000;
    Bars minute, hour;
    minute.timeframe = Timeframe::Min1;
    hour.timeframe = Timeframe::Hour1;
    for (std::size_t i = 0; i < n; ++i) {
        double c = 100 + 0.01 * i + 2 * std::sin(i / 40.0);
        minute.ts.push_back(static_cast<int64_t>(i) * 60);
        for (auto* v : {&minute.open, &minute.high, &minute.low, &minute.close}) v->push_back(c);
        minute.minutes_with_data.push_back(1);
        if (i % 60 == 59) {
            hour.ts.push_back(static_cast<int64_t>(i / 60) * 3600);
            hour.open.push_back(c);
            hour.high.push_back(c + 1);
            hour.low.push_back(c - 1);
            hour.close.push_back(c);
            hour.minutes_with_data.push_back(60);
        }
    }
    States s{0, std::vector(n, MarketState::TrendingUp), std::vector(n, TrendState::Uptrend),
             std::vector(n, VolatilityState::Normal)};
    return Markets::make({{"BTC", {minute, hour}, s}});
}

MarketCosts costs_for(const Markets& m) {
    MarketCosts c;
    for (const Market& x : m.all()) c[x.instrument] = Costs{.open_fee = 0.0001, .close_fee = 0.0001};
    return c;
}

bool same(const Result& a, const Result& b) {
    if (a.trades.size() != b.trades.size() || a.ending_balance != b.ending_balance) return false;
    for (std::size_t i = 0; i < a.trades.size(); ++i) {
        const Trade &x = a.trades[i], &y = b.trades[i];
        if (x.instrument != y.instrument || x.entry_bar != y.entry_bar || x.exit_bar != y.exit_bar ||
            x.entry_price != y.entry_price || x.exit_price != y.exit_price || x.size != y.size ||
            x.result != y.result || x.cause != y.cause)
            return false;
    }
    return true;
}

template <class S>
Result direct(const Markets& m) {
    S s;
    return backtest(s, m, costs_for(m), PortfolioSettings{});
}

void test_by_name_matches_direct() {
    Markets v = veranta(), t = trend();
    std::map<std::string, std::function<Result()>> direct_runs = {
        {"late_day_short", [&] { return direct<LateDayShort>(v); }},
        {"rally_short", [&] { return direct<RallyShort>(v); }},
        {"campaign_short", [&] { return direct<CampaignShort>(v); }},
        {"spike_short", [&] { return direct<SpikeShort>(v); }},
        {"gold_trend_long", [&] { return direct<GoldTrendLong>(v); }},
        {"state_trend", [&] { return direct<StateTrend>(t); }},
        {"campaign_and_spike", [&] { return direct<Combined<CampaignShort, SpikeShort>>(v); }},
        {"late_day_and_rally", [&] { return direct<Combined<LateDayShort, RallyShort>>(v); }},
    };
    check_true("eight strategies", strategies().size() == 8);
    for (const StrategyInfo& info : strategies()) {
        const Markets& m = info.name == "state_trend" ? t : v;
        Result by_name = run(info.name, {}, m, costs_for(m), PortfolioSettings{});
        check_true(info.name + " in table", direct_runs.count(info.name) == 1);
        if (direct_runs.count(info.name)) check_true(info.name + " same trades", same(by_name, direct_runs[info.name]()));
        std::printf("%s: %zu trades\n", info.name.c_str(), by_name.trades.size());
    }
}

bool throws(const std::string& name, const Params& p) {
    Markets v = veranta();
    try {
        run(name, p, v, costs_for(v), PortfolioSettings{});
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

bool throws_optimize(const std::vector<Knob>& knobs) {
    Markets m = veranta();
    try {
        optimize("rally_short", {}, knobs, m, costs_for(m), {}, 1);
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

void test_bad_input_throws() {
    check_true("unknown strategy", throws("no_such", {}));
    check_true("unknown param", throws("rally_short", {{"nope", 1}}));
    check_true("out of range", throws("rally_short", {{"stop", 2.0}}));
    check_true("wrong type", throws("rally_short", {{"instrument", 3}}));
    check_true("combined unknown param", throws("campaign_and_spike", {{"a.nope", 1}}));
    check_true("valid param runs", !throws("rally_short", {{"stop", 0.1}}));
}

// Every strategy in the table tunes by name, combined ones by prefixed knobs.
void test_every_strategy_tunes() {
    for (const StrategyInfo& info : strategies()) {
        Markets m = info.name == "state_trend" ? trend() : veranta();
        std::string pre = info.params[0].name.starts_with("a.") ? "a." : "";
        std::vector<Knob> knobs = {{pre + "leverage", {1.0, 2.0}}, {pre + "stop", {0.1, 0.05}}};
        if (info.name == "state_trend") knobs[1] = {"reward", {2.0, 3.0}};
        Search s = optimize(info.name, {}, knobs, m, costs_for(m), {}, 1);
        check_true(info.name + " tunes", s.runs.size() == 4 && s.best.size() == 2);
    }
    check_true("bad knob", throws_optimize({{"stop", {0.1, 2.0}}, {"lag", {1}}}));
    check_true("unknown knob", throws_optimize({{"nope", {1}}, {"lag", {1}}}));
}

}  // namespace

int main() {
    test_every_strategy_tunes();
    test_by_name_matches_direct();
    test_bad_input_throws();
    if (failures == 0) std::printf("all run tests passed\n");
    return failures == 0 ? 0 : 1;
}
