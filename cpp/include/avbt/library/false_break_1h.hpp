#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Whether the labels allow a fade on `side`. regime 0: always; 1: trend
// label NonTrending; 2: a range market label (MeanReversion, Consolidation,
// ChoppyRange, StableLowEnergy); 3: with the trend label (long only in an
// Uptrend, short only in a Downtrend); 4: not against the trend label.
inline bool fade_allowed(const States* st, int64_t now, int64_t delay, int regime, Side side) {
    if (regime == 0) return true;
    if (!st) return false;
    State s = state_at(*st, now, delay);
    using M = MarketState;
    switch (regime) {
    case 1: return s.trend == TrendState::NonTrending;
    case 2: return s.market == M::MeanReversion || s.market == M::Consolidation ||
                   s.market == M::ChoppyRange || s.market == M::StableLowEnergy;
    case 3: return side == Side::Long ? s.trend == TrendState::Uptrend : s.trend == TrendState::Downtrend;
    case 4: return side == Side::Long ? s.trend != TrendState::Downtrend : s.trend != TrendState::Uptrend;
    }
    return false;
}

// False break: every market passed in, both sides. A break bar closes above
// the prior `window` bars' high (below their low). If within `within` bars
// a close is back inside that level, fade it, when the labels allow it
// (regime, see fade_allowed). Out once a close reaches the range middle
// (with revert), at tp_atrs ATRs, at a wide stop of atr_stops ATRs, or after
// `hold` signal bars. At most max_open positions at once; one per
// instrument.
struct FalseBreak1h {
    struct Params {
        Timeframe timeframe = Timeframe::Hour1;
        int window = 24, within = 3, hold = 12, atr_period = 14, regime = 4, max_open = 3;
        double tp_atrs = 1.0, atr_stops = 6.0, leverage = 5.0;
        bool revert = true;
    } params;
    // Seconds back at which labels are read; set by use_settings.
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
                lines.push_back(Lines{.instrument = market.instrument, .high_of = PriorMax(params.window),
                                      .low_of = PriorMin(params.window), .atr_of = Atr(params.atr_period)});
                it = lines.end() - 1;
            }
            Lines& l = *it;
            l.bars = &m.at(l.instrument, params.timeframe);
            l.states = m.states(l.instrument);
            for (; l.done < l.bars->ts.size(); ++l.done) {
                size_t i = l.done;
                l.high.push_back(l.high_of.update(l.bars->high[i]));
                l.low.push_back(l.low_of.update(l.bars->low[i]));
                l.atr.push_back(l.atr_of.update(l.bars->high[i], l.bars->low[i], l.bars->close[i]));
            }
        }
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        const auto& p = params;
        std::vector<Order> orders;
        int open = static_cast<int>(std::count_if(positions.begin(), positions.end(),
                                                  [&](const Position& x) { return trades(x.instrument); }));
        for (Lines& l : lines) {
            int t = last_closed(*l.bars, now);
            if (t < 1 || t == l.seen_bar) continue;
            l.seen_bar = t;
            const auto& c = l.bars->close;
            auto mine = std::find_if(positions.begin(), positions.end(),
                                     [&](const Position& x) { return x.instrument == l.instrument; });
            if (mine != positions.end()) {
                bool late = mine->entry_time != unknown_time && now - mine->entry_time >= p.hold * seconds(p.timeframe);
                double mid = (l.high[t] + l.low[t]) / 2;
                bool back = p.revert && defined(mid) && (mine->side == Side::Long ? c[t] >= mid : c[t] <= mid);
                if (late || back) orders.push_back(Order{.kind = Order::Kind::Close, .instrument = l.instrument});
                continue;
            }
            if (open >= p.max_open || !defined(l.atr[t])) continue;
            bool up = false, down = false;
            // A break at j < t whose level the close at t is back inside, first time.
            for (int j = t - 1; j >= std::max(0, t - p.within) && !up && !down; --j) {
                if (!defined(l.high[j]) || !defined(l.low[j])) break;
                up = c[j] > l.high[j] && c[t] < l.high[j] && c[t - 1] >= l.high[j];
                down = c[j] < l.low[j] && c[t] > l.low[j] && c[t - 1] <= l.low[j];
            }
            if (!up && !down) continue;
            Side side = up ? Side::Short : Side::Long;
            if (!fade_allowed(l.states, now, state_delay, p.regime, side)) continue;
            double a = l.atr[t] / c[t];
            orders.push_back(Order{.kind = Order::Kind::Open, .instrument = l.instrument, .side = side,
                                   .stop_distance = p.atr_stops * a,
                                   .take_profit_distance = p.tp_atrs > 0 ? p.tp_atrs * a : std::nan(""),
                                   .leverage = p.leverage});
            ++open;
        }
        return orders;
    }
};
static_assert(Strategy<FalseBreak1h>);

namespace detail {
inline StrategyInfo false_break_1h_info() {
    using P = FalseBreak1h::Params;
    return single<FalseBreak1h>("false_break_1h", {
        field("timeframe", &P::timeframe), field("window", &P::window, 2, inf),
        field("within", &P::within, 1, inf), field("hold", &P::hold, 1, inf),
        field("atr_period", &P::atr_period, 1, inf), field("regime", &P::regime, 0, 4),
        field("max_open", &P::max_open, 1, inf), field("tp_atrs", &P::tp_atrs, 0.0, inf),
        field("atr_stops", &P::atr_stops, 0.0, inf), field("leverage", &P::leverage, 0.0, 100.0),
        field("revert", &P::revert)}, {Timeframe::Min15, Timeframe::Hour1});
}
}  // namespace detail

}
