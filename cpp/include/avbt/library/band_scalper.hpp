#pragma once
#include <algorithm>
#include <string>
#include <vector>

#include "avbt/registry.hpp"

namespace avbt {

// Band scalper: one instrument, both sides. On each closed signal bar, when
// the close is more than k ATRs below its `window`-bar moving average, go
// long; more than k ATRs above, go short. Only while the volatility label is
// Low or Normal. Confirm: wait for the close to come back inside the band
// (beyond it on the bar before, inside on this one). Trend 1: no long on a Downtrend label, no short on an
// Uptrend one; trend 2: long only on Uptrend, short only on Downtrend. Ranging: only on a mean_reversion, consolidation,
// choppy_range or stable_low_energy market label. Take profit: tp_atrs ATRs (small); stop: atr_stops ATRs
// (wide), at least
// min_stop of the price; out after max_bars signal bars (0 for no time exit).
struct BandScalper {
    struct Params {
        std::string instrument = "AVAX";
        Timeframe timeframe = Timeframe::Min15;
        int window = 20, atr_period = 14, max_bars = 0;
        double min_stop = 0.02, k = 2.0, tp_atrs = 0.5, atr_stops = 4.0, leverage = 3.0;
        int trend = 0;
        bool shorts = true, ranging = false, confirm = false;
    } params;
    int64_t state_delay = 60;
    const Bars* bars = nullptr;
    const States* states = nullptr;
    std::vector<double> mean, atr;
    Sma mean_of{1};
    Atr atr_of{1};
    size_t done = 0;
    int seen_bar = -1;

    void prepare(const Markets& m) {
        mean.clear(); atr.clear(); done = 0; seen_bar = -1;
        mean_of = Sma(params.window); atr_of = Atr(params.atr_period);
        update(m);
    }
    void update(const Markets& m) {
        bars = &m.at(params.instrument, params.timeframe);
        states = m.states(params.instrument);
        for (; done < bars->ts.size(); ++done) {
            mean.push_back(mean_of.update(bars->close[done]));
            atr.push_back(atr_of.update(bars->high[done], bars->low[done], bars->close[done]));
        }
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
        if (!states || !defined(mean[t]) || !defined(atr[t])) return {};
        State s = state_at(*states, now, state_delay);
        if (s.volatility != VolatilityState::Low && s.volatility != VolatilityState::Normal) return {};
        MarketState ms = s.market;
        bool range = ms == MarketState::MeanReversion || ms == MarketState::Consolidation ||
                     ms == MarketState::ChoppyRange || ms == MarketState::StableLowEnergy;
        if (p.ranging && !range) return {};
        double c = bars->close[t], gap = (c - mean[t]) / atr[t];
        if (p.confirm) {
            if (t < 1 || !defined(mean[t - 1]) || !defined(atr[t - 1])) return {};
            double was = (bars->close[t - 1] - mean[t - 1]) / atr[t - 1];
            gap = std::abs(gap) < p.k ? (was < -p.k ? -detail::inf : was > p.k ? detail::inf : 0.0) : 0.0;
        }
        auto fits = [&](TrendState with, TrendState against) {
            return p.trend == 0 || (p.trend == 1 ? s.trend != against : s.trend == with);
        };
        bool up = gap < -p.k && fits(TrendState::Uptrend, TrendState::Downtrend);
        bool down = p.shorts && gap > p.k && fits(TrendState::Downtrend, TrendState::Uptrend);
        if (!up && !down) return {};
        return {Order{.kind = Order::Kind::Open, .instrument = p.instrument,
                      .side = up ? Side::Long : Side::Short,
                      .stop_distance = std::max(p.atr_stops * atr[t] / c, p.min_stop),
                      .take_profit_distance = p.tp_atrs * atr[t] / c, .leverage = p.leverage}};
    }
};
static_assert(Strategy<BandScalper>);

namespace detail {
inline StrategyInfo band_scalper_info() {
    using P = BandScalper::Params;
    return single<BandScalper>("band_scalper", {
        field("instrument", &P::instrument), field("timeframe", &P::timeframe),
        field("window", &P::window, 2, inf), field("atr_period", &P::atr_period, 1, inf),
        field("max_bars", &P::max_bars, 0, inf), field("k", &P::k, 0.0, inf),
        field("min_stop", &P::min_stop, 0.0, 0.8),
        field("tp_atrs", &P::tp_atrs, 0.0, inf), field("atr_stops", &P::atr_stops, 0.0, inf),
        field("leverage", &P::leverage, 0.0, 100.0), field("shorts", &P::shorts),
        field("trend", &P::trend, 0, 2), field("ranging", &P::ranging), field("confirm", &P::confirm)},
        {Timeframe::Min15, Timeframe::Hour1});
}
}  // namespace detail

}
