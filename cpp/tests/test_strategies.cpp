// Tests for the five strategies in avbt/strategies.hpp, each on a small
// hand-built hourly series. Returns 0 when every check passes.

#include "avbt/strategies.hpp"

#include <algorithm>
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

// Every market at the given fee (the old strategy default), no holding costs.
MarketCosts flat_costs(const Markets& m, double fee = 0.0001) {
    MarketCosts c;
    for (const Market& x : m.all()) c[x.instrument] = Costs{.open_fee = fee, .close_fee = fee};
    return c;
}

// The old three-argument call, at the old default fee.
template <class S>
Result backtest(S& s, const Markets& m, PortfolioSettings settings) {
    return avbt::backtest(s, m, flat_costs(m), settings);
}

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

Position held(const std::string& instrument, Side side, int64_t entry_time = unknown_time) {
    Position p;
    p.instrument = instrument;
    p.side = side;
    p.entry_time = entry_time;
    return p;
}

bool has(const std::vector<Order>& orders, Order::Kind kind, const std::string& instrument) {
    return std::any_of(orders.begin(), orders.end(),
                       [&](const Order& o) { return o.kind == kind && o.instrument == instrument; });
}

void test_other_instruments_are_ignored() {
    // A ZORA position is not CampaignShort's: it still opens AVNT on the
    // bar-1 rise, and sends no close for a market it does not hold.
    CampaignShort s;
    s.params.lag = 1;
    Markets m = Markets::make({{"AVNT", {flat_bars({100, 110, 110})}}});
    s.prepare(m);
    auto orders = s.decide(2 * 3600, Report{}, {held("ZORA", Side::Short, 0)});
    check_true("opens own market", orders.size() == 1 && has(orders, Order::Kind::Open, "AVNT"));
}

void test_restart_entry_time() {
    // Bars from -3h. With no entry time there is no time exit; an entry
    // time of 0 is real: the signal bar is the one closed at 0, bar 2, so
    // the 2-bar time exit fires at bar 5.
    Markets m = Markets::make({{"AVNT", {flat_bars({100, 100, 100, 100, 100, 100}, -3)}}});
    CampaignShort unknown;
    unknown.params.time_exit = 2;
    unknown.prepare(m);
    check_true("unknown entry time keeps",
               unknown.decide(3 * 3600, Report{}, {held("AVNT", Side::Short)}).empty());
    CampaignShort known = CampaignShort{};
    known.params.time_exit = 2;
    known.prepare(m);
    check_true("entry time 0 times out",
               has(known.decide(3 * 3600, Report{}, {held("AVNT", Side::Short, 0)}), Order::Kind::Close, "AVNT"));
}

void test_combined_restart_finds_owner() {
    // A fresh Combined given an AVNT position: CampaignShort trades AVNT, so
    // it owns the position and closes it at its time exit; SpikeShort never sees it.
    Markets m = Markets::make({{"AVNT", {flat_bars({100, 100, 100, 100, 100})}},
                               {"DYM", {flat_bars({100, 100, 100, 100, 100})}}});
    Combined<CampaignShort, SpikeShort> both;
    both.a.params.time_exit = 2;
    both.prepare(m);
    auto orders = both.decide(5 * 3600, Report{}, {held("AVNT", Side::Short, 3600)});
    check_true("restart closes", has(orders, Order::Kind::Close, "AVNT"));
    check_true("restart no second open", !has(orders, Order::Kind::Open, "AVNT"));
    check_true("restart owner is A", both.owner.at("AVNT") == 0);
}

void test_state_trend_holds_before_average() {
    // 30 rising hourly bars, fewer than the 50 the average needs, all labelled
    // uptrend. With no average there is no evidence, so the long is kept.
    int hours = 30;
    std::vector<double> closes;
    for (int i = 0; i < hours * 60; ++i) closes.push_back(100 + i * 0.01);
    Bars minute = flat_bars(closes);
    minute.timeframe = Timeframe::Min1;
    for (int i = 0; i < hours * 60; ++i) minute.ts[i] = i * 60;
    std::vector<double> hourly;
    for (int h = 0; h < hours; ++h) hourly.push_back(closes[h * 60 + 59]);
    Bars hour = flat_bars(hourly);
    std::size_t n = closes.size();
    Markets m = Markets::make({Market{"BTC", {minute, hour},
                                      States{0, std::vector(n, MarketState::TrendingUp),
                                             std::vector(n, TrendState::Uptrend),
                                             std::vector(n, VolatilityState::Normal)}}});
    StateTrend s;
    s.prepare(m);
    auto orders = s.decide(hours * 3600, Report{}, {held("BTC", Side::Long, 0)});
    check_true("no close before the average", orders.empty());
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
    test_other_instruments_are_ignored();
    test_restart_entry_time();
    test_combined_restart_finds_owner();
    test_state_trend_holds_before_average();
    if (failures == 0) std::printf("all strategy checks passed\n");
    return failures == 0 ? 0 : 1;
}
