"""Engine parity harness — assert the Python port matches the TS engine.

Each block mirrors the assertions in the matching tests/engine/*.test.ts file,
so passing here == behaviourally equal to the Vercel TS engine.
Run:  cd ~/Container2/Hermes && python3 -m services.engine._parity
"""
from services.engine.wilson import wilson_lcb, calculate_wilson_bounds

_passed = 0


def check(cond: bool, label: str) -> None:
    global _passed
    assert cond, "FAIL: " + label
    _passed += 1


def wilson_parity() -> None:
    # mirrors tests/engine/wilson.test.ts
    check(wilson_lcb(0, 0) is None, "zero trials -> None")
    check(wilson_lcb(-1, 100) is None, "negative successes -> None")
    check(wilson_lcb(5, -10) is None, "negative trials -> None")
    check(wilson_lcb(200, 100) is None, "successes>trials -> None")
    check(abs(wilson_lcb(0, 100) - 0.0) < 0.01, "0/100 ~ 0")
    check(wilson_lcb(50, 100) <= 0.5 and wilson_lcb(50, 100) > 0, "LCB <= observed, >0")
    check(wilson_lcb(50, 500) > wilson_lcb(5, 50), "LCB grows with sample (same rate)")
    check(wilson_lcb(1, 2) < 0.25, "small sample conservative")
    check(0.48 < wilson_lcb(5000, 10000) < 0.5, "large sample -> ~observed")
    check(0.95 < wilson_lcb(100, 100) <= 1, "100% rate")
    check(0 < wilson_lcb(1, 1) < 1, "1/1")
    check(wilson_lcb(3, 3) < 0.5, "low-volume winner penalized (3/3)")
    check(wilson_lcb(10, 100) > wilson_lcb(1, 10), "10/100 > 1/10 confidence")

    b0 = calculate_wilson_bounds({})
    check(all(v is None for v in b0.values()), "zero data -> all None")
    b1 = calculate_wilson_bounds({"impressions": 10000, "linkClicks": 200})
    check(b1["ctrLcb"] is not None and 0 < b1["ctrLcb"] < 200 / 10000, "CTR LCB < observed")
    b2 = calculate_wilson_bounds({"linkClicks": 100, "landingPageViews": 70})
    check(b2["lpvPerClickLcb"] < 0.7, "LPV/click LCB < observed")
    b3 = calculate_wilson_bounds({"impressions": 5000, "linkClicks": 100})
    check(b3["lpvPerClickLcb"] == 0, "0/100 valid data -> 0 (not None)")


if __name__ == "__main__":
    wilson_parity()
    print(f"engine parity: ALL {_passed} checks PASS  (wilson)")
