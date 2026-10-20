// Tests for the backtest loop in avbt/backtest.hpp, with a test-only strategy.
// The program returns 0 when every check passes and 1 when any check fails.

#include "avbt/backtest.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <map>
#include <stdexcept>
#include <cstdio>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace {

int failures = 0;

constexpr double kTolerance = 1e-9;

void check_value(const std::string& label, double actual, double expected) {
    if (std::abs(actual - expected) >= kTolerance) {
        ++failures;
        std::printf("FAIL %s: expected %f, got %f\n", label.c_str(), expected, actual);
    }
}

void check_true(const std::string& label, bool condition) {
    if (!condition) {
        ++failures;
        std::printf("FAIL %s\n", label.c_str());
    }
}

using namespace avbt;

// Every market at the given fee, no holding costs.
MarketCosts flat_costs(const Markets& m, double fee = 0.0) {
    MarketCosts c;
    for (const Market& x : m.all()) c[x.instrument] = Costs{.open_fee = fee, .close_fee = fee, .spread = no_spread};
    return c;
}

// The old three-argument call: every market free.
template <class S>
Result backtest(S& s, const Markets& m, PortfolioSettings settings) {
    return avbt::backtest(s, m, flat_costs(m), settings);
}

// Long when the close rises, close the trade when it falls. Logs every
// order it returns as (bar, kind) for the lookahead test.
struct UpDown {
    const Bars* bars = nullptr;
    std::vector<std::pair<int, Order::Kind>> log;

    void prepare(const Markets& m) { bars = &m.base("X"); }
    void update(const Markets&) {}

    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        int t = last_closed(*bars, now);
        if (t < 1) return {};
        double close = bars->close[t], before = bars->close[t - 1];
        Order o{.instrument = "X", .side = Side::Long, .stop_distance = 0.5};
        if (positions.empty() && close > before) {
            o.kind = Order::Kind::Open;
        } else if (!positions.empty() && close < before) {
            o.kind = Order::Kind::Close;
        } else {
            return {};
        }
        log.emplace_back(t, o.kind);
        return {o};
    }
};
static_assert(Strategy<UpDown>);

// Flat hourly bars at each close, open = high = low = close; bar 0 opens at first_hour:00.
Bars flat_bars(const std::vector<double>& closes, int first_hour = 0) {
    Bars b;
    b.timeframe = Timeframe::Hour1;
    for (std::size_t i = 0; i < closes.size(); ++i) {
        b.ts.push_back((first_hour + static_cast<int64_t>(i)) * 3600);
        b.open.push_back(closes[i]);
        b.high.push_back(closes[i]);
        b.low.push_back(closes[i]);
        b.close.push_back(closes[i]);
        b.minutes_with_data.push_back(60);
    }
    return b;
}

// One market named X.
Markets one(const Bars& b) { return Markets::make({{"X", {b}}}); }

// Two markets, X and Y, each on one timeframe.
Markets two(const Bars& x, const Bars& y) { return Markets::make({{"X", {x}}, {"Y", {y}}}); }

void test_signal_fills_at_next_open() {
    // Rise at bar 1 opens at open[2] = 102; fall at bar 4 closes at open[5] = 101.
    Bars b = flat_bars({100, 101, 102, 103, 101, 101});
    UpDown s;
    Result r = backtest(s, one(b), PortfolioSettings{});
    check_value("one trade", r.trades.size(), 1);
    check_value("entry bar", r.trades.at(0).entry_bar, 2);
    check_value("entry price", r.trades.at(0).entry_price, 102);
    check_value("exit bar", r.trades.at(0).exit_bar, 5);
    check_value("exit price", r.trades.at(0).exit_price, 101);
    check_true("cause is order", r.trades.at(0).cause == Cause::Order);
    check_value("equity per bar", r.equity.size(), b.close.size());
}

void test_last_bar_order_never_fills() {
    // The only rise is on the last bar.
    Bars b = flat_bars({100, 100, 100, 101});
    UpDown s;
    Result r = backtest(s, one(b), PortfolioSettings{});
    check_value("order logged", s.log.size(), 1);
    check_value("no trade", r.trades.size(), 0);
    check_value("balance untouched", r.ending_balance, 10000);
}

void test_fee_at_open_and_close() {
    // 1% risk of 10,000 at a 50% stop from 102: 100 / 51 units.
    // Price result (101 - 102) * size; fees 0.001 * size * (102 + 101).
    Bars b = flat_bars({100, 101, 102, 103, 101, 101});
    UpDown s;
    Markets m = one(b);
    Result r = avbt::backtest(s, m, flat_costs(m, 0.001), PortfolioSettings{});
    double size = 100.0 / 51.0;
    double expected = -1.0 * size - 0.001 * size * 203.0;
    check_value("result after fees", r.trades.at(0).result, expected);
    check_value("balance after fees", r.ending_balance, 10000 + expected);
}

void test_no_lookahead() {
    // Change every bar after t = 5; orders and trades up to t must not move.
    std::vector<double> closes = {100, 101, 102, 103, 101, 101, 104, 99, 105};
    std::vector<double> changed = closes;
    for (std::size_t i = 6; i < changed.size(); ++i) changed[i] = 50 + 7 * i;
    UpDown a, b;
    Result ra = backtest(a, one(flat_bars(closes)), PortfolioSettings{});
    Result rb = backtest(b, one(flat_bars(changed)), PortfolioSettings{});
    auto upto = [](const std::vector<std::pair<int, Order::Kind>>& log) {
        std::vector<std::pair<int, Order::Kind>> out;
        for (auto e : log) if (e.first <= 5) out.push_back(e);
        return out;
    };
    check_true("orders up to t match", upto(a.log) == upto(b.log));
    int matched = 0;
    for (const Trade& ta : ra.trades) {
        if (ta.exit_bar > 5) continue;
        for (const Trade& tb : rb.trades) {
            if (tb.entry_bar == ta.entry_bar && tb.exit_bar == ta.exit_bar &&
                tb.exit_price == ta.exit_price) ++matched;
        }
    }
    check_value("trades up to t match", matched, 1);
    for (int t = 0; t <= 5; ++t) check_value("equity up to t", rb.equity[t], ra.equity[t]);
}

void test_timeframe_carried() {
    Bars b = flat_bars({100, 101});
    b.timeframe = Timeframe::Min15;
    b.ts = {0, 900};
    UpDown s;
    check_true("timeframe", backtest(s, one(b), PortfolioSettings{}).timeframe == Timeframe::Min15);
}


void test_markets_refuse_misaligned() {
    Bars a = flat_bars({100, 101, 102});
    auto refused = [](std::vector<Market> m) {
        try { Markets::make(std::move(m)); } catch (const std::invalid_argument&) { return true; }
        return false;
    };
    Bars four = flat_bars({100});
    four.timeframe = Timeframe::Hour4;
    check_true("markets pass", !refused({{"X", {a}}, {"Y", {a, four}}}));
    check_true("different lengths pass", !refused({{"X", {a}}, {"Y", {flat_bars({100, 101})}}}));
    check_true("empty refused", refused({}));
    check_true("no timeframes refused", refused({{"X", {}}}));
    check_true("same name refused", refused({{"X", {a}}, {"X", {a}}}));
    check_true("coarser first refused", refused({{"X", {four, a}}}));
    check_true("timeframe twice refused", refused({{"X", {a, a}}}));
    Bars minutes = a;
    minutes.timeframe = Timeframe::Min1;
    check_true("different base refused", refused({{"X", {a}}, {"Y", {minutes}}}));
    check_true("no bars refused", refused({{"X", {flat_bars({})}}}));
    Bars falling = a;
    falling.ts[2] = falling.ts[1];
    check_true("timestamps that do not rise refused", refused({{"X", {falling}}}));
    Bars ragged = a;
    ragged.low.pop_back();
    Bars gap = flat_bars({100, 101, 102});
    gap.ts[2] += 3600;
    check_true("missing base bar passes", !refused({{"X", {gap}}}));
    Bars sparse_four = flat_bars({100, 101});
    sparse_four.timeframe = Timeframe::Hour4;
    sparse_four.ts = {0, 8 * 3600};
    check_true("missing coarser bar passes", !refused({{"X", {a, sparse_four}}}));
    check_true("ragged columns refused", refused({{"X", {ragged}}}));
    // Weeks start on Monday: 1970-01-05 is a Monday, 1970-01-01 a Thursday.
    Bars weeks = flat_bars({100, 101});
    weeks.timeframe = Timeframe::Week1;
    weeks.ts = {4 * 86400, 11 * 86400};
    check_true("Monday weeks pass", !refused({{"X", {weeks}}}));
    weeks.ts = {0, 7 * 86400};
    check_true("Thursday weeks refused", refused({{"X", {weeks}}}));
}

// Returns the orders listed for each clock step, and nothing on other steps.
struct Script {
    std::map<int, std::vector<Order>> at;
    const std::vector<int64_t>* clock = nullptr;
    int64_t step = 0;
    void prepare(const Markets& m) { clock = &m.clock(); step = seconds(m.timeframe()); }
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>&) {
        // The step that closes at now.
        int t = static_cast<int>(std::find(clock->begin(), clock->end(), now - step) - clock->begin());
        auto it = at.find(t);
        return it == at.end() ? std::vector<Order>{} : it->second;
    }
};
static_assert(Strategy<Script>);

Order open_long(const std::string& instrument, double stop) {
    return Order{.instrument = instrument, .side = Side::Long, .stop_distance = stop};
}

Order close_order(const std::string& instrument) {
    return Order{.kind = Order::Kind::Close, .instrument = instrument};
}

void test_each_market_uses_its_own_bars() {
    // Y fills at its own open 200, stop 180; Y's bar 2 low 170 fills the
    // stop. X, flat at 100, stays open, so it records no trade.
    Bars x = flat_bars({100, 100, 100, 100});
    Bars y = flat_bars({200, 200, 200, 200});
    y.low[2] = 170;
    Script s{.at = {{0, {open_long("X", 0.1), open_long("Y", 0.1)}}}};
    Result r = backtest(s, two(x, y), PortfolioSettings{});
    check_value("one trade", r.trades.size(), 1);
    check_true("trade is Y", r.trades.at(0).instrument == "Y");
    check_value("Y entry price", r.trades.at(0).entry_price, 200);
    check_value("Y exit bar", r.trades.at(0).exit_bar, 2);
    check_value("Y exit price", r.trades.at(0).exit_price, 180);
    check_true("Y cause is stop", r.trades.at(0).cause == Cause::Stop);
}

void test_closes_fill_before_opens() {
    // 1.5% risk at a 2% stop is 7,500 of notional, so two positions do not
    // fit in 10,000. Bar 2 lists the Y open before the X close; the X close
    // fills first and frees the cash, so Y opens on bar 3.
    Bars flat = flat_bars({100, 100, 100, 100, 100, 100});
    Script s{.at = {{0, {open_long("X", 0.02)}},
                    {2, {open_long("Y", 0.02), close_order("X")}},
                    {4, {close_order("Y")}}}};
    PortfolioSettings settings{.risk_per_trade = 0.015};
    Result r = backtest(s, two(flat, flat), settings);
    check_value("two trades", r.trades.size(), 2);
    if (r.trades.size() < 2) return;
    check_true("X first", r.trades[0].instrument == "X");
    check_value("X exit bar", r.trades[0].exit_bar, 3);
    check_true("Y second", r.trades[1].instrument == "Y");
    check_value("Y entry bar", r.trades[1].entry_bar, 3);
    check_value("Y exit bar", r.trades[1].exit_bar, 5);
}

// Trades X on Y's closes: long X when Y rises, out when Y falls.
struct FollowY {
    const Bars* y = nullptr;
    void prepare(const Markets& m) { y = &m.base("Y"); }
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        int t = last_closed(*y, now);
        if (t < 1) return {};
        double close = y->close[t], before = y->close[t - 1];
        if (positions.empty() && close > before) return {open_long("X", 0.5)};
        if (!positions.empty() && close < before) return {close_order("X")};
        return {};
    }
};
static_assert(Strategy<FollowY>);

void test_no_lookahead_two_markets() {
    // Change every bar after t = 5 in both markets; trades and equity up to
    // t must not move.
    std::vector<double> xs = {100, 101, 102, 103, 104, 105, 106, 107, 108};
    std::vector<double> ys = {50, 51, 52, 51, 52, 50, 53, 49, 55};
    std::vector<double> xs2 = xs, ys2 = ys;
    for (std::size_t i = 6; i < xs.size(); ++i) {
        xs2[i] = 80 + 3 * i;
        ys2[i] = 90 - 2 * i;
    }
    FollowY a, b;
    Result ra = backtest(a, two(flat_bars(xs), flat_bars(ys)),
                         PortfolioSettings{});
    Result rb = backtest(b, two(flat_bars(xs2), flat_bars(ys2)),
                         PortfolioSettings{});
    auto upto = [](const Result& r) {
        std::vector<std::pair<int, int>> out;
        for (const Trade& t : r.trades) if (t.exit_bar <= 5) out.emplace_back(t.entry_bar, t.exit_bar);
        return out;
    };
    check_true("some trade up to t", !upto(ra).empty());
    check_true("trades up to t match", upto(ra) == upto(rb));
    for (int t = 0; t <= 5; ++t) check_value("equity up to t", rb.equity[t], ra.equity[t]);
}


void test_last_closed() {
    // Hourly bars opening at 0, 3600, 7200. Bar 0 closes at 3600, when bar 1 opens.
    Bars b = flat_bars({100, 101, 102});
    check_value("before the first bar", last_closed(b, -1), -1);
    check_value("first bar still open", last_closed(b, 1800), -1);
    check_value("first bar just closed", last_closed(b, 3600), 0);
    check_value("second bar half way", last_closed(b, 5400), 0);
    check_value("last bar still open", last_closed(b, 7200 + 3599), 1);
    check_value("last bar closed", last_closed(b, 7200 + 3600), 2);
}

void test_market_starts_late() {
    // X runs hours 0 to 5; Y starts at hour 3. The clock is X's six hours.
    // An open for Y at step 0 is dropped, Y has no bar at step 1; an open at
    // step 3, Y's first close, fills at Y's second bar, step 4.
    Bars x = flat_bars({100, 100, 100, 100, 100, 100});
    Bars y = flat_bars({200, 200, 200}, 3);
    Script s{.at = {{0, {open_long("Y", 0.1)}}, {3, {open_long("Y", 0.1)}}, {4, {close_order("Y")}}}};
    Result r = backtest(s, two(x, y), PortfolioSettings{});
    check_value("clock is the union", r.clock.size(), 6);
    check_value("one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_value("Y entry step", r.trades[0].entry_bar, 4);
    check_value("Y exit step", r.trades[0].exit_bar, 5);
}

void test_market_ends_early() {
    // Y's data ends at hour 2 while its position is open: it closes at
    // Y's last close, 210. X runs on to hour 4.
    Bars x = flat_bars({100, 100, 100, 100, 100});
    Bars y = flat_bars({200, 200, 210});
    Script s{.at = {{0, {open_long("Y", 0.1)}}, {2, {open_long("Y", 0.1)}}}};
    Result r = backtest(s, two(x, y), PortfolioSettings{});
    check_value("one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_value("Y exit step", r.trades[0].exit_bar, 2);
    check_value("Y exit price", r.trades[0].exit_price, 210);
    check_true("Y cause is end of data", r.trades[0].cause == Cause::EndOfData);
}

// Long X once when a 4-hour bar closes up; logs each 4-hour bar it acts on.
struct FourHourUp {
    const Bars* four = nullptr;
    int seen = -1;
    std::vector<int> acted;
    void prepare(const Markets& m) { four = &m.at("X", Timeframe::Hour4); }
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        int t = last_closed(*four, now);
        if (t < 0 || t == seen) return {};
        seen = t;
        acted.push_back(t);
        if (positions.empty() && four->close[t] > four->open[t]) return {open_long("X", 0.5)};
        return {};
    }
};
static_assert(Strategy<FourHourUp>);

void test_coarser_timeframe() {
    // Hourly base, 8 hours; the 4-hour bars are built from it. The first
    // 4-hour bar (hours 0 to 3) rises from 100 to 103 and closes at hour 4,
    // so the open fills at the open of hour 4, 104, and not earlier.
    Bars hours = flat_bars({100, 101, 102, 103, 104, 105, 106, 107});
    Bars four;
    four.timeframe = Timeframe::Hour4;
    four.ts = {0, 4 * 3600};
    four.open = {100, 104};
    four.high = {103, 107};
    four.low = {100, 104};
    four.close = {103, 107};
    four.minutes_with_data = {240, 240};
    FourHourUp s;
    Result r = backtest(s, Markets::make({{"X", {hours, four}}}), PortfolioSettings{});
    check_true("acts once per 4-hour bar", s.acted == std::vector<int>{0, 1});
    check_value("equity per step", r.equity.size(), 8);
    // Still open at the end, so no trade is recorded; check the entry through the equity.
    check_value("no closed trade", r.trades.size(), 0);
    check_value("flat before the fill", r.equity[3], 10000);
    check_true("gains after the fill", r.equity[7] > 10000);
}

// UpDown opens at open[2] = 102 and closes at open[5] = 101. Each rate
// below is charged at every base bar the position is open.
Result held(double rate, std::size_t n = 6) {
    Bars b = flat_bars({100, 101, 102, 103, 101, 101});
    UpDown s;
    Markets m = one(b);
    MarketCosts c{{"X", Costs{.hold_long = std::vector<double>(n, rate), .spread = no_spread}}};
    return avbt::backtest(s, m, c, PortfolioSettings{});
}

void test_holding_costs() {
    Result free = held(0.0), pays = held(0.001), receives = held(-0.001), nan = held(std::nan(""));
    double base = free.trades.at(0).result;
    check_true("long pays", pays.trades.at(0).holding_costs > 0 && pays.trades.at(0).result < base);
    check_value("pays in result", pays.trades.at(0).result, base - pays.trades.at(0).holding_costs);
    check_true("pays lowers equity", pays.equity[4] < free.equity[4]);
    check_true("negative receives", receives.trades.at(0).result > base);
    check_true("negative raises equity", receives.equity[4] > free.equity[4]);
    check_value("nan is zero", nan.trades.at(0).result, base);
    bool threw = false;
    try { held(0.001, 5); } catch (const std::invalid_argument&) { threw = true; }
    check_true("hold length mismatch throws", threw);
}

void test_short_receives() {
    // A short that pays a negative rate gains it.
    Portfolio p(PortfolioSettings{});
    p.open("X", Side::Short, 100.0, 150.0, 1.0);
    double size = p.positions().at(0).size;
    p.hold("X", -0.001, 100.0);
    auto c = p.close("X", 100.0);
    check_value("short receives", c->result, 0.001 * size * 100.0);
}

void test_open_and_close_fees() {
    // Open fee 0.1% at the 102 fill, close fee 0.2% at the 101 fill.
    Bars b = flat_bars({100, 101, 102, 103, 101, 101});
    UpDown s;
    Markets m = one(b);
    Result r = avbt::backtest(s, m, {{"X", Costs{.open_fee = 0.001, .close_fee = 0.002, .spread = no_spread}}}, PortfolioSettings{});
    double size = r.trades.at(0).size;
    check_value("fees", r.trades.at(0).fees, size * (0.001 * 102 + 0.002 * 101));
    check_value("result after fees", r.trades.at(0).result, -size - r.trades.at(0).fees);
}

void test_profit_share_fees() {
    // Opens at 102, closes at 140 at 1x: ROI 38/102, under 250%, so 25%.
    Bars b = flat_bars({100, 101, 102, 150, 140, 140});
    UpDown s;
    Markets m = one(b);
    Costs c{.profit_share = {{2.5, 0.25}, {5.0, 0.20}}, .spread = no_spread};
    Result r = avbt::backtest(s, m, {{"X", c}}, PortfolioSettings{});
    const auto& t = r.trades.at(0);
    check_value("share fees", t.fees, 0.25 * t.size * 38);
    check_value("share result", t.result, 0.75 * t.size * 38);
    c.open_fee = 0.001;
    bool threw = false;
    try { avbt::backtest(s, m, {{"X", c}}, PortfolioSettings{}); } catch (const std::invalid_argument&) { threw = true; }
    check_true("share and size fees throw", threw);
}

void test_missing_costs_throws() {
    UpDown s;
    Markets m = one(flat_bars({100, 101, 102}));
    bool threw = false;
    try { avbt::backtest(s, m, MarketCosts{}, PortfolioSettings{}); } catch (const std::invalid_argument&) { threw = true; }
    check_true("missing costs throws", threw);
}

// A spread column per bar, capped at 3.5% (ADR 0020). The open fills on bar
// 1 at 100 * 1.02; the close on bar 3 at 100 * (1 - 0.035), the cap. The
// stop stays at 90, on the oracle open: size 100 / (102 - 90 * 0.98).
void test_spread_column_and_cap() {
    Bars b = flat_bars({100, 100, 100, 100, 100, 100});
    Script s{.at = {{0, {open_long("X", 0.1)}}, {2, {close_order("X")}}}};
    Markets m = one(b);
    Costs c{.spread = std::vector<double>{0.01, 0.02, 0.03, 0.04, 0.05, 0.06}, .max_spread = 0.035};
    Result r = avbt::backtest(s, m, {{"X", c}}, PortfolioSettings{});
    check_value("spread one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    const Trade& t = r.trades[0];
    double size = 100.0 / (102.0 - 90.0 * 0.98);
    check_value("spread order entry", t.entry_price, 102.0);
    check_value("spread order exit", t.exit_price, 96.5);
    check_value("spread order size", t.size, size);
    check_value("spread order cost", t.spread_cost, size * 100.0 * (0.02 + 0.035));
    check_value("spread order result", t.result, size * (96.5 - 102.0));
}

// A constant 1% on three markets: X takes profit at 110 and fills at 108.9,
// Y stops at 90 and fills at 89.1, Z's data ends at 100 and fills at 99.
void test_spread_every_fill() {
    Bars x = flat_bars({100, 100, 100, 100, 100, 100});
    x.high[2] = 115;
    Bars y = flat_bars({100, 100, 100, 100, 100, 100});
    y.low[2] = 85;
    Bars z = flat_bars({100, 100, 100, 100});
    Order take{.instrument = "X", .side = Side::Long, .stop_distance = 0.1, .take_profit_distance = 0.1};
    Script s{.at = {{0, {take, open_long("Y", 0.1), open_long("Z", 0.1)}}}};
    Markets m = Markets::make({{"X", {x}}, {"Y", {y}}, {"Z", {z}}});
    Costs c{.spread = std::vector<double>{0.01}};
    Result r = avbt::backtest(s, m, {{"X", c}, {"Y", c}, {"Z", c}}, PortfolioSettings{});
    check_value("spread three trades", r.trades.size(), 3);
    for (const Trade& t : r.trades) {
        check_value("spread fill entry " + t.instrument, t.entry_price, 101.0);
        double exit = t.instrument == "X" ? 108.9 : t.instrument == "Y" ? 89.1 : 99.0;
        double oracle = t.instrument == "X" ? 110.0 : t.instrument == "Y" ? 90.0 : 100.0;
        check_value("spread fill exit " + t.instrument, t.exit_price, exit);
        check_value("spread fill cost " + t.instrument, t.spread_cost, t.size * (100.0 + oracle) * 0.01);
        check_value("spread fill identity " + t.instrument, t.result + t.fees + t.holding_costs,
                    t.size * (t.exit_price - t.entry_price));
    }
}

void test_spread_validation() {
    Markets m = one(flat_bars({100, 101, 102}));
    auto throws = [&](const Costs& c) {
        try { check_costs(m, {{"X", c}}); } catch (const std::invalid_argument&) { return true; }
        return false;
    };
    check_true("spread table has no X", throws(Costs{}));
    check_true("spread column too short", throws(Costs{.spread = std::vector<double>{0.01, 0.01}}));
    check_true("spread nan", throws(Costs{.spread = std::vector<double>{std::nan("")}}));
    check_true("spread negative", throws(Costs{.spread = std::vector<double>{-0.01}}));
    check_true("spread one", throws(Costs{.spread = std::vector<double>{1.0}}));
    check_true("max_spread negative", throws(Costs{.spread = no_spread, .max_spread = -0.01}));
    check_true("spread none valid", !throws(Costs{.spread = no_spread}));
    check_true("spread constant valid", !throws(Costs{.spread = std::vector<double>{0.01}}));
    check_true("spread column valid", !throws(Costs{.spread = std::vector<double>(3, 0.01)}));
}

}

// Opens at bar 0's close with a 10% take profit that closes half, and
// closes the rest at bar 3's close.
struct HalfAtTarget {
    const Bars* bars = nullptr;
    void prepare(const Markets& m) { bars = &m.base("X"); }
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>&) {
        int t = last_closed(*bars, now);
        if (t == 0) {
            return {Order{.instrument = "X", .side = Side::Long, .stop_distance = 0.5,
                          .take_profit_distance = 0.1, .take_profit_fraction = 0.5}};
        }
        if (t == 3) return {Order{.kind = Order::Kind::Close, .instrument = "X"}};
        return {};
    }
};

void test_partial_close_keeps_entry_bar() {
    // Opens at open[1] = 100; bar 2 opens at 111, past the 110 target, so half
    // fills at 111; the rest closes at open[4] = 111. Both trades entered at bar 1.
    Bars b = flat_bars({100, 100, 111, 111, 111});
    HalfAtTarget s;
    Result r = backtest(s, one(b), PortfolioSettings{});
    check_value("two trades", r.trades.size(), 2);
    check_true("first is take profit", r.trades.at(0).cause == Cause::PartialTakeProfit);
    check_value("first entry bar", r.trades.at(0).entry_bar, 1);
    check_value("first exit bar", r.trades.at(0).exit_bar, 2);
    check_value("rest entry bar", r.trades.at(1).entry_bar, 1);
    check_value("rest exit bar", r.trades.at(1).exit_bar, 4);
}

// Y trades hours 0-1 and 7-9; X every hour 0-9. Y is in a gap at steps 2-6.
Markets weekend(double reopen = 210) {
    Bars x = flat_bars({100, 100, 100, 100, 100, 100, 100, 100, 100, 100});
    Bars y = flat_bars({200, 200});
    Bars late = flat_bars({reopen, reopen, reopen}, 7);
    for (std::size_t i = 0; i < late.ts.size(); ++i) {
        y.ts.push_back(late.ts[i]);
        y.open.push_back(late.open[i]);
        y.high.push_back(late.high[i]);
        y.low.push_back(late.low[i]);
        y.close.push_back(late.close[i]);
        y.minutes_with_data.push_back(60);
    }
    return two(x, y);
}

void test_gap_close_replaces_waiting_open() {
    // The open from step 1 waits through the gap; the close at step 3
    // replaces it, so nothing opens at step 7. ADR 0021.
    Script s{.at = {{1, {open_long("Y", 0.1)}}, {3, {close_order("Y")}}}};
    Result r = backtest(s, weekend(), PortfolioSettings{});
    check_value("gap close: no trades", r.trades.size(), 0);
    check_value("gap close: no position", r.open_positions.size(), 0);
}

void test_gap_new_open_replaces_waiting_open() {
    Order short_y{.instrument = "Y", .side = Side::Short, .stop_distance = 0.1};
    Script s{.at = {{1, {open_long("Y", 0.1)}}, {3, {short_y}}}};
    Result r = backtest(s, weekend(), PortfolioSettings{});
    check_value("gap short: one position", r.open_positions.size(), 1);
    if (r.open_positions.empty()) return;
    check_true("gap short: the short fills", r.open_positions[0].side == Side::Short);
}

void test_gap_waiting_close_survives_open() {
    // Long Y from step 1; the close at step 1 waits through the gap, and a
    // short at step 3 does not replace it: at step 7 the long closes, then
    // the short opens.
    Order short_y{.instrument = "Y", .side = Side::Short, .stop_distance = 0.1};
    Script s{.at = {{0, {open_long("Y", 0.1)}}, {1, {close_order("Y")}}, {3, {short_y}}}};
    Result r = backtest(s, weekend(), PortfolioSettings{});
    check_value("gap reverse: one trade", r.trades.size(), 1);
    check_value("gap reverse: one position", r.open_positions.size(), 1);
    if (r.trades.empty() || r.open_positions.empty()) return;
    check_true("gap reverse: the long closed", r.trades[0].side == Side::Long && r.trades[0].cause == Cause::Order);
    check_value("gap reverse: at the reopen", r.trades[0].exit_bar, 7);
    check_true("gap reverse: the short opens", r.open_positions[0].side == Side::Short);
}

// A 50x long on Y from 200, stop 1% (198): 50 units, collateral 200,
// liquidation 200 - 0.85 * 200 / 50 = 196.6.
Order fifty_x(Side side) {
    return Order{.instrument = "Y", .side = side, .stop_distance = 0.01, .leverage = 50.0};
}

void check_liquidated(const std::string& label, const Result& r, double price, int bar) {
    check_value(label + ": one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_true(label + ": cause", r.trades[0].cause == Cause::Liquidation);
    check_value(label + ": exit", r.trades[0].exit_price, price);
    check_value(label + ": exit bar", r.trades[0].exit_bar, bar);
    check_value(label + ": loss", r.trades[0].result, -170.0);
}

void test_close_order_past_liquidation() {
    // Y opens at 150 on step 3, past the 196.6 liquidation: the close
    // order liquidates there instead of filling at 150. ADR 0020.
    Bars x = flat_bars({100, 100, 100, 100, 100, 100});
    Bars y = flat_bars({200, 200, 200, 150, 150, 150});
    Script s{.at = {{0, {fifty_x(Side::Long)}}, {2, {close_order("Y")}}}};
    check_liquidated("close past liq", backtest(s, two(x, y), PortfolioSettings{}), 196.6, 3);
}

void test_waiting_close_past_liquidation() {
    // The close waits over the gap; Y reopens at 150, past liquidation.
    Script s{.at = {{0, {fifty_x(Side::Long)}}, {1, {close_order("Y")}}}};
    check_liquidated("gap close past liq", backtest(s, weekend(150), PortfolioSettings{}), 196.6, 7);
}

void test_end_of_data_past_liquidation() {
    // Y ends at 199 (long) or 201 (short) with a 2% spread on its last bar:
    // 199 * 0.98 = 195.02 passes 196.6, and 201 * 1.02 = 205.02 passes the
    // short's 200 + 3.4 = 203.4. Each liquidates at its level.
    Bars x = flat_bars({100, 100, 100, 100, 100});
    for (auto [side, last, liq] : {std::tuple{Side::Long, 199.0, 196.6}, std::tuple{Side::Short, 201.0, 203.4}}) {
        Markets m = two(x, flat_bars({200, 200, last}));
        MarketCosts c = flat_costs(m);
        c["Y"].spread = std::vector<double>{0.0, 0.0, 0.02};
        Script s{.at = {{0, {fifty_x(side)}}}};
        check_liquidated(side == Side::Long ? "eod long" : "eod short",
                         avbt::backtest(s, m, c, PortfolioSettings{}), liq, 2);
    }
}

void test_two_part_close_keeps_entry_bar() {
    // Long Y fills at 200 on step 1, take profit 220 for half, stop 180.
    // Step 3 opens at 230, past the take profit, and falls to 170: half
    // closes at the open, then the rest at the stop. Both trades entered
    // at step 1.
    Bars x = flat_bars({100, 100, 100, 100, 100});
    Bars y = flat_bars({200, 200, 200, 175, 175});
    y.open[3] = y.high[3] = 230;
    y.low[3] = 170;
    Order o{.instrument = "Y", .side = Side::Long, .stop_distance = 0.1,
            .take_profit_distance = 0.1, .take_profit_fraction = 0.5};
    Script s{.at = {{0, {o}}}};
    Result r = backtest(s, two(x, y), PortfolioSettings{});
    check_value("two parts: two trades", r.trades.size(), 2);
    if (r.trades.size() < 2) return;
    check_true("two parts: partial first", r.trades[0].cause == Cause::PartialTakeProfit);
    check_true("two parts: then the stop", r.trades[1].cause == Cause::Stop);
    for (const Trade& t : r.trades) {
        check_value("two parts: entry bar", t.entry_bar, 1);
        check_value("two parts: entry time", t.entry_time, r.clock[1]);
        check_value("two parts: exit bar", t.exit_bar, 3);
    }
}

// Opens Y at step 0, then asks to close Y on every step from step 9.
struct CloseEveryStep {
    void prepare(const Markets&) {}
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>&) {
        if (now == 60) return {open_long("Y", 0.1)};
        if (now >= 600) return {close_order("Y")};
        return {};
    }
};
static_assert(Strategy<CloseEveryStep>);

void test_waiting_orders_stay_bounded() {
    // Y is in a 30-day gap of 1-minute steps. Each step's close replaces
    // the last one, so the run stays fast and closes once, at the reopen.
    const int64_t day = 1440;
    Bars x, y;
    x.timeframe = y.timeframe = Timeframe::Min1;
    for (int64_t i = 0; i < 32 * day; ++i) {
        for (Bars* b : {&x, &y}) {
            if (b == &y && i >= 10 && i < 30 * day) continue;
            b->ts.push_back(i * 60);
            b->open.push_back(100);
            b->high.push_back(100);
            b->low.push_back(100);
            b->close.push_back(100);
            b->minutes_with_data.push_back(1);
        }
    }
    CloseEveryStep s;
    auto start = std::chrono::steady_clock::now();
    Result r = backtest(s, two(x, y), PortfolioSettings{});
    double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    check_true("bounded: under 2 s", seconds < 2.0);
    check_value("bounded: one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_value("bounded: closes at the reopen", r.trades[0].exit_bar, 30 * day);
}

int main() {
    test_signal_fills_at_next_open();
    test_last_bar_order_never_fills();
    test_fee_at_open_and_close();
    test_no_lookahead();
    test_timeframe_carried();
    test_markets_refuse_misaligned();
    test_each_market_uses_its_own_bars();
    test_closes_fill_before_opens();
    test_no_lookahead_two_markets();
    test_last_closed();
    test_market_starts_late();
    test_market_ends_early();
    test_coarser_timeframe();
    test_partial_close_keeps_entry_bar();
    test_holding_costs();
    test_short_receives();
    test_open_and_close_fees();
    test_profit_share_fees();
    test_missing_costs_throws();
    test_spread_column_and_cap();
    test_spread_every_fill();
    test_spread_validation();
    test_gap_close_replaces_waiting_open();
    test_gap_new_open_replaces_waiting_open();
    test_gap_waiting_close_survives_open();
    test_close_order_past_liquidation();
    test_waiting_close_past_liquidation();
    test_end_of_data_past_liquidation();
    test_two_part_close_keeps_entry_bar();
    test_waiting_orders_stay_bounded();
    if (failures == 0) std::printf("all backtest checks passed\n");
    return failures == 0 ? 0 : 1;
}
