from financial_dashboard.decision import buy, sell, trade_lifecycle
from financial_dashboard.decision import eligibility, engine, execution, lifecycle, trade_exit


def _assert_same_surface(surface, owner_modules):
    for name in surface.__all__:
        assert hasattr(surface, name)
        assert any(getattr(surface, name) is getattr(owner, name, object()) for owner in owner_modules)


def test_buy_package_surface_still_exports_canonical_objects():
    _assert_same_surface(buy, (eligibility, engine, execution))


def test_sell_package_surface_still_exports_canonical_objects():
    _assert_same_surface(sell, (trade_exit,))


def test_trade_lifecycle_package_surface_still_exports_canonical_objects():
    _assert_same_surface(trade_lifecycle, (lifecycle,))
