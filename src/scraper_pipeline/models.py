"""Item schemas.

Every scraped record is validated against one of these before it is stored.
This is the first place a layout change shows up: the fetch still returns 200,
the parser still runs, but fields start arriving empty and validation rejects
them. Counting those rejections per field is what `drift.py` turns into an alert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ScrapedItem(BaseModel):
    """Fields shared by everything we extract."""

    source: str
    source_url: HttpUrl
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = {"extra": "forbid"}


class Book(ScrapedItem):
    """A product listing from a static, server-rendered page."""

    title: str = Field(min_length=1, max_length=500)
    price: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    availability: str = Field(min_length=1)
    rating: int | None = Field(default=None, ge=1, le=5)

    @field_validator("title", "availability")
    @classmethod
    def not_blank(cls, v: str) -> str:
        cleaned = " ".join(v.split())
        if not cleaned:
            raise ValueError("field collapsed to empty after whitespace normalisation")
        return cleaned


class Quote(ScrapedItem):
    """A record from a page whose content is rendered client side."""

    text: str = Field(min_length=1, max_length=2000)
    author: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list)

    @field_validator("text", "author")
    @classmethod
    def not_blank(cls, v: str) -> str:
        cleaned = " ".join(v.split())
        if not cleaned:
            raise ValueError("field collapsed to empty after whitespace normalisation")
        return cleaned


class RejectedItem(BaseModel):
    """A record that failed validation.

    Kept rather than dropped so it can be replayed once a parser is fixed,
    and so the failure reason is available when someone asks what broke.
    """

    source: str
    source_url: str
    raw: dict
    error: str
    run_id: str
    rejected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
