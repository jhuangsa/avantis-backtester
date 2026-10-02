// Replays a backtest step by step as a live caller would: start from the
// first bar, prepare, then append each closed bar and state, update, and
// decide with the positions the backtest had. The orders must be equal at
// every step. Bars arrive only once closed, so a strategy that looks ahead
// gives different orders. Returns 0 when every check passes.

#include "avbt/run.hpp"
#include "avbt/strategies.hpp"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace {

using namespace avbt;
int failures = 0;

// A deterministic random walk of n minute closes.
std::vector<double> walk(int n, double start, double step, unsigned seed) {
    std::vector<double> out;
    double x = start;
    for (int i = 0; i < n; ++i) {
        seed = seed * 1103515245u + 12345u;
        x *= 1.0 + step * (static_cast<double>((seed >> 16) % 2001) / 1000.0 - 1.0);
        out.push_back(x);
    }
    return out;
}

// Bars on tf built from minute closes starting at UTC second 0.
Bars bars_of(const std::vector<double>& minutes, Timeframe tf) {
    Bars b;
    b.timeframe = tf;
    size_t k = static_cast<size_t>(seconds(tf) / 60);
    for (size_t i = 0; i + k <= minutes.size(); i += k) {
        double prev = i ? minutes[i - 1] : minutes[0];
        b.ts.push_back(static_cast<int64_t>(i) * 60);
        b.open.push_back(prev);
        b.high.push_back(std::max(prev, *std::max_element(minutes.begin() + i, minutes.begin() + i + k)));
        b.low.push_back(std::min(prev, *std::min_element(minutes.begin() + i, minutes.begin() + i + k)));
        b.close.push_back(minutes[i + k - 1]);
        b.minutes_with_data.push_back(static_cast<int>(k));
    }
    return b;
}

Market market(const std::string& name, const std::vector<double>& minutes, std::vector<Timeframe> tfs,
              bool with_states, unsigned seed) {
    Market m{name, {}};
    for (Timeframe tf : tfs) m.timeframes.push_back(bars_of(minutes, tf));
    if (with_states) {
        States s;
        State now;
        for (size_t i = 0; i < minutes.size(); ++i) {
            seed = seed * 1103515245u + 12345u;
            if ((seed >> 16) % 20 == 0) {
                unsigned r = seed >> 20;
                now = {static_cast<MarketState>(r % 14), static_cast<TrendState>((r / 14) % 5),
                       static_cast<VolatilityState>((r / 70) % 8)};
            }
            s.market.push_back(now.market);
            s.trend.push_back(now.trend);
            s.volatility.push_back(now.volatility);
        }
        m.states = s;
    }
    return m;
}

bool same(double a, double b) { return std::memcmp(&a, &b, sizeof a) == 0; }
bool same(const Order& a, const Order& b) {
    return a.kind == b.kind && a.instrument == b.instrument && a.side == b.side &&
           same(a.stop_distance, b.stop_distance) && same(a.take_profit_distance, b.take_profit_distance) &&
           same(a.leverage, b.leverage) && same(a.trail_distance, b.trail_distance) &&
           same(a.take_profit_fraction, b.take_profit_fraction);
}

struct Step {
    std::vector<Position> positions;
    std::vector<Order> orders;
};

template <class S>
Result replay(const std::string& name, const S& fresh, const std::vector<Market>& full,
              PortfolioSettings settings = {}) {
    Markets all = Markets::make(full);
    MarketCosts costs;
    for (const Market& m : full) costs[m.instrument] = Costs{.open_fee = 0.0001, .close_fee = 0.0001};
    S s = fresh;
    std::vector<Step> steps;
    Result r = backtest(s, all, costs, settings,
                        [&](int, const std::vector<Position>& p, const std::vector<Order>& o) {
                            steps.push_back({p, o});
                        });

    // Live: every timeframe starts with its first bar, states with their first row.
    std::vector<Market> first;
    for (const Market& m : full) {
        Market f{m.instrument, {}};
        for (const Bars& b : m.timeframes) {
            Bars one{.timeframe = b.timeframe, .ts = {b.ts[0]}, .open = {b.open[0]}, .high = {b.high[0]},
                     .low = {b.low[0]}, .close = {b.close[0]}, .minutes_with_data = {b.minutes_with_data[0]}};
            f.timeframes.push_back(one);
        }
        if (m.states) f.states = States{m.states->start, {m.states->market[0]}, {m.states->trend[0]},
                                        {m.states->volatility[0]}};
        first.push_back(f);
    }
    Markets live = Markets::make(first);
    Live l = live_of(fresh, settings);
    l.prepare(live);
    int orders = 0;
    for (size_t t = 0; t < steps.size(); ++t) {
        int64_t now = all.clock()[t] + seconds(all.timeframe());
        for (size_t k = 0; k < full.size(); ++k) {
            const Market& m = full[k];
            for (const Bars& b : m.timeframes) {
                for (size_t i = live.at(m.instrument, b.timeframe).ts.size();
                     i < b.ts.size() && b.ts[i] + seconds(b.timeframe) <= now; ++i) {
                    live.append(m.instrument, b.timeframe,
                                Bar{b.ts[i], b.open[i], b.high[i], b.low[i], b.close[i], b.minutes_with_data[i]});
                }
            }
            if (!m.states) continue;
            const States& st = *m.states;
            for (size_t i = live.states(m.instrument)->market.size();
                 i < st.market.size() && st.start + static_cast<int64_t>(i) * 60 + 60 <= now; ++i) {
                live.append_states(m.instrument, st.start + static_cast<int64_t>(i) * 60,
                                   State{st.market[i], st.trend[i], st.volatility[i]});
            }
        }
        l.update(live);
        std::vector<Order> got = l.decide(now, Report{}, steps[t].positions);
        const std::vector<Order>& want = steps[t].orders;
        bool ok = got.size() == want.size();
        for (size_t i = 0; ok && i < got.size(); ++i) ok = same(got[i], want[i]);
        if (!ok) {
            ++failures;
            std::printf("FAIL %s: orders differ at step %zu\n", name.c_str(), t);
            return r;
        }
        orders += static_cast<int>(want.size());
    }
    if (r.trades.empty() || orders == 0) {
        ++failures;
        std::printf("FAIL %s: no trades to compare\n", name.c_str());
    }
    return r;
}

}

int main() {
    // Veranta: hourly bars over 40 days, 15:00 UTC start so hour 16 occurs.
    int n = 40 * 24 * 60;
    std::vector<Market> veranta;
    unsigned seed = 1;
    for (const char* name : {"ZORA", "AVNT", "DYM", "GOLD"}) {
        veranta.push_back(market(name, walk(n, 100, 0.004, seed++), {Timeframe::Hour1}, false, 0));
    }
    LateDayShort late;
    late.params.lag = 3;
    late.params.rise = 0.01;
    replay("LateDayShort", late, veranta);
    RallyShort rally;
    rally.params.rise = 0.02;
    replay("RallyShort", rally, veranta);
    CampaignShort campaign;
    campaign.params.rise = 0.03;
    campaign.params.take_profit = 0.03;
    campaign.params.stop = 0.03;
    replay("CampaignShort", campaign, veranta);
    SpikeShort spike;
    spike.params.spike = 0.01;
    replay("SpikeShort", spike, veranta);
    replay("GoldTrendLong", GoldTrendLong{}, veranta);
    replay("Combined", Combined<CampaignShort, SpikeShort>{campaign, spike, {}, {}}, veranta);

    // StateTrend on two markets, 1-minute and 1-hour bars, over 5 days.
    int m = 5 * 24 * 60;
    std::vector<Market> both = {
        market("BTC", walk(m, 60000, 0.002, 7), {Timeframe::Min1, Timeframe::Hour1}, true, 11),
        market("ETH", walk(m, 3000, 0.002, 8), {Timeframe::Min1, Timeframe::Hour1}, true, 12)};
    StateTrend trend;
    trend.params.average = 5;
    trend.params.atr_period = 5;
    trend.params.breakout = 10;
    Result base = replay("StateTrend", trend, both);
    // Labels read 7 minutes back: live must match, and the trades must move.
    Result late7 = replay("StateTrend delay 420", trend, both, PortfolioSettings{.state_delay = 420});
    bool moved = base.trades.size() != late7.trades.size() ||
                 base.trades[0].entry_time != late7.trades[0].entry_time;
    if (!moved) {
        ++failures;
        std::printf("FAIL StateTrend delay 420: same trades as delay 60\n");
    }

    if (failures == 0) std::printf("test_live: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
