// Tests for the portfolio in avbt/portfolio.hpp, with hand-worked numbers.
// The program returns 0 when every check passes and 1 when any check fails.

#include "avbt/portfolio.hpp"

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

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
using avbt::Fees;

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

// The long above is 2,000 of notional. ADR 0022.
void test_open_min_position() {
    const double nan = std::nan("");
    Portfolio p(PortfolioSettings{});
    check_true("skip notional below min position",
               !p.open("BTC", Side::Long, 100.0, 95.0, 10.0, nan, {}, 0.0, 1.0, avbt::unknown_time, 0.0, 2000.01));
    check_value("nothing opened", p.report().open_positions, 0);
    check_true("open at exactly min position",
               p.open("BTC", Side::Long, 100.0, 95.0, 10.0, nan, {}, 0.0, 1.0, avbt::unknown_time, 0.0, 2000.0));
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

// A bar that opens at 112, past the 110 take profit, then falls to 94, past the
// 95 stop: the open came first, so it takes profit at 112, 20 * 12 = +240.
void test_open_past_take_profit_beats_stop() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0);
    auto closed = p.check({Quote{"BTC", 112.0, 112.0, 94.0, 100.0}});
    check_true("open past take cause", closed.size() == 1 && closed[0].cause == Cause::TakeProfit);
    check_value("open past take fill", closed.at(0).exit_price, 112.0);
    check_value("open past take balance", p.report().balance, 10240.0);
}

// The short side: opens at 88, past the 90 take profit, then rises to 106.
void test_short_open_past_take_profit_beats_stop() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Short, 100.0, 105.0, 10.0, 90.0);
    auto closed = p.check({Quote{"BTC", 88.0, 106.0, 88.0, 100.0}});
    check_true("short open past take cause", closed.size() == 1 && closed[0].cause == Cause::TakeProfit);
    check_value("short open past take fill", closed.at(0).exit_price, 88.0);
    check_value("short open past take balance", p.report().balance, 10240.0);
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
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, std::nan(""), Fees{0.001, 0.001, {}});
    check_value("fee at open", p.report().balance, 9998.0);
    auto closed = p.close("BTC", 110.0);
    check_value("fee result", closed->result, 195.8);
    check_value("fee balance", p.report().balance, 10195.8);
}

// Upside Perps tiers on the 20-unit long, 200 of collateral. Half closed at
// 120: gross 200 on 100 of collateral, ROI 2.0, 25% = 50. The rest at 130:
// gross 300, ROI 3.0, 20% = 60. A mark at 110 owes 25% of 200 = 50.
const std::vector<avbt::Tier> upside{{2.5, 0.25}, {5.0, 0.20}, {15.0, 0.10}, {25.0, 0.05}};

void test_profit_share() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, std::nan(""), Fees{0.0, 0.0, upside});
    p.check({Quote{"BTC", 110.0, 110.0, 110.0, 110.0}});
    check_value("share pending", p.report().equity, 10150.0);
    auto half = p.close("BTC", 120.0, 0.5);
    check_value("share half fee", half->fees, 50.0);
    check_value("share half result", half->result, 150.0);
    auto rest = p.close("BTC", 130.0);
    check_value("share rest fee", rest->fees, 60.0);
    check_value("share balance", p.report().balance, 10390.0);
}

void test_profit_share_loss() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, std::nan(""), Fees{0.0, 0.0, upside});
    auto closed = p.close("BTC", 97.0);
    check_value("share loss fee", closed->fees, 0.0);
    check_value("share loss result", closed->result, -60.0);
}

// A 5 trail on the 100 long: a bar to 110 lifts the stop to 105; a bar to
// 108 leaves it there; a bar down to 103 fills it at 105, 20 * 5 = +100.
void test_trailing_stop() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, std::nan(""), Fees{}, 5.0);
    p.check({Quote{"BTC", 100.0, 110.0, 104.0, 108.0}});
    check_value("trail lifts stop", p.positions().at(0).stop_price, 105.0);
    p.check({Quote{"BTC", 108.0, 108.0, 106.0, 107.0}});
    check_value("trail never falls", p.positions().at(0).stop_price, 105.0);
    auto closed = p.check({Quote{"BTC", 106.0, 107.0, 103.0, 104.0}});
    check_true("trail stop cause", closed.size() == 1 && closed[0].cause == Cause::TrailingStop);
    check_value("trail stop fill", closed.at(0).exit_price, 105.0);
    check_value("trail stop balance", p.report().balance, 10100.0);
}

// A trail that never moved the stop: the stop-out is a plain Stop.
void test_unmoved_trail_is_a_stop() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, std::nan(""), Fees{}, 5.0);
    auto closed = p.check({Quote{"BTC", 100.0, 100.0, 94.0, 96.0}});
    check_true("unmoved trail cause", closed.size() == 1 && closed[0].cause == Cause::Stop);
    check_value("unmoved trail fill", closed.at(0).exit_price, 95.0);
}

// A short trails above the lows: a bar down to 90 drops the 105 stop to 95.
void test_trailing_stop_short() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Short, 100.0, 105.0, 10.0, std::nan(""), Fees{}, 5.0);
    p.check({Quote{"BTC", 100.0, 101.0, 90.0, 92.0}});
    check_value("short trail drops stop", p.positions().at(0).stop_price, 95.0);
}

// Half of 20 units closes at the 110 take profit, +100; the other 10 stay
// open with no take profit, so a bar to 130 closes nothing.
void test_partial_take_profit() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0, Fees{}, 0.0, 0.5);
    auto closed = p.check({Quote{"BTC", 105.0, 112.0, 104.0, 111.0}});
    check_true("partial take cause", closed.size() == 1 && closed[0].cause == Cause::PartialTakeProfit);
    check_value("partial take size", closed.at(0).size, 10.0);
    check_value("partial take balance", p.report().balance, 10100.0);
    check_value("rest size", p.positions().at(0).size, 10.0);
    check_true("rest has no take profit", std::isnan(p.positions().at(0).take_profit_price));
    check_true("rest runs", p.check({Quote{"BTC", 111.0, 130.0, 111.0, 125.0}}).empty());
}

void test_open_refuses_bad_trail_or_fraction() {
    Portfolio p(PortfolioSettings{});
    check_true("skip negative trail", !p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0, Fees{}, -1.0));
    check_true("skip zero fraction", !p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0, Fees{}, 0.0, 0.0));
    check_true("skip fraction above one", !p.open("BTC", Side::Long, 100.0, 95.0, 10.0, 110.0, Fees{}, 0.0, 1.5));
}

// Risk scaled by leverage: 1% * 5 = 5% of 10,000 is 500 at risk, so a stop 5
// below 100 gives 100 units, 5 times the 20 of an unscaled open, and the stop
// loses 500. Collateral is 100 * 100 / 5 = 2,000.
void test_scale_risk_with_leverage() {
    Portfolio p(PortfolioSettings{.scale_risk_with_leverage = true});
    p.open("BTC", Side::Long, 100.0, 95.0, 5.0);
    check_value("scaled size", p.positions().at(0).size, 100.0);
    check_value("scaled collateral", p.positions().at(0).collateral, 2000.0);
    p.check({Quote{"BTC", 96.0, 96.0, 94.0, 95.0}});
    check_value("scaled stop loss", p.report().balance, 9500.0);
}

// Spread 4% on a long from 100, stop 75, 2x (ADR 0020). The fill is 104;
// the stop fills at 75 * 0.96 = 72, so 100 at risk buys 100 / 32 = 3.125
// units. Collateral 3.125 * 104 / 2 = 162.5; liquidation
// 104 - 0.85 * 162.5 / 3.125 = 59.8.
Portfolio spread_long(double take = std::nan(""), double fraction = 1.0) {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 75.0, 2.0, take, Fees{}, 0.0, fraction, avbt::unknown_time, 0.04);
    return p;
}

void test_spread_open() {
    Portfolio p = spread_long();
    const auto& pos = p.positions().at(0);
    check_value("spread entry", pos.entry_price, 104.0);
    check_value("spread size", pos.size, 3.125);
    check_value("spread collateral", pos.collateral, 162.5);
    check_value("spread liquidation", pos.liquidation_price, 59.8);
    check_value("spread stop stays oracle", pos.stop_price, 75.0);
    check_value("spread stored", pos.spread, 0.04);
    check_value("spread entry cost", pos.spread_cost, 12.5);
}

// The stop at 75 fills at 72: 3.125 * 32 = 100, exactly the risk.
void test_spread_stop_loses_risk() {
    Portfolio p = spread_long();
    auto closed = p.check({Quote{"BTC", 80.0, 80.0, 70.0, 72.0, 0.04}});
    check_true("spread stop cause", closed.size() == 1 && closed[0].cause == Cause::Stop);
    check_value("spread stop exit", closed.at(0).exit_price, 72.0);
    check_value("spread stop balance", p.report().balance, 9900.0);
    // 3.125 * 100 * 0.04 + 3.125 * 75 * 0.04
    check_value("spread stop cost", closed.at(0).spread_cost, 21.875);
}

// A gap to 50 passes the 59.8 liquidation: it fills there, no spread, and
// loses 0.85 of the collateral.
void test_spread_liquidation_has_none() {
    Portfolio p = spread_long();
    auto closed = p.check({Quote{"BTC", 50.0, 50.0, 50.0, 50.0, 0.04}});
    check_true("spread liq cause", closed.size() == 1 && closed[0].cause == Cause::Liquidation);
    check_value("spread liq exit", closed.at(0).exit_price, 59.8);
    check_value("spread liq loss", closed.at(0).result, -0.85 * 162.5);
    check_value("spread liq cost", closed.at(0).spread_cost, 12.5);
}

// Half at the 120 take profit, on a bar with a 2% spread: fills at 117.6.
// 1.5625 units: gross 1.5625 * 13.6 = 21.25. Cost: half the 12.5 entry part,
// plus 1.5625 * 120 * 0.02 = 3.75.
void test_spread_partial_take_profit() {
    Portfolio p = spread_long(120.0, 0.5);
    auto closed = p.check({Quote{"BTC", 105.0, 125.0, 104.0, 122.0, 0.02}});
    check_true("spread partial cause", closed.size() == 1 && closed[0].cause == Cause::PartialTakeProfit);
    check_value("spread partial exit", closed.at(0).exit_price, 117.6);
    check_value("spread partial result", closed.at(0).result, 21.25);
    check_value("spread partial cost", closed.at(0).spread_cost, 10.0);
    check_value("spread rest cost", p.positions().at(0).spread_cost, 6.25);
    check_value("spread rest last", p.positions().at(0).spread, 0.02);
}

// A short fills below the oracle at open and above it at close.
void test_spread_short_prices() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Short, 100.0, 105.0, 10.0, std::nan(""), Fees{}, 0.0, 1.0, avbt::unknown_time, 0.01);
    check_value("short spread entry", p.positions().at(0).entry_price, 99.0);
    auto closed = p.close("BTC", 90.0, 1.0, Cause::Order, 0.01);
    check_value("short spread exit", closed->exit_price, 90.9);
    check_value("short spread gross", closed->result, (99.0 - 90.9) * closed->size);
}

// Fees are on the fill prices: 0.1% of 3.125 * 104 at the open.
void test_spread_fees_on_fills() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Long, 100.0, 75.0, 2.0, std::nan(""), Fees{0.001, 0.001, {}}, 0.0, 1.0,
           avbt::unknown_time, 0.04);
    check_value("spread open fee", p.report().balance, 10000.0 - 0.001 * 3.125 * 104.0);
    auto closed = p.close("BTC", 110.0, 1.0, Cause::Order, 0.04);
    double x = 110.0 * 0.96;
    check_value("spread close fees", closed->fees, 0.001 * 3.125 * (104.0 + x));
    check_value("spread identity", closed->result + closed->fees + closed->holding,
                (x - 104.0) * 3.125);
}

// The 80% cap counts the fill. Without the spread, 3x passes: 4 units,
// collateral 133.3, 100 < 106.7. With it, collateral 3.125 * 104 / 3 =
// 108.3, and 100 > 86.7: refused. 2.5x gives 130, 100 < 104: opens.
void test_spread_stop_cap_uses_fill() {
    Portfolio p(PortfolioSettings{});
    check_true("no spread 3x opens", p.open("ETH", Side::Long, 100.0, 75.0, 3.0));
    check_true("spread 3x refused", !p.open("BTC", Side::Long, 100.0, 75.0, 3.0, std::nan(""), Fees{},
                                            0.0, 1.0, avbt::unknown_time, 0.04));
    check_true("spread 2.5x opens", p.open("BTC", Side::Long, 100.0, 75.0, 2.5, std::nan(""), Fees{},
                                           0.0, 1.0, avbt::unknown_time, 0.04));
}

// Opened with no spread: 100 units from 100 at 1x, stop 50. A bar to 70 with
// a 2% spread brings equity to the 7,000 floor; the hard stop closes at the
// 70 mark less 2%: 68.6, balance 10,000 - 100 * 31.4.
void test_spread_hard_stop_uses_last() {
    Portfolio p(PortfolioSettings{.risk_per_trade = 0.5});
    check_true("hard stop spread opens", p.open("BTC", Side::Long, 100.0, 50.0, 1.0));
    auto closed = p.check({Quote{"BTC", 70.0, 70.0, 70.0, 70.0, 0.02}});
    check_true("hard stop spread cause", closed.size() == 1 && closed[0].cause == Cause::HardStop);
    check_value("hard stop spread exit", closed.at(0).exit_price, 68.6);
    check_value("hard stop spread balance", p.report().balance, 6860.0);
    check_value("hard stop spread cost", closed.at(0).spread_cost, 140.0);
}

// Equity marks at the oracle close with no spread: a mark at 100 shows only
// the entry spread paid, 3.125 * 4 = 12.5.
void test_spread_equity_mark() {
    Portfolio p = spread_long();
    p.check({Quote{"BTC", 100.0, 100.0, 100.0, 100.0, 0.04}});
    check_value("spread equity", p.report().equity, 10000.0 - 12.5);
}

// The stop at 75 on a bar with a 25% spread would fill at 56.25, past the
// 59.8 liquidation: it liquidates there instead, with no exit spread (ADR 0020).
void test_spread_stop_past_liquidation() {
    Portfolio p = spread_long();
    auto closed = p.check({Quote{"BTC", 80.0, 80.0, 70.0, 72.0, 0.25}});
    check_true("spread stop liq cause", closed.size() == 1 && closed[0].cause == Cause::Liquidation);
    check_value("spread stop liq exit", closed.at(0).exit_price, 59.8);
    check_value("spread stop liq loss", closed.at(0).result, -0.85 * 162.5);
    check_value("spread stop liq cost", closed.at(0).spread_cost, 12.5);
}

// A gap to 62 sits above the 59.8 liquidation, but its fill 62 * 0.96 =
// 59.52 does not: liquidation at 59.8.
void test_spread_gap_fill_past_liquidation() {
    Portfolio p = spread_long();
    auto closed = p.check({Quote{"BTC", 62.0, 62.0, 61.0, 61.5, 0.04}});
    check_true("spread gap liq cause", closed.size() == 1 && closed[0].cause == Cause::Liquidation);
    check_value("spread gap liq exit", closed.at(0).exit_price, 59.8);
    check_value("spread gap liq loss", closed.at(0).result, -0.85 * 162.5);
}

// Short from 100 at 2x, stop 125, spread 4%: fill 96, stop fill 130, so
// 100 / 34 units, collateral 48 each, liquidation 96 + 0.85 * 48 = 136.8.
// The stop on a 10% bar fills at 137.5, past it: liquidation at 136.8.
void test_spread_short_stop_past_liquidation() {
    Portfolio p(PortfolioSettings{});
    check_true("short liq opens", p.open("BTC", Side::Short, 100.0, 125.0, 2.0, std::nan(""), Fees{},
                                         0.0, 1.0, avbt::unknown_time, 0.04));
    double collateral = p.positions().at(0).collateral;
    check_value("short liq price", p.positions().at(0).liquidation_price, 136.8);
    auto closed = p.check({Quote{"BTC", 120.0, 126.0, 120.0, 124.0, 0.1}});
    check_true("short stop liq cause", closed.size() == 1 && closed[0].cause == Cause::Liquidation);
    check_value("short stop liq exit", closed.at(0).exit_price, 136.8);
    check_value("short stop liq loss", closed.at(0).result, -0.85 * collateral);
}

// 100 units from 100 at 1x: liquidation 15. A bar to 70 with an 80% spread
// trips the hard stop, whose fill 70 * 0.2 = 14 passes 15: it closes at 15.
void test_spread_hard_stop_past_liquidation() {
    Portfolio p(PortfolioSettings{.risk_per_trade = 0.5});
    check_true("hard stop liq opens", p.open("BTC", Side::Long, 100.0, 50.0, 1.0));
    auto closed = p.check({Quote{"BTC", 70.0, 70.0, 70.0, 70.0, 0.8}});
    check_true("hard stop liq cause", closed.size() == 1 && closed[0].cause == Cause::Liquidation);
    check_value("hard stop liq exit", closed.at(0).exit_price, 15.0);
    check_value("hard stop liq loss", closed.at(0).result, -8500.0);
    check_value("hard stop liq cost", closed.at(0).spread_cost, 0.0);
}

// A close order at 62 with a 4% spread fills at 59.52, past the 59.8
// liquidation: it liquidates there. A partial close is not checked.
void test_spread_close_order_past_liquidation() {
    Portfolio p = spread_long();
    auto closed = p.close("BTC", 62.0, 1.0, Cause::Order, 0.04);
    check_true("close liq cause", closed && closed->cause == Cause::Liquidation);
    check_value("close liq exit", closed->exit_price, 59.8);
    check_value("close liq loss", closed->result, -0.85 * 162.5);
    check_value("close liq cost", closed->spread_cost, 12.5);
}

// Half at the 120 take profit on a bar with a 51% spread fills at 58.8,
// past the 59.8 liquidation: the whole position liquidates there.
void test_spread_partial_past_liquidation() {
    Portfolio p = spread_long(120.0, 0.5);
    auto closed = p.check({Quote{"BTC", 105.0, 125.0, 104.0, 122.0, 0.51}});
    check_true("partial liq cause", closed.size() == 1 && closed[0].cause == Cause::Liquidation);
    check_value("partial liq exit", closed.at(0).exit_price, 59.8);
    check_value("partial liq size", closed.at(0).size, 3.125);
    check_value("partial liq loss", closed.at(0).result, -0.85 * 162.5);
    check_value("partial liq cost", closed.at(0).spread_cost, 12.5);
    check_true("partial liq: none left", p.positions().empty());
}

// The short mirror: from 100 at 2x, stop 125, spread 4%, liquidation 136.8.
// Half at the 90 take profit with a 60% spread fills at 144: liquidated.
void test_spread_short_partial_past_liquidation() {
    Portfolio p(PortfolioSettings{});
    p.open("BTC", Side::Short, 100.0, 125.0, 2.0, 90.0, Fees{}, 0.0, 0.5, avbt::unknown_time, 0.04);
    double collateral = p.positions().at(0).collateral;
    auto closed = p.check({Quote{"BTC", 95.0, 96.0, 85.0, 88.0, 0.6}});
    check_true("short partial liq cause", closed.size() == 1 && closed[0].cause == Cause::Liquidation);
    check_value("short partial liq exit", closed.at(0).exit_price, 136.8);
    check_value("short partial liq loss", closed.at(0).result, -0.85 * collateral);
    check_true("short partial liq: none left", p.positions().empty());
}

}


int main() {
    test_open_sizes_from_risk();
    test_open_short_liquidates_above();
    test_open_skips();
    test_open_min_position();
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
    test_open_past_take_profit_beats_stop();
    test_short_open_past_take_profit_beats_stop();
    test_take_profit_wrong_side();
    test_fees();
    test_profit_share();
    test_profit_share_loss();
    test_trailing_stop();
    test_unmoved_trail_is_a_stop();
    test_trailing_stop_short();
    test_partial_take_profit();
    test_open_refuses_bad_trail_or_fraction();
    test_scale_risk_with_leverage();
    test_spread_open();
    test_spread_stop_loses_risk();
    test_spread_liquidation_has_none();
    test_spread_partial_take_profit();
    test_spread_short_prices();
    test_spread_fees_on_fills();
    test_spread_stop_cap_uses_fill();
    test_spread_hard_stop_uses_last();
    test_spread_equity_mark();
    test_spread_stop_past_liquidation();
    test_spread_gap_fill_past_liquidation();
    test_spread_short_stop_past_liquidation();
    test_spread_hard_stop_past_liquidation();
    test_spread_close_order_past_liquidation();
    test_spread_partial_past_liquidation();
    test_spread_short_partial_past_liquidation();
    if (failures == 0) std::printf("all portfolio checks passed\n");
    return failures == 0 ? 0 : 1;
}
