"""
Recovery Policy Optimizer (spec section 47).

A simple, honest statistical ranking of which strategies actually recover
money — not machine learning. For each strategy that has been attempted,
we compute success rate and average recovered amount from real
RecoveryAction/RecoveryCase records. This is descriptive analytics the
operator (or a future AI ranking step) can use to prefer higher-performing
strategies; it does not change agent behavior on its own.
"""
from sqlalchemy.orm import Session

from app.models import RecoveryCase, RecoveryAction


def strategy_performance(db: Session) -> list[dict]:
    """Returns, per strategy: attempts, successes, success_rate, and total/avg
    amount recovered — computed from actual case outcomes, not predictions."""
    cases = db.query(RecoveryCase).filter(RecoveryCase.recommended_strategy.isnot(None)).all()

    stats: dict[str, dict] = {}
    for c in cases:
        s = c.recommended_strategy
        row = stats.setdefault(s, {"strategy": s, "cases": 0, "recovered_cases": 0,
                                    "total_recovered": 0.0, "total_at_risk": 0.0})
        row["cases"] += 1
        row["total_at_risk"] += c.amount_at_risk
        if c.status == "RECOVERED":
            row["recovered_cases"] += 1
            row["total_recovered"] += c.amount_recovered

    results = []
    for row in stats.values():
        success_rate = round(row["recovered_cases"] / row["cases"] * 100, 1) if row["cases"] else 0
        avg_recovered = round(row["total_recovered"] / row["recovered_cases"], 2) if row["recovered_cases"] else 0
        results.append({
            "strategy": row["strategy"],
            "cases_attempted": row["cases"],
            "cases_recovered": row["recovered_cases"],
            "success_rate_pct": success_rate,
            "total_recovered": round(row["total_recovered"], 2),
            "avg_recovered_per_success": avg_recovered,
        })
    # Best-performing (by success rate, tie-broken by total recovered) first.
    results.sort(key=lambda r: (r["success_rate_pct"], r["total_recovered"]), reverse=True)
    return results


def recommend_strategy_order(db: Session, candidate_strategies: list[str]) -> list[str]:
    """Given a set of policy-eligible strategies for a case, orders them by
    historical performance (highest success rate first). Strategies with no
    track record yet keep their original relative order at the end — we
    never let sparse data make confident claims."""
    perf = {r["strategy"]: r for r in strategy_performance(db)}
    with_history = [s for s in candidate_strategies if s in perf and perf[s]["cases_attempted"] >= 5]
    without_history = [s for s in candidate_strategies if s not in with_history]
    with_history.sort(key=lambda s: perf[s]["success_rate_pct"], reverse=True)
    return with_history + without_history
