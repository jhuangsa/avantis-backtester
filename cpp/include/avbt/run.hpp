#pragma once
#include <functional>
#include <limits>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

#include "avbt/backtest.hpp"
#include "avbt/strategies.hpp"

namespace avbt {

// One param value. signal is the only map: instrument -> Timeframe.
using Value = std::variant<bool, int, double, std::string, Timeframe, std::map<std::string, Timeframe>>;
using Params = std::map<std::string, Value>;

// A param's name, default, and the range a number must lie in.
struct Param {
    std::string name;
    Value value;
    double min = -std::numeric_limits<double>::infinity();
    double max = std::numeric_limits<double>::infinity();
};

// One strategy object behind a type-erased face, for live use: prepare once
// on the history, then update and decide after each appended bar.
struct Live {
    std::function<void(const Markets&)> prepare, update;
    std::function<std::vector<Order>(int64_t, const Report&, const std::vector<Position>&)> decide;
};

template <Strategy S>
Live live_of(S strategy) {
    auto s = std::make_shared<S>(std::move(strategy));
    return {[s](const Markets& m) { s->prepare(m); }, [s](const Markets& m) { s->update(m); },
            [s](int64_t now, const Report& r, const std::vector<Position>& p) { return s->decide(now, r, p); }};
}

// One row of the strategy table. timeframes are the ones it reads by
// default; run builds it from params and backtests it.
struct StrategyInfo {
    std::string name;
    std::vector<Param> params;
    std::vector<Timeframe> timeframes;
    std::function<Result(const Params&, const Markets&, const MarketCosts&, PortfolioSettings)> run;
    std::function<Live(const Params&)> live;
};

namespace detail {

// A param and how to write it into a strategy's Params struct.
template <class P>
struct Field {
    Param param;
    std::function<void(P&, const Value&)> set;
};

template <class T>
T as(const std::string& name, const Value& v, const Param& p) {
    T out;
    if constexpr (std::is_same_v<T, double>) {
        if (auto* i = std::get_if<int>(&v)) out = *i;
        else if (auto* d = std::get_if<double>(&v)) out = *d;
        else throw std::invalid_argument("param " + name + " must be a number");
    } else {
        auto* x = std::get_if<T>(&v);
        if (!x) throw std::invalid_argument("param " + name + " has the wrong type");
        out = *x;
    }
    if constexpr (std::is_arithmetic_v<T> && !std::is_same_v<T, bool>) {
        if (out < p.min || out > p.max) {
            throw std::invalid_argument("param " + name + " = " + std::to_string(out) + " is outside [" +
                                        std::to_string(p.min) + ", " + std::to_string(p.max) + "]");
        }
    }
    return out;
}

template <class P, class T>
Field<P> field(std::string name, T P::*member, double min = -std::numeric_limits<double>::infinity(),
               double max = std::numeric_limits<double>::infinity()) {
    Param p{name, Value(P{}.*member), min, max};
    return {p, [=](P& params, const Value& v) { params.*member = as<T>(name, v, p); }};
}

// Fills a Params struct from `given`; a missing param keeps its default.
template <class P>
P build(const std::string& strategy, const std::vector<Field<P>>& fields, const Params& given) {
    P out;
    for (const auto& entry : given) {
        const std::string& name = entry.first;
        const Value& v = entry.second;
        auto f = std::find_if(fields.begin(), fields.end(), [&](const Field<P>& x) { return x.param.name == name; });
        if (f == fields.end()) {
            std::string known;
            for (const Field<P>& x : fields) known += (known.empty() ? "" : ", ") + x.param.name;
            throw std::invalid_argument(strategy + " has no param " + name + "; it has " + known);
        }
        f->set(out, v);
    }
    return out;
}

template <class P>
std::vector<Param> specs(const std::vector<Field<P>>& fields, const std::string& prefix = "") {
    std::vector<Param> out;
    for (const Field<P>& f : fields) {
        out.push_back(f.param);
        out.back().name = prefix + f.param.name;
    }
    return out;
}

template <class S>
StrategyInfo single(std::string name, std::vector<Field<typename S::Params>> fields, std::vector<Timeframe> tfs) {
    std::vector<Param> ps = specs(fields);
    return {name, ps, tfs, [=](const Params& given, const Markets& m, const MarketCosts& c, PortfolioSettings s) {
                S strategy;
                strategy.params = build(name, fields, given);
                return backtest(strategy, m, c, s);
            },
            [=](const Params& given) {
                S strategy;
                strategy.params = build(name, fields, given);
                return live_of(std::move(strategy));
            }};
}

// Two strategies in one account; their params are prefixed "a." and "b.".
template <class A, class B>
StrategyInfo combined(std::string name, std::vector<Field<typename A::Params>> fa,
                      std::vector<Field<typename B::Params>> fb) {
    std::vector<Param> ps = specs(fa, "a.");
    for (Param& p : specs(fb, "b.")) ps.push_back(p);
    auto make = [=](const Params& given) {
        Params pa, pb;
        for (const auto& [k, v] : given) {
            if (k.starts_with("a.")) pa[k.substr(2)] = v;
            else if (k.starts_with("b.")) pb[k.substr(2)] = v;
            else throw std::invalid_argument(name + " params start with a. or b., got " + k);
        }
        Combined<A, B> strategy;
        strategy.a.params = build(name + " a", fa, pa);
        strategy.b.params = build(name + " b", fb, pb);
        return strategy;
    };
    return {name, ps, {Timeframe::Hour1},
            [=](const Params& given, const Markets& m, const MarketCosts& c, PortfolioSettings s) {
                auto strategy = make(given);
                return backtest(strategy, m, c, s);
            },
            [=](const Params& given) { return live_of(make(given)); }};
}

constexpr double inf = std::numeric_limits<double>::infinity();

// The params every Veranta strategy shares.
template <class P>
std::vector<Field<P>> common(std::vector<Field<P>> own) {
    own.push_back(field("instrument", &P::instrument));
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
    static const std::vector<StrategyInfo> table = {
        single<LateDayShort>("late_day_short", late_day_fields(), {Timeframe::Hour1}),
        single<RallyShort>("rally_short", rally_fields(), {Timeframe::Hour1}),
        single<CampaignShort>("campaign_short", campaign_fields(), {Timeframe::Hour1}),
        single<SpikeShort>("spike_short", spike_fields(), {Timeframe::Hour1}),
        single<GoldTrendLong>("gold_trend_long", gold_fields(), {Timeframe::Hour1}),
        single<StateTrend>("state_trend", state_trend_fields(), {Timeframe::Min1, Timeframe::Hour1}),
        combined<CampaignShort, SpikeShort>("campaign_and_spike", campaign_fields(), spike_fields()),
        combined<LateDayShort, RallyShort>("late_day_and_rally", late_day_fields(), rally_fields()),
    };
    return table;
}

// Builds the strategy `name` from params and backtests it. Throws
// std::invalid_argument for an unknown name (the message lists the known
// ones), an unknown param, a value of the wrong type or out of range, or bad costs.
inline Result run(const std::string& name, const Params& params, const Markets& markets,
                  const MarketCosts& costs, PortfolioSettings settings) {
    std::string known;
    for (const StrategyInfo& s : strategies()) {
        if (s.name == name) return s.run(params, markets, costs, settings);
        known += (known.empty() ? "" : ", ") + s.name;
    }
    throw std::invalid_argument("no strategy " + name + "; known: " + known);
}

}
