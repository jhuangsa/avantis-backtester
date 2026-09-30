#include "avbt/markets.hpp"

#include <stdexcept>

namespace avbt {

Markets Markets::make(std::vector<Market> markets) {
    if (markets.empty()) throw std::invalid_argument("Markets needs at least one market");
    const Bars& first = markets.front().bars;
    for (std::size_t i = 0; i < markets.size(); ++i) {
        const Market& m = markets[i];
        const Bars& b = m.bars;
        std::size_t n = b.ts.size();
        if (b.open.size() != n || b.high.size() != n || b.low.size() != n ||
            b.close.size() != n || b.minutes_with_data.size() != n) {
            throw std::invalid_argument(m.instrument + ": every Bars column must be the same size");
        }
        for (std::size_t j = 0; j < i; ++j) {
            if (markets[j].instrument == m.instrument) {
                throw std::invalid_argument(m.instrument + ": two markets have this name");
            }
        }
        if (b.bar_size_seconds != first.bar_size_seconds) {
            throw std::invalid_argument(m.instrument + ": bar size differs from the first market");
        }
        if (b.ts != first.ts) {
            throw std::invalid_argument(m.instrument + ": timestamps differ from the first market");
        }
    }
    return Markets(std::move(markets));
}

const Bars& Markets::at(const std::string& instrument) const {
    for (const Market& m : markets_) {
        if (m.instrument == instrument) return m.bars;
    }
    throw std::invalid_argument("no market named " + instrument);
}

}
