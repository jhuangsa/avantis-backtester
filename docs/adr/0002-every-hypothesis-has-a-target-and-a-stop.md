# Every hypothesis has a target and a stop

Superseded in part by ADR 0007. The stop stays required. The take profit may be omitted.

A price return needs a way out, and a hypothesis names one take profit and one stop loss, both distances from the entry. It may also name a rule exit and a time exit. The first of these to happen closes the whole trade. An entry with no target and no stop is not a hypothesis. Leaving the price exits optional was the alternative, and it left trades that could run to the end of the series with no stated loss.
