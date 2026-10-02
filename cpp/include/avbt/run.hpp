#pragma once
#include <stdexcept>
#include <string>
#include <vector>

#include "avbt/library.hpp"
#include "avbt/registry.hpp"

namespace avbt {

namespace detail {


// The params every Veranta strategy shares.
template <class P>
std::vector<Field<P>> common(std::vector<Field<P>> own) {
    own.push_back(field("instrument", &P::instrument));
    own.push_back(side_field<P>());
    own.push_back(field("take_profit", &P::take_profit, 0.0, inf));
    own.push_back(field("stop", &P::stop, 0.0, 1.0));
    own.push_back(field("time_exit", &P::time_exit, 0, inf));
    own.push_back(field("leverage", &P::leverage, 0.0, 100.0));
    own.push_back(field("timeframe", &P::timeframe));
    return own;
}

inline auto late_day_fields() {
    using P = LateDayShort::Params;
    return common<P>({field("hour", &P::hour, 0, 23), field("lag", &P::lag, 1, inf), field("rise", &P::rise)});
}
inline auto rally_fields() {
    using P = RallyShort::Params;
    return common<P>({field("lag", &P::lag, 1, inf), field("window", &P::window, 1, inf), field("rise", &P::rise)});
}
inline auto campaign_fields() {
    using P = CampaignShort::Params;
    return common<P>({field("lag", &P::lag, 1, inf), field("rise", &P::rise)});
}
inline auto spike_fields() {
    using P = SpikeShort::Params;
    return common<P>({field("spike", &P::spike)});
}
inline auto gold_fields() {
    using P = GoldTrendLong::Params;
    return common<P>({field("window", &P::window, 1, inf), field("lag", &P::lag, 1, inf)});
}
inline auto state_trend_fields() {
    using P = StateTrend::Params;
    return std::vector<Field<P>>{
        field("average", &P::average, 1, inf), field("breakout", &P::breakout, 1, inf),
        field("atr_period", &P::atr_period, 1, inf), field("atr_stops", &P::atr_stops, 0.0, inf),
        field("reward", &P::reward, 0.0, inf), field("leverage", &P::leverage, 0.0, 100.0),
        field("flip", &P::flip), field("min_stop", &P::min_stop, 0.0, 1.0),
        field("trail_atrs", &P::trail_atrs, 0.0, inf), field("take_fraction", &P::take_fraction, 0.0, 1.0),
        field("signal", &P::signal)};
}

}  // namespace detail

// Every strategy run can build, by name.
inline const std::vector<StrategyInfo>& strategies() {
    using namespace detail;
    static std::vector<StrategyInfo> table = {
        single<LateDayShort>("late_day_short", late_day_fields(), {Timeframe::Hour1}),
        single<RallyShort>("rally_short", rally_fields(), {Timeframe::Hour1}),
        single<CampaignShort>("campaign_short", campaign_fields(), {Timeframe::Hour1}),
        single<SpikeShort>("spike_short", spike_fields(), {Timeframe::Hour1}),
        single<GoldTrendLong>("gold_trend_long", gold_fields(), {Timeframe::Hour1}),
        single<StateTrend>("state_trend", state_trend_fields(), {Timeframe::Min1, Timeframe::Hour1}),
        combined<CampaignShort, SpikeShort>("campaign_and_spike", campaign_fields(), spike_fields()),
        combined<LateDayShort, RallyShort>("late_day_and_rally", late_day_fields(), rally_fields()),
    };
    static const bool joined = [] { for (StrategyInfo& s : library()) table.push_back(s); return true; }();
    (void)joined;
    return table;
}

// The table row named `name`. Throws std::invalid_argument for an unknown
// name; the message lists the known ones.
inline const StrategyInfo& strategy(const std::string& name) {
    std::string known;
    for (const StrategyInfo& s : strategies()) {
        if (s.name == name) return s;
        known += (known.empty() ? "" : ", ") + s.name;
    }
    throw std::invalid_argument("no strategy " + name + "; known: " + known);
}

// Builds the strategy `name` from params and backtests it. Throws
// std::invalid_argument for an unknown name, an unknown param, a value of
// the wrong type or out of range, or bad costs.
inline Result run(const std::string& name, const Params& params, const Markets& markets,
                  const MarketCosts& costs, PortfolioSettings settings) {
    return strategy(name).run(params, markets, costs, settings);
}

// The orders `live` gives at `now`, from a fresh start: prepare on the bars
// so far, decide one base step earlier to learn which bars were already
// seen, then decide at `now`. So it returns orders only at the step where
// run would, with no state kept between calls. Positions need entry_time
// for time exits; test_live checks the orders equal run's.
inline std::vector<Order> decide_once(Live live, const Markets& markets, const std::vector<Position>& positions,
                                      int64_t now) {
    live.prepare(markets);
    live.decide(now - seconds(markets.timeframe()), Report{}, positions);
    return live.decide(now, Report{}, positions);
}

// The orders the strategy `name` gives at `now` on the bars so far. Throws
// as run does.
inline std::vector<Order> decide(const std::string& name, const Params& params, const Markets& markets,
                                 const std::vector<Position>& positions, int64_t now,
                                 PortfolioSettings settings = {}) {
    return decide_once(strategy(name).live(params, settings), markets, positions, now);
}

}
