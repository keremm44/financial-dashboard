from financial_dashboard.engines import OrderBlockConfig, OrderBlockEngine, OrderBlockRecord


def test_order_block_tur1_public_surface() -> None:
    assert OrderBlockEngine.__module__ == "financial_dashboard.engines.order_block_engine"
    assert OrderBlockConfig().fill_cancel_threshold == 0.70
    assert OrderBlockRecord.__name__ == "OrderBlockRecord"
