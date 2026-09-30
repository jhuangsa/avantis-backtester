// Python bindings for the avbt indicators.
// numpy arrays go in, numpy arrays come out. Inputs are copied into Bars
// or std::vector once; outputs are moved into a numpy array without a copy.

#include "avbt/indicators.hpp"

#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>

#include <stdexcept>
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

avbt::Bars make_bars(int bar_seconds,
                     const py::array_t<int64_t, py::array::c_style | py::array::forcecast>& ts,
                     const InArray& open, const InArray& high, const InArray& low,
                     const InArray& close,
                     const py::array_t<int, py::array::c_style | py::array::forcecast>& minutes) {
    avbt::Bars bars;
    bars.bar_seconds = bar_seconds;
    bars.ts = to_vector(ts);
    bars.open = to_vector(open);
    bars.high = to_vector(high);
    bars.low = to_vector(low);
    bars.close = to_vector(close);
    bars.minutes = to_vector(minutes);
    const std::size_t n = bars.ts.size();
    if (bars.open.size() != n || bars.high.size() != n || bars.low.size() != n ||
        bars.close.size() != n || bars.minutes.size() != n) {
        throw std::invalid_argument("every Bars column must be the same size");
    }
    return bars;
}

}  // namespace

PYBIND11_MODULE(avbt_cpp, m) {
    m.doc() = "avbt C++ indicators";

    py::class_<avbt::Bars>(m, "Bars")
        .def(py::init(&make_bars), py::arg("bar_seconds"), py::arg("ts"), py::arg("open"),
             py::arg("high"), py::arg("low"), py::arg("close"), py::arg("minutes"))
        .def_readonly("bar_seconds", &avbt::Bars::bar_seconds)
        .def("__len__", [](const avbt::Bars& b) { return b.ts.size(); });

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
}
