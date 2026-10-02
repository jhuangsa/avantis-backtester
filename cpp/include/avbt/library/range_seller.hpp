#pragma once
#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Range seller: one instrument, both sides, only while the market label is
// consolidation, mean_reversion or choppy_range. On a closed signal bar,
// place = where the close sits in the prior `window` bars' range (0 low,
// 1 high). Short when place > 1 - edge, long when place < edge. Take profit
// `take` of the way to the range middle; stop atr_stops ATRs (wide). Out
// after max_bars signal bars (0 for none). flat also needs the trend label
// non_trending; back_inside needs the prior bar outside the range and this
// one back in (two bars).
struct RangeSeller {
    struct Params {
        std::string instrument = "ETH";
        Timeframe timeframe = Timeframe::Hour1;
        int window = 48, atr_period = 14, max_bars = 24;
        double edge = 0.1, take = 0.5, atr_stops = 6.0, leverage = 3.0;
        bool flat = false, back_inside = false;
    } params;
    int64_t state_delay = 60;
    const Bars* bars = nullptr;
    const States* states = nullptr;
    std::vector<double> high, low, atr;
    PriorMax high_of{1};
    PriorMin low_of{1};
    Atr atr_of{1};
    size_t done = 0;
    int seen_bar = -1;

    void prepare(const Markets& m) {
        high.clear(); low.clear(); atr.clear(); done = 0; seen_bar = -1;
        high_of = PriorMax(params.window); low_of = PriorMin(params.window); atr_of = Atr(params.atr_period);
        update(m);
    }
    void update(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        states = m.states(params.instrument);
        for (; done < bars->ts.size(); ++done) {
            const Bars& b = *bars;
            high.push_back(high_of.update(b.high[done]));
            low.push_back(low_of.update(b.low[done]));
            atr.push_back(atr_of.update(b.high[done], b.low[done], b.close[done]));
        }
    }
    static bool ranging(MarketState s) {
        return s == MarketState::Consolidation || s == MarketState::MeanReversion || s == MarketState::ChoppyRange;
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        int t = last_closed(*bars, now);
        if (t < 0 || t == seen_bar) return {};
        seen_bar = t;
        const auto& p = params;
        auto mine = std::find_if(positions.begin(), positions.end(),
                                 [&](const Position& x) { return x.instrument == p.instrument; });
        if (mine != positions.end()) {
            bool late = p.max_bars > 0 && mine->entry_time != unknown_time &&
                        now - mine->entry_time >= p.max_bars * seconds(p.timeframe);
            if (late) return {Order{.kind = Order::Kind::Close, .instrument = p.instrument}};
            return {};
        }
        if (!states) return {};
        State st = state_at(*states, now, state_delay);
        if (!ranging(st.market) || (p.flat && st.trend != TrendState::NonTrending)) return {};
        auto place_at = [&](int i) {
            bool ok = i >= 0 && defined(high[i]) && defined(low[i]) && high[i] > low[i];
            return ok ? (bars->close[i] - low[i]) / (high[i] - low[i]) : std::nan("");
        };
        double c = bars->close[t], place = place_at(t), before = place_at(t - 1);
        if (!defined(place) || !defined(atr[t]) || (p.back_inside && !defined(before))) return {};
        bool sell = place > 1 - p.edge, buy = place < p.edge;
        // back_inside: the prior bar closed outside the range, this one inside.
        if (p.back_inside) sell = sell && before > 1 && place <= 1, buy = buy && before < 0 && place >= 0;
        if (!sell && !buy) return {};
        double mid = (high[t] + low[t]) / 2, to_mid = std::abs(c - mid) / c;
        if (to_mid <= 0) return {};
        return {Order{.kind = Order::Kind::Open, .instrument = p.instrument, .side = sell ? Side::Short : Side::Long,
                      .stop_distance = p.atr_stops * atr[t] / c, .take_profit_distance = p.take * to_mid,
                      .leverage = p.leverage}};
    }
};
static_assert(Strategy<RangeSeller>);

namespace detail {
inline StrategyInfo range_seller_info() {
    using P = RangeSeller::Params;
    return single<RangeSeller>("range_seller", {
        field("instrument", &P::instrument), field("timeframe", &P::timeframe),
        field("window", &P::window, 2, inf), field("atr_period", &P::atr_period, 1, inf),
        field("max_bars", &P::max_bars, 0, inf), field("edge", &P::edge, 0.0, 0.5),
        field("take", &P::take, 0.0, 2.0), field("atr_stops", &P::atr_stops, 0.0, inf),
        field("leverage", &P::leverage, 0.0, 100.0), field("flat", &P::flat),
        field("back_inside", &P::back_inside)}, {Timeframe::Min15, Timeframe::Hour1});
}
}  // namespace detail

}
