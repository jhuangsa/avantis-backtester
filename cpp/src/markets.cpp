#include "avbt/markets.hpp"

#include <algorithm>
#include <stdexcept>

namespace avbt {

namespace {

// Throws when the bars are empty, their columns differ in length, or their timestamps do not rise.
void check_bars(const std::string& instrument, const Bars& b) {
    std::string where = instrument + ", " + name(b.timeframe) + ": ";
    std::size_t n = b.ts.size();
    if (n == 0) throw std::invalid_argument(where + "no bars");
    if (b.open.size() != n || b.high.size() != n || b.low.size() != n ||
        b.close.size() != n || b.minutes_with_data.size() != n ||
        (!b.volume.empty() && b.volume.size() != n)) {
        throw std::invalid_argument(where + "every Bars column must be the same size");
    }
    for (std::size_t i = 1; i < n; ++i) {
        if (b.ts[i] <= b.ts[i - 1]) throw std::invalid_argument(where + "timestamps must rise");
    }
}

}

Markets Markets::make(std::vector<Market> markets) {
    if (markets.empty()) throw std::invalid_argument("Markets needs at least one market");
    std::vector<int64_t> clock;
    for (std::size_t i = 0; i < markets.size(); ++i) {
        const Market& m = markets[i];
        if (m.timeframes.empty()) throw std::invalid_argument(m.instrument + ": no timeframes");
        for (std::size_t j = 0; j < i; ++j) {
            if (markets[j].instrument == m.instrument) {
                throw std::invalid_argument(m.instrument + ": two markets have this name");
            }
        }
        for (std::size_t k = 0; k < m.timeframes.size(); ++k) {
            check_bars(m.instrument, m.timeframes[k]);
            // Strictly finer to coarser, which also rules out a timeframe twice.
            if (k > 0 && m.timeframes[k].timeframe <= m.timeframes[k - 1].timeframe) {
                throw std::invalid_argument(m.instrument + ": timeframes must go finest first, each once");
            }
        }
        if (m.states) {
            const States& s = *m.states;
            if (s.trend.size() != s.market.size() || s.volatility.size() != s.market.size()) {
                throw std::invalid_argument(m.instrument + ": every States column must be the same size");
            }
            if (s.start % 60 != 0) throw std::invalid_argument(m.instrument + ": States start must be a whole minute");
        }
        const Bars& base = m.timeframes.front();
        if (base.timeframe != markets.front().timeframes.front().timeframe) {
            throw std::invalid_argument(m.instrument + ": base timeframe differs from the first market");
        }
        clock.insert(clock.end(), base.ts.begin(), base.ts.end());
    }
    std::sort(clock.begin(), clock.end());
    clock.erase(std::unique(clock.begin(), clock.end()), clock.end());
    return Markets(std::move(markets), std::move(clock));
}

const Market& Markets::find(const std::string& instrument) const {
    for (const Market& m : markets_) {
        if (m.instrument == instrument) return m;
    }
    throw std::invalid_argument("no market named " + instrument);
}

const Bars& Markets::at(const std::string& instrument, Timeframe tf) const {
    for (const Bars& b : find(instrument).timeframes) {
        if (b.timeframe == tf) return b;
    }
    throw std::invalid_argument(instrument + ": no bars on " + name(tf));
}

const Bars& Markets::base(const std::string& instrument) const {
    return find(instrument).timeframes.front();
}

const States* Markets::states(const std::string& instrument) const {
    const Market& m = find(instrument);
    return m.states ? &*m.states : nullptr;
}

int Markets::bar_at(const std::string& instrument, int t) const {
    const std::vector<int64_t>& ts = base(instrument).ts;
    auto it = std::lower_bound(ts.begin(), ts.end(), clock_[t]);
    if (it == ts.end() || *it != clock_[t]) return -1;
    return static_cast<int>(it - ts.begin());
}

Market& Markets::find(const std::string& instrument) {
    return const_cast<Market&>(static_cast<const Markets&>(*this).find(instrument));
}

void Markets::append(const std::string& instrument, Timeframe tf, const Bar& bar) {
    Market& m = find(instrument);
    Bars* b = nullptr;
    for (Bars& x : m.timeframes) {
        if (x.timeframe == tf) b = &x;
    }
    if (!b) throw std::invalid_argument(instrument + ": no bars on " + name(tf));
    std::string where = instrument + ", " + name(tf) + ": ";
    if (!b->ts.empty() && bar.ts <= b->ts.back()) {
        throw std::invalid_argument(where + "appended ts must be later than the last bar");
    }
    if (bar.volume.has_value() != !b->volume.empty()) {
        throw std::invalid_argument(where + "volume must be given exactly when the bars have volume");
    }
    bool is_base = b == &m.timeframes.front();
    bool extends = is_base && (clock_.empty() || bar.ts > clock_.back());
    if (is_base && !extends && !std::binary_search(clock_.begin(), clock_.end(), bar.ts)) {
        throw std::invalid_argument(where + "base bar ts is before the clock's end and not on the clock");
    }
    b->ts.push_back(bar.ts);
    b->open.push_back(bar.open);
    b->high.push_back(bar.high);
    b->low.push_back(bar.low);
    b->close.push_back(bar.close);
    b->minutes_with_data.push_back(bar.minutes_with_data);
    if (bar.volume) b->volume.push_back(*bar.volume);
    if (extends) clock_.push_back(bar.ts);
}

void Markets::append_states(const std::string& instrument, int64_t ts, const State& state) {
    Market& m = find(instrument);
    if (ts % 60 != 0) throw std::invalid_argument(instrument + ": States ts must be a whole minute");
    if (!m.states) m.states = States{ts, {}, {}, {}};
    States& s = *m.states;
    if (ts != s.start + 60 * static_cast<int64_t>(s.market.size())) {
        throw std::invalid_argument(instrument + ": States must continue one minute after the last row");
    }
    s.market.push_back(state.market);
    s.trend.push_back(state.trend);
    s.volatility.push_back(state.volatility);
}

}
