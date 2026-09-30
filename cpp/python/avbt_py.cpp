// Python bindings for the avbt indicators.
// numpy arrays go in, numpy arrays come out. Inputs are copied into Bars
// or std::vector once; outputs are moved into a numpy array without a copy.

#include "avbt/indicators.hpp"
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
py::array_t<double> to_numpy(std::vector<double>&& v) {
    auto* owned = new std::vector<double>(std::move(v));
    py::capsule free_when_done(owned, [](void* p) {
        delete static_cast<std::vector<double>*>(p);
    });
    return py::array_t<double>(owned->size(), owned->data(), free_when_done);
}

avbt::Bars make_bars(int bar_size_seconds,
                     const py::array_t<int64_t, py::array::c_style | py::array::forcecast>& ts,
                     const InArray& open, const InArray& high, const InArray& low,
                     const InArray& close,
                     const py::array_t<int, py::array::c_style | py::array::forcecast>& minutes_with_data) {
    avbt::Bars bars;
    bars.bar_size_seconds = bar_size_seconds;
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
    out["bar_size_seconds"] = r.bar_size_seconds;
    return out;
}

// A strategy with its default params on one market, named after its instrument.
template <class S>
py::dict run(const avbt::Bars& bars, const avbt::PortfolioSettings& settings) {
    S strategy;
    return run_markets(strategy, avbt::Markets::make({{strategy.params.instrument, bars}}), settings);
}

// Several markets, given as {instrument: Bars}, all on one clock.
template <class S>
py::dict run_many(const std::map<std::string, avbt::Bars>& bars,
                  const avbt::PortfolioSettings& settings) {
    std::vector<avbt::Market> markets;
    for (const auto& [name, b] : bars) markets.push_back({name, b});
    S strategy;
    return run_markets(strategy, avbt::Markets::make(std::move(markets)), settings);
}

}  // namespace

PYBIND11_MODULE(avbt_cpp, m) {
    m.doc() = "avbt C++ indicators";

    py::class_<avbt::Bars>(m, "Bars")
        .def(py::init(&make_bars), py::arg("bar_size_seconds"), py::arg("ts"), py::arg("open"),
             py::arg("high"), py::arg("low"), py::arg("close"), py::arg("minutes_with_data"))
        .def_readonly("bar_size_seconds", &avbt::Bars::bar_size_seconds)
        .def("__len__", [](const avbt::Bars& b) { return b.ts.size(); });

    py::class_<avbt::PortfolioSettings>(m, "PortfolioSettings")
        .def(py::init([](double starting_balance, double risk_per_trade, double hard_stop) {
                 return avbt::PortfolioSettings{starting_balance, risk_per_trade, hard_stop};
             }),
             py::arg("starting_balance") = 10000.0, py::arg("risk_per_trade") = 0.01,
             py::arg("hard_stop") = 0.30)
        .def_readwrite("starting_balance", &avbt::PortfolioSettings::starting_balance)
        .def_readwrite("risk_per_trade", &avbt::PortfolioSettings::risk_per_trade)
        .def_readwrite("hard_stop", &avbt::PortfolioSettings::hard_stop);

    m.def("late_day_short", &run<avbt::LateDayShort>, py::arg("bars"), py::arg("settings"));
    m.def("rally_short", &run<avbt::RallyShort>, py::arg("bars"), py::arg("settings"));
    m.def("campaign_short", &run<avbt::CampaignShort>, py::arg("bars"), py::arg("settings"));
    m.def("spike_short", &run<avbt::SpikeShort>, py::arg("bars"), py::arg("settings"));
    m.def("gold_trend_long", &run<avbt::GoldTrendLong>, py::arg("bars"), py::arg("settings"));
    m.def("campaign_and_spike", &run_many<avbt::Combined<avbt::CampaignShort, avbt::SpikeShort>>,
          py::arg("markets"), py::arg("settings"));
    m.def("late_day_and_rally", &run_many<avbt::Combined<avbt::LateDayShort, avbt::RallyShort>>,
          py::arg("markets"), py::arg("settings"));

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
