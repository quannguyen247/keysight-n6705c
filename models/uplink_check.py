"""Symbolic check of the authenticated ML-KEM uplink exchange described in the paper (property P3).

Terms are tuples. ("pk", x) is the public key of secret x, ("sig", s, m) a signature by key s on
term m, and ("ct", x, ss) a KEM ciphertext that encapsulates ss to the key pair of x. Anyone who
holds x can open a ciphertext encapsulated to x. Only holders of s can build signatures by s.

The device (or gateway) D runs one session with the cloud C:
  1. D -> C: epk = ("pk", "e1"), sig(sD, epk)
  2. C -> D: ct = ("ct", "e1", "ss1"), sig(sC, (ct, epk))
Both take the encapsulated secret as the session key. The adversary holds its own signing key sA,
ephemeral secret eA, and session secret ssA. It intercepts both messages and may replace each one
with any message it can build from what it holds or has seen. After the session, the long-term
keys of D and C leak; the ephemeral secret e1 is erased.

Checks: a key accepted for an honest peer stays secret before the leak (secrecy) and after it
(forward secrecy), and when D accepts, C holds the same key (agreement). Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

ADVERSARY = {"sA", "eA", "ssA", ("pk", "eA"), ("pk", "sA")}


@dataclass(frozen=True)
class Variant:
    name: str
    sign_device_key: bool = True    # C checks sig(sD, epk)
    sign_response: bool = True      # D checks sig(sC, (ct, epk))
    ephemeral: bool = True          # False: D encapsulates to C's static KEM key kC instead


VARIANTS = (
    Variant("design"),
    Variant("device key not signed", sign_device_key=False),
    Variant("cloud response not signed", sign_response=False),
    Variant("static cloud KEM key", ephemeral=False),
)


def pk(x):
    return ("pk", x)


def sig(s, m):
    return ("sig", s, m)


def ct(x, ss):
    return ("ct", x, ss)


def closure(knowledge: set) -> set:
    """Add every session secret the holder can open."""
    known = set(knowledge)
    changed = True
    while changed:
        changed = False
        for term in list(known):
            if isinstance(term, tuple) and term[0] == "ct" and term[1] in known and term[2] not in known:
                known.add(term[2])
                changed = True
    return known


def ephemeral_runs(v: Variant):
    """Yield (cloud key, device key or None, adversary view) for every adversary choice."""
    m1 = (pk("e1"), sig("sD", pk("e1")))
    for epk, sigma in product([pk("e1"), pk("eA")], [m1[1], sig("sA", pk("eA")), sig("sA", pk("e1"))]):
        if v.sign_device_key and sigma != sig("sD", epk):
            continue
        reply = ct(epk[1], "ss1")
        m2 = (reply, sig("sC", (reply, epk)))
        seen = ADVERSARY | set(m1) | {epk, sigma} | set(m2)
        yield "ss1", None, seen  # C holds its key once it replies, whether or not D finishes
        for r_ct, r_sig in product([reply, ct("e1", "ssA"), ct("eA", "ssA")],
                                   [m2[1], sig("sA", (ct("e1", "ssA"), pk("e1")))]):
            if v.sign_response and r_sig != sig("sC", (r_ct, pk("e1"))):
                continue
            device_key = r_ct[2] if r_ct[1] == "e1" else None
            yield "ss1", device_key, seen | {r_ct, r_sig}


def static_runs(v: Variant):
    sent = ct("kC", "ss1")
    for r_ct, r_sig in product([sent, ct("kC", "ssA")], [sig("sD", sent), sig("sA", ct("kC", "ssA"))]):
        if v.sign_device_key and r_sig != sig("sD", r_ct):
            continue
        yield r_ct[2], "ss1", ADVERSARY | {sent, sig("sD", sent), r_ct, r_sig}


def check(v: Variant) -> dict[str, str]:
    results = {"secrecy": "holds", "forward secrecy": "holds", "agreement": "holds"}
    leaked = {"sD", "sC"} if v.ephemeral else {"sD", "sC", "kC"}
    for cloud_key, device_key, seen in (ephemeral_runs(v) if v.ephemeral else static_runs(v)):
        before, after = closure(seen), closure(seen | leaked)
        for key in (cloud_key, device_key):
            if key is None:
                continue
            if key in before:
                results["secrecy"] = "violated"
            elif key in after:
                results["forward secrecy"] = "violated"
        if device_key is not None and device_key != cloud_key:
            results["agreement"] = "violated"
    return results


def main() -> None:
    for v in VARIANTS:
        print(f"{v.name}: " + ", ".join(f"{k} {r}" for k, r in check(v).items()))


if __name__ == "__main__":
    main()
