from financial_dashboard.engines import (
    OrderBlockConfig,
    OrderBlockDataQuality,
    OrderBlockEngine,
    OrderBlockExport,
    OrderBlockRecord,
    OrderBlockSideExport,
)


def test_order_block_final_public_surface() -> None:
    assert OrderBlockEngine.__module__ == "financial_dashboard.engines.order_block"
    assert OrderBlockConfig().fill_cancel_threshold == 0.70
    assert OrderBlockRecord.__name__ == "OrderBlockRecord"
    assert OrderBlockExport.__name__ == "OrderBlockExport"
    assert OrderBlockSideExport.__name__ == "OrderBlockSideExport"
    assert OrderBlockDataQuality.OK.value == "OK"
