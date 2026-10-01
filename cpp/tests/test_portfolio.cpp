// Tests for the portfolio in avbt/portfolio.hpp, with hand-worked numbers.
// The program returns 0 when every check passes and 1 when any check fails.

#include "avbt/portfolio.hpp"

#include <cmath>
#include <cstdio>
#include <string>

namespace {

int failures = 0;

constexpr double kTolerance = 1e-9;

void check_value(const std::string& label, double actual, double expected) {
    if (std::abs(actual - expected) >= kTolerance) {
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

using avbt::Portfolio;
using avbt::PortfolioSettings;
using avbt::Quote;
using avbt::Side;
using avbt::Cause;

// 1% of 10,000 is 100 at risk. A stop 5 below 100 gives 20 units, 2,000 of
// notional, 200 of collateral at 10x, and liquidation 0.85 * 200 / 20 = 8.5 away.
Portfolio btc_long() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0);
    return p;
}

void test_open_sizes_from_risk() {
    Portfolio p = btc_long();
    const auto& pos = p.positions().at(0);
    check_value("open size", pos.size, 20.0);
    check_value("open collateral", pos.collateral, 200.0);
    check_value("open liquidation", pos.liquidation_price, 91.5);
    check_value("open free cash", p.report().free_cash, 9800.0);
}

void test_open_short_liquidates_above() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Short, 100.0, 105.0, 10.0);
    check_value("short liquidation", p.positions().at(0).liquidation_price, 108.5);
}

void test_open_skips() {
    Portfolio p = btc_long();
    check_true("skip second position in one instrument",
               !p.open("BTC", Side::Long, 100.0, 95.0, 10.0));
    check_true("skip stop on the wrong side", !p.open("ETH", Side::Long, 100.0, 105.0, 10.0));
    // 20x: collateral 100, but the stop loses 100 > 0.80 * 100.
    check_true("skip stop past the 80% cap", !p.open("ETH", Side::Long, 100.0, 95.0, 20.0));
    // A 0.1 stop needs 1,000 units: 100,000 of collateral at 1x.
    check_true("skip collateral over free cash", !p.open("ETH", Side::Long, 100.0, 99.9, 1.0));
    check_true("open a second instrument", p.open("GOOGL", Side::Long, 200.0, 190.0, 10.0));
    check_value("two positions", p.report().open_positions, 2);
}

void test_partial_close() {
    Portfolio p = btc_long();
    check_value("half close result", p.close("BTC", 110.0, 0.5)->result, 100.0);
    check_value("half close balance", p.report().balance, 10100.0);
    check_value("half close size", p.positions().at(0).size, 10.0);
    check_value("half close keeps liquidation", p.positions().at(0).liquidation_price, 91.5);
    p.close("BTC", 110.0);
    check_value("full close empties", p.report().open_positions, 0);
}

void test_stop_fills_at_stop() {
    Portfolio p = btc_long();
    auto closed = p.check({Quote{"BTC", 99.0, 99.0, 94.0, 96.0}});
    check_true("stop cause", closed.size() == 1 && closed[0].cause == Cause::Stop);
    check_value("stop balance", p.report().balance, 9900.0);
    check_value("stop closes", p.report().open_positions, 0);
}

void test_gap_past_stop_fills_at_open() {
    Portfolio p = btc_long();
    p.check({Quote{"BTC", 93.0, 93.0, 92.0, 92.5}});
    check_value("gap balance", p.report().balance, 9860.0);
}

void test_gap_past_liquidation() {
    Portfolio p = btc_long();
    auto closed = p.check({Quote{"BTC", 90.0, 90.0, 80.0, 85.0}});
    check_true("liquidation cause", closed.size() == 1 && closed[0].cause == Cause::Liquidation);
    // Loss capped at 0.85 of 200 collateral.
    check_value("liquidation balance", p.report().balance, 9830.0);
}

void test_mark_moves_equity() {
    Portfolio p = btc_long();
    p.check({Quote{"BTC", 100.0, 104.0, 99.0, 103.0}});
    check_value("mark equity", p.report().equity, 10060.0);
    check_value("mark balance", p.report().balance, 10000.0);
}

// Two positions of 5,000 each at 1x, both stops far away. BTC 100 -> 60 loses
// 2,000 and GOOGL 200 -> 150 loses 1,250: equity 6,750 is under the 7,000 floor.
void test_hard_stop_counts_unrealized() {
    Portfolio p(PortfolioSettings{.risk_per_trade = 0.25});
    check_true("hard stop opens BTC", p.open("BTC", Side::Long, 100.0, 50.0, 1.0));
    check_true("hard stop opens GOOGL", p.open("GOOGL", Side::Long, 200.0, 100.0, 1.0));
    auto closed = p.check({Quote{"BTC", 60.0, 60.0, 60.0, 60.0},
                           Quote{"GOOGL", 150.0, 150.0, 150.0, 150.0}});
    check_true("hard stop cause", closed.size() == 2 && closed[0].cause == Cause::HardStop &&
                                      closed[1].cause == Cause::HardStop);
    avbt::Report r = p.report();
    check_true("hard stop halts", r.halted);
    check_value("hard stop closes all", r.open_positions, 0);
    check_value("hard stop balance", r.balance, 6750.0);
    check_true("no trade after halt", !p.open("BTC", Side::Long, 60.0, 55.0, 1.0));
}

void test_hard_stop_holds_above_floor() {
    Portfolio p(PortfolioSettings{.risk_per_trade = 0.25});
    p.open("BTC", Side::Long, 100.0, 50.0, 1.0);
    p.check({Quote{"BTC", 70.0, 70.0, 70.0, 70.0}});
    check_true("above floor keeps trading", !p.report().halted);
}

// 20 units from 100, take profit 110. A bar from 105 up to 112 fills at 110: +200.
void test_take_profit_fills_at_level() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0);
    auto closed = p.check({Quote{"BTC", 105.0, 112.0, 104.0, 111.0}});
    check_true("take profit cause", closed.size() == 1 && closed[0].cause == Cause::TakeProfit);
    check_value("take profit fill", closed.at(0).exit_price, 110.0);
    check_value("take profit balance", p.report().balance, 10200.0);
}

// A bar that opens at 115, past the 110 take profit, fills at 115: 20 * 15 = +300.
void test_take_profit_gap_fills_at_open() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0);
    auto closed = p.check({Quote{"BTC", 115.0, 116.0, 114.0, 115.0}});
    check_value("take profit gap fill", closed.at(0).exit_price, 115.0);
    check_value("take profit gap balance", p.report().balance, 10300.0);
}

// A bar from 94 to 112 reaches both the 95 stop and the 110 take profit: stop, -100.
void test_stop_wins_over_take_profit() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0);
    auto closed = p.check({Quote{"BTC", 100.0, 112.0, 94.0, 100.0}});
    check_true("both levels stop cause", closed.size() == 1 && closed[0].cause == Cause::Stop);
    check_value("both levels balance", p.report().balance, 9900.0);
}

void test_take_profit_wrong_side() {
    Portfolio p(PortfolioSettings{});
    check_true("skip long take profit below entry",
               !p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 90.0));
    check_true("skip short take profit above entry",
               !p.open("BTC", Side::Short, 100.0, 105.0, 10.0, 110.0));
}

// Fee 0.1%. Open: 0.001 * 20 * 100 = 2, balance 9,998. Close at 110: fee
// 0.001 * 20 * 110 = 2.2. Result 200 - 2 - 2.2 = 195.8; balance 9,998 + 200 - 2.2.
void test_fees() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, std::nan(""), 0.001);
    check_value("fee at open", p.report().balance, 9998.0);
    auto closed = p.close("BTC", 110.0);
    check_value("fee result", closed->result, 195.8);
    check_value("fee balance", p.report().balance, 10195.8);
}

// A 5 trail on the 100 long: a bar to 110 lifts the stop to 105; a bar to
// 108 leaves it there; a bar down to 103 fills it at 105, 20 * 5 = +100.
void test_trailing_stop() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, std::nan(""), 0.0, 5.0);
    p.check({Quote{"BTC", 100.0, 110.0, 104.0, 108.0}});
    check_value("trail lifts stop", p.positions().at(0).stop_price, 105.0);
    p.check({Quote{"BTC", 108.0, 108.0, 106.0, 107.0}});
    check_value("trail never falls", p.positions().at(0).stop_price, 105.0);
    auto closed = p.check({Quote{"BTC", 106.0, 107.0, 103.0, 104.0}});
    check_true("trail stop cause", closed.size() == 1 && closed[0].cause == Cause::Stop);
    check_value("trail stop fill", closed.at(0).exit_price, 105.0);
    check_value("trail stop balance", p.report().balance, 10100.0);
}

// A short trails above the lows: a bar down to 90 drops the 105 stop to 95.
void test_trailing_stop_short() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Short, 100.0, 105.0, 10.0, std::nan(""), 0.0, 5.0);
    p.check({Quote{"BTC", 100.0, 101.0, 90.0, 92.0}});
    check_value("short trail drops stop", p.positions().at(0).stop_price, 95.0);
}

// Half of 20 units closes at the 110 take profit, +100; the other 10 stay
// open with no take profit, so a bar to 130 closes nothing.
void test_partial_take_profit() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0, 0.0, 0.0, 0.5);
    auto closed = p.check({Quote{"BTC", 105.0, 112.0, 104.0, 111.0}});
    check_true("partial take cause", closed.size() == 1 && closed[0].cause == Cause::TakeProfit);
    check_value("partial take size", closed.at(0).size, 10.0);
    check_value("partial take balance", p.report().balance, 10100.0);
    check_value("rest size", p.positions().at(0).size, 10.0);
    check_true("rest has no take profit", std::isnan(p.positions().at(0).take_profit_price));
    check_true("rest runs", p.check({Quote{"BTC", 111.0, 130.0, 111.0, 125.0}}).empty());
}

void test_open_refuses_bad_trail_or_fraction() {
    Portfolio p(PortfolioSettings{});
    check_true("skip negative trail", !p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0, 0.0, -1.0));
    check_true("skip zero fraction", !p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0, 0.0, 0.0, 0.0));
    check_true("skip fraction above one", !p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0, 0.0, 0.0, 1.5));
}

}


int main() {
    test_open_sizes_from_risk();
    test_open_short_liquidates_above();
    test_open_skips();
    test_partial_close();
    test_stop_fills_at_stop();
    test_gap_past_stop_fills_at_open();
    test_gap_past_liquidation();
    test_mark_moves_equity();
    test_hard_stop_counts_unrealized();
    test_hard_stop_holds_above_floor();
    test_take_profit_fills_at_level();
    test_take_profit_gap_fills_at_open();
    test_stop_wins_over_take_profit();
    test_take_profit_wrong_side();
    test_fees();
    test_trailing_stop();
    test_trailing_stop_short();
    test_partial_take_profit();
    test_open_refuses_bad_trail_or_fraction();
    if (failures == 0) std::printf("all portfolio checks passed\n");
    return failures == 0 ? 0 : 1;
}
