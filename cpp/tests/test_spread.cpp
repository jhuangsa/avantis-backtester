// Tests for the spread (issue 21, part 1), written from the spec alone.
// A scripted strategy sends given orders at given steps on one market of
// hourly bars. Every expected number is worked by hand in a comment.
// Settings are the defaults unless named: balance 10,000, risk 1% = 100.
// dir = +1 long, -1 short. Open fill E = P*(1 + dir*s); close X = P*(1 - dir*s).
// The program returns 0 when every check passes and 1 when any check fails.

#include "avbt/backtest.hpp"
#include "avbt/costs.hpp"
#include "avbt/spreads.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <limits>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

int failures = 0;

// Relative tolerance: prices times sizes carry rounding.
void check_value(const std::string& label, double actual, double expected) {
    double tol = 1e-9 * std::max(1.0, std::abs(expected));
    if (!(std::abs(actual - expected) < tol)) {
        ++failures;
        std::printf("FAIL %s: expected %.12f, got %.12f\n", label.c_str(), expected, actual);
    }
}

void check_true(const std::string& label, bool condition) {
    if (!condition) {
        ++failures;
        std::printf("FAIL %s\n", label.c_str());
    }
}

using namespace avbt;

const double kNan = std::nan("");
const double kInf = std::numeric_limits<double>::infinity();

// Sends the orders in `at` at the step whose index is the key.
struct Script {
    std::map<int, std::vector<Order>> at;
    int t = 0;
    void prepare(const Markets&) { t = 0; }
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t, const Report&, const std::vector<Position>&) {
        auto it = at.find(t++);
        return it == at.end() ? std::vector<Order>{} : it->second;
    }
};
static_assert(Strategy<Script>);

// Hourly bars from {open, high, low, close}; bar i opens at (first + i) hours.
Bars bars(const std::vector<std::array<double, 4>>& ohlc, int first = 0) {
    Bars b;
    b.timeframe = Timeframe::Hour1;
    for (std::size_t i = 0; i < ohlc.size(); ++i) {
        b.ts.push_back((first + static_cast<int64_t>(i)) * 3600);
        b.open.push_back(ohlc[i][0]);
        b.high.push_back(ohlc[i][1]);
        b.low.push_back(ohlc[i][2]);
        b.close.push_back(ohlc[i][3]);
        b.minutes_with_data.push_back(60);
    }
    return b;
}

std::array<double, 4> flat(double p) { return {p, p, p, p}; }

Order open_order(Side side, double stop, double take = kNan, double lev = 1.0,
                 double trail = 0.0, double fraction = 1.0) {
    return Order{.kind = Order::Kind::Open, .instrument = "X", .side = side,
                 .stop_distance = stop, .take_profit_distance = take, .leverage = lev,
                 .trail_distance = trail, .take_profit_fraction = fraction};
}

Order close_order() { return Order{.kind = Order::Kind::Close, .instrument = "X"}; }

Costs spread_costs(std::vector<double> s, double cap = kNan) {
    Costs c;
    c.spread = std::move(s);
    c.max_spread = cap;
    return c;
}

// One market "X" with these costs.
Result go(const Bars& b, std::map<int, std::vector<Order>> at, const Costs& c,
          PortfolioSettings settings = {}) {
    Script s{.at = std::move(at)};
    Markets m = Markets::make({{"X", {b}}});
    return backtest(s, m, MarketCosts{{"X", c}}, settings);
}

// gross = dir*(X - E)*size = result + fees + holding_costs.
void check_identity(const std::string& label, const Trade& t) {
    double dir = t.side == Side::Long ? 1.0 : -1.0;
    check_value(label + " gross identity", t.result + t.fees + t.holding_costs,
                dir * (t.exit_price - t.entry_price) * t.size);
}

bool one_trade(const std::string& label, const Result& r) {
    bool ok = r.trades.size() == 1;
    check_true(label + " one trade", ok);
    return ok;
}

void test_open_and_close_long() {
    // s = 0.001. Open at open[1] = 100: E = 100*1.001 = 100.1. Stop 95.
    // size = 100 / (100.1 - 95*0.999) = 100 / 5.195.
    // Close fills at open[3] = 110: X = 110*0.999 = 109.89.
    // result = (109.89 - 100.1)*size = 9.79*size.
    // spread_cost = size*100*0.001 + size*110*0.001 = 0.21*size.
    Bars b = bars({flat(100), flat(100), flat(100), flat(110), flat(110)});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05)}}, {2, {close_order()}}},
                  spread_costs({0.001}));
    if (!one_trade("long", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.195;
    check_true("long cause order", t.cause == Cause::Order);
    check_value("long entry bar", t.entry_bar, 1);
    check_value("long exit bar", t.exit_bar, 3);
    check_value("long entry price", t.entry_price, 100.1);
    check_value("long exit price", t.exit_price, 109.89);
    check_value("long size", t.size, size);
    check_value("long result", t.result, 9.79 * size);
    check_value("long fees", t.fees, 0.0);
    check_value("long spread_cost", t.spread_cost, 0.21 * size);
    check_value("long balance", r.ending_balance, 10000 + 9.79 * size);
    check_identity("long", t);
}

void test_open_and_close_short() {
    // s = 0.001. E = 100*(1 - 0.001) = 99.9. Stop 105.
    // size = 100 / (105*1.001 - 99.9) = 100 / 5.205.
    // Close at open[3] = 90: X = 90*1.001 = 90.09.
    // result = -(90.09 - 99.9)*size = 9.81*size; spread_cost = (0.1 + 0.09)*size.
    Bars b = bars({flat(100), flat(100), flat(100), flat(90), flat(90)});
    Result r = go(b, {{0, {open_order(Side::Short, 0.05)}}, {2, {close_order()}}},
                  spread_costs({0.001}));
    if (!one_trade("short", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.205;
    check_value("short entry price", t.entry_price, 99.9);
    check_value("short exit price", t.exit_price, 90.09);
    check_value("short size", t.size, size);
    check_value("short result", t.result, 9.81 * size);
    check_value("short spread_cost", t.spread_cost, 0.19 * size);
    check_identity("short", t);
}

void test_stop_loses_risk() {
    // s = 0.002. E = 100.2, stop 95 (oracle), X = 95*0.998 = 94.81.
    // size = 100 / (100.2 - 94.81) = 100 / 5.39. Loss = 5.39*size = 100 exactly.
    // spread_cost = size*(100*0.002 + 95*0.002) = 0.39*size.
    Bars b = bars({flat(100), flat(100), {100, 100, 90, 92}, flat(92)});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05)}}}, spread_costs({0.002}));
    if (!one_trade("long stop", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.39;
    check_true("long stop cause", t.cause == Cause::Stop);
    check_value("long stop exit bar", t.exit_bar, 2);
    check_value("long stop entry", t.entry_price, 100.2);
    check_value("long stop exit", t.exit_price, 94.81);
    check_value("long stop size", t.size, size);
    check_value("long stop loses risk", t.result, -100.0);
    check_value("long stop spread_cost", t.spread_cost, 0.39 * size);
    check_identity("long stop", t);

    // Short, s = 0.002: E = 99.8, stop 105, X = 105*1.002 = 105.21.
    // size = 100 / (105.21 - 99.8) = 100 / 5.41; loss 100 exactly.
    // spread_cost = size*(0.2 + 0.21).
    Bars u = bars({flat(100), flat(100), {100, 110, 99, 108}, flat(108)});
    Result q = go(u, {{0, {open_order(Side::Short, 0.05)}}}, spread_costs({0.002}));
    if (!one_trade("short stop", q)) return;
    const Trade& s = q.trades[0];
    check_value("short stop exit", s.exit_price, 105.21);
    check_value("short stop size", s.size, 100.0 / 5.41);
    check_value("short stop loses risk", s.result, -100.0);
    check_value("short stop spread_cost", s.spread_cost, 0.41 * (100.0 / 5.41));
    check_identity("short stop", s);
}

void test_gapped_stop() {
    // s = 0.002. Bar 2 opens at 90, below the stop 95: X = 90*0.998 = 89.82.
    // size = 100 / 5.39; result = (89.82 - 100.2)*size = -10.38*size.
    // spread_cost = size*(0.2 + 0.18).
    Bars b = bars({flat(100), flat(100), {90, 91, 88, 89}, flat(89)});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05)}}}, spread_costs({0.002}));
    if (!one_trade("gap", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.39;
    check_true("gap cause", t.cause == Cause::Stop);
    check_value("gap exit", t.exit_price, 89.82);
    check_value("gap result", t.result, -10.38 * size);
    check_value("gap spread_cost", t.spread_cost, 0.38 * size);
    check_identity("gap", t);
}

void test_take_profit() {
    // s = 0.001, stop 95, take 110 (oracle). Bar 2 high 112 reaches it.
    // X = 110*0.999 = 109.89; size = 100 / 5.195; result = 9.79*size.
    Bars b = bars({flat(100), flat(100), {100, 112, 99, 111}, flat(111)});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05, 0.10)}}}, spread_costs({0.001}));
    if (!one_trade("take", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.195;
    check_true("take cause", t.cause == Cause::TakeProfit);
    check_value("take exit", t.exit_price, 109.89);
    check_value("take result", t.result, 9.79 * size);
    check_value("take spread_cost", t.spread_cost, 0.21 * size);
    check_identity("take", t);
}

void test_partial_take_profit() {
    // Same, take_profit_fraction 0.5. size = 100 / 5.195.
    // Part 1 at bar 2: size/2 at 109.89.
    //   spread_cost = 0.5*size*100*0.001 + (size/2)*110*0.001 = 0.5*size*0.21.
    // Part 2 by close order at open[3] = 105: X = 105*0.999 = 104.895.
    //   spread_cost = 0.5*size*0.1 + (size/2)*105*0.001 = 0.5*size*0.205.
    // Sum = size*0.1 (whole entry part) + 0.5*size*0.11 + 0.5*size*0.105.
    Bars b = bars({flat(100), flat(100), {100, 112, 99, 111}, flat(105), flat(105)});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05, 0.10, 1.0, 0.0, 0.5)}}, {2, {close_order()}}},
                  spread_costs({0.001}));
    check_true("partial two trades", r.trades.size() == 2);
    if (r.trades.size() != 2) return;
    double size = 100.0 / 5.195;
    const Trade& a = r.trades[0];
    const Trade& c = r.trades[1];
    check_true("partial cause", a.cause == Cause::PartialTakeProfit);
    check_value("partial size", a.size, size / 2);
    check_value("partial exit", a.exit_price, 109.89);
    check_value("partial spread_cost", a.spread_cost, 0.5 * size * 0.21);
    check_value("rest exit", c.exit_price, 104.895);
    check_value("rest size", c.size, size / 2);
    check_value("rest spread_cost", c.spread_cost, 0.5 * size * 0.205);
    check_value("partial sum", a.spread_cost + c.spread_cost,
                size * 0.1 + 0.5 * size * 0.11 + 0.5 * size * 0.105);
    check_identity("partial", a);
    check_identity("rest", c);
}

void test_trailing_stop() {
    // s = 0.001, stop 95, trail = 0.05*100 = 5 (oracle open).
    // Bar 2 high 110 moves the stop to 105. Bar 3 opens 107, low 104: fills at 105.
    // X = 105*0.999 = 104.895; size = 100 / 5.195; result = 4.795*size.
    // spread_cost = size*(0.1 + 0.105).
    Bars b = bars({flat(100), flat(100), {100, 110, 100, 108}, {107, 107, 104, 104}, flat(104)});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05, kNan, 1.0, 0.05)}}}, spread_costs({0.001}));
    if (!one_trade("trail", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.195;
    check_true("trail cause", t.cause == Cause::TrailingStop);
    check_value("trail exit bar", t.exit_bar, 3);
    check_value("trail exit", t.exit_price, 104.895);
    check_value("trail result", t.result, 4.795 * size);
    check_value("trail spread_cost", t.spread_cost, 0.205 * size);
    check_identity("trail", t);
}

void test_end_of_data() {
    // X ends at step 2, Y runs one more step. s = 0.001 on X, none on Y.
    // E = 100.1; X closes at its last close 102: X = 102*0.999 = 101.898.
    // size = 100 / 5.195; result = 1.798*size; spread_cost = size*(0.1 + 0.102).
    Bars x = bars({flat(100), flat(100), {100, 103, 100, 102}});
    Bars y = bars({flat(1)}, 3);
    Script s{.at = {{0, {open_order(Side::Long, 0.05)}}}};
    Markets m = Markets::make({{"X", {x}}, {"Y", {y}}});
    Costs ny;
    ny.spread = no_spread;
    Result r = backtest(s, m, MarketCosts{{"X", spread_costs({0.001})}, {"Y", ny}}, PortfolioSettings{});
    if (!one_trade("end", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.195;
    check_true("end cause", t.cause == Cause::EndOfData);
    check_value("end exit bar", t.exit_bar, 2);
    check_value("end exit", t.exit_price, 101.898);
    check_value("end result", t.result, 1.798 * size);
    check_value("end spread_cost", t.spread_cost, 0.202 * size);
    check_identity("end", t);
}

void test_liquidation() {
    // 10x, s = 0.001. E = 100.1, stop 95, size = 100 / 5.195.
    // collateral = size*100.1/10 = 10.01*size.
    // liquidation = 100.1 - 0.85*10.01 = 100.1 - 8.5085 = 91.5915.
    // Bar 2 opens at 80, past the stop and the liquidation price: fills at 91.5915, no spread.
    // result = -8.5085*size = -0.85*collateral; spread_cost = size*100*0.001 only.
    Bars b = bars({flat(100), flat(100), {80, 81, 79, 80}, flat(80)});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05, kNan, 10.0)}}}, spread_costs({0.001}));
    if (!one_trade("liq", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.195;
    check_true("liq cause", t.cause == Cause::Liquidation);
    check_value("liq entry", t.entry_price, 100.1);
    check_value("liq exit", t.exit_price, 91.5915);
    check_value("liq exit by formula", t.exit_price, 100.1 - 0.85 * (size * 100.1 / 10.0) / size);
    check_value("liq result", t.result, -8.5085 * size);
    check_value("liq spread_cost", t.spread_cost, 0.1 * size);
    check_identity("liq", t);
}

void test_hard_stop() {
    // hard_stop 0.005: floor 9,950. Column s = {0.001, 0.001, 0.003, 0.003}.
    // Entry at bar 1: E = 100.1, size = 100 / (100.1 - 95*0.999) = 100 / 5.195.
    // Bar 2 close 96 (stop 95 not reached): equity = 10000 - 4.1*19.249 ~ 9921 <= 9950.
    // Hard stop at the last mark with bar 2's spread: X = 96*(1 - 0.003) = 95.712.
    // result = (95.712 - 100.1)*size = -4.388*size; spread_cost = size*(0.1 + 0.288).
    Bars b = bars({flat(100), flat(100), {100, 100, 96, 96}, flat(96)});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05)}}}, spread_costs({0.001, 0.001, 0.003, 0.003}),
                  PortfolioSettings{.hard_stop = 0.005});
    if (!one_trade("hard", r)) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / 5.195;
    check_true("hard cause", t.cause == Cause::HardStop);
    check_value("hard exit bar", t.exit_bar, 2);
    check_value("hard exit", t.exit_price, 95.712);
    check_value("hard result", t.result, -4.388 * size);
    check_value("hard spread_cost", t.spread_cost, 0.388 * size);
    check_identity("hard", t);
}

bool same_trades(const Result& a, const Result& b) {
    if (a.trades.size() != b.trades.size()) return false;
    for (std::size_t i = 0; i < a.trades.size(); ++i) {
        const Trade& x = a.trades[i];
        const Trade& y = b.trades[i];
        if (x.entry_bar != y.entry_bar || x.exit_bar != y.exit_bar || x.cause != y.cause ||
            x.entry_price != y.entry_price || x.exit_price != y.exit_price || x.size != y.size ||
            x.fees != y.fees || x.result != y.result || x.spread_cost != y.spread_cost) {
            return false;
        }
    }
    return a.equity == b.equity && a.ending_balance == b.ending_balance;
}

void test_per_bar_column() {
    // Column {0.001, 0.002, 0.003, 0.004, 0.005}. Entry bar 1 (s = 0.002), exit bar 3 (s = 0.004).
    // E = 100*1.002 = 100.2; size = 100 / (100.2 - 95*0.998) = 100 / 5.39.
    // X = 110*0.996 = 109.56; result = 9.36*size.
    // spread_cost = size*(100*0.002 + 110*0.004) = 0.64*size.
    Bars b = bars({flat(100), flat(100), flat(100), flat(110), flat(110)});
    std::map<int, std::vector<Order>> at{{0, {open_order(Side::Long, 0.05)}}, {2, {close_order()}}};
    Result r = go(b, at, spread_costs({0.001, 0.002, 0.003, 0.004, 0.005}));
    if (one_trade("column", r)) {
        const Trade& t = r.trades[0];
        double size = 100.0 / 5.39;
        check_value("column entry", t.entry_price, 100.2);
        check_value("column exit", t.exit_price, 109.56);
        check_value("column size", t.size, size);
        check_value("column result", t.result, 9.36 * size);
        check_value("column spread_cost", t.spread_cost, 0.64 * size);
        check_identity("column", t);
    }

    // A size-1 constant equals a full column of that value.
    Result k = go(b, at, spread_costs({0.002}));
    Result f = go(b, at, spread_costs(std::vector<double>(5, 0.002)));
    check_true("constant equals full column", same_trades(k, f));

    // No spread: E = 100, size = 100 / 5 = 20, X = 110, result = 200, spread_cost 0.
    Result z = go(b, at, spread_costs({}));
    if (one_trade("no spread", z)) {
        const Trade& t = z.trades[0];
        check_value("no spread entry", t.entry_price, 100.0);
        check_value("no spread exit", t.exit_price, 110.0);
        check_value("no spread size", t.size, 20.0);
        check_value("no spread result", t.result, 200.0);
        check_value("no spread spread_cost", t.spread_cost, 0.0);
        check_value("no spread balance", z.ending_balance, 10200.0);
    }
    // A zero column is the same as no spread.
    check_true("zero column equals none", same_trades(z, go(b, at, spread_costs({0.0}))));
}

void test_cap() {
    // Column 0.01 capped at 0.002 is a constant 0.002: E = 100.2, X = 109.78.
    // size = 100 / 5.39; result = 9.58*size.
    Bars b = bars({flat(100), flat(100), flat(100), flat(110), flat(110)});
    std::map<int, std::vector<Order>> at{{0, {open_order(Side::Long, 0.05)}}, {2, {close_order()}}};
    Result r = go(b, at, spread_costs({0.01}, 0.002));
    if (one_trade("cap", r)) {
        const Trade& t = r.trades[0];
        double size = 100.0 / 5.39;
        check_value("cap entry", t.entry_price, 100.2);
        check_value("cap exit", t.exit_price, 109.78);
        check_value("cap result", t.result, 9.58 * size);
    }
    check_true("cap equals constant", same_trades(r, go(b, at, spread_costs({0.002}))));
    // Per bar: {0.001, 0.005, 0.001, 0.005, 0.001} capped at 0.003: entry 0.003, exit 0.003.
    Result p = go(b, at, spread_costs({0.001, 0.005, 0.001, 0.005, 0.001}, 0.003));
    check_true("cap per bar", same_trades(p, go(b, at, spread_costs({0.003}))));
    // A cap above the value changes nothing.
    check_true("cap above", same_trades(go(b, at, spread_costs({0.002}, 0.5)), go(b, at, spread_costs({0.002}))));
}

void test_equity_mark() {
    // s = 0.001, never closed. E = 100.1, size = 100 / 5.195.
    // Equity at the oracle close, no spread: step 1 (close 100): 10000 - 0.1*size;
    // step 2 (close 104): 10000 + 3.9*size.
    Bars b = bars({flat(100), flat(100), {100, 105, 100, 104}});
    Result r = go(b, {{0, {open_order(Side::Long, 0.05)}}}, spread_costs({0.001}));
    double size = 100.0 / 5.195;
    check_true("mark no trade", r.trades.empty());
    check_true("mark one open", r.open_positions.size() == 1);
    if (r.open_positions.size() == 1) {
        check_value("mark entry", r.open_positions[0].entry_price, 100.1);
    }
    check_true("mark equity length", r.equity.size() == 3);
    if (r.equity.size() == 3) {
        check_value("mark equity 0", r.equity[0], 10000.0);
        check_value("mark equity 1", r.equity[1], 10000.0 - 0.1 * size);
        check_value("mark equity 2", r.equity[2], 10000.0 + 3.9 * size);
    }
    check_value("mark balance", r.ending_balance, 10000.0);
}

void test_profit_share_after_spread() {
    // 10x, tiers {0.49: 25%, 10: 10%}. Close at open[3] = 105.
    // No spread: E = 100, size 20, collateral 200, gross 100, ROI 0.5 -> 10%: fee 10.
    // s = 0.001: E = 100.1, size = 100 / 5.195, collateral = 10.01*size,
    // X = 104.895, gross = 4.795*size, ROI = 4.795 / 10.01 = 0.479 -> 25%.
    std::vector<Tier> tiers{{0.49, 0.25}, {10.0, 0.10}};
    Bars b = bars({flat(100), flat(100), flat(100), flat(105), flat(105)});
    std::map<int, std::vector<Order>> at{{0, {open_order(Side::Long, 0.05, kNan, 10.0)}}, {2, {close_order()}}};
    Costs none = spread_costs({});
    none.profit_share = tiers;
    Result a = go(b, at, none);
    if (one_trade("share none", a)) {
        check_value("share none fee", a.trades[0].fees, 10.0);
        check_value("share none result", a.trades[0].result, 90.0);
    }
    Costs some = spread_costs({0.001});
    some.profit_share = tiers;
    Result r = go(b, at, some);
    if (one_trade("share", r)) {
        const Trade& t = r.trades[0];
        double gross = 4.795 * (100.0 / 5.195);
        check_value("share exit", t.exit_price, 104.895);
        check_value("share fee", t.fees, 0.25 * gross);
        check_value("share result", t.result, 0.75 * gross);
        check_identity("share", t);
    }
}

bool throws(const std::string& name, const Costs& c, int n = 5) {
    std::vector<std::array<double, 4>> ohlc(n, flat(100));
    Script s{.at = {{0, {Order{.instrument = name, .stop_distance = 0.05}}}}};
    Markets m = Markets::make({{name, {bars(ohlc)}}});
    try {
        backtest(s, m, MarketCosts{{name, c}}, PortfolioSettings{});
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

void test_validation() {
    check_true("unknown market, table", throws("ZZZ", Costs{}));
    check_true("column length 3 of 5", throws("X", spread_costs({0.001, 0.001, 0.001})));
    check_true("column length 2 of 5", throws("X", spread_costs({0.001, 0.001})));
    check_true("column length 6 of 5", throws("X", spread_costs(std::vector<double>(6, 0.001))));
    check_true("nan", throws("X", spread_costs({kNan})));
    check_true("nan in column", throws("X", spread_costs({0.001, kNan, 0.001, 0.001, 0.001})));
    check_true("negative", throws("X", spread_costs({-0.001})));
    check_true("inf", throws("X", spread_costs({kInf})));
    check_true("one", throws("X", spread_costs({1.0})));
    check_true("above one", throws("X", spread_costs({1.5})));
    check_true("negative cap", throws("X", spread_costs({0.001}, -0.1)));
    check_true("inf cap", throws("X", spread_costs({0.001}, kInf)));
    check_true("unknown market, table cap", throws("ZZZ", Costs{.max_spread = 0.01}));

    check_true("empty valid", !throws("X", spread_costs({})));
    check_true("zero valid", !throws("X", spread_costs({0.0})));
    check_true("full column valid", !throws("X", spread_costs(std::vector<double>(5, 0.001))));
    check_true("zero cap valid", !throws("X", spread_costs({0.001}, 0.0)));
    check_true("table valid on BTC", !throws("BTC", Costs{}));
    check_true("no_spread valid on ZZZ", !throws("ZZZ", Costs{.spread = no_spread}));

    // The pitfall: {} is nullopt (the table), no_spread is no spread.
    Costs pit{.spread = {}};
    check_true("{} is the table", !pit.spread.has_value());
    Costs none{.spread = no_spread};
    check_true("no_spread is empty", none.spread.has_value() && none.spread->empty());
    check_true("default is the table", !Costs{}.spread.has_value());
    check_true("default cap NaN", std::isnan(Costs{}.max_spread));
}

void test_table_default_fills() {
    // "BTC" with nullopt uses the BTC row: s = min(row.spread, row.max_spread).
    // E = 100*(1 + s), size = 100 / (E - 95*(1 - s)), close at 110: X = 110*(1 - s).
    auto row = veranta_spread("BTC");
    check_true("BTC row", row.has_value());
    if (!row) return;
    double s = std::isnan(row->max_spread) ? row->spread : std::min(row->spread, row->max_spread);
    Bars b = bars({flat(100), flat(100), flat(100), flat(110), flat(110)});
    Script sc{.at = {{0, {Order{.instrument = "BTC", .stop_distance = 0.05}}},
                     {2, {Order{.kind = Order::Kind::Close, .instrument = "BTC"}}}}};
    Markets m = Markets::make({{"BTC", {b}}});
    Result r = backtest(sc, m, MarketCosts{{"BTC", Costs{}}}, PortfolioSettings{});
    if (!one_trade("table", r)) return;
    const Trade& t = r.trades[0];
    double e = 100.0 * (1 + s);
    double size = 100.0 / (e - 95.0 * (1 - s));
    check_value("table entry", t.entry_price, e);
    check_value("table exit", t.exit_price, 110.0 * (1 - s));
    check_value("table size", t.size, size);
    check_value("table spread_cost", t.spread_cost, size * 100.0 * s + size * 110.0 * s);

    // The caller's cap replaces the row's: cap 0 means no spread.
    Script sz = sc;
    Result z = backtest(sz, m, MarketCosts{{"BTC", Costs{.max_spread = 0.0}}}, PortfolioSettings{});
    if (one_trade("table cap 0", z)) {
        check_value("table cap 0 entry", z.trades[0].entry_price, 100.0);
        check_value("table cap 0 exit", z.trades[0].exit_price, 110.0);
    }
}

void test_table() {
    auto a = veranta_spread("BTC");
    auto b = veranta_spread("BTC/USD");
    check_true("BTC found", a.has_value());
    check_true("BTC/USD found", b.has_value());
    if (a && b) {
        check_true("same row", a->market == b->market && a->spread == b->spread &&
                                   a->read_on == b->read_on &&
                                   (a->max_spread == b->max_spread ||
                                    (std::isnan(a->max_spread) && std::isnan(b->max_spread))));
        check_true("row name is the pair", a->market == "BTC/USD");
    }
    check_true("ETH found", veranta_spread("ETH").has_value());
    check_true("unknown nullopt", !veranta_spread("ZZZ").has_value());
    check_true("unknown pair nullopt", !veranta_spread("ZZZ/USD").has_value());
    check_true("rows exist", !veranta_spreads().empty());
    for (const SpreadRow& r : veranta_spreads()) {
        std::string n(r.market);
        check_true(n + " spread in [0, 1)", r.spread >= 0.0 && r.spread < 1.0);
        check_true(n + " max NaN or >= spread", std::isnan(r.max_spread) || r.max_spread >= r.spread);
        check_true(n + " read_on", !r.read_on.empty());
        auto found = veranta_spread(r.market);
        check_true(n + " found by name", found.has_value() && found->spread == r.spread);
    }
}

}  // namespace

int main() {
    test_open_and_close_long();
    test_open_and_close_short();
    test_stop_loses_risk();
    test_gapped_stop();
    test_take_profit();
    test_partial_take_profit();
    test_trailing_stop();
    test_end_of_data();
    test_liquidation();
    test_hard_stop();
    test_per_bar_column();
    test_cap();
    test_equity_mark();
    test_profit_share_after_spread();
    test_validation();
    test_table_default_fills();
    test_table();
    if (failures == 0) std::printf("all spread tests passed\n");
    return failures == 0 ? 0 : 1;
}
