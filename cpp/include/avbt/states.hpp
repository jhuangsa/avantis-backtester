#pragma once
#include <cstdint>
#include <vector>

#include "avbt/indicators.hpp"

namespace avbt {

// Three labels for each minute of a market, computed outside the engine.
// The codes are fixed: Python writes them as uint8.
enum class MarketState : uint8_t {
    Unknown = 0, MeanReversion, VolatilityCompression, Mixed, Consolidation, Breakout,
    TrendingUp, TrendingDown, FailedBreakout, ShockStress, ChoppyRange, StableLowEnergy,
    VolatilityExpansion, Transition,
};
enum class TrendState : uint8_t { Unknown = 0, Uptrend, Downtrend, NonTrending, MixedConflicted };
enum class VolatilityState : uint8_t {
    Unknown = 0, Low, Normal, High, Extreme, Compression, Expansion, Shock,
};

// One row per minute from start (UTC seconds, a whole minute), no holes.
// Python has already filled short gaps and set the rest to Unknown.
struct States {
    int64_t start = 0;
    std::vector<MarketState> market;
    std::vector<TrendState> trend;
    std::vector<VolatilityState> volatility;
};

// The three labels of one minute.
struct State {
    MarketState market = MarketState::Unknown;
    TrendState trend = TrendState::Unknown;
    VolatilityState volatility = VolatilityState::Unknown;
};

// The labels a strategy may read at `now`: the row stamped one minute
// before. At 10:00 it sees the 9:59 label, whose minute closed at 10:00, so
// there is no lookahead whether or not a label uses its own minute. All
// Unknown before the first row or past the last. `delay` (seconds, a whole
// number of minutes, at least 60) reads an older row, for labels that
// arrive late; PortfolioSettings::state_delay.
inline State state_at(const States& s, int64_t now, int64_t delay = 60) {
    int64_t at = now - delay - s.start;
    if (at < 0) return {};
    std::size_t row = static_cast<std::size_t>(at / 60);
    if (row >= s.market.size()) return {};
    return {s.market[row], s.trend[row], s.volatility[row]};
}

}
