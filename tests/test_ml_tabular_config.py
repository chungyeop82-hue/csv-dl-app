"""TabularML 설정 검증(torch 불필요)."""

from __future__ import annotations

import pytest

from app.ml.tabular import PRESETS, TabularConfig, TabularML, STOP_REASON_LABELS


def test_presets_match_spec():
    assert PRESETS["small"] == ((64, 32), 0.1)
    assert PRESETS["medium"] == ((128, 64, 32), 0.2)
    assert PRESETS["large"] == ((256, 128, 64), 0.3)
    c = TabularConfig.from_preset("regression", "medium", max_epochs=5)
    assert c.hidden_layers == (128, 64, 32) and c.dropout == 0.2 and c.max_epochs == 5
    d = TabularConfig("classification")
    assert (d.learning_rate, d.batch_size, d.max_epochs, d.patience, d.seed, d.early_stopping) == (0.001, 64, 100, 10, 42, True)


@pytest.mark.parametrize("kw", [
    {"task": "clustering"},
    {"task": "regression", "hidden_layers": ()},
    {"task": "regression", "hidden_layers": (32,) * 5},
    {"task": "regression", "hidden_layers": (8,)},
    {"task": "regression", "hidden_layers": (512,)},
    {"task": "regression", "dropout": 0.6},
    {"task": "regression", "learning_rate": 0.1},
    {"task": "regression", "learning_rate": 0.00001},
    {"task": "regression", "batch_size": 100},
    {"task": "regression", "max_epochs": 0},
    {"task": "regression", "max_epochs": 201},
    {"task": "regression", "patience": 0},
])
def test_out_of_range_config_is_rejected(kw):
    with pytest.raises(ValueError):
        TabularConfig(**kw)


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError):
        TabularConfig.from_preset("regression", "huge")


def test_use_before_fit_is_an_error_and_torch_is_not_imported_by_constructor():
    m = TabularML(TabularConfig("classification"))
    assert m._torch is None
    with pytest.raises(RuntimeError):
        m.predict(None)
    assert set(STOP_REASON_LABELS) == {"max_epochs", "early_stopping", "cancelled"}
