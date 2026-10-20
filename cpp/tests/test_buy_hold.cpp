// Tests for buy_hold on synthetic minute bars, beside a one-bar market that
// ends later, so BTC's data ends before the run does: it opens once at the
// next open, holds, closes at the end of data, opens at stop_loss 0.8 at any
// leverage, and stays out after a stop. Exit 0 is a pass.

#include "avbt/library.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>

using namespace avbt;

int failures = 0;
void check(const char* label, bool ok) {
    if (!ok) { ++failures; std::printf("FAIL %s\n", label); }
}

// n minute bars of BTC from the closes c(i), and a one-bar market after them.
template <class F>
Markets make(int n, F c) {
    Bars b, pad;
    b.timeframe = pad.timeframe = Timeframe::Min1;
    for (int i = 0; i < n; ++i) {
        double open = i ? c(i - 1) : c(0);
        b.ts.push_back(int64_t(i) * 60);
        b.open.push_back(open);
        b.high.push_back(std::max(open, c(i)));
        b.low.push_back(std::min(open, c(i)));
        b.close.push_back(c(i));
        b.minutes_with_data.push_back(1);
    }
    pad.ts = {int64_t(n) * 60};
    pad.open = pad.high = pad.low = pad.close = {1.0};
    pad.minutes_with_data = {1};
    return Markets::make({Market{"BTC", {b}, std::nullopt}, Market{"PAD", {pad}, std::nullopt}});
}

Result go(const Markets& m, BuyHold s, PortfolioSettings p = {}) {
    return backtest(s, m, {{"BTC", Costs{.spread = no_spread}}, {"PAD", Costs{.spread = no_spread}}}, p);
}

int main() {
    const int n = 200;
    Markets up = make(n, [](int i) { return 100.0 + i; });
    const Bars& b = up.at("BTC", Timeframe::Min1);
    Result r = go(up, BuyHold{});
    check("one trade", r.trades.size() == 1);
    if (r.trades.size() == 1) {
        const Trade& t = r.trades[0];
        check("fills at the next open", t.entry_bar == 1 && t.entry_price == b.open[1]);
        check("long", t.side == Side::Long);
        check("closes at end of data", t.cause == Cause::EndOfData && t.exit_bar == n - 1);
        check("at the last close", t.exit_price == b.close[n - 1]);
    }
    check("nothing left open", r.open_positions.empty());

    // stop_loss 0.8 opens at any leverage; collateral is risk / 0.8.
    for (double lev : {1.0, 10.0, 50.0, 250.0}) {
        BuyHold s;
        s.params.leverage = lev;
        Result x = go(up, s, PortfolioSettings{.risk_per_trade = 0.08});
        bool ok = x.trades.size() == 1;
        if (ok) {
            double collateral = x.trades[0].size * x.trades[0].entry_price / lev;
            ok = std::abs(collateral - 1000.0) < 1e-3;
        }
        check("opens at stop_loss 0.8, collateral 1000", ok);
    }

    // A crash to the stop, then a rally: one stopped trade, no re-entry.
    Markets crash = make(n, [](int i) { return i < 50 ? 100.0 : i < 60 ? 10.0 : 200.0; });
    BuyHold s;
    s.params.stop_loss = 0.5;
    Result c = go(crash, s);
    check("stopped once", c.trades.size() == 1 && c.trades[0].cause == Cause::Stop);
    check("no re-entry", c.open_positions.empty());

    // A short: the mirror, closed at the end.
    BuyHold sh;
    sh.params.side = Side::Short;
    Result d = go(make(n, [](int i) { return 300.0 - i; }), sh);
    check("short", d.trades.size() == 1 && d.trades[0].side == Side::Short &&
                       d.trades[0].cause == Cause::EndOfData);
    return failures == 0 ? 0 : 1;
}
