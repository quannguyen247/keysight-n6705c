"""Bounded explicit-state check of the device-to-gateway layer described in the paper.

AEAD frames are symbolic terms (key, device, counter, payload) that only a key holder can build;
the nonce is the counter. Two honest devices send frames, a third device is compromised, and the
adversary sees every frame. It can deliver any seen frame in any order and any number of times,
and it can build frames under the keys it holds. Counters are bounded, so the reachable state
space is finite; the search visits all of it (or every interleaving up to --depth actions) and
checks:

  P0  nonce uniqueness: no key encrypts two different frames under the same counter
  P1  origin: every accepted frame that claims an honest device was sent by that device
  P2  freshness: no frame of an honest device is accepted twice

The design uses per-device keys, counters that persist across resets, and a gateway that keeps
the last accepted counter per device. Each other variant drops one of these requirements.
Standard library only.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass

HONEST = (1, 2)
COMPROMISED = 3
DEVICES = HONEST + (COMPROMISED,)
PAYLOADS = ("a", "b")
MAX_COUNTER = 2


def configure(honest: int, counters: int) -> None:
    """Set the model bounds: number of honest devices and highest counter value."""
    global HONEST, COMPROMISED, DEVICES, MAX_COUNTER
    HONEST = tuple(range(1, honest + 1))
    COMPROMISED = honest + 1
    DEVICES = HONEST + (COMPROMISED,)
    MAX_COUNTER = counters


@dataclass(frozen=True)
class Variant:
    name: str
    group_key: bool = False      # one key shared by every device instead of per-device keys
    device_reset: bool = False   # an honest device may restart its counter once without re-keying
    gateway_reset: bool = False  # the gateway may lose its counter table once


VARIANTS = (
    Variant("design"),
    Variant("shared group key", group_key=True),
    Variant("device counter reset without re-keying", device_reset=True),
    Variant("gateway loses its counter table", gateway_reset=True),
)


def key_of(variant: Variant, device: int) -> str:
    return "kG" if variant.group_key else f"k{device}"


def with_item(values: tuple, index: int, value) -> tuple:
    return values[:index] + (value,) + values[index + 1:]


def successors(variant: Variant, state: tuple):
    counters, sent, last, accepted, device_reset_used, gateway_reset_used = state
    for device in HONEST:
        i = device - 1
        if counters[i] < MAX_COUNTER:
            counter = counters[i] + 1
            for payload in PAYLOADS:
                frame = (key_of(variant, device), device, counter, payload)
                yield (f"device {device} sends counter {counter}, payload {payload}",
                       (with_item(counters, i, counter), sent | {frame}, last, accepted,
                        device_reset_used, gateway_reset_used))
        if variant.device_reset and not device_reset_used and counters[i] > 0:
            yield (f"device {device} restarts its counter",
                   (with_item(counters, i, 0), sent, last, accepted, True, gateway_reset_used))
    if variant.gateway_reset and not gateway_reset_used:
        yield ("gateway loses its counter table",
               (counters, sent, (0,) * len(DEVICES), accepted, device_reset_used, True))
    held = key_of(variant, COMPROMISED)
    forged = {(held, d, c, p) for d in DEVICES for c in range(1, MAX_COUNTER + 1) for p in PAYLOADS}
    for frame in sorted(sent | forged):
        key, device, counter, _ = frame
        if key == key_of(variant, device) and counter > last[device - 1]:
            yield (f"adversary delivers {frame}",
                   (counters, sent, with_item(last, device - 1, counter),
                    tuple(sorted(accepted + (frame,))), device_reset_used, gateway_reset_used))


def violations(state: tuple) -> set[str]:
    _, sent, _, accepted, _, _ = state
    found = set()
    by_nonce: dict[tuple, set] = {}
    for key, device, counter, payload in sent:
        by_nonce.setdefault((key, counter), set()).add((device, payload))
    if any(len(frames) > 1 for frames in by_nonce.values()):
        found.add("P0")
    if any(frame[1] in HONEST and frame not in sent for frame in accepted):
        found.add("P1")
    honest = [frame for frame in accepted if frame[1] in HONEST]
    if len(set(honest)) < len(honest):
        found.add("P2")
    return found


def search(variant: Variant, depth: int | None) -> tuple[dict[str, list[str]], int]:
    start = ((0,) * len(HONEST), frozenset(), (0,) * len(DEVICES), (), False, False)
    parents = {start: None}
    queue = deque([(start, 0)])
    first: dict[str, list[str]] = {}
    while queue:
        state, level = queue.popleft()
        for prop in violations(state) - set(first):
            trace, node = [], state
            while parents[node] is not None:
                node, action = parents[node]
                trace.append(action)
            first[prop] = trace[::-1]
        if level == depth:
            continue
        for action, nxt in successors(variant, state):
            if nxt not in parents:
                parents[nxt] = (state, action)
                queue.append((nxt, level + 1))
    return first, len(parents)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--depth", type=int, help="stop after this many actions (default: explore every reachable state)")
    parser.add_argument("--honest", type=int, default=len(HONEST), help="number of honest devices")
    parser.add_argument("--counters", type=int, default=MAX_COUNTER, help="highest counter value per device")
    parser.add_argument("--variant", choices=[v.name for v in VARIANTS], help="check one variant only")
    args = parser.parse_args()
    configure(args.honest, args.counters)
    print(f"{args.honest} honest devices, 1 compromised device, counters up to {args.counters}")
    for variant in (v for v in VARIANTS if args.variant in (None, v.name)):
        found, states = search(variant, args.depth)
        scope = "all reachable states" if args.depth is None else f"up to depth {args.depth}"
        print(f"{variant.name}: {states} states ({scope})")
        for prop in ("P0", "P1", "P2"):
            if prop in found:
                print(f"  {prop} violated in {len(found[prop])} actions: " + "; ".join(found[prop]))
            else:
                print(f"  {prop} holds")


if __name__ == "__main__":
    main()
