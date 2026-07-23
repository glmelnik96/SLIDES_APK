"""Тарифная арифметика (webapp.pricing): токены → рубли по прайсу MiniMax.

Тариф: входные 240,22 ₽ / 1 млн токенов, генерируемые 1008,85 ₽ / 1 млн.
Считаем в копейках (округление до 2 знаков) — это деньги.
"""
from webapp import pricing


def test_input_only_matches_tariff():
    # Ровно 1 млн входных токенов = входной тариф.
    assert pricing.cost_rub(1_000_000, 0) == 240.22


def test_output_only_matches_tariff():
    # Ровно 1 млн генерируемых токенов = выходной тариф.
    assert pricing.cost_rub(0, 1_000_000) == 1008.85


def test_zero_tokens_zero_cost():
    assert pricing.cost_rub(0, 0) == 0


def test_mixed_tokens_rounded_to_kopecks():
    # 12 400 входных + 8 900 выходных, округление до копеек:
    # 12400/1e6*240.22 + 8900/1e6*1008.85 = 2.978728 + 8.978765 = 11.957493 → 11.96
    assert pricing.cost_rub(12_400, 8_900) == 11.96
