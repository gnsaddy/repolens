from repolens.services import calibration as cal
from tests.conftest import register


def test_pav_gives_a_non_decreasing_curve_and_pools_violations():
    pairs = [(0.2, 0), (0.3, 1), (0.4, 0), (0.6, 1), (0.7, 1), (0.9, 1)]
    curve = cal.fit_isotonic(pairs)
    ys = [v for _, v in curve]
    assert ys == sorted(ys) and ys[:2] == [0.0, 0.5]  # 0.3 (yes) and 0.4 (no) are pooled to one half
    assert cal.adjust(0.1, curve) == ys[0] and cal.adjust(0.95, curve) == ys[-1]


def test_report_is_honest_about_too_few_reviews_and_flags_overconfidence():
    assert cal.report([])["status"] == "empty"
    few = cal.report([(0.9, 0), (0.8, 1), (0.85, 0)])
    assert few["status"] == "too_few" and "curve" not in few and few["bias"] == "overconfident"
    assert "not a probability" in few["message"]


def test_a_perfectly_calibrated_score_has_small_error_and_a_curve():
    pairs = [(0.9, 1)] * 9 + [(0.9, 0)] + [(0.3, 1)] * 3 + [(0.3, 0)] * 7 + [(0.5, 1)] * 5 + [(0.5, 0)] * 5
    r = cal.report(pairs)
    assert r["status"] == "ok" and r["ece"] < 0.05 and r["bias"] == "about right"
    assert r["brier"] < r["brier_if_always_base_rate"] + 0.05
    assert [b["n"] for b in r["bands"]] == [0, 10, 10, 0, 10]
    assert all(r["curve"][i][1] <= r["curve"][i + 1][1] for i in range(len(r["curve"]) - 1))


def test_endpoint_uses_only_reviewed_investigations_the_caller_can_see(client, auth, session):
    from repolens.models import Investigation, User

    u = session.query(User).first()
    for conf, st in ((0.9, "confirmed"), (0.8, "rejected"), (0.7, "unconfirmed")):
        session.add(
            Investigation(
                org_id=u.org_id,
                created_by=u.id,
                title="t",
                query="q",
                status="completed",
                confidence=conf,
                root_cause_status=st,
            )
        )
    session.commit()
    r = client.get("/api/dashboard/calibration", headers=auth).json()
    assert r["reviewed"] == 2 and r["confirmed_rate"] == 0.5 and r["status"] == "too_few"
    other = register(client, "z@other.io", "Other")
    assert client.get("/api/dashboard/calibration", headers=other).json()["reviewed"] == 0
    assert client.get("/api/dashboard/calibration").status_code == 401
