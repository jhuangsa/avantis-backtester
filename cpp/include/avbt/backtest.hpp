#pragma once
#include <algorithm>
#include <cmath>
#include <concepts>
#include <map>
#include <string>
#include <vector>

#include "avbt/indicators.hpp"
#include "avbt/markets.hpp"
#include "avbt/portfolio.hpp"

namespace avbt {

// False for NaN, the value an indicator holds before it has enough bars.
inline bool defined(double x) { return !std::isnan(x); }

// What a strategy returns at the close of bar t. It fills at the open of
// bar t+1. Distances are fractions of that open, because the strategy does
// not know it yet. See ADR 0009.
struct Order {
    enum class Kind { Open, Close };
    Kind kind = Kind::Open;
    std::string instrument;
    Side side = Side::Long;
    // 0.05 puts the stop 5% from the fill, on the losing side.
    double stop_distance = 0.0;
    // NaN for no take profit.
    double take_profit_distance = std::nan("");
    double leverage = 1.0;
    // Fraction of notional charged at every fill.
    double fee_rate = 0.0;
};

// One closed trade, with the clock steps of its fills. A step indexes
// Result::clock, not one market's bars; on one market they are the same.
struct Trade {
    std::string instrument;
    int entry_bar = 0;
    int exit_bar = 0;
    Side side = Side::Long;
    double entry_price = 0.0;
    double exit_price = 0.0;
    // Account money, after both fees.
    double result = 0.0;
    Cause cause = Cause::Order;
};

struct Result {
    std::vector<Trade> trades;
    // Equity at the close of every clock step. Open positions are marked at
    // the close: a mark, not a fill, so no fee.
    std::vector<double> equity;
    // Realized money at the end; positions still open are not in it.
    double ending_balance = 0.0;
    // The base timeframe the run stepped on.
    Timeframe timeframe = Timeframe::Min1;
    // The UTC second at which each step opens; Markets::clock().
    std::vector<int64_t> clock;
};

// What every strategy has: prepare computes its lines once from any of the
// markets; decide is called at the close of every clock step with `now`, the
// UTC second of that close, and returns orders. A strategy reads its bars
// through last_closed(bars, now), which never returns a bar still open. No
// base class, no virtual call.
template <class S>
concept Strategy = requires(S s, const Markets& markets, int64_t now, const Report& report,
                            const std::vector<Position>& positions) {
    s.prepare(markets);
    { s.decide(now, report, positions) } -> std::same_as<std::vector<Order>>;
};

// Runs one strategy on several markets on one clock (Markets::clock). On
// each step t:
// 1. Fill the orders from step t-1, closes before opens so freed collateral
//    can pay for a new trade, each at the open of its own market's base bar.
//    An order for a market with no bar at step t (its data has not started,
//    or has ended) is dropped.
// 2. Check every position's levels on its own market's base bar.
// 3. Close the position of a market whose data ends at this step, at its
//    last close, unless this is the run's last step.
// 4. Record equity, then ask the strategy.
// Orders returned on the last step never fill. The caller chooses the bars;
// nothing is trimmed. ADR 0010, 0011.
template <Strategy S>
Result backtest(S& strategy, const Markets& markets, PortfolioSettings settings) {
    Portfolio portfolio(settings);
    Result result{.timeframe = markets.timeframe(), .clock = markets.clock()};
    // The portfolio does not know clock steps, so the loop keeps them.
    std::map<std::string, int> entry_bar;
    std::vector<Order> waiting;

    auto record = [&](const Closed& c, int t) {
        result.trades.push_back(Trade{c.instrument, entry_bar[c.instrument], t, c.side,
                                      c.entry_price, c.exit_price, c.result, c.cause});
        entry_bar.erase(c.instrument);
    };

    strategy.prepare(markets);
    const std::vector<int64_t>& clock = markets.clock();
    int n = static_cast<int>(clock.size());
    for (int t = 0; t < n; ++t) {
        std::stable_partition(waiting.begin(), waiting.end(),
                              [](const Order& o) { return o.kind == Order::Kind::Close; });
        for (const Order& o : waiting) {
            int i = markets.bar_at(o.instrument, t);
            if (i < 0) continue;
            double open = markets.base(o.instrument).open[i];
            if (o.kind == Order::Kind::Close) {
                if (auto c = portfolio.close(o.instrument, open)) record(*c, t);
                continue;
            }
            double sign = o.side == Side::Long ? 1.0 : -1.0;
            double stop = open * (1.0 - sign * o.stop_distance);
            double take = open * (1.0 + sign * o.take_profit_distance);
            // A refused open (no free cash, halted, a position already) records nothing.
            if (portfolio.open(o.instrument, o.side, open, stop, o.leverage, take, o.fee_rate)) {
                entry_bar[o.instrument] = t;
            }
        }
        waiting.clear();

        std::vector<Quote> quotes;
        for (const Position& p : portfolio.positions()) {
            int i = markets.bar_at(p.instrument, t);
            if (i < 0) continue;
            const Bars& b = markets.base(p.instrument);
            quotes.push_back(Quote{p.instrument, b.open[i], b.high[i], b.low[i], b.close[i]});
        }
        for (const Closed& c : portfolio.check(quotes)) record(c, t);

        if (t + 1 < n) {
            for (const Market& m : markets.all()) {
                const Bars& b = m.timeframes.front();
                int i = markets.bar_at(m.instrument, t);
                bool ends = i == static_cast<int>(b.ts.size()) - 1;
                if (!ends) continue;
                if (auto c = portfolio.close(m.instrument, b.close[i], 1.0, Cause::EndOfData)) {
                    record(*c, t);
                }
            }
        }

        result.equity.push_back(portfolio.report().equity);
        int64_t now = clock[t] + seconds(markets.timeframe());
        waiting = strategy.decide(now, portfolio.report(), portfolio.positions());
    }
    result.ending_balance = portfolio.report().balance;
    return result;
}

}
