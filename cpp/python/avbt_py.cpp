// Python bindings for the avbt indicators.
// numpy arrays go in, numpy arrays come out. Inputs are copied into Bars
// or std::vector once; outputs are moved into a numpy array without a copy.

#include "avbt/indicators.hpp"
#include "avbt/optimize.hpp"
#include "avbt/run.hpp"
#include "avbt/strategies.hpp"
#include "avbt/version.hpp"

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <limits>
#include <map>
#include <optional>
#include <variant>
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
                     const py::array_t<int, py::array::c_style | py::array::forcecast>& minutes_with_data,
                     const std::optional<InArray>& volume) {
    avbt::Bars bars;
    bars.timeframe = timeframe;
    bars.ts = to_vector(ts);
    bars.open = to_vector(open);
    bars.high = to_vector(high);
    bars.low = to_vector(low);
    bars.close = to_vector(close);
    bars.minutes_with_data = to_vector(minutes_with_data);
    if (volume) bars.volume = to_vector(*volume);
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

// One Python param value as an avbt::Value. bool is checked before int,
// since a Python bool is an int. numpy numbers count: a numpy bool is a
// bool, any integer (numbers.Integral) an int, any other real a double. An
// integer too big for an int becomes a double.
avbt::Value to_value(const std::string& name, py::handle v) {
    py::module_ numbers = py::module_::import("numbers");
    bool numpy_bool = py::hasattr(v, "dtype") && py::str(v.attr("dtype").attr("kind")).cast<std::string>() == "b";
    if (py::isinstance<py::bool_>(v) || numpy_bool) return py::reinterpret_borrow<py::object>(v).attr("__bool__")().cast<bool>();
    if (py::isinstance<avbt::Timeframe>(v)) return v.cast<avbt::Timeframe>();
    if (py::isinstance(v, numbers.attr("Integral"))) {
        py::int_ i(py::reinterpret_borrow<py::object>(v));
        long long x = i.cast<long long>();
        if (x >= std::numeric_limits<int>::min() && x <= std::numeric_limits<int>::max()) return static_cast<int>(x);
        return py::float_(i).cast<double>();
    }
    if (py::isinstance(v, numbers.attr("Real"))) return py::float_(py::reinterpret_borrow<py::object>(v)).cast<double>();
    if (py::isinstance<py::str>(v)) return v.cast<std::string>();
    if (py::isinstance<py::dict>(v)) return v.cast<std::map<std::string, avbt::Timeframe>>();
    throw std::invalid_argument("param " + name + " has a type avbt cannot take");
}

py::object from_value(const avbt::Value& v) {
    return std::visit([](const auto& x) { return py::cast(x); }, v);
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

avbt::Result run(const std::string& name, const py::dict& params, const avbt::Markets& markets,
                 const avbt::MarketCosts& costs, const avbt::PortfolioSettings& settings) {
    avbt::Params ps;
    for (auto [k, v] : params) ps[k.cast<std::string>()] = to_value(k.cast<std::string>(), v);
    py::gil_scoped_release release;
    return avbt::run(name, ps, markets, costs, settings);
}

// A strategy from the table, prepared on `markets` and kept alive between decides.
struct PyLive {
    avbt::Live live;
    const avbt::Markets& markets;
    PyLive(const std::string& name, const py::dict& params, const avbt::Markets& m,
           const avbt::PortfolioSettings& settings)
        : markets(m) {
        avbt::Params ps;
        for (auto [k, v] : params) ps[k.cast<std::string>()] = to_value(k.cast<std::string>(), v);
        std::string known;
        for (const avbt::StrategyInfo& s : avbt::strategies()) {
            if (s.name == name) live = s.live(ps, settings);
            known += (known.empty() ? "" : ", ") + s.name;
        }
        if (!live.decide) throw std::invalid_argument("no strategy " + name + "; known: " + known);
        py::gil_scoped_release release;
        live.prepare(markets);
    }
    std::vector<avbt::Order> decide(int64_t now, const std::vector<avbt::Position>& positions,
                                    const std::optional<avbt::Report>& report) {
        py::gil_scoped_release release;
        live.update(markets);
        return live.decide(now, report.value_or(avbt::Report{}), positions);
    }
};

py::dict to_dict(const avbt::Params& ps) {
    py::dict d;
    for (const auto& [k, v] : ps) d[k.c_str()] = from_value(v);
    return d;
}

// avbt::optimize. knobs is {name: [values]}. The search runs without the
// GIL; progress(round, rounds, sharpe, best) takes it, and an exception it
// raises stops the search and reaches the caller.
py::dict optimize(const std::string& name, const py::dict& start, const py::dict& knobs,
                  const avbt::Markets& markets, const avbt::MarketCosts& costs,
                  const avbt::PortfolioSettings& settings, int rounds, const py::object& progress) {
    avbt::Params ps;
    for (auto [k, v] : start) ps[k.cast<std::string>()] = to_value(k.cast<std::string>(), v);
    std::vector<avbt::Knob> list;
    for (auto [k, values] : knobs) {
        avbt::Knob knob{k.cast<std::string>()};
        for (py::handle v : values) knob.values.push_back(to_value(knob.name, v));
        list.push_back(std::move(knob));
    }
    avbt::Progress call;
    if (!progress.is_none()) {
        call = [&](int r, int total, double sharpe, const avbt::Params& best) {
            py::gil_scoped_acquire gil;
            return progress(r, total, sharpe, to_dict(best)).cast<bool>();
        };
    }
    avbt::Search s;
    {
        py::gil_scoped_release release;
        s = avbt::optimize(name, ps, list, markets, costs, settings, rounds, call);
    }
    py::list runs;
    for (const avbt::Run& r : s.runs) {
        runs.append(py::dict(py::arg("round") = r.round, py::arg("params") = to_dict(r.params),
                             py::arg("sharpe") = r.sharpe));
    }
    return py::dict(py::arg("best") = to_dict(s.best), py::arg("sharpe") = s.sharpe,
                    py::arg("runs") = runs, py::arg("timeframe") = s.timeframe);
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
             py::arg("high"), py::arg("low"), py::arg("close"), py::arg("minutes_with_data"),
             py::arg("volume") = py::none())
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
                         bool scale_risk_with_leverage, int64_t state_delay) {
                 return avbt::PortfolioSettings{starting_balance, risk_per_trade, hard_stop,
                                                scale_risk_with_leverage, state_delay};
             }),
             py::arg("starting_balance") = 10000.0, py::arg("risk_per_trade") = 0.01,
             py::arg("hard_stop") = 0.30, py::arg("scale_risk_with_leverage") = false,
             py::arg("state_delay") = 60)
        .def_readwrite("starting_balance", &avbt::PortfolioSettings::starting_balance)
        .def_readwrite("risk_per_trade", &avbt::PortfolioSettings::risk_per_trade)
        .def_readwrite("hard_stop", &avbt::PortfolioSettings::hard_stop)
        .def_readwrite("scale_risk_with_leverage", &avbt::PortfolioSettings::scale_risk_with_leverage)
        .def_readwrite("state_delay", &avbt::PortfolioSettings::state_delay);

    py::class_<avbt::StateTrend::Params>(m, "StateTrendParams")
        .def(py::init<>())
        .def_readwrite("average", &avbt::StateTrend::Params::average)
        .def_readwrite("breakout", &avbt::StateTrend::Params::breakout)
        .def_readwrite("atr_period", &avbt::StateTrend::Params::atr_period)
        .def_readwrite("atr_stops", &avbt::StateTrend::Params::atr_stops)
        .def_readwrite("reward", &avbt::StateTrend::Params::reward)
        .def_readwrite("leverage", &avbt::StateTrend::Params::leverage)
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

    py::enum_<avbt::Side>(m, "Side").value("Long", avbt::Side::Long).value("Short", avbt::Side::Short);
    py::enum_<avbt::Cause>(m, "Cause")
        .value("Stop", avbt::Cause::Stop)
        .value("TakeProfit", avbt::Cause::TakeProfit)
        .value("Liquidation", avbt::Cause::Liquidation)
        .value("HardStop", avbt::Cause::HardStop)
        .value("Order", avbt::Cause::Order)
        .value("EndOfData", avbt::Cause::EndOfData)
        .value("TrailingStop", avbt::Cause::TrailingStop)
        .value("PartialTakeProfit", avbt::Cause::PartialTakeProfit);

    py::class_<avbt::Bar>(m, "Bar")
        .def(py::init([](int64_t ts, double open, double high, double low, double close, int minutes_with_data,
                         std::optional<double> volume) {
                 return avbt::Bar{ts, open, high, low, close, minutes_with_data, volume};
             }),
             py::arg("ts"), py::arg("open"), py::arg("high"), py::arg("low"), py::arg("close"),
             py::arg("minutes_with_data"), py::arg("volume") = py::none());

    py::class_<avbt::State>(m, "State")
        .def(py::init([](uint8_t market, uint8_t trend, uint8_t volatility) {
                 auto code = [](uint8_t c, auto last, const char* what) {
                     return to_codes(Codes(py::array_t<uint8_t>(1, &c)), last, what)[0];
                 };
                 return avbt::State{code(market, avbt::MarketState::Transition, "market"),
                                    code(trend, avbt::TrendState::MixedConflicted, "trend"),
                                    code(volatility, avbt::VolatilityState::Shock, "volatility")};
             }),
             py::arg("market") = 0, py::arg("trend") = 0, py::arg("volatility") = 0);

    py::class_<avbt::Order> order(m, "Order");
    py::enum_<avbt::Order::Kind>(order, "Kind")
        .value("Open", avbt::Order::Kind::Open)
        .value("Close", avbt::Order::Kind::Close);
    order.def(py::init<>())
        .def_readwrite("kind", &avbt::Order::kind)
        .def_readwrite("instrument", &avbt::Order::instrument)
        .def_readwrite("side", &avbt::Order::side)
        .def_readwrite("stop_distance", &avbt::Order::stop_distance)
        .def_readwrite("take_profit_distance", &avbt::Order::take_profit_distance)
        .def_readwrite("leverage", &avbt::Order::leverage)
        .def_readwrite("trail_distance", &avbt::Order::trail_distance)
        .def_readwrite("take_profit_fraction", &avbt::Order::take_profit_fraction);

    py::class_<avbt::Position>(m, "Position")
        .def(py::init([](std::string instrument, avbt::Side side, double entry_price, double size,
                             std::optional<int64_t> entry_time) {
                 avbt::Position p;
                 p.entry_time = entry_time.value_or(avbt::unknown_time);
                 p.instrument = std::move(instrument);
                 p.side = side;
                 p.entry_price = entry_price;
                 p.size = size;
                 return p;
             }),
             py::arg("instrument"), py::arg("side"), py::arg("entry_price") = 0.0, py::arg("size") = 0.0,
             py::arg("entry_time") = py::none())
        .def_readwrite("instrument", &avbt::Position::instrument)
        .def_readwrite("side", &avbt::Position::side)
        .def_readwrite("entry_price", &avbt::Position::entry_price)
        .def_readwrite("size", &avbt::Position::size)
        // None when the entry time is unknown.
        .def_property("entry_time",
                      [](const avbt::Position& p) -> std::optional<int64_t> {
                          if (p.entry_time == avbt::unknown_time) return std::nullopt;
                          return p.entry_time;
                      },
                      [](avbt::Position& p, std::optional<int64_t> t) { p.entry_time = t.value_or(avbt::unknown_time); });

    py::class_<avbt::Report>(m, "Report")
        .def(py::init<>())
        .def_readwrite("balance", &avbt::Report::balance)
        .def_readwrite("equity", &avbt::Report::equity)
        .def_readwrite("free_cash", &avbt::Report::free_cash)
        .def_readwrite("open_positions", &avbt::Report::open_positions)
        .def_readwrite("halted", &avbt::Report::halted);

    py::class_<avbt::Market>(m, "Market")
        .def(py::init([](std::string instrument, std::vector<avbt::Bars> timeframes,
                         std::optional<avbt::States> states) {
                 return avbt::Market{std::move(instrument), std::move(timeframes), std::move(states)};
             }),
             py::arg("instrument"), py::arg("timeframes"), py::arg("states") = py::none())
        .def_readonly("instrument", &avbt::Market::instrument)
        .def_readonly("timeframes", &avbt::Market::timeframes)
        .def_readonly("states", &avbt::Market::states);

    py::class_<avbt::Markets>(m, "Markets")
        .def(py::init(&avbt::Markets::make), py::arg("markets"))
        .def_property_readonly("clock", [](const avbt::Markets& x) { return to_numpy(std::vector(x.clock())); })
        .def_property_readonly("timeframe", &avbt::Markets::timeframe)
        .def("append", &avbt::Markets::append, py::arg("instrument"), py::arg("timeframe"), py::arg("bar"))
        .def("append_states", &avbt::Markets::append_states, py::arg("instrument"), py::arg("ts"),
             py::arg("state"))
        .def_property_readonly("instruments", [](const avbt::Markets& x) {
            std::vector<std::string> out;
            for (const avbt::Market& mk : x.all()) out.push_back(mk.instrument);
            return out;
        });

    py::class_<avbt::Costs>(m, "Costs")
        .def(py::init([](double open_fee, double close_fee, const std::optional<InArray>& hold_long,
                         const std::optional<InArray>& hold_short) {
                 return avbt::Costs{open_fee, close_fee, hold_long ? to_vector(*hold_long) : std::vector<double>{},
                                    hold_short ? to_vector(*hold_short) : std::vector<double>{}};
             }),
             py::arg("open_fee"), py::arg("close_fee"), py::arg("hold_long") = py::none(),
             py::arg("hold_short") = py::none())
        .def_readonly("open_fee", &avbt::Costs::open_fee)
        .def_readonly("close_fee", &avbt::Costs::close_fee)
        .def_property_readonly("hold_long", [](const avbt::Costs& c) { return to_numpy(std::vector(c.hold_long)); })
        .def_property_readonly("hold_short", [](const avbt::Costs& c) { return to_numpy(std::vector(c.hold_short)); });

    py::class_<avbt::Trade>(m, "Trade")
        .def_readonly("instrument", &avbt::Trade::instrument)
        .def_readonly("entry_bar", &avbt::Trade::entry_bar)
        .def_readonly("exit_bar", &avbt::Trade::exit_bar)
        .def_readonly("entry_time", &avbt::Trade::entry_time)
        .def_readonly("exit_time", &avbt::Trade::exit_time)
        .def_readonly("side", &avbt::Trade::side)
        .def_readonly("entry_price", &avbt::Trade::entry_price)
        .def_readonly("exit_price", &avbt::Trade::exit_price)
        .def_readonly("size", &avbt::Trade::size)
        .def_readonly("leverage", &avbt::Trade::leverage)
        .def_readonly("fees", &avbt::Trade::fees)
        .def_readonly("holding_costs", &avbt::Trade::holding_costs)
        .def_readonly("result", &avbt::Trade::result)
        .def_readonly("cause", &avbt::Trade::cause);

    py::class_<avbt::Result>(m, "Result")
        .def(py::init([](const InArray& equity, avbt::Timeframe timeframe, const py::array_t<int64_t>& clock) {
                 return avbt::Result{.equity = std::vector<double>(equity.data(), equity.data() + equity.size()),
                                     .timeframe = timeframe,
                                     .clock = std::vector<int64_t>(clock.data(), clock.data() + clock.size())};
             }),
             py::arg("equity"), py::arg("timeframe"), py::arg("clock"))
        .def_readonly("trades", &avbt::Result::trades)
        .def_property_readonly("equity", [](const avbt::Result& r) { return to_numpy(std::vector(r.equity)); })
        .def_readonly("ending_balance", &avbt::Result::ending_balance)
        .def_readonly("timeframe", &avbt::Result::timeframe)
        .def_property_readonly("clock", [](const avbt::Result& r) { return to_numpy(std::vector(r.clock)); })
        .def_readonly("version", &avbt::Result::version);

    py::class_<avbt::Param>(m, "Param")
        .def_readonly("name", &avbt::Param::name)
        .def_property_readonly("default", [](const avbt::Param& p) { return from_value(p.value); })
        .def_readonly("min", &avbt::Param::min)
        .def_readonly("max", &avbt::Param::max)
        .def_property_readonly("type", [](const avbt::Param& p) {
            const char* names[] = {"bool", "int", "float", "str", "timeframe", "timeframes"};
            return names[p.value.index()];
        });

    py::class_<avbt::StrategyInfo>(m, "StrategyInfo")
        .def_readonly("name", &avbt::StrategyInfo::name)
        .def_readonly("params", &avbt::StrategyInfo::params)
        .def_readonly("timeframes", &avbt::StrategyInfo::timeframes);

    py::class_<PyLive>(m, "Live")
        .def(py::init<const std::string&, const py::dict&, const avbt::Markets&, const avbt::PortfolioSettings&>(),
             py::arg("name"), py::arg("params"), py::arg("markets"),
             py::arg("settings") = avbt::PortfolioSettings{}, py::keep_alive<1, 4>())
        .def("decide", &PyLive::decide, py::arg("now"), py::arg("positions"), py::arg("report") = py::none());

    m.def("strategies", &avbt::strategies, py::return_value_policy::reference);
    m.def("run", &run, py::arg("name"), py::arg("params") = py::dict(), py::arg("markets"), py::arg("costs"),
          py::arg("settings") = avbt::PortfolioSettings{});
    m.attr("version") = avbt::version;
    m.def("sharpe", &avbt::sharpe, py::arg("result"));
    m.def("optimize", &optimize, py::arg("name"), py::arg("start"), py::arg("knobs"), py::arg("markets"),
          py::arg("costs"), py::arg("settings"), py::arg("rounds"), py::arg("progress") = py::none());
    m.def("summary", [](const avbt::Result& r) {
        avbt::Summary s = avbt::summary(r);
        return py::dict(py::arg("sharpe") = s.sharpe, py::arg("total_return") = s.total_return,
                        py::arg("max_drawdown") = s.max_drawdown, py::arg("trades") = s.trades);
    }, py::arg("result"));

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
