// Tests for the backtest loop in avbt/backtest.hpp, with a test-only strategy.
// The program returns 0 when every check passes and 1 when any check fails.

#include "avbt/backtest.hpp"

#include <cmath>
#include <map>
#include <stdexcept>
#include <cstdio>
#include <string>
#include <utility>
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

using namespace avbt;

// Long when the close rises, close the trade when it falls. Logs every
// order it returns as (bar, kind) for the lookahead test.
struct UpDown {
    double fee_rate = 0.0;
    const Bars* bars = nullptr;
    std::vector<std::pair<int, Order::Kind>> log;

    void prepare(const Markets& m) { bars = &m.at("X"); }

    std::vector<Order> decide(int t, const Report&, const std::vector<Position>& positions) {
        if (t < 1) return {};
        double now = bars->close[t], before = bars->close[t - 1];
        Order o{.instrument = "X", .side = Side::Long, .stop_distance = 0.5,
                .fee_rate = fee_rate};
        if (positions.empty() && now > before) {
            o.kind = Order::Kind::Open;
        } else if (!positions.empty() && now < before) {
            o.kind = Order::Kind::Close;
        } else {
            return {};
        }
        log.emplace_back(t, o.kind);
        return {o};
    }
};
static_assert(Strategy<UpDown>);

// Flat bars at each close: open = high = low = close.
Bars flat_bars(const std::vector<double>& closes) {
    Bars b;
    b.bar_size_seconds = 3600;
    for (std::size_t i = 0; i < closes.size(); ++i) {
        b.ts.push_back(static_cast<int64_t>(i) * 3600);
        b.open.push_back(closes[i]);
        b.high.push_back(closes[i]);
        b.low.push_back(closes[i]);
        b.close.push_back(closes[i]);
        b.minutes_with_data.push_back(60);
    }
    return b;
}

// One market named X.
Markets one(const Bars& b) { return Markets::make({{"X", b}}); }

void test_signal_fills_at_next_open() {
    // Rise at bar 1 opens at open[2] = 102; fall at bar 4 closes at open[5] = 101.
    Bars b = flat_bars({100, 101, 102, 103, 101, 101});
    UpDown s;
    Result r = backtest(s, one(b), PortfolioSettings{});
    check_value("one trade", r.trades.size(), 1);
    check_value("entry bar", r.trades.at(0).entry_bar, 2);
    check_value("entry price", r.trades.at(0).entry_price, 102);
    check_value("exit bar", r.trades.at(0).exit_bar, 5);
    check_value("exit price", r.trades.at(0).exit_price, 101);
    check_true("cause is order", r.trades.at(0).cause == Cause::Order);
    check_value("equity per bar", r.equity.size(), b.close.size());
}

void test_last_bar_order_never_fills() {
    // The only rise is on the last bar.
    Bars b = flat_bars({100, 100, 100, 101});
    UpDown s;
    Result r = backtest(s, one(b), PortfolioSettings{});
    check_value("order logged", s.log.size(), 1);
    check_value("no trade", r.trades.size(), 0);
    check_value("balance untouched", r.ending_balance, 10000);
}

void test_fee_at_open_and_close() {
    // 1% risk of 10,000 at a 50% stop from 102: 100 / 51 units.
    // Price result (101 - 102) * size; fees 0.001 * size * (102 + 101).
    Bars b = flat_bars({100, 101, 102, 103, 101, 101});
    UpDown s{.fee_rate = 0.001};
    Result r = backtest(s, one(b), PortfolioSettings{});
    double size = 100.0 / 51.0;
    double expected = -1.0 * size - 0.001 * size * 203.0;
    check_value("result after fees", r.trades.at(0).result, expected);
    check_value("balance after fees", r.ending_balance, 10000 + expected);
}

void test_no_lookahead() {
    // Change every bar after t = 5; orders and trades up to t must not move.
    std::vector<double> closes = {100, 101, 102, 103, 101, 101, 104, 99, 105};
    std::vector<double> changed = closes;
    for (std::size_t i = 6; i < changed.size(); ++i) changed[i] = 50 + 7 * i;
    UpDown a, b;
    Result ra = backtest(a, one(flat_bars(closes)), PortfolioSettings{});
    Result rb = backtest(b, one(flat_bars(changed)), PortfolioSettings{});
    auto upto = [](const std::vector<std::pair<int, Order::Kind>>& log) {
        std::vector<std::pair<int, Order::Kind>> out;
        for (auto e : log) if (e.first <= 5) out.push_back(e);
        return out;
    };
    check_true("orders up to t match", upto(a.log) == upto(b.log));
    int matched = 0;
    for (const Trade& ta : ra.trades) {
        if (ta.exit_bar > 5) continue;
        for (const Trade& tb : rb.trades) {
            if (tb.entry_bar == ta.entry_bar && tb.exit_bar == ta.exit_bar &&
                tb.exit_price == ta.exit_price) ++matched;
        }
    }
    check_value("trades up to t match", matched, 1);
    for (int t = 0; t <= 5; ++t) check_value("equity up to t", rb.equity[t], ra.equity[t]);
}

void test_bar_size_carried() {
    Bars b = flat_bars({100, 101});
    b.bar_size_seconds = 900;
    UpDown s;
    check_value("bar size", backtest(s, one(b), PortfolioSettings{}).bar_size_seconds, 900);
}


void test_markets_refuse_misaligned() {
    Bars a = flat_bars({100, 101, 102});
    auto refused = [](std::vector<Market> m) {
        try { Markets::make(std::move(m)); } catch (const std::invalid_argument&) { return true; }
        return false;
    };
    check_true("aligned markets pass", !refused({{"X", a}, {"Y", a}}));
    check_true("empty refused", refused({}));
    check_true("same name refused", refused({{"X", a}, {"X", a}}));
    Bars size = a;
    size.bar_size_seconds = 900;
    check_true("bar size refused", refused({{"X", a}, {"Y", size}}));
    check_true("length refused", refused({{"X", a}, {"Y", flat_bars({100, 101})}}));
    Bars shifted = a;
    shifted.ts[2] += 60;
    check_true("timestamp refused", refused({{"X", a}, {"Y", shifted}}));
    Bars ragged = a;
    ragged.low.pop_back();
    check_true("ragged columns refused", refused({{"X", ragged}}));
}

// Returns the orders listed for each bar, and nothing on other bars.
struct Script {
    std::map<int, std::vector<Order>> at;
    void prepare(const Markets&) {}
    std::vector<Order> decide(int t, const Report&, const std::vector<Position>&) {
        auto it = at.find(t);
        return it == at.end() ? std::vector<Order>{} : it->second;
    }
};
static_assert(Strategy<Script>);

Order open_long(const std::string& instrument, double stop) {
    return Order{.instrument = instrument, .side = Side::Long, .stop_distance = stop};
}

Order close_order(const std::string& instrument) {
    return Order{.kind = Order::Kind::Close, .instrument = instrument};
}

void test_each_market_uses_its_own_bars() {
    // Y fills at its own open 200, stop 180; Y's bar 2 low 170 fills the
    // stop. X, flat at 100, stays open, so it records no trade.
    Bars x = flat_bars({100, 100, 100, 100});
    Bars y = flat_bars({200, 200, 200, 200});
    y.low[2] = 170;
    Script s{.at = {{0, {open_long("X", 0.1), open_long("Y", 0.1)}}}};
    Result r = backtest(s, Markets::make({{"X", x}, {"Y", y}}), PortfolioSettings{});
    check_value("one trade", r.trades.size(), 1);
    check_true("trade is Y", r.trades.at(0).instrument == "Y");
    check_value("Y entry price", r.trades.at(0).entry_price, 200);
    check_value("Y exit bar", r.trades.at(0).exit_bar, 2);
    check_value("Y exit price", r.trades.at(0).exit_price, 180);
    check_true("Y cause is stop", r.trades.at(0).cause == Cause::Stop);
}

void test_closes_fill_before_opens() {
    // 1.5% risk at a 2% stop is 7,500 of notional, so two positions do not
    // fit in 10,000. Bar 2 lists the Y open before the X close; the X close
    // fills first and frees the cash, so Y opens on bar 3.
    Bars flat = flat_bars({100, 100, 100, 100, 100, 100});
    Script s{.at = {{0, {open_long("X", 0.02)}},
                    {2, {open_long("Y", 0.02), close_order("X")}},
                    {4, {close_order("Y")}}}};
    PortfolioSettings settings{.risk_per_trade = 0.015};
    Result r = backtest(s, Markets::make({{"X", flat}, {"Y", flat}}), settings);
    check_value("two trades", r.trades.size(), 2);
    if (r.trades.size() < 2) return;
    check_true("X first", r.trades[0].instrument == "X");
    check_value("X exit bar", r.trades[0].exit_bar, 3);
    check_true("Y second", r.trades[1].instrument == "Y");
    check_value("Y entry bar", r.trades[1].entry_bar, 3);
    check_value("Y exit bar", r.trades[1].exit_bar, 5);
}

// Trades X on Y's closes: long X when Y rises, out when Y falls.
struct FollowY {
    const Bars* y = nullptr;
    void prepare(const Markets& m) { y = &m.at("Y"); }
    std::vector<Order> decide(int t, const Report&, const std::vector<Position>& positions) {
        if (t < 1) return {};
        double now = y->close[t], before = y->close[t - 1];
        if (positions.empty() && now > before) return {open_long("X", 0.5)};
        if (!positions.empty() && now < before) return {close_order("X")};
        return {};
    }
};
static_assert(Strategy<FollowY>);

void test_no_lookahead_two_markets() {
    // Change every bar after t = 5 in both markets; trades and equity up to
    // t must not move.
    std::vector<double> xs = {100, 101, 102, 103, 104, 105, 106, 107, 108};
    std::vector<double> ys = {50, 51, 52, 51, 52, 50, 53, 49, 55};
    std::vector<double> xs2 = xs, ys2 = ys;
    for (std::size_t i = 6; i < xs.size(); ++i) {
        xs2[i] = 80 + 3 * i;
        ys2[i] = 90 - 2 * i;
    }
    FollowY a, b;
    Result ra = backtest(a, Markets::make({{"X", flat_bars(xs)}, {"Y", flat_bars(ys)}}),
                         PortfolioSettings{});
    Result rb = backtest(b, Markets::make({{"X", flat_bars(xs2)}, {"Y", flat_bars(ys2)}}),
                         PortfolioSettings{});
    auto upto = [](const Result& r) {
        std::vector<std::pair<int, int>> out;
        for (const Trade& t : r.trades) if (t.exit_bar <= 5) out.emplace_back(t.entry_bar, t.exit_bar);
        return out;
    };
    check_true("some trade up to t", !upto(ra).empty());
    check_true("trades up to t match", upto(ra) == upto(rb));
    for (int t = 0; t <= 5; ++t) check_value("equity up to t", rb.equity[t], ra.equity[t]);
}

}

int main() {
    test_signal_fills_at_next_open();
    test_last_bar_order_never_fills();
    test_fee_at_open_and_close();
    test_no_lookahead();
    test_bar_size_carried();
    test_markets_refuse_misaligned();
    test_each_market_uses_its_own_bars();
    test_closes_fill_before_opens();
    test_no_lookahead_two_markets();
    if (failures == 0) std::printf("all backtest checks passed\n");
    return failures == 0 ? 0 : 1;
}
