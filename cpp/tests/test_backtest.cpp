// Tests for the backtest loop in avbt/backtest.hpp, with a test-only strategy.
// The program returns 0 when every check passes and 1 when any check fails.

#include "avbt/backtest.hpp"

#include <cmath>
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

    void prepare(const Bars& b) { bars = &b; }

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

void test_signal_fills_at_next_open() {
    // Rise at bar 1 opens at open[2] = 102; fall at bar 4 closes at open[5] = 101.
    Bars b = flat_bars({100, 101, 102, 103, 101, 101});
    UpDown s;
    Result r = backtest(s, b, PortfolioSettings{});
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
    Result r = backtest(s, b, PortfolioSettings{});
    check_value("order logged", s.log.size(), 1);
    check_value("no trade", r.trades.size(), 0);
    check_value("balance untouched", r.ending_balance, 10000);
}

void test_fee_at_open_and_close() {
    // 1% risk of 10,000 at a 50% stop from 102: 100 / 51 units.
    // Price result (101 - 102) * size; fees 0.001 * size * (102 + 101).
    Bars b = flat_bars({100, 101, 102, 103, 101, 101});
    UpDown s{.fee_rate = 0.001};
    Result r = backtest(s, b, PortfolioSettings{});
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
    Result ra = backtest(a, flat_bars(closes), PortfolioSettings{});
    Result rb = backtest(b, flat_bars(changed), PortfolioSettings{});
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
    check_value("bar size", backtest(s, b, PortfolioSettings{}).bar_size_seconds, 900);
}

}

int main() {
    test_signal_fills_at_next_open();
    test_last_bar_order_never_fills();
    test_fee_at_open_and_close();
    test_no_lookahead();
    test_bar_size_carried();
    if (failures == 0) std::printf("all backtest checks passed\n");
    return failures == 0 ? 0 : 1;
}
