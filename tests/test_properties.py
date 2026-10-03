"""Property-based tests: any trace, any policy -> a legal command stream and every request served.

The oracle is the independent protocol checker (memsim.checker). Its own tests
are at the bottom: it must flag violations planted in a legal log and the
output of a controller with a constraint deliberately left out.
"""

import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from memsim import AddressMapping, Request, check, ddr4_3200, hbm2e_pc, random_uniform, simulate, streaming
from memsim.controller import Channel

TIMINGS = [ddr4_3200(), hbm2e_pc(channels=2), ddr4_3200(channels=2, ranks=2)]


@st.composite
def traces(draw):
    t = draw(st.sampled_from(TIMINGS))
    n = draw(st.integers(1, 120))
    span = draw(st.sampled_from([1 << 14, 1 << 20, 1 << 28]))
    rows = []
    now = 0
    for _ in range(n):
        now += draw(st.integers(0, 60))
        rows.append(Request(draw(st.integers(0, span // t.access_bytes - 1)) * t.access_bytes,
                            write=draw(st.booleans()), arrival=now))
    return t, rows


@settings(max_examples=150, deadline=None)
@given(traces(), st.sampled_from(["fcfs", "frfcfs"]), st.sampled_from(["open", "closed"]),
       st.sampled_from(["RoRaBaCoBgCh", "RoRaBgBaCoCh", "RoCoRaBgBaCh"]), st.booleans(), st.integers(1, 32),
       st.booleans())
def test_any_trace_is_legal_and_complete(tr, sched, page, scheme, xor, queue, refresh):
    t, reqs = tr
    res = simulate(t, reqs, AddressMapping(t, scheme, xor), sched, page, queue, refresh, log=True)
    log = [c for ch in res.channels for c in ch.log]
    errs = [e for ch in res.channels for e in check(ch.log, t)] + check([], t, reqs)
    assert not errs, errs[:5]
    assert all(r.done >= r.arrival + (t.CWL if r.write else t.CL) + t.burst_cycles for r in reqs)
    assert sum(c.cmd in ("RD", "WR") for c in log) == len(reqs)
    assert res.bandwidth_gbps() <= res.timing.peak_gbps() + 1e-9


# ── the checker checks out ──────────────────────────────────────────
def legal_log():
    t = ddr4_3200()
    res = simulate(t, random_uniform(t, 400, 1 << 24, seed=4), log=True)
    return t, res.channels[0].log


def test_checker_accepts_a_legal_log():
    t, log = legal_log()
    assert check(log, t) == []


def test_checker_flags_an_early_column_command():
    t, log = legal_log()
    bad = copy.deepcopy(log)
    rd = next(c for c in bad if c.cmd == "RD")
    act = [c for c in bad if c.cmd == "ACT" and (c.ra, c.bg, c.ba) == (rd.ra, rd.bg, rd.ba) and c.t < rd.t][-1]
    rd.t = act.t + t.RCD - 1                     # one cycle too early
    assert any("RCD" in e for e in check(bad, t))


def test_checker_flags_a_missing_precharge():
    t, log = legal_log()
    i = next(k for k, c in enumerate(log) if c.cmd == "PRE")
    bad = log[:i] + log[i + 1:]
    assert any("ACT to an open bank" in e or "open row" in e for e in check(bad, t))


def test_checker_flags_a_controller_that_ignores_faw(monkeypatch):
    class NoFAW(Channel):
        def t_act(self, ra, bg, b):
            rk = self.ranks[ra]
            saved = list(rk.acts)
            rk.acts.clear()                     # pretend there is no four-activate window
            e = super().t_act(ra, bg, b)
            rk.acts.extend(saved)
            return e

    import memsim.controller as mc
    from memsim import random_uniform
    t = ddr4_3200()
    monkeypatch.setattr(mc, "Channel", NoFAW)
    res = mc.simulate(t, random_uniform(t, 400, 1 << 28, seed=6), page="closed", log=True)
    assert any("FAW" in e for e in check(res.channels[0].log, t))


def test_checker_flags_overlapping_bursts():
    t = ddr4_3200()
    res = simulate(t, streaming(t, 64), refresh=False, log=True)
    bad = copy.deepcopy(res.channels[0].log)
    rds = [c for c in bad if c.cmd == "RD"]
    rds[5].t = rds[4].t + 1
    assert check(bad, t)
