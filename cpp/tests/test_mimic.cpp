// Tests for mimic on two synthetic hourly markets that rise slowly with a
// sharp spike every 40 bars: with the move filter set to fade, it only
// shorts after spikes, never opens on an undefined operand, fills at the
// next open, leaves after hold bars, and runs through the table by name.
// Exit 0 is a pass.

#include "avbt/run.hpp"

#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <vector>

using namespace avbt;

int failures = 0;
void check(const char* label, bool ok) {
    if (!ok) { ++failures; std::printf("FAIL %s\n", label); }
}

Bars spiky(int n, int offset) {
    Bars b;
    b.timeframe = Timeframe::Hour1;
    double prev = 100;
    for (int i = 0; i < n; ++i) {
        double c = prev * (i % 2 ? 1.004 : 0.997);
        if ((i + offset) % 40 == 30) c = prev * 1.06;
        b.ts.push_back(int64_t(i) * 3600);
        b.open.push_back(prev);
        b.high.push_back(std::max(prev, c) * 1.001);
        b.low.push_back(std::min(prev, c) * 0.999);
        b.close.push_back(c);
        b.minutes_with_data.push_back(60);
        prev = c;
    }
    return b;
}

int main() {
    const int n = 800;
    Bars a = spiky(n, 0), z = spiky(n, 13);
    Markets m = Markets::make({Market{"AAA", {a}, std::nullopt}, Market{"ZZZ", {z}, std::nullopt}});
    MarketCosts costs{{"AAA", Costs{}}, {"ZZZ", Costs{}}};
    Mimic s;
    s.params.move_on = true;
    s.params.move_lag = 1, s.params.move_size = 0.05, s.params.move_dir = -1;
    s.params.hold = 6, s.params.tp_atrs = 50.0, s.params.stop_atrs = 5.0;
    std::vector<std::pair<int, std::string>> opens;
    bool bad = false, wrong = false;
    Result r = backtest(s, m, costs, PortfolioSettings{},
                        [&](int t, const std::vector<Position>&, const std::vector<Order>& orders) {
        for (const Order& o : orders) {
            if (o.kind != Order::Kind::Open) continue;
            opens.push_back({t, o.instrument});
            const Mimic::Lines& l = s.lines[o.instrument == "AAA" ? 0 : 1];
            if (!defined(l.move[t]) || !defined(l.atr[t])) bad = true;
            if (o.side != Side::Short || l.move[t] < 0.05) wrong = true;
        }
    });
    check("it trades both markets", r.trades.size() >= 20);
    check("no open on an undefined operand", !bad);
    check("only fades spikes", !wrong);
    bool next_open = !r.trades.empty(), held = true;
    for (const Trade& x : r.trades) {
        const Bars& b = x.instrument == "AAA" ? a : z;
        bool asked = false;
        for (auto& [t, inst] : opens) asked = asked || (t + 1 == x.entry_bar && inst == x.instrument);
        next_open = next_open && asked && std::abs(x.entry_price - b.open[x.entry_bar]) < 1e-9;
        if (x.cause == Cause::Order) held = held && x.exit_bar - x.entry_bar == s.params.hold;
    }
    check("fills at the next open", next_open);
    check("time exit after hold bars", held);

    Params p{{"move_on", true}, {"move_lag", 1}, {"move_size", 0.05}, {"move_dir", -1},
             {"hold", 6}, {"tp_atrs", 50.0}, {"stop_atrs", 5.0}};
    Result named = run("mimic", p, m, costs, PortfolioSettings{});
    check("run by name gives the same trades", named.trades.size() == r.trades.size() &&
                                                   named.ending_balance == r.ending_balance);
    // All filters off: it opens on every closed bar where it is flat.
    Result all = run("mimic", {{"side", -1}, {"hold", 1}}, m, costs, PortfolioSettings{});
    check("no filters trades often", all.trades.size() > r.trades.size());

    // Hours wrap past midnight: 21 to 3 opens only on bars at 21..2 UTC.
    Mimic w;
    w.params.side = -1, w.params.hold = 1, w.params.hours_on = true;
    w.params.hour_from = 21, w.params.hour_to = 3;
    bool in_window = true;
    int wrapped = 0;
    backtest(w, m, costs, PortfolioSettings{},
             [&](int t, const std::vector<Position>&, const std::vector<Order>& orders) {
        for (const Order& o : orders) {
            if (o.kind != Order::Kind::Open) continue;
            int h = t % 24;
            in_window = in_window && (h >= 21 || h < 3);
            wrapped += h < 3;
        }
    });
    check("wrapped hours open only inside the window", in_window);
    check("wrapped hours open after midnight", wrapped > 0);
    Params off{{"side", -1}, {"hold", 1}};
    Params same{{"side", -1}, {"hold", 1}, {"hours_on", true}, {"hour_from", 5}, {"hour_to", 5}};
    Params full{{"side", -1}, {"hold", 1}, {"hours_on", true}, {"hour_from", 0}, {"hour_to", 24}};
    check("from == to is the whole day", run("mimic", same, m, costs, PortfolioSettings{}).trades.size() ==
                                              run("mimic", off, m, costs, PortfolioSettings{}).trades.size());
    check("0 to 24 is the whole day", run("mimic", full, m, costs, PortfolioSettings{}).trades.size() ==
                                           run("mimic", off, m, costs, PortfolioSettings{}).trades.size());

    auto rejected = [&](const char* name) {
        try { run("mimic", {{name, 0.0}}, m, costs, PortfolioSettings{}); } catch (const std::invalid_argument&) { return true; }
        return false;
    };
    check("stop_atrs 0 is rejected", rejected("stop_atrs"));
    check("tp_atrs 0 is rejected", rejected("tp_atrs"));
    return failures == 0 ? 0 : 1;
}
