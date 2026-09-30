// Tests for the five strategies in avbt/strategies.hpp, each on a small
// hand-built hourly series. Returns 0 when every check passes.

#include "avbt/strategies.hpp"

#include <cmath>
#include <cstdio>
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

void check_true(const std::string& label, bool condition) {
    if (!condition) {
        ++failures;
        std::printf("FAIL %s\n", label.c_str());
    }
}

using namespace avbt;

// Flat hourly bars at each close; bar 0 opens at first_hour:00 UTC.
Bars flat_bars(const std::vector<double>& closes, int first_hour = 0) {
    Bars b;
    b.timeframe = Timeframe::Hour1;
    for (std::size_t i = 0; i < closes.size(); ++i) {
        b.ts.push_back((first_hour + static_cast<int64_t>(i)) * 3600);
        for (auto* v : {&b.open, &b.high, &b.low, &b.close}) v->push_back(closes[i]);
        b.minutes_with_data.push_back(60);
    }
    return b;
}

// Runs a strategy on one market named after its instrument.
template <class S>
Result run(S& s, const Bars& b) {
    return backtest(s, Markets::make({{s.params.instrument, {b}}}), PortfolioSettings{});
}

void check_trade(const std::string& name, const Result& r, std::size_t i, int entry, int exit,
                 double exit_price, Cause cause) {
    check_true(name + " trade exists", r.trades.size() > i);
    if (r.trades.size() <= i) return;
    check_value(name + " entry bar", r.trades[i].entry_bar, entry);
    check_value(name + " exit bar", r.trades[i].exit_bar, exit);
    check_value(name + " exit price", r.trades[i].exit_price, exit_price);
    check_true(name + " cause", r.trades[i].cause == cause);
}

void test_late_day_short() {
    // lag 1: rise[1] = 10/100 = 0.10. Bar 1 opens 16:00, fills open[2];
    // time exit 3 decided at close of bar 4, fills open[5] = 110.
    std::vector<double> c = {100, 110, 110, 110, 110, 110, 110};
    LateDayShort s;
    s.params.lag = 1;
    Result r = run(s, flat_bars(c, 15));
    check_value("late one trade", r.trades.size(), 1);
    check_trade("late", r, 0, 2, 5, 110, Cause::Order);
    // Same rise with bar 1 at 15:00: no entry.
    LateDayShort s2;
    s2.params.lag = 1;
    check_value("late not 16", run(s2, flat_bars(c, 14)).trades.size(), 0);
}

void test_rally_short_take_profit() {
    // lag 1, window 2: rise[2] = 0.10, average 105 < 110. Short fills 110,
    // take profit 110 * 0.98 = 107.8, touched by bar 3's low 107.
    Bars b = flat_bars({100, 100, 110, 110, 110});
    b.low[3] = 107;
    RallyShort s;
    s.params.lag = 1;
    s.params.window = 2;
    check_trade("rally", run(s, b), 0, 3, 3, 107.8, Cause::TakeProfit);
}

void test_campaign_short_stop() {
    // lag 1: rise[1] = 0.10 >= 0.09. Short fills open[2] = 110, stop
    // 110 * 1.3 = 143, touched by bar 3's high 150.
    Bars b = flat_bars({100, 110, 110, 110, 110});
    b.high[3] = 150;
    CampaignShort s;
    s.params.lag = 1;
    s.params.time_exit = 10;
    check_trade("campaign", run(s, b), 0, 2, 3, 143, Cause::Stop);
}

void test_spike_short_strict_and_time_exit() {
    // Bar 1 high 105 over close 100 is exactly 5%: not more, no entry.
    Bars five = flat_bars({100, 100, 100, 100, 100, 100});
    five.high[1] = 105;
    SpikeShort s;
    check_value("spike 5% no entry", run(s, five).trades.size(), 0);
    // High 106 is 6%: fills open[2]; time exit 2 decided at bar 3, fills open[4].
    Bars six = five;
    six.high[1] = 106;
    SpikeShort s2;
    s2.params.time_exit = 2;
    check_trade("spike", run(s2, six), 0, 2, 4, 100, Cause::Order);
}

void test_gold_rule_exit() {
    // window 2, lag 1: bar 1 close 101 > average 100.5, drift 0.01 > 0; fills
    // open[2] = 102. Bar 4 close 99 < average 100.5: closes at open[5] = 99.
    // Stop 50% keeps bar 4's drop from filling the stop.
    GoldTrendLong s;
    s.params.window = 2;
    s.params.lag = 1;
    s.params.stop = 0.5;
    Result r = run(s, flat_bars({100, 101, 102, 102, 99, 99}));
    check_trade("gold", r, 0, 2, 5, 99, Cause::Order);
}

void test_entry_on_level_bar_fills_next_open() {
    // Stop at 143 fills in bar 3, whose close 150 gives rise[3] = 40/110 >= 0.09:
    // a new short fills open[4] = 150, stop 195, touched by bar 5's high 200.
    Bars b = flat_bars({100, 110, 110, 150, 150, 150});
    b.open[3] = 110;
    b.low[3] = 110;
    b.high[5] = 200;
    CampaignShort s;
    s.params.lag = 1;
    s.params.time_exit = 10;
    Result r = run(s, b);
    check_value("reentry two trades", r.trades.size(), 2);
    check_trade("first", r, 0, 2, 3, 143, Cause::Stop);
    check_trade("second", r, 1, 4, 5, 195, Cause::Stop);
    if (r.trades.size() > 1) check_value("second entry price", r.trades[1].entry_price, 150);
}


void test_combined_matches_each_alone() {
    // The campaign and spike series from the tests above, one market each.
    // Together they make the same trades as alone, each named by market.
    Bars avnt = flat_bars({100, 110, 110, 110, 110, 110});
    avnt.high[3] = 150;
    Bars dym = flat_bars({100, 100, 100, 100, 100, 100});
    dym.high[1] = 106;
    Combined<CampaignShort, SpikeShort> both;
    both.a.params.lag = 1;
    both.a.params.time_exit = 10;
    both.b.params.time_exit = 2;
    Result r = backtest(both, Markets::make({{"AVNT", {avnt}}, {"DYM", {dym}}}), PortfolioSettings{});
    check_value("combined two trades", r.trades.size(), 2);
    check_trade("combined campaign", r, 0, 2, 3, 143, Cause::Stop);
    check_trade("combined spike", r, 1, 2, 4, 100, Cause::Order);
    if (r.trades.size() > 1) {
        check_true("first is AVNT", r.trades[0].instrument == "AVNT");
        check_true("second is DYM", r.trades[1].instrument == "DYM");
    }
}


void test_combined_one_instrument_takes_turns() {
    // Both signal ZORA at bar 1 (the late-day series above). The late-day
    // short is A, so it opens at bar 2 and owns the position; the rally
    // short's open is dropped. The trade ends at A's 3-bar time exit, bar 5,
    // not at the rally short's 4-bar exit.
    Combined<LateDayShort, RallyShort> both;
    both.a.params.lag = 1;
    both.b.params.lag = 1;
    both.b.params.window = 2;
    Bars zora = flat_bars({100, 110, 110, 110, 110, 110, 110, 110}, 15);
    Result r = backtest(both, Markets::make({{"ZORA", {zora}}}), PortfolioSettings{});
    check_value("turns one trade", r.trades.size(), 1);
    check_trade("turns", r, 0, 2, 5, 110, Cause::Order);
}

}

int main() {
    test_late_day_short();
    test_rally_short_take_profit();
    test_campaign_short_stop();
    test_spike_short_strict_and_time_exit();
    test_gold_rule_exit();
    test_entry_on_level_bar_fills_next_open();
    test_combined_matches_each_alone();
    test_combined_one_instrument_takes_turns();
    if (failures == 0) std::printf("all strategy checks passed\n");
    return failures == 0 ? 0 : 1;
}
