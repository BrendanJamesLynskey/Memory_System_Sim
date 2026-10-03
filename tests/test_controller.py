"""Analytic checks: the simulator against closed-form results for cases simple enough to solve by hand."""

import pytest

from memsim import AddressMapping, Request, check, ddr4_3200, hbm2e_pc, random_uniform, simulate, streaming, strided
from memsim.controller import Channel

PRESETS = {"ddr4": ddr4_3200(), "hbm": hbm2e_pc(channels=1)}


def checked(t, reqs, **kw):
    res = simulate(t, reqs, log=True, **kw)
    errs = check([c for ch in res.channels for c in ch.log], t, reqs)
    assert not errs, errs[:5]
    return res


def lat(r):
    return r.done - r.arrival


# ── unloaded latencies ───────────────────────────────────────────────
@pytest.mark.parametrize("name", PRESETS)
def test_read_to_closed_bank(name):
    t = PRESETS[name]
    r = Request(0)
    checked(t, [r])
    assert r.kind == "miss"
    assert lat(r) == t.RCD + t.CL + t.burst_cycles


@pytest.mark.parametrize("name", PRESETS)
def test_write_to_closed_bank(name):
    t = PRESETS[name]
    r = Request(0, write=True)
    checked(t, [r])
    assert lat(r) == t.RCD + t.CWL + t.burst_cycles


@pytest.mark.parametrize("name", PRESETS)
def test_row_hit(name):
    t = PRESETS[name]
    a, b = Request(0), Request(t.access_bytes * t.bankgroups, arrival=1000)   # same bank, same row
    checked(t, [a, b])
    assert b.kind == "hit"
    assert lat(b) == t.CL + t.burst_cycles


@pytest.mark.parametrize("name", PRESETS)
def test_row_conflict(name):
    t = PRESETS[name]
    m = AddressMapping(t)
    other_row = 1 << m.shift["ro"]
    a, b = Request(0), Request(other_row, arrival=1000)                       # same bank, another row
    assert m.decode(other_row).ba == m.decode(0).ba and m.decode(other_row).ro == 1
    checked(t, [a, b], mapping=m)
    assert b.kind == "conflict"
    assert lat(b) == t.RP + t.RCD + t.CL + t.burst_cycles


@pytest.mark.parametrize("name", PRESETS)
def test_closed_page_turns_hits_into_misses(name):
    t = PRESETS[name]
    a, b = Request(0), Request(t.access_bytes * t.bankgroups, arrival=1000)
    checked(t, [a, b], page="closed")
    assert b.kind == "miss" and lat(b) == t.RCD + t.CL + t.burst_cycles


# ── bandwidth limits ────────────────────────────────────────────────
@pytest.mark.parametrize("name", PRESETS)
def test_streaming_reaches_peak_without_refresh(name):
    t = PRESETS[name]
    res = checked(t, streaming(t, 4000), refresh=False)
    assert 0.99 <= res.efficiency() <= 1.0


@pytest.mark.parametrize("name", PRESETS)
def test_refresh_costs_about_rfc_over_refi(name):
    t = PRESETS[name]
    on = checked(t, streaming(t, 12000)).efficiency()
    off = checked(t, streaming(t, 12000), refresh=False).efficiency()
    assert (off - on) / off == pytest.approx(t.RFC / t.REFI, abs=0.015)


def test_bank_group_mapping_halves_ddr4_streaming():
    t = PRESETS["ddr4"]
    good = checked(t, streaming(t, 4000), refresh=False).efficiency()
    bad = checked(t, streaming(t, 4000), mapping=AddressMapping(t, "RoRaBgBaCoCh"), refresh=False).efficiency()
    # back-to-back reads in one bank group are CCD_L apart instead of CCD_S: about half the bandwidth.
    # Slightly more than half, because FR-FCFS mixes the two bank groups near each group boundary.
    assert good * t.CCD_S / t.CCD_L <= bad <= 1.1 * good * t.CCD_S / t.CCD_L


@pytest.mark.parametrize("name", PRESETS)
def test_same_bank_row_stride_is_limited_by_trc(name):
    t = PRESETS[name]
    m = AddressMapping(t)
    stride = 1 << m.shift["ro"]                                               # next row, same bank
    res = checked(t, strided(t, 600, stride), mapping=m, refresh=False)
    assert res.efficiency() == pytest.approx(t.burst_cycles / t.RC, rel=0.02)


@pytest.mark.parametrize("name", PRESETS)
def test_random_reads_obey_and_approach_the_faw_bound(name):
    t = PRESETS[name]
    res = checked(t, random_uniform(t, 6000, 1 << 30, seed=3), page="closed", refresh=False)
    acts = res.commands()["ACT"]
    span = res.cycles
    assert acts <= 4 * span / t.FAW + 4                                        # never more than 4 per FAW
    bound = min(1.0, 4 * t.burst_cycles / t.FAW)                               # one ACT per random request
    assert 0.8 * bound <= res.efficiency() <= bound + 1e-9


def test_frfcfs_beats_fcfs():
    for t in PRESETS.values():
        for gen, args in ((streaming, (4000,)), (random_uniform, (3000, 1 << 26, 1))):
            fr = simulate(t, gen(t, *args), scheduler="frfcfs").efficiency()
            fc = simulate(t, gen(t, *args), scheduler="fcfs").efficiency()
            assert fr >= fc


def test_xor_bank_mapping_spreads_a_row_stride():
    t = PRESETS["ddr4"]
    m0, m1 = AddressMapping(t), AddressMapping(t, xor_bank=True)
    stride = 1 << m0.shift["ro"]
    plain = checked(t, strided(t, 800, stride), mapping=m0, refresh=False).efficiency()
    xored = checked(t, strided(t, 800, stride), mapping=m1, refresh=False).efficiency()
    assert xored > 3 * plain


def test_channels_add_bandwidth():
    t1, t4 = hbm2e_pc(channels=1), hbm2e_pc(channels=4)
    r1 = simulate(t1, streaming(t1, 4000), refresh=False)
    r4 = simulate(t4, streaming(t4, 16000), refresh=False)
    assert r4.bandwidth_gbps() == pytest.approx(4 * r1.bandwidth_gbps(), rel=0.02)


def test_queue_depth_one_serialises():
    t = PRESETS["ddr4"]
    reqs = random_uniform(t, 200, 1 << 26, seed=2)
    deep = simulate(t, reqs, queue=32, refresh=False).efficiency()
    reqs = random_uniform(t, 200, 1 << 26, seed=2)
    shallow = simulate(t, reqs, queue=1, refresh=False).efficiency()
    assert shallow < deep / 3


def test_bad_policy_names():
    with pytest.raises(ValueError):
        Channel(PRESETS["ddr4"], scheduler="lifo")
    with pytest.raises(ValueError):
        Channel(PRESETS["ddr4"], page="adaptive")


def test_regression_wtr_l_after_a_later_write_to_another_group():
    """Found by Hypothesis: WR (group 0), WR (group 1), RD (group 0). The read must wait WTR_L after
    the group-0 write, not just WTR_S after the later group-1 write."""
    t = PRESETS["ddr4"]
    m = AddressMapping(t)
    g0, g1 = 0, 1 << m.shift["bg"]
    reqs = [Request(g0, write=True), Request(g1, write=True), Request(g0 + (1 << m.shift["co"]))]
    checked(t, reqs, mapping=m, refresh=False)
    wr0, rd = reqs[0], reqs[2]
    assert rd.issue >= wr0.done + t.WTR_L
