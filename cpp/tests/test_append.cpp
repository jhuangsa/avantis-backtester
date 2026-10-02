// Markets::append and append_states: growth, pointer validity, refusals.
#include <cstdio>
#include <cstdlib>
#include <stdexcept>

#include "avbt/markets.hpp"

using namespace avbt;

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

template <class F> static bool throws(F f) {
    try { f(); } catch (const std::invalid_argument&) { return true; }
    return false;
}

int main() {
    Bars b;
    b.timeframe = Timeframe::Min1;
    b.ts = {0, 60};
    b.open = b.high = b.low = b.close = {1, 2};
    b.minutes_with_data = {1, 1};
    Bars h = b;
    h.timeframe = Timeframe::Hour1;
    h.ts = {0};
    h.open = h.high = h.low = h.close = {1};
    h.minutes_with_data = {60};
    Markets ms = Markets::make({Market{"BTC", {b, h}}});

    const Bars* base = &ms.base("BTC");
    ms.append("BTC", Timeframe::Min1, Bar{120, 3, 3, 3, 3, 1});
    CHECK(base == &ms.base("BTC"));
    CHECK(base->ts.size() == 3 && base->close.back() == 3);
    CHECK(ms.clock().size() == 3 && ms.clock().back() == 120);
    CHECK(ms.bar_at("BTC", 2) == 2);

    CHECK(throws([&] { ms.append("BTC", Timeframe::Min1, Bar{120, 1, 1, 1, 1, 1}); }));
    // A base bar one minute late would leave a missing bar.
    CHECK(throws([&] { ms.append("BTC", Timeframe::Min1, Bar{240, 1, 1, 1, 1, 1}); }));
    CHECK(throws([&] { ms.append("BTC", Timeframe::Min5, Bar{300, 1, 1, 1, 1, 1}); }));
    CHECK(throws([&] { ms.append("ETH", Timeframe::Min1, Bar{180, 1, 1, 1, 1, 1}); }));
    CHECK(throws([&] { ms.append("BTC", Timeframe::Min1, Bar{180, 1, 1, 1, 1, 1, 5.0}); }));
    CHECK(base->ts.size() == 3);

    ms.append_states("BTC", 0, State{});
    ms.append_states("BTC", 60, State{});
    CHECK(ms.states("BTC")->market.size() == 2);
    CHECK(throws([&] { ms.append_states("BTC", 180, State{}); }));
    CHECK(throws([&] { ms.append_states("BTC", 150, State{}); }));

    // Base bars off their timeframe grid would let one market's clock step skip ahead; issue 8.
    Bars off = h;
    off.ts = {1800};
    CHECK(throws([&] { Markets::make({Market{"ETH", {off}}}); }));
    Bars m1 = h;
    m1.timeframe = Timeframe::Month1;
    m1.ts = {86400};
    CHECK(throws([&] { Markets::make({Market{"ETH", {m1}}}); }));
    Bars late = h;
    late.ts = {7200};
    Markets two = Markets::make({Market{"BTC", {h}}, Market{"ETH", {late}}});
    CHECK(two.clock().size() == 2);
    m1.ts = {0};
    Markets months = Markets::make({Market{"BTC", {m1}}});
    CHECK(throws([&] { months.append("BTC", Timeframe::Month1, Bar{86400, 1, 1, 1, 1, 60}); }));
    months.append("BTC", Timeframe::Month1, Bar{2678400, 1, 1, 1, 1, 60});

    if (failures == 0) std::printf("test_append: all passed\n");
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
