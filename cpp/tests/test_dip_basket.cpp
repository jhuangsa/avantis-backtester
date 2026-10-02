// Tests for dip_basket on three synthetic rising markets (hourly bars plus
// 4-hour bars built from them), each with sharp dips, some shared: it buys
// dips on several markets, never opens on an undefined operand, keeps to
// max_open, fills at the next open, and leaves after hold bars. Exit 0 is
// a pass.

#include "avbt/library.hpp"

#include <cmath>
#include <cstdio>
#include <set>
#include <vector>

using namespace avbt;

int failures = 0;
void check(const char* label, bool ok) {
    if (!ok) { ++failures; std::printf("FAIL %s\n", label); }
}

const int n = 800;

Market make(const std::string& name, int offset) {
    Bars b, h4;
    b.timeframe = Timeframe::Hour1;
    h4.timeframe = Timeframe::Hour4;
    double prev = 100;
    for (int i = 0; i < n; ++i) {
        double c = prev * (i % 2 ? 1.005 : 0.999);
        if (i % 40 == offset || i % 120 == 70) c = prev * 0.95;
        b.ts.push_back(int64_t(i) * 3600);
        b.open.push_back(prev);
        b.high.push_back(std::max(prev, c) * 1.001);
        b.low.push_back(std::min(prev, c) * 0.999);
        b.close.push_back(c);
        b.minutes_with_data.push_back(60);
        prev = c;
    }
    for (int i = 0; i + 4 <= n; i += 4) {
        h4.ts.push_back(b.ts[i]);
        h4.open.push_back(b.open[i]);
        h4.high.push_back(*std::max_element(b.high.begin() + i, b.high.begin() + i + 4));
        h4.low.push_back(*std::min_element(b.low.begin() + i, b.low.begin() + i + 4));
        h4.close.push_back(b.close[i + 3]);
        h4.minutes_with_data.push_back(240);
    }
    return Market{name, {b, h4}, std::nullopt};
}

int main() {
    std::vector<Market> ms = {make("A", 10), make("B", 20), make("C", 30)};
    Markets m = Markets::make(ms);
    DipBasket s;
    s.params.max_open = 2;
    s.params.tp_atrs = 50.0, s.params.revert = false;  // so time exits are seen
    std::vector<std::pair<int, std::string>> opens;
    bool bad = false, too_many = false;
    Result r = backtest(s, m, {{"A", Costs{}}, {"B", Costs{}}, {"C", Costs{}}}, PortfolioSettings{},
                        [&](int t, const std::vector<Position>& pos, const std::vector<Order>& orders) {
        int count = 0;
        for (const Order& o : orders) {
            if (o.kind != Order::Kind::Open) { --count; continue; }
            ++count;
            opens.push_back({t, o.instrument});
            for (const DipBasket::Lines& l : s.lines)
                if (l.instrument == o.instrument && (!defined(l.ret[t]) || !defined(l.atr[t]))) bad = true;
            if (o.side != Side::Long) bad = true;
        }
        if (int(pos.size()) + count > s.params.max_open) too_many = true;
    });
    std::set<std::string> names;
    for (const Trade& x : r.trades) names.insert(x.instrument);
    check("it trades", r.trades.size() >= 6);
    check("trades several markets", names.size() >= 2);
    check("no open on an undefined operand", !bad);
    check("keeps to max_open", !too_many);
    bool next_open = !r.trades.empty(), held = true;
    for (const Trade& x : r.trades) {
        int e = x.entry_bar;
        bool asked = false;
        for (auto& [t, ins] : opens) asked = asked || (t + 1 == e && ins == x.instrument);
        const Market& mk = ms[x.instrument[0] - 'A'];
        next_open = next_open && asked && std::abs(x.entry_price - mk.timeframes[0].open[e]) < 1e-9;
        if (x.cause == Cause::Order) held = held && x.exit_bar - x.entry_bar == s.params.hold;
    }
    check("fills at the next open", next_open);
    check("time exit after hold bars", held);
    return failures == 0 ? 0 : 1;
}
