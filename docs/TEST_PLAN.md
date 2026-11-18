# Test Plan: Simulated BMS ECU

## 1. Scope

The item under test is a Battery Management System (BMS) ECU for a 96-cell
(96s) lithium-ion pack with 100 Ah capacity. It talks to a Vehicle Control
Unit (VCU) over CAN using the messages defined in
[`src/canbench/dbc/bms.dbc`](../src/canbench/dbc/bms.dbc).

In scope: cyclic communication, end-to-end (E2E) protection, the operating
state machine, contactor control, cell protection limits, sensor
plausibility, communication-loss handling and fault latching/recovery.

Out of scope: cell balancing, SOC estimation accuracy beyond coulomb counting,
insulation monitoring, and bootloader/diagnostic (UDS) services.

## 2. Requirements

| ID | Requirement | Verification |
|---|---|---|
| BMS-001 | `BMS_Status`, `BMS_CellStats` and `BMS_Faults` are transmitted every 100 ms. The median period is within ±10 % and no gap exceeds 150 ms. | Integration |
| BMS-002 | Every BMS frame carries a valid checksum, and its 4-bit counter increments by exactly 1 (mod 16) per frame. | Unit + Integration |
| BMS-003 | After power-up the BMS reaches `STANDBY` with contactors open and no faults. | Unit + Integration |
| BMS-004 | The BMS enters `DRIVE` or `CHARGE` only on VCU request. Contactors are closed only in `DRIVE`/`CHARGE`. A direct `DRIVE`↔`CHARGE` switch is rejected; the vehicle must pass through `STANDBY`. | Unit + Integration |
| BMS-005 | Any valid cell temperature above 60 °C sets `OverTemp`, enters `FAULT` and opens the contactors within 500 ms. A failed sensor on another cell must not mask the event. | Unit + Integration |
| BMS-006 | Cell limit violations are debounced: a condition must persist for 3 consecutive 100 ms cycles to latch. Shorter excursions are ignored. | Unit |
| BMS-007 | Any cell below 2.80 V sets `UnderVoltage`; any cell above 4.25 V sets `OverVoltage`. Both lead to `FAULT` within 500 ms. | Unit + Integration |
| BMS-008 | Pack current magnitude above 500 A sets `OverCurrent` and leads to `FAULT`. Current flows only when contactors are closed. | Unit + Integration |
| BMS-009 | A temperature reading outside −40…120 °C, or a voltage reading outside 0.5…5.0 V, is a `SensorFault`. Such readings are excluded from the threshold checks, so an open thermistor is not reported as `OverTemp`. | Unit + Integration |
| BMS-010 | Once the first valid VCU frame has been received, more than 500 ms without a valid one sets `CommLoss` and leads to `FAULT` (detection budget 750 ms). Gaps shorter than the timeout are tolerated. | Unit + Integration |
| BMS-011 | VCU frames with a bad checksum or a repeated (frozen) counter are discarded and do not count as a heartbeat. | Unit + Integration |
| BMS-012 | Faults stay latched after the condition disappears. The VCU `ClearFaults` request returns the BMS to `STANDBY` only when no fault condition is active. | Unit + Integration |
| BMS-013 | Reported values are consistent: pack voltage ≈ sum of cells, voltage sags under load, current is reported, and SOC decreases while discharging. | Integration |

## 3. Test levels

| Level | Location | Target | Clock | Purpose |
|---|---|---|---|---|
| Unit | `tests/unit/` | `BmsLogic`, `BatteryPack`, protocol, fault injector | Simulated (deterministic) | Exact cycle-level behaviour: boundaries, debounce counts, timeouts |
| Integration (SIL) | `tests/integration/` | BMS on a python-can virtual bus | Real time | Black-box behaviour over CAN, timing, fault reactions |
| Integration (SocketCAN) | Same tests, run with `CANBENCH_EXTERNAL=1` | `canbench-sim` as a separate process on Linux `vcan0` | Real time | Same suite against an out-of-process ECU, as on a HIL rig |
| HIL (planned) | Same tests | BMS firmware on a microcontroller with a CAN transceiver | Real time | Validate real hardware with an unchanged suite |

Integration tests observe the target only through CAN. The battery plant is
driven through the test-only `SIM_PlantControl` message (0x700). This is the
same role a HIL simulator's I/O plays, so the suite does not depend on how
the target is hosted.

## 4. Fault injection matrix

| Fault | Injection point | Mechanism | Expected reaction | Tests |
|---|---|---|---|---|
| Cell overtemperature | Plant | `SET_CELL_TEMP` | `OverTemp`, `FAULT`, contactors open | BMS-005 |
| Cell under/overvoltage | Plant | `SET_CELL_VOLTAGE` | `UnderVoltage`/`OverVoltage`, `FAULT` | BMS-007 |
| Overcurrent | Plant | `SET_LOAD_CURRENT` | `OverCurrent`, `FAULT` | BMS-008 |
| Open thermistor | Plant | `OPEN_TEMP_SENSOR` | `SensorFault`, no `OverTemp` | BMS-009 |
| Lost VCU frames | Bus (harness TX) | `FrameFaultInjector.drop` | `CommLoss` after 500 ms; short gaps tolerated | BMS-010 |
| Corrupted VCU frames | Bus (harness TX) | `FrameFaultInjector.corrupt_checksum` | Frames ignored → `CommLoss` | BMS-011 |
| Stuck VCU counter | Bus (harness TX) | `FrameFaultInjector.freeze_counter` | Frames ignored → `CommLoss` | BMS-011 |

## 5. Pass/fail criteria and timing tolerances

* Real-time assertions use explicit budgets (reaction ≤ 500 ms, comm-loss
  detection ≤ 750 ms) with frame timestamps, not wall-clock sleeps.
* Exact boundaries (for example, 60.0 °C must not trip but 60.5 °C must) are
  checked at unit level with a simulated clock, so they are deterministic.
* Each integration test gets a fresh target, either a new simulator instance
  or a `RESET` plant command, so tests are independent and order-agnostic.

## 6. Entry and exit criteria

**Entry:** the DBC parses, unit tests pass, and the target reaches `STANDBY`.

**Exit:** all requirements report `PASS` in the traceability matrix
(`pytest --traceability reports/traceability`), there are no lint errors, and
three consecutive CI runs are green with no flaky failures.

## 7. Reporting

CI publishes JUnit XML, a self-contained HTML report and the requirement
traceability matrix for every run. The matrix is also shown in the GitHub
Actions job summary. The SocketCAN job additionally archives a `candump`
trace of the whole run for offline analysis.
