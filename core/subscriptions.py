"""Subscription plans and access policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import Config


@dataclass(frozen=True)
class Plan:
    id: str
    title: str
    stars: int
    days: int
    modules: tuple[str, ...]
    custom_modules: bool = True
    max_custom_modules: int = 0


def build_plans(config: Config) -> dict[str, Plan]:
    return {
        "basic": Plan(
            "basic",
            "Basic",
            config.basic_stars,
            config.basic_days,
            config.basic_modules,
            custom_modules=config.custom_modules_enabled,
            max_custom_modules=config.basic_custom_modules,
        ),
        "pro": Plan(
            "pro",
            "Pro",
            config.pro_stars,
            config.pro_days,
            config.pro_modules,
            custom_modules=config.custom_modules_enabled,
            max_custom_modules=config.pro_custom_modules,
        ),
        "premium": Plan(
            "premium",
            "Premium",
            config.premium_stars,
            config.premium_days,
            config.premium_modules,
            custom_modules=config.custom_modules_enabled,
            max_custom_modules=config.premium_custom_modules,
        ),
    }


def normalize_modules(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = str(value).strip().lower()
        if value and value not in result:
            result.append(value)
    return result
