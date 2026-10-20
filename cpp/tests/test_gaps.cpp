// Bars that skip time (issue 21, part 2). A market may have no base bar for
// a while (a weekend, a closed session). Nothing happens to it in the gap:
// its orders wait for its next open, its levels are checked on its next
// bar, it pays no holding cost, and time exits count its bars, not the
// clock. Returns 0 when every check passes.

#include "avbt/run.hpp"
#include "avbt/strategies.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using namespace avbt;
int failures = 0;

void check_value(const std::string& label, double actual, double expected) {
    if (!(std::abs(actual - expected) < 1e-9)) {
        ++failures;
        std::printf("FAIL %s: expected %.10g, got %.10g\n", label.c_str(), expected, actual);
    }
}

void check_true(const std::string& label, bool ok) {
    if (!ok) {
        ++failures;
        std::printf("FAIL %s\n", label.c_str());
    }
}

template <class F>
bool throws(F f) {
    try { f(); } catch (const std::invalid_argument&) { return true; }
    return false;
}

constexpr int64_t H = 3600, D = 86400;
// Fri 2024-01-05 00:00 UTC; Sat and Mon after it.
constexpr int64_t FRI = 1704412800, SAT = FRI + D, MON = FRI + 3 * D;
// The first second of these UTC months.
constexpr int64_t DEC23 = 1701388800, JAN24 = 1704067200, FEB24 = 1706745600, MAR24 = 1709251200;

// n hourly opens from `from`.
std::vector<int64_t> hours(int64_t from, int n) {
    std::vector<int64_t> out;
    for (int i = 0; i < n; ++i) out.push_back(from + i * H);
    return out;
}

std::vector<int64_t> join(std::vector<int64_t> a, const std::vector<int64_t>& b) {
    a.insert(a.end(), b.begin(), b.end());
    return a;
}

// Flat bars: open = high = low = close = px[i].
Bars flat(Timeframe tf, const std::vector<int64_t>& ts, const std::vector<double>& px) {
    Bars b{.timeframe = tf, .ts = ts};
    for (double p : px) {
        b.open.push_back(p);
        b.high.push_back(p);
        b.low.push_back(p);
        b.close.push_back(p);
        b.minutes_with_data.push_back(static_cast<int>(tf == Timeframe::Month1 ? 1 : seconds(tf) / 60));
    }
    return b;
}

Bars flat(Timeframe tf, const std::vector<int64_t>& ts, double p) {
    return flat(tf, ts, std::vector<double>(ts.size(), p));
}

// Every market free, no spread.
MarketCosts free_costs(const Markets& m) {
    MarketCosts c;
    for (const Market& x : m.all()) c[x.instrument] = Costs{.spread = no_spread};
    return c;
}

// Returns the orders listed for each clock step, and nothing on other steps.
struct Script {
    std::map<int, std::vector<Order>> at;
    const std::vector<int64_t>* clock = nullptr;
    Timeframe tf = Timeframe::Hour1;
    bool trades(const std::string&) const { return true; }
    void prepare(const Markets& m) { clock = &m.clock(); tf = m.timeframe(); }
    void update(const Markets&) {}
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>&) {
        for (int t = 0; t < static_cast<int>(clock->size()); ++t) {
            if (bar_end(tf, (*clock)[t]) != now) continue;
            auto it = at.find(t);
            return it == at.end() ? std::vector<Order>{} : it->second;
        }
        return {};
    }
};
static_assert(Strategy<Script>);

Order open_long(const std::string& ins, double stop) {
    return Order{.instrument = ins, .side = Side::Long, .stop_distance = stop};
}
Order close_order(const std::string& ins) { return Order{.kind = Order::Kind::Close, .instrument = ins}; }

// The weekend set: X trades every hour from Fri 20:00 to Mon 03:00 (56
// steps). Y has Fri 20:00-23:00 (steps 0-3) and Mon 00:00-03:00 (steps
// 52-55), nothing on Sat and Sun. Y is flat at 200 on Friday, `mon` on Monday.
// Step 52 is Mon 00:00.
Markets weekend(Bars mon) {
    Bars x = flat(Timeframe::Hour1, hours(FRI + 20 * H, 56), 100);
    Bars y = flat(Timeframe::Hour1, hours(FRI + 20 * H, 4), 200);
    for (size_t i = 0; i < mon.ts.size(); ++i) {
        y.ts.push_back(mon.ts[i]);
        y.open.push_back(mon.open[i]);
        y.high.push_back(mon.high[i]);
        y.low.push_back(mon.low[i]);
        y.close.push_back(mon.close[i]);
        y.minutes_with_data.push_back(60);
    }
    return Markets::make({{"X", {x}}, {"Y", {y}}});
}
Markets weekend(double mon = 210) { return weekend(flat(Timeframe::Hour1, hours(MON, 4), mon)); }

// --- 1. Markets accept base gaps; the grid check stays.

void test_markets_accept_gaps() {
    // Hourly bars on Mon 1 Jan and Wed 3 Jan; all of Tue missing.
    std::vector<int64_t> ts = join(hours(JAN24, 24), hours(JAN24 + 2 * D, 24));
    check_true("make accepts a missing day", !throws([&] { Markets::make({{"X", {flat(Timeframe::Hour1, ts, 1)}}}); }));
    Markets m = Markets::make({{"X", {flat(Timeframe::Hour1, ts, 1)}}});
    check_value("clock skips the day", m.clock().size(), 48);
    check_value("clock after the gap", m.clock()[24], JAN24 + 2 * D);

    // Two markets: Y has no bar at the steps only X has.
    Markets two = weekend();
    check_value("weekend clock", two.clock().size(), 56);
    check_value("Y has no bar on Saturday", two.bar_at("Y", 4), -1);
    check_value("Y's Monday bar", two.bar_at("Y", 52), 4);

    // Append after a gap; the clock grows by one step.
    Markets a = Markets::make({{"X", {flat(Timeframe::Hour1, hours(0, 3), 1)}}});
    check_true("append after a gap", !throws([&] { a.append("X", Timeframe::Hour1, Bar{30 * H, 2, 2, 2, 2, 60}); }));
    check_value("clock after append", a.clock().size(), 4);
    check_value("clock end after append", a.clock().back(), 30 * H);
    // Off-grid is still refused, at append and at make.
    check_true("off-grid append refused", throws([&] { a.append("X", Timeframe::Hour1, Bar{31 * H + 1800, 2, 2, 2, 2, 60}); }));
    check_true("off-grid make refused",
               throws([&] { Markets::make({{"X", {flat(Timeframe::Hour1, {0, H + 1800}, 1)}}}); }));
    // A Month1 base with a missing month is fine; a mid-month ts is not.
    check_true("month gap accepted",
               !throws([&] { Markets::make({{"X", {flat(Timeframe::Month1, {DEC23, FEB24}, 1)}}}); }));
    check_true("mid-month refused",
               throws([&] { Markets::make({{"X", {flat(Timeframe::Month1, {DEC23, JAN24 + D}, 1)}}}); }));

    // Live append keeps its rule: a base bar before the clock's end must be on the clock.
    // X has hours 0-9 without hour 7; Y has hours 0-3.
    std::vector<int64_t> xs = join(hours(0, 7), hours(8 * H, 2));
    Markets live = Markets::make({{"X", {flat(Timeframe::Hour1, xs, 1)}}, {"Y", {flat(Timeframe::Hour1, hours(0, 4), 1)}}});
    check_true("Y on the clock after a gap", !throws([&] { live.append("Y", Timeframe::Hour1, Bar{6 * H, 1, 1, 1, 1, 60}); }));
    check_true("Y off the clock refused", throws([&] { live.append("Y", Timeframe::Hour1, Bar{7 * H, 1, 1, 1, 1, 60}); }));
}

// --- 2. bar_end.

void test_bar_end() {
    check_value("hour", bar_end(Timeframe::Hour1, 5 * H), 6 * H);
    check_value("4 hours", bar_end(Timeframe::Hour4, 8 * H), 12 * H);
    check_value("day", bar_end(Timeframe::Day1, MON), MON + D);
    check_value("week", bar_end(Timeframe::Week1, MON), MON + 7 * D);
    check_value("Jan 2024", bar_end(Timeframe::Month1, JAN24), FEB24);
    check_value("Dec 2023", bar_end(Timeframe::Month1, DEC23), JAN24);
    check_value("Feb 2024, leap", bar_end(Timeframe::Month1, FEB24), MAR24);
}

// --- 3. last_closed across a gap.

void test_last_closed_across_gap() {
    // Fri 22:00, Fri 23:00, Mon 00:00.
    Bars b = flat(Timeframe::Hour1, {FRI + 22 * H, FRI + 23 * H, MON}, 1);
    check_value("Fri 23:00 still open", last_closed(b, FRI + 23 * H + 3599), 0);
    // The Fri 23:00 bar closes at Sat 00:00, not when Monday opens.
    check_value("Sat 00:00", last_closed(b, SAT), 1);
    check_value("Sunday noon", last_closed(b, SAT + D + 12 * H), 1);
    check_value("Mon 00:00", last_closed(b, MON), 1);
    check_value("Mon 01:00", last_closed(b, MON + H), 2);

    Bars m = flat(Timeframe::Month1, {JAN24, FEB24}, 1);
    check_value("Jan open just before Feb 1", last_closed(m, FEB24 - 1), -1);
    check_value("Jan closed at Feb 1", last_closed(m, FEB24), 0);
    check_value("Feb open just before Mar 1", last_closed(m, MAR24 - 1), 0);
    // 29 days, not 31: Feb closes at Mar 1.
    check_value("Feb closed at Mar 1", last_closed(m, MAR24), 1);
    // A month missing between: Dec 2023 then Feb 2024. Dec closes at Jan 1.
    Bars skip = flat(Timeframe::Month1, {DEC23, FEB24}, 1);
    check_value("Dec closed at Jan 1", last_closed(skip, JAN24), 0);
    check_value("Feb open on Feb 15", last_closed(skip, FEB24 + 14 * D), 0);
}

// --- 4. Waiting orders.

void test_order_waits_over_gap() {
    // Open Y at step 3 (Y's Fri 23:00 bar). Step 4 (Sat 00:00) has no Y
    // bar, so the order waits to Mon 00:00, step 52, at Y's Monday open
    // 210. Close at step 53 fills at step 54, Mon 02:00, at 210.
    Markets m = weekend();
    Script s{.at = {{3, {open_long("Y", 0.1)}}, {53, {close_order("Y")}}}};
    Result r = backtest(s, m, free_costs(m), PortfolioSettings{});
    check_value("waited: one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    const Trade& t = r.trades[0];
    check_true("trade is Y", t.instrument == "Y");
    check_value("entry step", t.entry_bar, 52);
    check_value("entry time Mon 00:00", t.entry_time, MON);
    check_value("entry price is Monday open", t.entry_price, 210);
    check_value("exit time Mon 02:00", t.exit_time, MON + 2 * H);
    check_value("exit price", t.exit_price, 210);
    check_true("cause order", t.cause == Cause::Order);
    check_value("no open positions", r.open_positions.size(), 0);
}

void test_order_not_started_or_ended() {
    // X runs hours 0-5. Z starts at hour 3: an open at step 0 is dropped
    // (Z has not started at step 1); an open at step 3 fills at step 4.
    Bars x = flat(Timeframe::Hour1, hours(0, 6), 100);
    Markets late = Markets::make({{"X", {x}}, {"Z", {flat(Timeframe::Hour1, hours(3 * H, 3), 200)}}});
    Script s{.at = {{0, {open_long("Z", 0.1)}}, {3, {open_long("Z", 0.1)}}, {4, {close_order("Z")}}}};
    Result r = backtest(s, late, free_costs(late), PortfolioSettings{});
    check_value("late start: one trade", r.trades.size(), 1);
    if (!r.trades.empty()) {
        check_value("late start: entry step", r.trades[0].entry_bar, 4);
        check_value("late start: exit step", r.trades[0].exit_bar, 5);
    }

    // Z ends at hour 2: opens at step 2 and step 3 find it ended; both dropped.
    Markets ended = Markets::make({{"X", {x}}, {"Z", {flat(Timeframe::Hour1, hours(0, 3), 200)}}});
    Script e{.at = {{2, {open_long("Z", 0.1)}}, {3, {open_long("Z", 0.1)}}}};
    Result re = backtest(e, ended, free_costs(ended), PortfolioSettings{});
    check_value("ended: no trades", re.trades.size(), 0);
    check_value("ended: no open positions", re.open_positions.size(), 0);
    check_value("ended: balance untouched", re.ending_balance, 10000);
}

// --- 5. A gap is not the end of data.

void test_gap_is_not_end_of_data() {
    // Y opens at step 1 (Fri 21:00) at 200 and is held over the weekend;
    // the close at step 53 fills at Mon 02:00 at 210.
    Markets m = weekend();
    Script s{.at = {{0, {open_long("Y", 0.1)}}, {53, {close_order("Y")}}}};
    Result r = backtest(s, m, free_costs(m), PortfolioSettings{});
    check_value("one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_true("not closed at end of data", r.trades[0].cause == Cause::Order);
    check_value("entry time Fri 21:00", r.trades[0].entry_time, FRI + 21 * H);
    check_value("exit time Mon 02:00", r.trades[0].exit_time, MON + 2 * H);
    check_value("exit price", r.trades[0].exit_price, 210);
    // Nothing moves in the gap: the mark stays 200, equity 10000.
    for (int t = 4; t < 52; ++t) check_value("flat equity in the gap", r.equity[t], 10000);
}

// --- 6. A reopen gap past a level.

Trade levels(const Order& o, double open, double high, double low, double close) {
    Bars mon = flat(Timeframe::Hour1, hours(MON, 4), 210);
    mon.open[0] = open, mon.high[0] = high, mon.low[0] = low, mon.close[0] = close;
    Markets m = weekend(mon);
    Script s{.at = {{0, {o}}}};
    Result r = backtest(s, m, free_costs(m), PortfolioSettings{});
    check_value("levels: one trade", r.trades.size(), 1);
    return r.trades.empty() ? Trade{} : r.trades[0];
}

void test_reopen_gap_past_levels() {
    // Long at 200, stop 180; Monday opens at 170: fills at the open.
    Trade stop = levels(open_long("Y", 0.1), 170, 175, 165, 172);
    check_true("stop cause", stop.cause == Cause::Stop);
    check_value("stop at the Monday open", stop.exit_price, 170);
    check_value("stop on Monday's first bar", stop.exit_time, MON);

    // Long at 200, take profit 220; Monday opens at 230: fills at the open.
    Order tp = open_long("Y", 0.1);
    tp.take_profit_distance = 0.1;
    Trade take = levels(tp, 230, 235, 228, 232);
    check_true("take profit cause", take.cause == Cause::TakeProfit);
    check_value("take profit at the Monday open", take.exit_price, 230);
    check_value("take profit on Monday's first bar", take.exit_time, MON);

    // 10x long at 200, stop 190. Risk 100 / 10 = size 10, collateral 200,
    // liquidation 200 - 0.85 * 200 / 10 = 183. Monday opens at 170, past
    // both: fills at 183.
    Order lev = open_long("Y", 0.05);
    lev.leverage = 10;
    Trade liq = levels(lev, 170, 175, 165, 172);
    check_true("liquidation cause", liq.cause == Cause::Liquidation);
    check_value("liquidation price", liq.exit_price, 183);
    check_value("liquidation on Monday's first bar", liq.exit_time, MON);
}

// --- 7. No holding cost in the gap.

void test_no_holding_in_gap() {
    // Y has 8 bars; hold_long[i] = 0.001 * (i + 1). Long Y at 200 (step 1,
    // Y bar 1), stop 0.1: size 100 / 20 = 5. Closed by an order filled at
    // step 54 (Y bar 6), before that step's charge. Charged on Y bars 1-3
    // at close 200 and 4-5 at close 210:
    // 5 * (200 * (0.002 + 0.003 + 0.004) + 210 * (0.005 + 0.006)) = 20.55.
    Markets m = weekend();
    MarketCosts c = free_costs(m);
    for (int i = 0; i < 8; ++i) c["Y"].hold_long.push_back(0.001 * (i + 1));
    Script s{.at = {{0, {open_long("Y", 0.1)}}, {53, {close_order("Y")}}}};
    Result r = backtest(s, m, c, PortfolioSettings{});
    check_value("one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_value("holding only on Y's bars", r.trades[0].holding_costs, 20.55);
    // Gross 5 * (210 - 200) = 50.
    check_value("result", r.trades[0].result, 50 - 20.55);
}

// --- 8. Time exits count bars.

void test_bars_held() {
    // No gaps: hourly bars 0-9; entry 2:00, now 7:00: 5 bars.
    Bars b = flat(Timeframe::Hour1, hours(0, 10), 1);
    check_value("no gap", bars_held(b, 2 * H, 7 * H), 5);
    check_value("no gap, one bar", bars_held(b, 2 * H, 3 * H), 1);
    check_value("no gap, at the entry", bars_held(b, 2 * H, 2 * H), 0);

    // Fri 22:00, Fri 23:00, Mon 00:00, Mon 01:00; entry Fri 23:00.
    Bars w = flat(Timeframe::Hour1, {FRI + 22 * H, FRI + 23 * H, MON, MON + H}, 1);
    check_value("Sat 00:00", bars_held(w, FRI + 23 * H, SAT), 1);
    check_value("the weekend counts 0", bars_held(w, FRI + 23 * H, MON), 1);
    check_value("Mon 01:00", bars_held(w, FRI + 23 * H, MON + H), 2);
    check_value("Mon 02:00", bars_held(w, FRI + 23 * H, MON + 2 * H), 3);

    // Entry mid-bar on 4-hour bars (0, 4, 8, 12 h): the 0:00 bar does not
    // count; the 4:00 bar is the first.
    Bars f = flat(Timeframe::Hour4, {0, 4 * H, 8 * H, 12 * H}, 1);
    check_true("mid-bar, none yet", bars_held(f, 90 * 60, 4 * H) <= 0);
    check_value("mid-bar, one", bars_held(f, 90 * 60, 8 * H), 1);
    check_value("mid-bar, two", bars_held(f, 90 * 60, 12 * H), 2);
}

Position long_x(int64_t entry_time) {
    return Position{.instrument = "X", .side = Side::Long, .entry_price = 100, .size = 1, .stop_price = 50,
                    .take_profit_price = std::nan(""), .entry_time = entry_time};
}

// Hourly X: Fri 12:00-23:00 (bars 0-11), then Mon 00:00-05:00 (bars 12-17).
Markets gapped_x() {
    return Markets::make({{"X", {flat(Timeframe::Hour1, join(hours(FRI + 12 * H, 12), hours(MON, 6)), 100)}}});
}

// Whether a fresh strategy, prepared on m, closes the position at now.
template <class S>
bool closes(S s, const Markets& m, const Position& p, int64_t now) {
    s.prepare(m);
    std::vector<Order> o = s.decide(now, Report{}, {p});
    return o.size() == 1 && o[0].kind == Order::Kind::Close;
}

void test_library_time_exits() {
    // Entry Fri 23:00 (bar 11); a 3-bar time exit. At Mon 01:00 two bars
    // have closed (Fri 23:00, Mon 00:00): stay. At Mon 02:00, three: out.
    // A calendar count would close at Mon 01:00 (50 hours).
    Markets m = gapped_x();
    RsiSnap rsi;
    rsi.params.instrument = "X";
    rsi.params.timeframe = Timeframe::Hour1;
    rsi.params.hold = 3;
    rsi.params.exit_period = 1000;  // no rule exit: its average never has a value
    BandScalper band;
    band.params.instrument = "X";
    band.params.timeframe = Timeframe::Hour1;
    band.params.max_bars = 3;
    Position p = long_x(FRI + 23 * H);
    check_true("rsi_snap stays at Mon 01:00", !closes(rsi, m, p, MON + H));
    check_true("rsi_snap out at Mon 02:00", closes(rsi, m, p, MON + 2 * H));
    check_true("band_scalper stays at Mon 01:00", !closes(band, m, p, MON + H));
    check_true("band_scalper out at Mon 02:00", closes(band, m, p, MON + 2 * H));
    // No entry time: no time exit.
    check_true("rsi_snap unknown time", !closes(rsi, m, long_x(unknown_time), MON + 4 * H));
    check_true("band_scalper unknown time", !closes(band, m, long_x(unknown_time), MON + 4 * H));

    // No gaps: same as the calendar. Entry 5:00, out at 8:00, not 7:00.
    Markets plain = Markets::make({{"X", {flat(Timeframe::Hour1, hours(0, 20), 100)}}});
    check_true("rsi_snap no gap stays", !closes(rsi, plain, long_x(5 * H), 7 * H));
    check_true("rsi_snap no gap out", closes(rsi, plain, long_x(5 * H), 8 * H));
    check_true("band_scalper no gap stays", !closes(band, plain, long_x(5 * H), 7 * H));
    check_true("band_scalper no gap out", closes(band, plain, long_x(5 * H), 8 * H));
}

// --- 9. Live replay on gapped data.

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

// Bars on tf from hourly closes at t0 + i hours, only where have[i]; a
// bucket with no hour is left out, as resample(keep_empty=False) does.
Bars from_hours(int64_t t0, const std::vector<double>& c, const std::vector<bool>& have, Timeframe tf) {
    Bars b{.timeframe = tf};
    int64_t len = seconds(tf);
    double prev = c[0];
    for (size_t i = 0; i < c.size(); ++i) {
        if (!have[i]) continue;
        int64_t ts = t0 + static_cast<int64_t>(i) * H;
        int64_t k = ts - ts % len;
        if (b.ts.empty() || b.ts.back() != k) {
            b.ts.push_back(k);
            b.open.push_back(prev);
            b.high.push_back(std::max(prev, c[i]));
            b.low.push_back(std::min(prev, c[i]));
            b.close.push_back(c[i]);
            b.minutes_with_data.push_back(60);
        } else {
            b.high.back() = std::max(b.high.back(), c[i]);
            b.low.back() = std::min(b.low.back(), c[i]);
            b.close.back() = c[i];
            b.minutes_with_data.back() += 60;
        }
        prev = c[i];
    }
    return b;
}

// Eight days of hourly and 4-hour bars from MON. Both markets miss day 1
// 10:00-15:00 (all closed: the 4-hour bar opened at 8:00 closes at 12:00,
// inside it). Y also misses all of day 3; X misses day 5 00:00-11:00.
constexpr int64_t SHUT = MON + D + 10 * H, OPEN_AGAIN = MON + D + 16 * H;
std::vector<Market> gapped_pair() {
    int n = 8 * 24;
    std::vector<Market> out;
    unsigned seed = 21;
    for (const char* name : {"X", "Y"}) {
        std::vector<double> c = walk(n, 100, 0.01, seed++);
        std::vector<bool> have(n, true);
        for (int i = 0; i < n; ++i) {
            int64_t ts = MON + i * H;
            if (ts >= SHUT && ts < OPEN_AGAIN) have[i] = false;
            if (std::string(name) == "Y" && i / 24 == 3) have[i] = false;
            if (std::string(name) == "X" && i / 24 == 5 && i % 24 < 12) have[i] = false;
        }
        out.push_back(Market{name, {from_hours(MON, c, have, Timeframe::Hour1),
                                    from_hours(MON, c, have, Timeframe::Hour4)}});
    }
    return out;
}

// On each new closed 4-hour bar of X or Y: close a held position, else open one.
struct Toggle {
    std::map<std::string, const Bars*> four;
    std::map<std::string, int> seen;
    bool trades(const std::string&) const { return true; }
    void prepare(const Markets& m) {
        four.clear();
        seen.clear();
        update(m);
    }
    void update(const Markets& m) {
        for (const char* ins : {"X", "Y"}) four[ins] = &m.at(ins, Timeframe::Hour4);
    }
    std::vector<Order> decide(int64_t now, const Report&, const std::vector<Position>& positions) {
        std::vector<Order> out;
        for (const char* ins : {"X", "Y"}) {
            int t = last_closed(*four[ins], now);
            auto it = seen.find(ins);
            if (t < 0 || (it != seen.end() && it->second == t)) continue;
            seen[ins] = t;
            bool held = std::any_of(positions.begin(), positions.end(),
                                    [&](const Position& p) { return p.instrument == ins; });
            out.push_back(held ? close_order(ins) : open_long(ins, 0.05));
        }
        return out;
    }
};
static_assert(Strategy<Toggle>);

bool same(double a, double b) { return std::memcmp(&a, &b, sizeof a) == 0; }
bool same(const Order& a, const Order& b) {
    return a.kind == b.kind && a.instrument == b.instrument && a.side == b.side &&
           same(a.stop_distance, b.stop_distance) && same(a.take_profit_distance, b.take_profit_distance) &&
           same(a.leverage, b.leverage) && same(a.trail_distance, b.trail_distance) &&
           same(a.take_profit_fraction, b.take_profit_fraction);
}
bool same(const std::vector<Order>& a, const std::vector<Order>& b) {
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); ++i) {
        if (!same(a[i], b[i])) return false;
    }
    return true;
}

struct Step {
    std::vector<Position> positions;
    std::vector<Order> orders;
};

// Backtests, then replays the bars one step at a time, appending each bar
// once it has closed, every market's bars in time order. Live decide,
// decide_once at every step, and decide by name (if given) must give the
// backtest's orders. Returns the orders per step.
template <class S>
std::vector<Step> replay(const std::string& label, const S& fresh, const std::vector<Market>& full,
                         const std::string& table = "", const Params& params = {}) {
    Markets all = Markets::make(full);
    S s = fresh;
    std::vector<Step> steps;
    backtest(s, all, free_costs(all), PortfolioSettings{},
             [&](int, const std::vector<Position>& p, const std::vector<Order>& o) { steps.push_back({p, o}); });

    std::vector<Market> first;
    for (const Market& m : full) {
        Market f{m.instrument, {}};
        for (const Bars& b : m.timeframes) {
            f.timeframes.push_back(Bars{.timeframe = b.timeframe, .ts = {b.ts[0]}, .open = {b.open[0]},
                                        .high = {b.high[0]}, .low = {b.low[0]}, .close = {b.close[0]},
                                        .minutes_with_data = {b.minutes_with_data[0]}});
        }
        first.push_back(f);
    }
    Markets live = Markets::make(first);
    Live l = live_of(fresh);
    l.prepare(live);
    for (size_t t = 0; t < steps.size(); ++t) {
        int64_t now = bar_end(all.timeframe(), all.clock()[t]);
        for (const Market& m : full) {
            for (const Bars& b : m.timeframes) {
                for (size_t i = live.at(m.instrument, b.timeframe).ts.size();
                     i < b.ts.size() && bar_end(b.timeframe, b.ts[i]) <= now; ++i) {
                    live.append(m.instrument, b.timeframe,
                                Bar{b.ts[i], b.open[i], b.high[i], b.low[i], b.close[i], b.minutes_with_data[i]});
                }
            }
        }
        l.update(live);
        const std::vector<Order>& want = steps[t].orders;
        if (!same(l.decide(now, Report{}, steps[t].positions), want)) {
            ++failures;
            std::printf("FAIL %s: live orders differ at step %zu\n", label.c_str(), t);
            return steps;
        }
        if (!same(decide_once(live_of(fresh), live, steps[t].positions, now), want)) {
            ++failures;
            std::printf("FAIL %s: decide_once differs at step %zu (clock %lld)\n", label.c_str(), t,
                        static_cast<long long>(all.clock()[t]));
            return steps;
        }
        if (!table.empty() && !same(avbt::decide(table, params, live, steps[t].positions, now), want)) {
            ++failures;
            std::printf("FAIL %s: decide by name differs at step %zu\n", label.c_str(), t);
            return steps;
        }
    }
    return steps;
}

void test_live_replay_with_gaps() {
    std::vector<Market> data = gapped_pair();
    Markets all = Markets::make(data);
    // The step that reopens after the all-closed gap.
    auto it = std::find(all.clock().begin(), all.clock().end(), OPEN_AGAIN);
    check_true("clock skips the all-closed gap",
               it != all.clock().end() && *(it - 1) == SHUT - H);
    int reopen = static_cast<int>(it - all.clock().begin());

    std::vector<Step> steps = replay("Toggle", Toggle{}, data);
    int orders = 0;
    for (const Step& s : steps) orders += static_cast<int>(s.orders.size());
    check_true("Toggle gives orders", orders > 10);
    // X's 4-hour bar opened at 8:00 closed at 12:00, in the gap; the
    // backtest acts on it at the step after the gap.
    if (reopen < static_cast<int>(steps.size())) {
        const std::vector<Order>& o = steps[reopen].orders;
        check_true("acts on the bar that closed in the gap",
                   std::any_of(o.begin(), o.end(), [](const Order& x) { return x.instrument == "X"; }));
    }

    DipBasket dip;
    dip.params.timeframe = Timeframe::Hour1;
    dip.params.trend_timeframe = Timeframe::Hour4;
    dip.params.trend_period = 5;
    dip.params.lookback = 2;
    dip.params.atr_period = 3;
    dip.params.drop = 0.5;
    dip.params.hold = 3;
    dip.params.breadth = 0.0;
    replay("dip_basket", dip, data, "dip_basket",
           {{"timeframe", Timeframe::Hour1}, {"trend_timeframe", Timeframe::Hour4}, {"trend_period", 5},
            {"lookback", 2}, {"atr_period", 3}, {"drop", 0.5}, {"hold", 3}, {"breadth", 0.0}});

    RsiSnap rsi;
    rsi.params.instrument = "Y";
    rsi.params.timeframe = Timeframe::Hour4;
    rsi.params.trend_period = 3;
    rsi.params.low = 30;
    rsi.params.hold = 2;
    replay("rsi_snap", rsi, data, "rsi_snap",
           {{"instrument", std::string("Y")}, {"timeframe", Timeframe::Hour4}, {"trend_period", 3},
            {"low", 30.0}, {"hold", 2}});
}

// --- 10. Combined keeps a waiting claim.

void test_combined_keeps_waiting_claim() {
    // A opens Y long at step 3 (Fri 23:00, decided at Sat 00:00); it waits
    // to Mon 00:00. B's short opens on Y at steps 4 and 10 (Saturday) are
    // dropped: A's order still waits. A closes Y at step 53 (fills step
    // 54). B's open at step 54 passes: A's claim is over.
    Markets m = weekend();
    Order shorty{.instrument = "Y", .side = Side::Short, .stop_distance = 0.1};
    Combined<Script, Script> c;
    c.a.at = {{3, {open_long("Y", 0.1)}}, {53, {close_order("Y")}}};
    c.b.at = {{4, {shorty}}, {10, {shorty}}, {54, {shorty}}};
    std::vector<std::vector<Order>> seen;
    Result r = backtest(c, m, free_costs(m), PortfolioSettings{},
                        [&](int, const std::vector<Position>&, const std::vector<Order>& o) { seen.push_back(o); });
    check_value("A's open goes out", seen.at(3).size(), 1);
    check_true("B dropped at step 4", seen.at(4).empty());
    check_true("B dropped at step 10", seen.at(10).empty());
    check_value("B's later open goes out", seen.at(54).size(), 1);
    check_value("one trade", r.trades.size(), 1);
    if (!r.trades.empty()) {
        check_true("A's long", r.trades[0].side == Side::Long);
        check_value("A's entry Mon 00:00", r.trades[0].entry_time, MON);
    }
    check_value("B holds Y at the end", r.open_positions.size(), 1);
    if (!r.open_positions.empty()) {
        check_true("B's short", r.open_positions[0].side == Side::Short);
        check_value("B's entry Mon 03:00", r.open_positions[0].entry_time, MON + 3 * H);
    }
}

// --- 11. dip_basket: a closed first market does not stop the others.

void test_dip_basket_first_market_closed() {
    // X (the first market) has hours 0-3 and 8-11, rising. Y has hours
    // 0-11: 100, 110, 120, 130, 140, 138, then 138. lookback 1, ATR 1: at
    // Y's 5:00 bar the move is -2 / 2 = -1 ATR, past drop 0.5; the 3-bar
    // trend average (130 + 140 + 138) / 3 = 136 is below 138. Decided at
    // 6:00, while X is closed; fills at Y's 6:00 open, 138.
    Bars x = flat(Timeframe::Hour1, join(hours(MON, 4), hours(MON + 8 * H, 4)), {100, 101, 102, 103, 104, 105, 106, 107});
    Bars y = flat(Timeframe::Hour1, hours(MON, 12), {100, 110, 120, 130, 140, 138, 138, 138, 138, 138, 138, 138});
    Markets m = Markets::make({{"X", {x}}, {"Y", {y}}});
    Params p{{"timeframe", Timeframe::Hour1}, {"trend_timeframe", Timeframe::Hour1}, {"trend_period", 3},
             {"lookback", 1}, {"atr_period", 1}, {"drop", 0.5}, {"breadth", 0.0}, {"shorts", false},
             {"hold", 12}};
    Result r = run("dip_basket", p, m, free_costs(m), PortfolioSettings{});
    std::vector<std::pair<int64_t, double>> entries;
    for (const Trade& t : r.trades) if (t.instrument == "Y") entries.emplace_back(t.entry_time, t.entry_price);
    for (const Position& q : r.open_positions) if (q.instrument == "Y") entries.emplace_back(q.entry_time, q.entry_price);
    check_value("dip_basket: one Y entry", entries.size(), 1);
    if (entries.empty()) return;
    check_value("dip_basket: Y entry 6:00", entries[0].first, MON + 6 * H);
    check_value("dip_basket: Y entry price", entries[0].second, 138);
}

// --- 12. No gaps: unchanged.

// Bars on tf from 15-minute closes at t0, t0 + 900, ...
Bars agg(int64_t t0, const std::vector<double>& c, Timeframe tf) {
    Bars b{.timeframe = tf};
    int64_t len = seconds(tf);
    for (size_t i = 0; i < c.size(); ++i) {
        int64_t ts = t0 + static_cast<int64_t>(i) * 900;
        int64_t k = ts - ts % len;
        double prev = i ? c[i - 1] : c[0];
        if (b.ts.empty() || b.ts.back() != k) {
            b.ts.push_back(k);
            b.open.push_back(prev);
            b.high.push_back(std::max(prev, c[i]));
            b.low.push_back(std::min(prev, c[i]));
            b.close.push_back(c[i]);
            b.minutes_with_data.push_back(15);
        } else {
            b.high.back() = std::max(b.high.back(), c[i]);
            b.low.back() = std::min(b.low.back(), c[i]);
            b.close.back() = c[i];
            b.minutes_with_data.back() += 15;
        }
    }
    return b;
}

void test_no_gaps_unchanged() {
    // Two markets, 30 days of 15-minute bars with 1-hour and 4-hour bars.
    // The expected numbers come from the engine before this change
    // (commit d914508), on the same data with the same fees.
    int n = 30 * 96;
    std::vector<Market> data;
    unsigned seed = 3;
    for (const char* name : {"X", "Y"}) {
        std::vector<double> c = walk(n, 100, 0.006, seed++);
        data.push_back(Market{name, {agg(0, c, Timeframe::Min15), agg(0, c, Timeframe::Hour1),
                                     agg(0, c, Timeframe::Hour4)}});
    }
    Markets m = Markets::make(data);
    MarketCosts costs;
    for (const Market& x : m.all()) costs[x.instrument] = Costs{.open_fee = 0.0005, .close_fee = 0.0005, .spread = no_spread};
    struct Want {
        std::string name;
        Params params;
        size_t trades;
        double balance;
        long long times;
    };
    std::vector<Want> wants = {
        {"rsi_snap", {{"instrument", std::string("X")}, {"trend_period", 50}, {"hold", 8}}, 85,
         9294.5139055014297, 262122},
        {"dip_basket", {{"drop", 1.5}, {"trend_period", 20}}, 41, 9385.3249797875797, 124930},
        {"dip_buyer", {{"instrument", std::string("Y")}, {"k", 1.5}, {"trend_period", 20}}, 21,
         9727.1091272677877, 63851},
    };
    for (const Want& w : wants) {
        Result r = run(w.name, w.params, m, costs, PortfolioSettings{});
        long long times = 0;
        for (const Trade& t : r.trades) times += t.entry_time / 900 + t.exit_time / 900;
        check_value(w.name + " no gaps: trades", r.trades.size(), w.trades);
        check_value(w.name + " no gaps: balance", r.ending_balance, w.balance);
        check_value(w.name + " no gaps: fill times", times, w.times);
    }

    // Script on two gap-free markets, as test_backtest: Y opens at 200,
    // stop 180, stopped on bar 2 at 180.
    Bars x = flat(Timeframe::Hour1, hours(0, 4), 100);
    Bars y = flat(Timeframe::Hour1, hours(0, 4), 200);
    y.low[2] = 170;
    Markets two = Markets::make({{"X", {x}}, {"Y", {y}}});
    Script s{.at = {{0, {open_long("X", 0.1), open_long("Y", 0.1)}}}};
    Result r = backtest(s, two, free_costs(two), PortfolioSettings{});
    check_value("no gaps script: one trade", r.trades.size(), 1);
    if (r.trades.empty()) return;
    check_value("no gaps script: entry", r.trades[0].entry_price, 200);
    check_value("no gaps script: exit step", r.trades[0].exit_bar, 2);
    check_value("no gaps script: exit price", r.trades[0].exit_price, 180);
}

}  // namespace

int main() {
    test_markets_accept_gaps();
    test_bar_end();
    test_last_closed_across_gap();
    test_order_waits_over_gap();
    test_order_not_started_or_ended();
    test_gap_is_not_end_of_data();
    test_reopen_gap_past_levels();
    test_no_holding_in_gap();
    test_bars_held();
    test_library_time_exits();
    test_live_replay_with_gaps();
    test_combined_keeps_waiting_claim();
    test_dip_basket_first_market_closed();
    test_no_gaps_unchanged();
    if (failures == 0) std::printf("test_gaps: all checks passed\n");
    return failures == 0 ? 0 : 1;
}
