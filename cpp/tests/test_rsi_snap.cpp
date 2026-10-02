// Tests for rsi_snap on synthetic hourly bars rising with a sharp dip every
// 40 bars: it buys the dips above the trend average, never opens on an
// undefined operand, fills at the next open, and leaves after hold bars.
// Exit 0 is a pass.

#include "avbt/library.hpp"

#include <cmath>
#include <cstdio>
#include <vector>

using namespace avbt;

int failures = 0;
void check(const char* label, bool ok) {
    if (!ok) { ++failures; std::printf("FAIL %s\n", label); }
}

int main() {
    const int n = 800;
    Bars b;
    b.timeframe = Timeframe::Hour1;
    double prev = 100;
    for (int i = 0; i < n; ++i) {
        double c = prev * (i % 2 ? 1.005 : 0.999);
        if (i % 40 == 30) c = prev * 0.97;
        b.ts.push_back(int64_t(i) * 3600);
        b.open.push_back(prev);
        b.high.push_back(std::max(prev, c) * 1.001);
        b.low.push_back(std::min(prev, c) * 0.999);
        b.close.push_back(c);
        b.minutes_with_data.push_back(60);
        prev = c;
    }
    Markets m = Markets::make({Market{"BTC", {b}, std::nullopt}});
    RsiSnap s;
    s.params = {.instrument = "BTC", .timeframe = Timeframe::Hour1, .rsi_period = 2, .trend_period = 50, .exit_period = 1000,
                .tp_atrs = 50.0};  // exits only by time
    std::vector<int> open_steps;
    bool bad = false, wrong_side = false;
    Result r = backtest(s, m, {{"BTC", Costs{}}}, PortfolioSettings{},
                        [&](int t, const std::vector<Position>&, const std::vector<Order>& orders) {
        for (const Order& o : orders) {
            if (o.kind != Order::Kind::Open) continue;
            open_steps.push_back(t);
            if (!defined(s.rsi[t]) || !defined(s.trend[t]) || !defined(s.atr[t])) bad = true;
            if (o.side != Side::Long || b.close[t] <= s.trend[t] || s.rsi[t] >= s.params.low) wrong_side = true;
        }
    });
    check("it trades", r.trades.size() >= 4);
    check("no open on an undefined operand", !bad);
    check("buys a low RSI above the trend average", !wrong_side);
    bool next_open = !r.trades.empty(), held = true;
    for (const Trade& x : r.trades) {
        int e = x.entry_bar;
        bool asked = false;
        for (int t : open_steps) asked = asked || t + 1 == e;
        next_open = next_open && asked && std::abs(x.entry_price - b.open[e]) < 1e-9;
        if (x.cause == Cause::Order) held = held && x.exit_bar - x.entry_bar == s.params.hold;
    }
    check("fills at the next open", next_open);
    check("time exit after hold bars", held);
    return failures == 0 ? 0 : 1;
}
