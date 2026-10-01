#pragma once
#include <algorithm>
#include <cmath>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include "avbt/backtest.hpp"
#include "avbt/indicators.hpp"
#include "avbt/markets.hpp"
#include "avbt/states.hpp"

namespace avbt {

// The five ideas in examples/veranta_rules.py. Each reads one timeframe of
// its instrument, params.timeframe, hourly by default. An entry is known at
// the close of bar t on that timeframe and fills at the next base open.
// Stops and take profits are levels the portfolio fills; strategies only
// return entries, time exits, and rule exits. Defaults are the figures in
// veranta_rules.py.

// Shared decide. t is the strategy's last closed bar, and it acts once per
// bar: on the clock steps between two hourly closes, t does not change and
// it returns nothing. While a position is open, return a close order for a
// time exit or a rule exit; otherwise open when the entry holds. A time exit
// of N closes at the open after bar signal + N, the bar of the fill being
// bar 1. signal_bar is read only while a position is open and is overwritten
// by every new entry, so a trade closed by a level leaves no stale memory.
template <class S>
std::vector<Order> decide_entry_and_exits(S& s, int64_t now, const std::vector<Position>& positions) {
    int t = last_closed(*s.bars, now);
    if (t < 0 || t == s.seen_bar) return {};
    s.seen_bar = t;
    const auto& p = s.params;
    if (!positions.empty()) {
        bool timed = p.time_exit > 0 && t - s.signal_bar >= p.time_exit;
        bool ruled = false;
        if constexpr (requires { s.rule_exit(t); }) ruled = s.rule_exit(t);
        if (timed || ruled) return {Order{.kind = Order::Kind::Close, .instrument = p.instrument}};
        return {};
    }
    if (!s.entry(t)) return {};
    s.signal_bar = t;
    return {Order{.kind = Order::Kind::Open, .instrument = p.instrument, .side = p.side,
                  .stop_distance = p.stop, .take_profit_distance = p.take_profit,
                  .leverage = p.leverage, .fee_rate = p.fee_rate}};
}

// 1. Short ZORA at a 16:00 UTC bar after a 72-bar rise of at least 10%.
struct LateDayShort {
    struct Params {
        std::string instrument = "ZORA";
        Side side = Side::Short;
        int hour = 16, lag = 72;
        double rise = 0.10, take_profit = 0.02, stop = 0.05;
        int time_exit = 3;
        double leverage = 1.0, fee_rate = 0.0001;
        Timeframe timeframe = Timeframe::Hour1;
    } params;
    std::vector<double> hour, rise;
    const Bars* bars = nullptr;
    // The last bar decide acted on, and the bar of the last entry.
    int seen_bar = -1, signal_bar = -1;

    void prepare(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        const Bars& b = *bars;
        hour = hour_of_day(b);
        rise = pct_change(b.close, params.lag);
    }
    bool entry(int t) const { return hour[t] == params.hour && rise[t] >= params.rise; }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        return decide_entry_and_exits(*this, now, positions);
    }
};

// 2. Short ZORA after a 24-bar rise of at least 10% with the close above its 21-bar average.
struct RallyShort {
    struct Params {
        std::string instrument = "ZORA";
        Side side = Side::Short;
        int lag = 24, window = 21;
        double rise = 0.10, take_profit = 0.02, stop = 0.05;
        int time_exit = 4;
        double leverage = 1.0, fee_rate = 0.0001;
        Timeframe timeframe = Timeframe::Hour1;
    } params;
    std::vector<double> close, rise, average;
    const Bars* bars = nullptr;
    // The last bar decide acted on, and the bar of the last entry.
    int seen_bar = -1, signal_bar = -1;

    void prepare(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        const Bars& b = *bars;
        close = b.close;
        rise = pct_change(b.close, params.lag);
        average = sma(b.close, params.window);
    }
    bool entry(int t) const { return rise[t] >= params.rise && close[t] > average[t]; }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        return decide_entry_and_exits(*this, now, positions);
    }
};

// 3. Short AVNT after a 24-bar rise of at least 9%, held up to 120 bars.
struct CampaignShort {
    struct Params {
        std::string instrument = "AVNT";
        Side side = Side::Short;
        int lag = 24;
        double rise = 0.09, take_profit = 0.20, stop = 0.30;
        int time_exit = 120;
        double leverage = 1.0, fee_rate = 0.0001;
        Timeframe timeframe = Timeframe::Hour1;
    } params;
    std::vector<double> rise;
    const Bars* bars = nullptr;
    // The last bar decide acted on, and the bar of the last entry.
    int seen_bar = -1, signal_bar = -1;

    void prepare(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        const Bars& b = *bars;
        rise = pct_change(b.close, params.lag);
    }
    bool entry(int t) const { return rise[t] >= params.rise; }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        return decide_entry_and_exits(*this, now, positions);
    }
};

// 4. Short DYM in a bar whose high is more than 5% over the previous close.
struct SpikeShort {
    struct Params {
        std::string instrument = "DYM";
        Side side = Side::Short;
        double spike = 0.05, take_profit = 0.06, stop = 0.08;
        int time_exit = 16;
        double leverage = 1.0, fee_rate = 0.0001;
        Timeframe timeframe = Timeframe::Hour1;
    } params;
    std::vector<double> spike;
    const Bars* bars = nullptr;
    // The last bar decide acted on, and the bar of the last entry.
    int seen_bar = -1, signal_bar = -1;

    void prepare(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        const Bars& b = *bars;
        spike = bar_change(b, Field::High, Field::Close, 1);
    }
    bool entry(int t) const { return spike[t] > params.spike; }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        return decide_entry_and_exits(*this, now, positions);
    }
};

// 5. Long gold above its 55-bar average after a positive 72-bar drift; out
// when the close falls below the average. No take profit, no time exit.
struct GoldTrendLong {
    struct Params {
        std::string instrument = "GOLD";
        Side side = Side::Long;
        int window = 55, lag = 72;
        double take_profit = std::nan(""), stop = 0.015;
        int time_exit = 0;
        double leverage = 1.0, fee_rate = 0.0001;
        Timeframe timeframe = Timeframe::Hour1;
    } params;
    std::vector<double> close, average, drift;
    const Bars* bars = nullptr;
    // The last bar decide acted on, and the bar of the last entry.
    int seen_bar = -1, signal_bar = -1;

    void prepare(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        const Bars& b = *bars;
        close = b.close;
        average = sma(b.close, params.window);
        drift = pct_change(b.close, params.lag);
    }
    bool entry(int t) const { return close[t] > average[t] && drift[t] > 0.0; }
    bool rule_exit(int t) const { return close[t] < average[t]; }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        return decide_entry_and_exits(*this, now, positions);
    }
};

// Two strategies run as one, in one account. The strategy whose open
// order starts a position owns it: only the owner sees that position and
// decides its exits. While one owns an instrument, the other's opens on it
// are dropped, so two strategies on one instrument take turns. Their orders
// go out together, A's first, so A wins when both open on the same bar.
template <Strategy A, Strategy B>
struct Combined {
    A a;
    B b;
    // Instrument -> 0 for A, 1 for B.
    std::map<std::string, int> owner;

    void prepare(const Markets& m) { a.prepare(m); b.prepare(m); }
    std::vector<Order> decide(int64_t now, const Report& report, const std::vector<Position>& positions) {
        // A position gone (a level closed it, or the open was refused) frees its instrument.
        std::erase_if(owner, [&](const auto& o) {
            return std::none_of(positions.begin(), positions.end(),
                                [&](const Position& p) { return p.instrument == o.first; });
        });
        std::vector<Order> orders;
        take(orders, a.decide(now, report, owned(positions, 0)), 0);
        take(orders, b.decide(now, report, owned(positions, 1)), 1);
        return orders;
    }

private:
    std::vector<Position> owned(const std::vector<Position>& positions, int who) const {
        std::vector<Position> out;
        for (const Position& p : positions) {
            auto it = owner.find(p.instrument);
            if (it != owner.end() && it->second == who) out.push_back(p);
        }
        return out;
    }

    void take(std::vector<Order>& orders, std::vector<Order> mine, int who) {
        for (Order& o : mine) {
            if (o.kind == Order::Kind::Open) {
                if (owner.contains(o.instrument)) continue;
                owner[o.instrument] = who;
            }
            orders.push_back(std::move(o));
        }
    }
};

// 6. Trade every market that has states, each its own position. The bias is
// the signal close (hourly unless params.signal says otherwise) against its
// average, agreeing with the trend label. Enter
// with the bias when the market label is a trend that way or a breakout, the
// minute close breaks the prior high (low for a short), and volatility is not
// extreme or a shock. Exit when the bias stops matching the position or the
// market label turns to the opposite trend. Unknown never opens a trade and
// never forces an exit. Stop: atr_stops signal ATRs; take profit: reward
// times the stop. With flip, every trade takes the other side; the signals,
// exits, stop and take profit distances stay the same.
struct StateTrend {
    struct Params {
        int average = 50, breakout = 30, atr_period = 14;
        double atr_stops = 2.0, reward = 2.0, leverage = 1.0, fee_rate = 0.0001;
        bool flip = false;
        // No entry when the stop would be nearer than this fraction of price.
        double min_stop = 0.0;
        // The bars each instrument's average, ATR, and trend check read; an
        // instrument not listed reads Hour1. Entries and fills stay on base bars.
        std::map<std::string, Timeframe> signal;
    } params;
    // One market's bars, lines, and states.
    struct Lines {
        std::string instrument;
        const Bars* bars = nullptr;
        const Bars* minute = nullptr;
        const States* states = nullptr;
        // prior_max and prior_min leave out the current bar, so the minute
        // close can break them.
        std::vector<double> average, atr, high, low;
        // The last minute bar decide acted on.
        int seen_bar = -1;
    };
    std::vector<Lines> lines;

    void prepare(const Markets& m) {
        lines.clear();
        for (const Market& market : m.all()) {
            if (!market.states) continue;
            auto tf = params.signal.find(market.instrument);
            Lines l{.instrument = market.instrument,
                    .bars = &m.at(market.instrument, tf == params.signal.end() ? Timeframe::Hour1 : tf->second),
                    .minute = &m.base(market.instrument), .states = &*market.states};
            l.average = sma(l.bars->close, params.average);
            l.atr = atr(*l.bars, params.atr_period);
            l.high = prior_max(l.minute->high, params.breakout);
            l.low = prior_min(l.minute->low, params.breakout);
            lines.push_back(std::move(l));
        }
    }

    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        std::vector<Order> orders;
        for (Lines& l : lines) {
            int m = last_closed(*l.minute, now);
            if (m < 0 || m == l.seen_bar) continue;
            l.seen_bar = m;
            int h = last_closed(*l.bars, now);
            if (h < 0) continue;
            State s = state_at(*l.states, now);
            double close = l.bars->close[h];
            bool up = defined(l.average[h]) && close > l.average[h] && s.trend == TrendState::Uptrend;
            bool down = defined(l.average[h]) && close < l.average[h] && s.trend == TrendState::Downtrend;

            auto open = std::find_if(positions.begin(), positions.end(),
                                     [&](const Position& p) { return p.instrument == l.instrument; });
            if (open != positions.end()) {
                // The side the signal wanted, before any flip.
                bool is_long = (open->side == Side::Long) != params.flip;
                // An Unknown label is no evidence either way, so it keeps the position.
                bool bias_gone = s.trend != TrendState::Unknown && (is_long ? !up : !down);
                bool turned = s.market == (is_long ? MarketState::TrendingDown : MarketState::TrendingUp);
                if (bias_gone || turned) {
                    orders.push_back(Order{.kind = Order::Kind::Close, .instrument = l.instrument});
                }
                continue;
            }

            double price = l.minute->close[m];
            bool calm = s.volatility != VolatilityState::Unknown &&
                        s.volatility != VolatilityState::Extreme && s.volatility != VolatilityState::Shock;
            if (!calm || !defined(l.atr[h]) || !defined(l.high[m]) || !defined(l.low[m])) continue;
            bool long_entry = up && (s.market == MarketState::TrendingUp || s.market == MarketState::Breakout) &&
                              price > l.high[m];
            bool short_entry = down && (s.market == MarketState::TrendingDown || s.market == MarketState::Breakout) &&
                               price < l.low[m];
            if (!long_entry && !short_entry) continue;
            double stop = params.atr_stops * l.atr[h] / price;
            if (stop < params.min_stop) continue;
            orders.push_back(Order{.kind = Order::Kind::Open, .instrument = l.instrument,
                                   .side = long_entry != params.flip ? Side::Long : Side::Short,
                                   .stop_distance = stop, .take_profit_distance = params.reward * stop,
                                   .leverage = params.leverage, .fee_rate = params.fee_rate});
        }
        return orders;
    }
};

static_assert(Strategy<LateDayShort> && Strategy<RallyShort> && Strategy<CampaignShort> &&
              Strategy<SpikeShort> && Strategy<GoldTrendLong> &&
              Strategy<Combined<CampaignShort, SpikeShort>> &&
              Strategy<Combined<LateDayShort, RallyShort>> && Strategy<StateTrend>);

}
