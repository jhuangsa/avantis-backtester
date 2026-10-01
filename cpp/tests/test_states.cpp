// Tests for the state labels in avbt/states.hpp and the StateTrend strategy,
// on small hand-built series. Returns 0 when every check passes.

#include "avbt/states.hpp"
#include "avbt/strategies.hpp"

#include <cmath>
#include <cstdio>
#include <stdexcept>
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

// Flat bars at each close, bar i opening at first + i * seconds(tf).
Bars flat_bars(Timeframe tf, int64_t first, const std::vector<double>& closes) {
    Bars b;
    b.timeframe = tf;
    for (std::size_t i = 0; i < closes.size(); ++i) {
        b.ts.push_back(first + static_cast<int64_t>(i) * seconds(tf));
        for (auto* v : {&b.open, &b.high, &b.low, &b.close}) v->push_back(closes[i]);
        b.minutes_with_data.push_back(static_cast<int>(seconds(tf) / 60));
    }
    return b;
}

// n rows, every one the same three labels.
States same_states(int64_t start, std::size_t n, MarketState m, TrendState t, VolatilityState v) {
    return States{start, std::vector(n, m), std::vector(n, t), std::vector(n, v)};
}

void test_state_at_lag() {
    // Row 0 is the minute 600..660, row 1 is 660..720.
    States s{600, {MarketState::Breakout, MarketState::TrendingUp},
             {TrendState::Uptrend, TrendState::Downtrend}, {VolatilityState::Low, VolatilityState::High}};
    check_true("row 0 seen when its minute closes", state_at(s, 660).market == MarketState::Breakout);
    check_true("row 1 not seen at its own open", state_at(s, 660).trend == TrendState::Uptrend);
    check_true("row 1 seen at 720", state_at(s, 720).volatility == VolatilityState::High);
    check_true("unknown before start", state_at(s, 600).market == MarketState::Unknown);
    check_true("unknown well before start", state_at(s, 0).trend == TrendState::Unknown);
    check_true("unknown past end", state_at(s, 780).volatility == VolatilityState::Unknown);
}

void test_make_checks_states() {
    Bars b = flat_bars(Timeframe::Min1, 0, {1, 1});
    States s = same_states(0, 2, MarketState::Mixed, TrendState::Uptrend, VolatilityState::Low);
    s.trend.pop_back();
    bool threw = false;
    try { Markets::make({{"X", {b}, s}}); } catch (const std::invalid_argument&) { threw = true; }
    check_true("make rejects unequal lengths", threw);
    s = same_states(30, 2, MarketState::Mixed, TrendState::Uptrend, VolatilityState::Low);
    threw = false;
    try { Markets::make({{"X", {b}, s}}); } catch (const std::invalid_argument&) { threw = true; }
    check_true("make rejects a start inside a minute", threw);
    check_true("no states, nullptr", Markets::make({{"X", {b}}}).states("X") == nullptr);
}

// Hourly closes 100, 110, 120: at 2:00 the 1:00 bar has closed, 110 over its
// 2-bar average 105, ATR(1) 10. Minute bars from 2:00: the close 111 at
// minute 3 breaks the prior 2-minute high 110.
Result run_state_trend(const States& states, bool flip = false) {
    Bars hour = flat_bars(Timeframe::Hour1, 0, {100, 110, 120});
    Bars minute = flat_bars(Timeframe::Min1, 7200, {110, 110, 110, 111, 111, 111});
    StateTrend s;
    s.params.average = 2;
    s.params.atr_period = 1;
    s.params.breakout = 2;
    s.params.flip = flip;
    return backtest(s, Markets::make({{"BTC", {minute, hour}, states}}), PortfolioSettings{});
}

void test_state_trend_unknown_no_trade() {
    States s = same_states(7200, 6, MarketState::Unknown, TrendState::Unknown, VolatilityState::Unknown);
    check_value("unknown no trade", run_state_trend(s).trades.size(), 0);
}

void test_state_trend_opens_long() {
    // Decided at the close of minute 3, filled at the open of minute 4. The
    // minute-4 label turns to TrendingDown, seen at its close: out at minute 5.
    States s = same_states(7200, 6, MarketState::TrendingUp, TrendState::Uptrend, VolatilityState::Normal);
    s.market[4] = MarketState::TrendingDown;
    Result r = run_state_trend(s);
    check_value("long one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_true("long side", r.trades[0].side == Side::Long);
    check_value("long entry bar", r.trades[0].entry_bar, 4);
    check_value("long entry price", r.trades[0].entry_price, 111);
    check_value("long exit bar", r.trades[0].exit_bar, 5);
    check_true("long cause", r.trades[0].cause == Cause::Order);
    // A shock blocks the same entry.
    s = same_states(7200, 6, MarketState::TrendingUp, TrendState::Uptrend, VolatilityState::Shock);
    check_value("shock no trade", run_state_trend(s).trades.size(), 0);
}

void test_state_trend_flip_opens_short() {
    // The same signal and the same exit as the long test, on the other side.
    States s = same_states(7200, 6, MarketState::TrendingUp, TrendState::Uptrend, VolatilityState::Normal);
    s.market[4] = MarketState::TrendingDown;
    Result r = run_state_trend(s, true);
    check_value("flip one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_true("flip short side", r.trades[0].side == Side::Short);
    check_value("flip entry bar", r.trades[0].entry_bar, 4);
    check_value("flip exit bar", r.trades[0].exit_bar, 5);
}

void test_state_trend_min_stop() {
    // The stop is 2 ATRs, 20 / 111, about 18%: a 20% minimum blocks the entry.
    States s = same_states(7200, 6, MarketState::TrendingUp, TrendState::Uptrend, VolatilityState::Normal);
    Bars hour = flat_bars(Timeframe::Hour1, 0, {100, 110, 120});
    Bars minute = flat_bars(Timeframe::Min1, 7200, {110, 110, 110, 111, 111, 111});
    StateTrend st;
    st.params.average = 2;
    st.params.atr_period = 1;
    st.params.breakout = 2;
    st.params.min_stop = 0.2;
    Result r = backtest(st, Markets::make({{"BTC", {minute, hour}, s}}), PortfolioSettings{});
    check_value("min stop no trade", r.trades.size(), 0);
}


void test_state_trend_signal() {
    // Hour1 named outright matches the default run. Minute signal bars give
    // another ATR, so another stop and another result. A missing timeframe
    // throws.
    States s = same_states(7200, 6, MarketState::TrendingUp, TrendState::Uptrend, VolatilityState::Normal);
    s.market[4] = MarketState::TrendingDown;
    Markets m = Markets::make({{"BTC", {flat_bars(Timeframe::Min1, 7200, {110, 110, 110, 111, 111, 111}),
                                        flat_bars(Timeframe::Hour1, 0, {100, 110, 120})}, s}});
    auto run = [&](Timeframe tf) {
        StateTrend st;
        st.params.average = 2;
        st.params.atr_period = 1;
        st.params.breakout = 2;
        st.params.signal["BTC"] = tf;
        return backtest(st, m, PortfolioSettings{});
    };
    Result hour = run(Timeframe::Hour1), base = run_state_trend(s);
    check_value("signal Hour1 trades", hour.trades.size(), base.trades.size());
    check_value("signal Hour1 balance", hour.ending_balance, base.ending_balance);
    check_true("signal Min1 differs", run(Timeframe::Min1).ending_balance != base.ending_balance);
    bool threw = false;
    try { run(Timeframe::Hour4); } catch (const std::invalid_argument&) { threw = true; }
    check_true("signal missing timeframe throws", threw);
}

}

int main() {
    test_state_at_lag();
    test_make_checks_states();
    test_state_trend_unknown_no_trade();
    test_state_trend_opens_long();
    test_state_trend_flip_opens_short();
    test_state_trend_min_stop();
    test_state_trend_signal();
    if (failures == 0) std::printf("all state checks passed\n");
    return failures == 0 ? 0 : 1;
}
