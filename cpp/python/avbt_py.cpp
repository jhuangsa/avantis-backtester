// Python bindings for the avbt indicators.
// numpy arrays go in, numpy arrays come out. Inputs are copied into Bars
// or std::vector once; outputs are moved into a numpy array without a copy.

#include "avbt/indicators.hpp"
#include "avbt/optimize.hpp"
#include "avbt/strategies.hpp"

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace py = pybind11;

namespace {

using InArray = py::array_t<double, py::array::c_style | py::array::forcecast>;

template <typename T>
std::vector<T> to_vector(const py::array_t<T, py::array::c_style | py::array::forcecast>& a) {
    if (a.ndim() != 1) {
        throw std::invalid_argument("expected a 1-d array");
    }
    const T* p = a.data();
    return std::vector<T>(p, p + a.size());
}

// Hands the vector's buffer to numpy. A capsule owns it and frees it with the array.
template <typename T>
py::array_t<T> to_numpy(std::vector<T>&& v) {
    auto* owned = new std::vector<T>(std::move(v));
    py::capsule free_when_done(owned, [](void* p) {
        delete static_cast<std::vector<T>*>(p);
    });
    return py::array_t<T>(owned->size(), owned->data(), free_when_done);
}

avbt::Bars make_bars(avbt::Timeframe timeframe,
                     const py::array_t<int64_t, py::array::c_style | py::array::forcecast>& ts,
                     const InArray& open, const InArray& high, const InArray& low,
                     const InArray& close,
                     const py::array_t<int, py::array::c_style | py::array::forcecast>& minutes_with_data) {
    avbt::Bars bars;
    bars.timeframe = timeframe;
    bars.ts = to_vector(ts);
    bars.open = to_vector(open);
    bars.high = to_vector(high);
    bars.low = to_vector(low);
    bars.close = to_vector(close);
    bars.minutes_with_data = to_vector(minutes_with_data);
    const std::size_t n = bars.ts.size();
    if (bars.open.size() != n || bars.high.size() != n || bars.low.size() != n ||
        bars.close.size() != n || bars.minutes_with_data.size() != n) {
        throw std::invalid_argument("every Bars column must be the same size");
    }
    return bars;
}

avbt::Field field_named(const std::string& name) {
    if (name == "open") return avbt::Field::Open;
    if (name == "high") return avbt::Field::High;
    if (name == "low") return avbt::Field::Low;
    if (name == "close") return avbt::Field::Close;
    throw std::invalid_argument("field must be open, high, low, or close, got " + name);
}

avbt::Side side_named(const std::string& name) {
    if (name == "long") return avbt::Side::Long;
    if (name == "short") return avbt::Side::Short;
    throw std::invalid_argument("side must be long or short, got " + name);
}

const char* cause_name(avbt::Cause c) {
    switch (c) {
        case avbt::Cause::Stop: return "stop";
        case avbt::Cause::TakeProfit: return "take_profit";
        case avbt::Cause::Liquidation: return "liquidation";
        case avbt::Cause::HardStop: return "hard_stop";
        case avbt::Cause::Order: return "order";
        case avbt::Cause::EndOfData: return "end_of_data";
    }
    return "order";
}

// Runs a strategy on the markets and returns the result as a dict.
template <class S>
py::dict run_markets(S& strategy, const avbt::Markets& markets,
                     const avbt::PortfolioSettings& settings) {
    avbt::Result r = avbt::backtest(strategy, markets, settings);
    py::list trades;
    for (const avbt::Trade& t : r.trades) {
        py::dict d;
        d["instrument"] = t.instrument;
        d["entry_bar"] = t.entry_bar;
        d["exit_bar"] = t.exit_bar;
        d["side"] = t.side == avbt::Side::Long ? "long" : "short";
        d["entry_price"] = t.entry_price;
        d["exit_price"] = t.exit_price;
        d["result"] = t.result;
        d["cause"] = cause_name(t.cause);
        trades.append(d);
    }
    py::dict out;
    out["trades"] = trades;
    out["equity"] = to_numpy(std::move(r.equity));
    out["ending_balance"] = r.ending_balance;
    out["timeframe"] = avbt::name(r.timeframe);
    // The UTC second at which each step opens; entry_bar and exit_bar index it.
    out["clock"] = to_numpy(std::move(r.clock));
    return out;
}

// A strategy with its default params on one market, named after its
// instrument. `bars` holds the market's timeframes, finest first.
template <class S>
py::dict run(const std::vector<avbt::Bars>& bars, const avbt::PortfolioSettings& settings) {
    S strategy;
    return run_markets(strategy, avbt::Markets::make({{strategy.params.instrument, bars}}), settings);
}

// Several markets, given as {instrument: [Bars, ...]}, timeframes finest
// first, run by a Combined strategy. a_timeframe and b_timeframe are the
// timeframes its two strategies read.
template <class S>
py::dict run_many(const std::map<std::string, std::vector<avbt::Bars>>& bars,
                  const avbt::PortfolioSettings& settings,
                  avbt::Timeframe a_timeframe, avbt::Timeframe b_timeframe) {
    std::vector<avbt::Market> markets;
    for (const auto& [name, b] : bars) markets.push_back({name, b});
    S strategy;
    strategy.a.params.timeframe = a_timeframe;
    strategy.b.params.timeframe = b_timeframe;
    return run_markets(strategy, avbt::Markets::make(std::move(markets)), settings);
}

// Copies uint8 codes into an enum column, checking each is at most `last`.
template <class E>
std::vector<E> to_codes(const py::array_t<uint8_t, py::array::c_style | py::array::forcecast>& a,
                        E last, const char* what) {
    std::vector<E> out;
    for (uint8_t c : to_vector(a)) {
        if (c > static_cast<uint8_t>(last)) {
            throw std::invalid_argument(std::string(what) + " code " + std::to_string(c) +
                                        " is past the last, " + std::to_string(static_cast<int>(last)));
        }
        out.push_back(static_cast<E>(c));
    }
    return out;
}

using StateTrendMarkets = std::map<std::string, std::pair<std::vector<avbt::Bars>, avbt::States>>;

avbt::Markets state_trend_markets(const StateTrendMarkets& markets) {
    std::vector<avbt::Market> list;
    for (const auto& [name, m] : markets) list.push_back({name, m.first, m.second});
    return avbt::Markets::make(std::move(list));
}

// StateTrend with the given params on {instrument: ([Bars, ...], States)}.
py::dict run_state_trend(const StateTrendMarkets& markets, const avbt::PortfolioSettings& settings,
                         const avbt::StateTrend::Params& params) {
    avbt::StateTrend strategy;
    strategy.params = params;
    return run_markets(strategy, state_trend_markets(markets), settings);
}

// optimize over StateTrend. `knobs` is [(name, [values])]: a name is a
// StateTrendParams field, or "<instrument> signal" for that instrument's
// signal timeframe. Runs and the best choice come back as {knob: value}.
py::dict optimize_state_trend(const StateTrendMarkets& markets, const avbt::PortfolioSettings& settings,
                              const avbt::StateTrend::Params& start,
                              const std::vector<std::pair<std::string, py::list>>& knobs, int rounds) {
    using P = avbt::StateTrend::Params;
    std::vector<avbt::Knob<P>> list;
    for (const auto& [name, values] : knobs) {
        avbt::Knob<P> k{.name = name};
        for (py::handle v : values) {
            if (name.ends_with(" signal")) {
                auto tf = v.cast<avbt::Timeframe>();
                std::string instrument = name.substr(0, name.size() - 7);
                k.labels.push_back(avbt::name(tf));
                k.choices.push_back([=](P& p) { p.signal[instrument] = tf; });
            } else {
                k.labels.push_back(py::str(v));
                auto value = py::reinterpret_borrow<py::object>(v);
                std::string field = name;
                k.choices.push_back([=](P& p) {
                    py::cast(&p, py::return_value_policy::reference).attr(field.c_str()) = value;
                });
            }
        }
        list.push_back(std::move(k));
    }
    auto s = avbt::optimize<avbt::StateTrend>(start, list, state_trend_markets(markets), settings, rounds);
    auto named = [&](const std::vector<int>& choice) {
        py::dict d;
        for (std::size_t k = 0; k < list.size(); ++k) d[list[k].name.c_str()] = list[k].labels[choice[k]];
        return d;
    };
    py::list runs;
    for (const avbt::Run& r : s.runs) {
        py::dict d = named(r.choice);
        d["round"] = r.round;
        d["sharpe"] = r.sharpe;
        runs.append(d);
    }
    py::dict out;
    out["best"] = s.best;
    out["choice"] = named(s.choice);
    out["sharpe"] = s.sharpe;
    out["timeframe"] = avbt::name(s.timeframe);
    out["runs"] = runs;
    return out;
}

}  // namespace

PYBIND11_MODULE(avbt_cpp, m) {
    m.doc() = "avbt C++ indicators";

    py::enum_<avbt::Timeframe>(m, "Timeframe")
        .value("Min1", avbt::Timeframe::Min1)
        .value("Min3", avbt::Timeframe::Min3)
        .value("Min5", avbt::Timeframe::Min5)
        .value("Min15", avbt::Timeframe::Min15)
        .value("Min30", avbt::Timeframe::Min30)
        .value("Hour1", avbt::Timeframe::Hour1)
        .value("Hour4", avbt::Timeframe::Hour4)
        .value("Hour8", avbt::Timeframe::Hour8)
        .value("Hour12", avbt::Timeframe::Hour12)
        .value("Day1", avbt::Timeframe::Day1)
        .value("Week1", avbt::Timeframe::Week1)
        .value("Month1", avbt::Timeframe::Month1);
    m.def("timeframe_name", &avbt::name, py::arg("timeframe"));
    m.def("timeframe_seconds", &avbt::seconds, py::arg("timeframe"));

    py::class_<avbt::Bars>(m, "Bars")
        .def(py::init(&make_bars), py::arg("timeframe"), py::arg("ts"), py::arg("open"),
             py::arg("high"), py::arg("low"), py::arg("close"), py::arg("minutes_with_data"))
        .def_readonly("timeframe", &avbt::Bars::timeframe)
        // Copies of the columns, for charts and checks.
        .def_property_readonly("ts", [](const avbt::Bars& b) { return to_numpy(std::vector(b.ts)); })
        .def_property_readonly("open", [](const avbt::Bars& b) { return to_numpy(std::vector(b.open)); })
        .def_property_readonly("high", [](const avbt::Bars& b) { return to_numpy(std::vector(b.high)); })
        .def_property_readonly("low", [](const avbt::Bars& b) { return to_numpy(std::vector(b.low)); })
        .def_property_readonly("close", [](const avbt::Bars& b) { return to_numpy(std::vector(b.close)); })
        .def("__len__", [](const avbt::Bars& b) { return b.ts.size(); });

    py::class_<avbt::PortfolioSettings>(m, "PortfolioSettings")
        .def(py::init([](double starting_balance, double risk_per_trade, double hard_stop,
                         bool scale_risk_with_leverage) {
                 return avbt::PortfolioSettings{starting_balance, risk_per_trade, hard_stop,
                                                scale_risk_with_leverage};
             }),
             py::arg("starting_balance") = 10000.0, py::arg("risk_per_trade") = 0.01,
             py::arg("hard_stop") = 0.30, py::arg("scale_risk_with_leverage") = false)
        .def_readwrite("starting_balance", &avbt::PortfolioSettings::starting_balance)
        .def_readwrite("risk_per_trade", &avbt::PortfolioSettings::risk_per_trade)
        .def_readwrite("hard_stop", &avbt::PortfolioSettings::hard_stop)
        .def_readwrite("scale_risk_with_leverage", &avbt::PortfolioSettings::scale_risk_with_leverage);

    py::class_<avbt::StateTrend::Params>(m, "StateTrendParams")
        .def(py::init<>())
        .def_readwrite("average", &avbt::StateTrend::Params::average)
        .def_readwrite("breakout", &avbt::StateTrend::Params::breakout)
        .def_readwrite("atr_period", &avbt::StateTrend::Params::atr_period)
        .def_readwrite("atr_stops", &avbt::StateTrend::Params::atr_stops)
        .def_readwrite("reward", &avbt::StateTrend::Params::reward)
        .def_readwrite("leverage", &avbt::StateTrend::Params::leverage)
        .def_readwrite("fee_rate", &avbt::StateTrend::Params::fee_rate)
        .def_readwrite("flip", &avbt::StateTrend::Params::flip)
        .def_readwrite("min_stop", &avbt::StateTrend::Params::min_stop)
        .def_readwrite("trail_atrs", &avbt::StateTrend::Params::trail_atrs)
        .def_readwrite("take_fraction", &avbt::StateTrend::Params::take_fraction)
        .def_readwrite("signal", &avbt::StateTrend::Params::signal);

    using Codes = py::array_t<uint8_t, py::array::c_style | py::array::forcecast>;
    py::class_<avbt::States>(m, "States")
        .def(py::init([](int64_t start, const Codes& market, const Codes& trend, const Codes& volatility) {
                 return avbt::States{start,
                                     to_codes(market, avbt::MarketState::Transition, "market"),
                                     to_codes(trend, avbt::TrendState::MixedConflicted, "trend"),
                                     to_codes(volatility, avbt::VolatilityState::Shock, "volatility")};
             }),
             py::arg("start"), py::arg("market"), py::arg("trend"), py::arg("volatility"))
        .def_readonly("start", &avbt::States::start)
        .def("__len__", [](const avbt::States& s) { return s.market.size(); });

    m.def("late_day_short", &run<avbt::LateDayShort>, py::arg("bars"), py::arg("settings"));
    m.def("rally_short", &run<avbt::RallyShort>, py::arg("bars"), py::arg("settings"));
    m.def("campaign_short", &run<avbt::CampaignShort>, py::arg("bars"), py::arg("settings"));
    m.def("spike_short", &run<avbt::SpikeShort>, py::arg("bars"), py::arg("settings"));
    m.def("gold_trend_long", &run<avbt::GoldTrendLong>, py::arg("bars"), py::arg("settings"));
    m.def("campaign_and_spike", &run_many<avbt::Combined<avbt::CampaignShort, avbt::SpikeShort>>,
          py::arg("markets"), py::arg("settings"),
          py::arg("a_timeframe") = avbt::Timeframe::Hour1,
          py::arg("b_timeframe") = avbt::Timeframe::Hour1);
    m.def("late_day_and_rally", &run_many<avbt::Combined<avbt::LateDayShort, avbt::RallyShort>>,
          py::arg("markets"), py::arg("settings"),
          py::arg("a_timeframe") = avbt::Timeframe::Hour1,
          py::arg("b_timeframe") = avbt::Timeframe::Hour1);
    m.def("run_state_trend", &run_state_trend, py::arg("markets"), py::arg("settings"),
          py::arg("params") = avbt::StateTrend::Params{});
    m.def("optimize_state_trend", &optimize_state_trend, py::arg("markets"), py::arg("settings"),
          py::arg("start"), py::arg("knobs"), py::arg("rounds"));

    m.def("sma", [](const InArray& s, int period) {
        return to_numpy(avbt::sma(to_vector(s), period));
    }, py::arg("series"), py::arg("period"));

    m.def("pct_change", [](const InArray& s, int lag) {
        return to_numpy(avbt::pct_change(to_vector(s), lag));
    }, py::arg("series"), py::arg("lag"));

    m.def("prior_max", [](const InArray& s, int n) {
        return to_numpy(avbt::prior_max(to_vector(s), n));
    }, py::arg("series"), py::arg("n"));

    m.def("prior_min", [](const InArray& s, int n) {
        return to_numpy(avbt::prior_min(to_vector(s), n));
    }, py::arg("series"), py::arg("n"));

    m.def("true_range", [](const avbt::Bars& b) {
        return to_numpy(avbt::true_range(b));
    }, py::arg("bars"));

    m.def("atr", [](const avbt::Bars& b, int n) {
        return to_numpy(avbt::atr(b, n));
    }, py::arg("bars"), py::arg("n"));

    m.def("hour_of_day", [](const avbt::Bars& b) {
        return to_numpy(avbt::hour_of_day(b));
    }, py::arg("bars"));

    m.def("bar_change", [](const avbt::Bars& b, const std::string& now_field,
                           const std::string& then_field, int lag) {
        return to_numpy(avbt::bar_change(b, field_named(now_field), field_named(then_field), lag));
    }, py::arg("bars"), py::arg("now_field"), py::arg("then_field"), py::arg("lag"));

    m.def("chandelier", [](const avbt::Bars& b, int n, double k, const std::string& side) {
        return to_numpy(avbt::chandelier(b, n, k, side_named(side)));
    }, py::arg("bars"), py::arg("n"), py::arg("k"), py::arg("side"));
}
