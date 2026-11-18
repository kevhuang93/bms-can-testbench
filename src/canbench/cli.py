"""Run the BMS simulator on a CAN interface, e.g. a Linux vcan0 device.

canbench-sim --interface socketcan --channel vcan0
candump -td vcan0          # in another terminal
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

import can

from canbench.bms import BmsNode
from canbench.protocol import RequestedState
from canbench.vcu import VcuSimulator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulated EV battery management system ECU")
    parser.add_argument("--interface", default="socketcan", help="python-can interface (default: socketcan)")
    parser.add_argument("--channel", default="vcan0", help="CAN channel (default: vcan0)")
    parser.add_argument(
        "--with-vcu",
        choices=[s.name.lower() for s in RequestedState if s != RequestedState.NONE],
        help="also simulate a VCU that requests this state",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    with can.Bus(interface=args.interface, channel=args.channel) as bms_bus:
        node = BmsNode(bms_bus).start()
        vcu_bus = vcu = None
        if args.with_vcu:
            vcu_bus = can.Bus(interface=args.interface, channel=args.channel)
            vcu = VcuSimulator(vcu_bus)
            vcu.requested_state = RequestedState[args.with_vcu.upper()]
            vcu.start()
        logging.info("BMS simulator running on %s:%s (Ctrl-C to stop)", args.interface, args.channel)
        stop.wait()
        if vcu is not None:
            vcu.stop()
            vcu_bus.shutdown()
        node.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
