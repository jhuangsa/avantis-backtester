#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Range basket: the range_seller rule on every market passed in, one
// account, one position per instrument. Only while the market's label is
// consolidation, mean_reversion or choppy_range: when the prior signal bar
// closed above the prior `window` bars' high and this one closes back
// inside, short; the mirror below the low is a long (two bars). Take
// profit `take` of the way to the range middle; stop atr_stops ATRs (wide);
// out after max_bars signal bars (0 for none). flat also needs the trend
// label non_trending. A trade whose stop is wider than max_stop of the
// price is skipped (0 for no limit). calm skips a high, extreme,
// expansion or shock volatility label. At most max_open positions at
// once (0 for no limit). depth: the prior close was at most depth ATRs
// past the range (0 for any). leave closes when the label stops being ranging.
struct RangeBasket {
    struct Params {
        Timeframe timeframe = Timeframe::Hour1;
        int window = 100, atr_period = 14, max_bars = 96, max_open = 0;
        double take = 1.0, depth = 0.0, atr_stops = 8.0, max_stop = 0.0, leverage = 5.0;
        bool flat = false, calm = false, leave = false;
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
            if (!market.states) continue;
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
            l.states = &*market.states;
            const Bars& b = *l.bars;
            for (; l.done < b.ts.size(); ++l.done) {
                size_t i = l.done;
                l.high.push_back(l.high_of.update(b.high[i]));
                l.low.push_back(l.low_of.update(b.low[i]));
                l.atr.push_back(l.atr_of.update(b.high[i], b.low[i], b.close[i]));
            }
        }
    }
    static bool ranging(MarketState s) {
        return s == MarketState::Consolidation || s == MarketState::MeanReversion || s == MarketState::ChoppyRange;
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
            auto mine = std::find_if(positions.begin(), positions.end(),
                                     [&](const Position& x) { return x.instrument == l.instrument; });
            if (mine != positions.end()) {
                bool late = p.max_bars > 0 && mine->entry_time != unknown_time &&
                            now - mine->entry_time >= p.max_bars * seconds(p.timeframe);
                // leave: close once the label is no longer a ranging one.
                if (p.leave && !ranging(state_at(*l.states, now, state_delay).market)) late = true;
                if (late) orders.push_back(Order{.kind = Order::Kind::Close, .instrument = l.instrument});
                continue;
            }
            State st = state_at(*l.states, now, state_delay);
            if (!ranging(st.market) || (p.flat && st.trend != TrendState::NonTrending)) continue;
            VolatilityState v = st.volatility;
            bool wild = v == VolatilityState::High || v == VolatilityState::Extreme || v == VolatilityState::Expansion ||
                        v == VolatilityState::Shock;
            if (p.calm && wild) continue;
            auto place_at = [&](int i) {
                bool ok = defined(l.high[i]) && defined(l.low[i]) && l.high[i] > l.low[i];
                return ok ? (l.bars->close[i] - l.low[i]) / (l.high[i] - l.low[i]) : std::nan("");
            };
            double now_place = place_at(t), before = place_at(t - 1);
            if (!defined(now_place) || !defined(before) || !defined(l.atr[t])) continue;
            bool sell = before > 1 && now_place <= 1 && now_place > 0.5;
            bool buy = before < 0 && now_place >= 0 && now_place < 0.5;
            if (!sell && !buy) continue;
            // depth: the prior close was at most depth ATRs past the range (0: any).
            double w = l.high[t - 1] - l.low[t - 1], past = (sell ? before - 1 : -before) * w;
            if (!defined(l.atr[t - 1]) || (p.depth > 0 && past > p.depth * l.atr[t - 1])) continue;
            double c = l.bars->close[t], mid = (l.high[t] + l.low[t]) / 2, stop = p.atr_stops * l.atr[t] / c;
            if (p.max_stop > 0 && stop > p.max_stop) continue;
            if (p.max_open > 0 && open >= p.max_open) continue;
            ++open;
            orders.push_back(Order{.kind = Order::Kind::Open, .instrument = l.instrument,
                                   .side = sell ? Side::Short : Side::Long,
                                   .stop_distance = stop,
                                   .take_profit_distance = p.take * std::abs(c - mid) / c, .leverage = p.leverage});
        }
        return orders;
    }
};
static_assert(Strategy<RangeBasket>);

namespace detail {
inline StrategyInfo range_basket_info() {
    using P = RangeBasket::Params;
    return single<RangeBasket>("range_basket", {
        field("timeframe", &P::timeframe), field("window", &P::window, 2, inf),
        field("atr_period", &P::atr_period, 1, inf), field("max_bars", &P::max_bars, 0, inf),
        field("max_open", &P::max_open, 0, inf),
        field("take", &P::take, 0.0, 2.0), field("depth", &P::depth, 0.0, inf), field("atr_stops", &P::atr_stops, 0.0, inf),
        field("max_stop", &P::max_stop, 0.0, 1.0),
        field("leverage", &P::leverage, 0.0, 100.0), field("flat", &P::flat),
        field("calm", &P::calm), field("leave", &P::leave)},
        {Timeframe::Min15, Timeframe::Hour1});
}
}  // namespace detail

}
