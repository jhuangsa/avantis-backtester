// Tests for range_seller on synthetic hourly bars that swing in a range:
// it trades both sides, only on a ranging label, never on an undefined
// operand, fills at the next open, and sells the top and buys the bottom.
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
    const int n = 600;
    Bars b;
    b.timeframe = Timeframe::Hour1;
    double prev = 100;
    for (int i = 0; i < n; ++i) {
        double c = 100 + 5 * std::sin(i / 6.0);
        b.ts.push_back(int64_t(i) * 3600);
        b.open.push_back(prev);
        b.high.push_back(std::max(prev, c) + 0.1);
        b.low.push_back(std::min(prev, c) - 0.1);
        b.close.push_back(c);
        b.minutes_with_data.push_back(60);
        prev = c;
    }
    // Consolidation in the first half, TrendingUp in the second.
    States st;
    for (int i = 0; i < n * 60; ++i) {
        st.market.push_back(i < n * 30 ? MarketState::Consolidation : MarketState::TrendingUp);
        st.trend.push_back(TrendState::NonTrending);
        st.volatility.push_back(VolatilityState::Normal);
    }
    Markets m = Markets::make({Market{"ETH", {b}, st}});
    RangeSeller s;
    std::vector<int> open_steps;
    bool bad = false, wrong_side = false;
    Result r = backtest(s, m, {{"ETH", Costs{}}}, PortfolioSettings{},
                        [&](int t, const std::vector<Position>&, const std::vector<Order>& orders) {
        for (const Order& o : orders) {
            if (o.kind != Order::Kind::Open) continue;
            open_steps.push_back(t);
            if (!defined(s.high[t]) || !defined(s.low[t]) || !defined(s.atr[t])) bad = true;
            double mid = (s.high[t] + s.low[t]) / 2;
            if ((o.side == Side::Short) != (b.close[t] > mid)) wrong_side = true;
        }
    });
    check("it trades", r.trades.size() >= 4);
    check("no open on an undefined operand", !bad);
    check("sells the top, buys the bottom", !wrong_side);
    bool ranging_only = true, longs = false, shorts = false;
    for (int t : open_steps) ranging_only = ranging_only && t < n / 2;
    for (const Trade& x : r.trades) (x.side == Side::Long ? longs : shorts) = true;
    check("no open off a ranging label", ranging_only);
    check("both sides", longs && shorts);
    bool next_open = !r.trades.empty();
    for (const Trade& x : r.trades) {
        int e = x.entry_bar;
        bool asked = false;
        for (int t : open_steps) asked = asked || t + 1 == e;
        next_open = next_open && asked && std::abs(x.entry_price - b.open[e]) < 1e-9;
    }
    check("fills at the next open", next_open);
    return failures == 0 ? 0 : 1;
}
