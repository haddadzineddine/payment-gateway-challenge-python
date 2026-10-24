from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

Currency = Literal["USD", "GBP", "EUR"]

DECEMBER = 12


class PaymentStatus(StrEnum):
    AUTHORIZED = "Authorized"
    DECLINED = "Declined"
    REJECTED = "Rejected"
    PENDING = "Pending"  # sent to the bank, answer lost: settled later by reconciliation


class PaymentRequest(BaseModel):
    """A card payment to process."""

    model_config = ConfigDict(strict=True, frozen=True)

    card_number: str = Field(
        pattern=r"^[0-9]{14,19}$",
        description="14-19 digits",
        examples=["2222405343248877"],
    )
    expiry_month: int = Field(ge=1, le=12, examples=[4])
    expiry_year: int = Field(ge=1000, le=9999, description="Not in the past", examples=[2030])
    currency: Currency = Field(examples=["GBP"])
    amount: int = Field(gt=0, description="Minor units: 1050 = 10.50", examples=[1050])
    cvv: str = Field(
        pattern=r"^[0-9]{3,4}$",
        description="3-4 digits, never stored",
        examples=["123"],
    )

    @field_validator("expiry_year")
    @classmethod
    def reject_expired_card(cls, expiry_year: int, info: ValidationInfo) -> int:
        # An invalid month has its own error: judge the year alone.
        expiry_month = info.data.get("expiry_month", DECEMBER)
        today = datetime.now(UTC)

        if (expiry_year, expiry_month) < (today.year, today.month):
            raise ValueError("card has expired")

        return expiry_year


class Payment(BaseModel):
    """A processed payment (card masked)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    status: Literal[PaymentStatus.AUTHORIZED, PaymentStatus.DECLINED, PaymentStatus.PENDING] = Field(
        description="Bank answer, or Pending while it is unknown"
    )
    last_four: str = Field(examples=["8877"])
    expiry_month: int = Field(examples=[4])
    expiry_year: int = Field(examples=[2030])
    currency: Currency = Field(examples=["GBP"])
    amount: int = Field(description="Minor units", examples=[1050])

    @classmethod
    def from_request(cls, request: PaymentRequest, status: PaymentStatus) -> Self:
        return cls(
            id=uuid4(),
            status=status,
            last_four=request.card_number[-4:],
            expiry_month=request.expiry_month,
            expiry_year=request.expiry_year,
            currency=request.currency,
            amount=request.amount,
        )

    def with_status(self, status: PaymentStatus) -> Self:
        return self.model_copy(update={"status": status})
