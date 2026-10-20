#pragma once
#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "avbt/markets.hpp"
#include "avbt/portfolio.hpp"
#include "avbt/spreads.hpp"

namespace avbt {

// What one market charges. Costs belong to the market, not the strategy;
// ADR 0016. Fees are fractions of notional at the open and at the close
// fill. hold_long and hold_short hold one value per base bar: the cost of
// holding through that bar, as a fraction of notional, signed: positive
// pays, negative receives. NaN is zero. Empty means no holding cost.
// profit_share holds the tiers of a venue that takes a share of each
// winning close instead of size fees, such as Upside Perps; empty means
// none. ADR 0019.
// spread is the half-spread each fill pays, as a fraction of price: a long
// fills above the oracle price, a short below. nullopt uses Veranta's row
// for the market (spreads.hpp). A value is the caller's: empty for none,
// one value for every bar, or one value per base bar. max_spread caps it;
// NaN is no cap, or the row's cap when spread is nullopt. ADR 0020.
struct Costs {
    double open_fee = 0.0;
    double close_fee = 0.0;
    std::vector<double> hold_long, hold_short;
    std::vector<Tier> profit_share;
    std::optional<std::vector<double>> spread;
    double max_spread = std::numeric_limits<double>::quiet_NaN();
};

// No spread: write .spread = avbt::no_spread. Note .spread = {} is
// nullopt, which uses the table.
inline const std::vector<double> no_spread{};

// A market's half-spread per base bar, read from its Costs.
struct Spread {
    std::vector<double> values;
    double cap = std::numeric_limits<double>::quiet_NaN();
    // The half-spread on base bar i: 0 with no values; NaN cap is none.
    double at(int i) const {
        if (values.empty()) return 0.0;
        double s = values.size() == 1 ? values[0] : values[i];
        return std::isnan(cap) ? s : std::min(s, cap);
    }
};

// The caller's spread, else the table's row for `market`. Throws
// std::invalid_argument when neither exists.
inline Spread spread_of(const std::string& market, const Costs& c) {
    if (c.spread) return {*c.spread, c.max_spread};
    auto row = veranta_spread(market);
    if (!row) throw std::invalid_argument("market " + market + " has no spread in the table; pass one");
    return {{row->spread}, std::isnan(c.max_spread) ? row->max_spread : c.max_spread};
}

// Throws std::invalid_argument when a spread value is not finite and in
// [0, 1), or max_spread is neither NaN nor finite and at least 0.
inline void check_spread(const Costs& c) {
    if (!std::isnan(c.max_spread) && !(std::isfinite(c.max_spread) && c.max_spread >= 0.0)) {
        throw std::invalid_argument("max_spread must be NaN or at least 0");
    }
    if (!c.spread) return;
    for (double s : *c.spread) {
        if (!(std::isfinite(s) && s >= 0.0 && s < 1.0)) {
            throw std::invalid_argument("spread values must be in [0, 1), got " + std::to_string(s));
        }
    }
}

// Throws std::invalid_argument when profit-share tiers sit beside a size
// fee, or a tier's max_roi is not positive and strictly increasing, or its
// share is outside [0, 1], or a value is not finite.
inline void check_profit_share(const Costs& c) {
    if (c.profit_share.empty()) return;
    if (c.open_fee != 0.0 || c.close_fee != 0.0) {
        throw std::invalid_argument("profit share and size fees do not mix");
    }
    double last = 0.0;
    for (const Tier& t : c.profit_share) {
        if (!std::isfinite(t.max_roi) || t.max_roi <= last) {
            throw std::invalid_argument("profit share max_roi must be positive and increasing");
        }
        if (!(t.share >= 0.0 && t.share <= 1.0)) {
            throw std::invalid_argument("profit share must be in [0, 1]");
        }
        last = t.max_roi;
    }
}

using MarketCosts = std::map<std::string, Costs>;

// Throws std::invalid_argument when a market has no Costs, a Costs names
// no market, a holding column is neither empty nor one value per base bar,
// the profit share fails check_profit_share, the spread fails check_spread,
// a spread column is not of size 0, 1 or one per base bar, or a market with
// no spread has no row in the table.
inline void check_costs(const Markets& markets, const MarketCosts& costs) {
    for (const Market& m : markets.all()) {
        auto it = costs.find(m.instrument);
        if (it == costs.end()) throw std::invalid_argument("market " + m.instrument + " has no Costs");
        check_profit_share(it->second);
        check_spread(it->second);
        spread_of(m.instrument, it->second);
        std::size_t n = m.timeframes.front().ts.size();
        const auto& spread = it->second.spread;
        if (spread && spread->size() > 1 && spread->size() != n) {
            throw std::invalid_argument("market " + m.instrument + ": spread has " +
                                        std::to_string(spread->size()) + " values, base bars " +
                                        std::to_string(n));
        }
        for (const auto* hold : {&it->second.hold_long, &it->second.hold_short}) {
            if (!hold->empty() && hold->size() != n) {
                throw std::invalid_argument("market " + m.instrument + ": holding costs have " +
                                            std::to_string(hold->size()) + " values, base bars " +
                                            std::to_string(n));
            }
        }
    }
    for (const auto& [name, c] : costs) {
        bool known = false;
        for (const Market& m : markets.all()) known = known || m.instrument == name;
        if (!known) throw std::invalid_argument("Costs for " + name + ", which is not a market");
    }
}

}
