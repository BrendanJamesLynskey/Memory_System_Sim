"""An independent protocol checker: replays a command log and reports every timing violation.

The controller decides *when* commands may issue; this module only checks the
result, with its own bookkeeping and no code shared with controller.py. That
independence is the point: a constraint the controller forgets is caught here,
because the checker states each rule directly ("any two ACTs to one bank are at
least RC apart"), not as the incremental earliest-time arithmetic the controller
uses. The tests run it on every random trace they generate.
"""

from __future__ import annotations

from collections import defaultdict

from .controller import Command, Request
from .timing import Timing


def check(log: list[Command], t: Timing, reqs: list[Request] | None = None) -> list[str]:
    """Return a list of violations (empty when the log obeys every rule)."""
    errs: list[str] = []
    B = t.burst_cycles
    seen_slot: set[int] = set()
    open_row: dict = {}                         # bank -> row
    last: dict = defaultdict(lambda: None)      # (event, key) -> cycle
    acts_by_rank: dict = defaultdict(list)
    bursts: list[tuple[int, int, str]] = []     # data bus occupancy (start, end, dir)

    def err(c: Command, msg: str) -> None:
        if len(errs) < 50:
            errs.append(f"t={c.t} {c.cmd} r{c.ra} bg{c.bg} b{c.ba}: {msg}")

    def gap(c: Command, key, need: int, what: str) -> None:
        prev = last[key]
        if prev is not None and c.t - prev < need:
            err(c, f"{what}: {c.t - prev} < {need}")

    for c in sorted(log, key=lambda c: (c.t, c.cmd == "PREA")):
        bank = (c.ra, c.bg, c.ba)
        if c.cmd != "PREA":                     # one command per cycle on the command bus
            if c.t in seen_slot:
                err(c, "two commands in one cycle")
            seen_slot.add(c.t)
        if last[("REF", c.ra)] is not None and c.cmd != "REF" and c.t < last[("REF", c.ra)] + t.RFC:
            err(c, "command during refresh (RFC)")
        if c.cmd == "ACT":
            if open_row.get(bank, -1) >= 0:
                err(c, "ACT to an open bank")
            gap(c, ("ACT", bank), t.RC, "ACT-ACT same bank (RC)")
            gap(c, ("PRE", bank), t.RP, "PRE-ACT (RP)")
            prev = acts_by_rank[c.ra]
            gap(c, ("ACTR", c.ra), t.RRD_S, "ACT-ACT same rank (RRD_S)")
            gap(c, ("ACTR", c.ra, c.bg), t.RRD_L, "ACT-ACT same bank group (RRD_L)")
            last[("ACTR", c.ra)] = last[("ACTR", c.ra, c.bg)] = c.t
            if len(prev) >= 4 and c.t - prev[-4][0] < t.FAW:
                err(c, f"five ACTs within FAW: {c.t - prev[-4][0]} < {t.FAW}")
            prev.append((c.t, c.bg))
            open_row[bank] = c.ro
            last[("ACT", bank)] = c.t
        elif c.cmd in ("PRE", "PREA"):
            if open_row.get(bank, -1) < 0:
                err(c, "PRE to a closed bank")
            gap(c, ("ACT", bank), t.RAS, "ACT-PRE (RAS)")
            gap(c, ("RD", bank), t.RTP, "RD-PRE (RTP)")
            gap(c, ("WR", bank), t.CWL + B + t.WR, "WR-PRE (write recovery)")
            open_row[bank] = -1
            last[("PRE", bank)] = c.t
        elif c.cmd in ("RD", "WR"):
            if open_row.get(bank, -1) != c.ro:
                err(c, f"column command to row {c.ro}, open row {open_row.get(bank, -1)}")
            gap(c, ("ACT", bank), t.RCD, "ACT-column (RCD)")
            for bg_key, need in ((("COL", c.ra, c.bg), t.CCD_L), (("COL", c.ra), t.CCD_S)):
                gap(c, bg_key, need, "column-column (CCD)")
            if c.cmd == "RD":
                gap(c, ("WR", c.ra, c.bg), t.CWL + B + t.WTR_L, "WR-RD same bank group (WTR_L)")
                gap(c, ("WR", c.ra), t.CWL + B + t.WTR_S, "WR-RD (WTR_S)")
                start = c.t + t.CL
            else:
                start = c.t + t.CWL
            bursts.append((start, start + B, "r" if c.cmd == "RD" else "w"))
            last[("COL", c.ra, c.bg)] = last[("COL", c.ra)] = c.t
            last[(c.cmd, bank)] = c.t
            if c.cmd == "WR":
                last[("WR", c.ra, c.bg)] = last[("WR", c.ra)] = c.t
        elif c.cmd == "REF":
            if any(r >= 0 for (ra, _, _), r in open_row.items() if ra == c.ra):
                err(c, "REF with a bank open")
            for (ra, bg, ba), _ in list(open_row.items()):
                if ra == c.ra:
                    gap(c, ("PRE", (ra, bg, ba)), t.RP, "PRE-REF (RP)")
            last[("REF", c.ra)] = c.t
    bursts.sort()
    for (_, e0, d0), (s1, _, d1) in zip(bursts, bursts[1:], strict=False):
        need = t.RTRW if (d0, d1) == ("r", "w") else 0
        if s1 < e0 + need:
            errs.append(f"data bus: burst at {s1} overlaps or turns too fast after burst ending {e0} ({d0}->{d1})")
            break
    if reqs is not None:
        for r in reqs:
            if r.done < 0:
                errs.append(f"request {r.id} never served")
                break
            if r.issue < r.arrival:
                errs.append(f"request {r.id} issued before it arrived")
                break
    return errs
