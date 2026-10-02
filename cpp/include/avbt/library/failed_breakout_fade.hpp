#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Failed breakout fade: every market passed in, both sides. A breakout bar
// closes above the prior `window` bars' high (below their low). If within
// `within` bars a close falls back inside that level, fade it: short after
// a failed break up, long after a failed break down. With use_label, the
// market label must also be failed_breakout. Take profit: tp_atrs ATRs;
// stop: atr_stops ATRs; out after `hold` signal bars.
struct FailedBreakoutFade {
    struct Params {
        Timeframe timeframe = Timeframe::Hour1;
        int window = 24, within = 3, hold = 12, atr_period = 14;
        double tp_atrs = 1.0, atr_stops = 4.0, leverage = 3.0;
        bool use_label = false, shorts = true;
    } params;
    int64_t state_delay = 60;
    struct Lines {
        std::string instrument;
        const Bars* bars = nullptr;
        const States* states = nullptr;
        std::vector<double> high, low, atr;
        PriorMax high_of{1};
        PriorMin low_of{1};
        Atr atr_of{1};
        size_t done = 0;
        int seen_bar = -1;
    };
    std::vector<Lines> lines;

    bool trades(const std::string& instrument) const {
        return std::any_of(lines.begin(), lines.end(), [&](const Lines& l) { return l.instrument == instrument; });
    }
    void prepare(const Markets& m) {
        lines.clear();
        update(m);
    }
    void update(const Markets& m) {
        for (const Market& market : m.all()) {
            auto it = std::find_if(lines.begin(), lines.end(),
                                   [&](const Lines& l) { return l.instrument == market.instrument; });
            if (it == lines.end()) {
                lines.push_back(Lines{.instrument = market.instrument,
                                      .high_of = PriorMax(params.window), .low_of = PriorMin(params.window),
                                      .atr_of = Atr(params.atr_period)});
                it = lines.end() - 1;
            }
            Lines& l = *it;
            l.bars = &m.at(market.instrument, params.timeframe);
            l.states = market.states ? &*market.states : nullptr;
            const Bars& b = *l.bars;
            for (; l.done < b.ts.size(); ++l.done) {
                size_t i = l.done;
                l.high.push_back(l.high_of.update(b.high[i]));
                l.low.push_back(l.low_of.update(b.low[i]));
                l.atr.push_back(l.atr_of.update(b.high[i], b.low[i], b.close[i]));
            }
        }
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        const auto& p = params;
        std::vector<Order> orders;
        for (Lines& l : lines) {
            int t = last_closed(*l.bars, now);
            if (t < 1 || t == l.seen_bar) continue;
            l.seen_bar = t;
            auto mine = std::find_if(positions.begin(), positions.end(),
                                     [&](const Position& x) { return x.instrument == l.instrument; });
            if (mine != positions.end()) {
                if (mine->entry_time != unknown_time && now - mine->entry_time >= p.hold * seconds(p.timeframe))
                    orders.push_back(Order{.kind = Order::Kind::Close, .instrument = l.instrument});
                continue;
            }
            if (!defined(l.atr[t])) continue;
            if (p.use_label && (!l.states || state_at(*l.states, now, state_delay).market != MarketState::FailedBreakout))
                continue;
            const auto& c = l.bars->close;
            bool up = false, down = false;
            // Look for a break at j < t whose level the close at t is back inside.
            for (int j = t - 1; j >= std::max(0, t - p.within) && !up && !down; --j) {
                if (!defined(l.high[j]) || !defined(l.low[j])) break;
                up = c[j] > l.high[j] && c[t] < l.high[j] && c[t - 1] >= l.high[j];
                down = c[j] < l.low[j] && c[t] > l.low[j] && c[t - 1] <= l.low[j];
            }
            if (up && !p.shorts) up = false;
            if (!up && !down) continue;
            double a = l.atr[t] / c[t];
            orders.push_back(Order{.kind = Order::Kind::Open, .instrument = l.instrument,
                                   .side = up ? Side::Short : Side::Long, .stop_distance = p.atr_stops * a,
                                   .take_profit_distance = p.tp_atrs * a, .leverage = p.leverage});
        }
        return orders;
    }
};
static_assert(Strategy<FailedBreakoutFade>);

namespace detail {
inline StrategyInfo failed_breakout_fade_info() {
    using P = FailedBreakoutFade::Params;
    return single<FailedBreakoutFade>("failed_breakout_fade", {
        field("timeframe", &P::timeframe), field("window", &P::window, 2, inf),
        field("within", &P::within, 1, inf), field("hold", &P::hold, 1, inf),
        field("atr_period", &P::atr_period, 1, inf), field("tp_atrs", &P::tp_atrs, 0.0, inf),
        field("atr_stops", &P::atr_stops, 0.0, inf), field("leverage", &P::leverage, 0.0, 100.0),
        field("use_label", &P::use_label), field("shorts", &P::shorts)}, {Timeframe::Min15, Timeframe::Hour4});
}
}  // namespace detail

}
