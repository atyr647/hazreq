"""ORM models. Snapshot fields on request_line are intentional and
must never be repopulated from the catalog after creation.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin


class MIP(Base, TimestampMixin):
    __tablename__ = "mip"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(256))
    notes: Mapped[str | None] = mapped_column(Text)

    mrcs: Mapped[list["MRC"]] = relationship(
        back_populates="mip", cascade="all, delete-orphan", order_by="MRC.code"
    )


class MRC(Base, TimestampMixin):
    __tablename__ = "mrc"
    __table_args__ = (UniqueConstraint("mip_id", "code", name="uq_mrc_mip_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mip_id: Mapped[int] = mapped_column(
        ForeignKey("mip.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    periodicity: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)

    mip: Mapped[MIP] = relationship(back_populates="mrcs")
    items: Mapped[list["MRCItem"]] = relationship(
        back_populates="mrc",
        cascade="all, delete-orphan",
        order_by="MRCItem.sort_order",
    )

    @property
    def display(self) -> str:
        return f"{self.mip.code} / {self.code}"


class SPMIG(Base, TimestampMixin):
    __tablename__ = "spmig"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["HazmatItem"]] = relationship(
        back_populates="spmig", cascade="all, delete-orphan", order_by="HazmatItem.nomenclature"
    )


class HazmatItem(Base, TimestampMixin):
    __tablename__ = "hazmat_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    spmig_id: Mapped[int] = mapped_column(
        ForeignKey("spmig.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    nomenclature: Mapped[str] = mapped_column(String(256), nullable=False)
    niin: Mapped[str | None] = mapped_column(String(32))
    unit_of_issue: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)

    spmig: Mapped[SPMIG] = relationship(back_populates="items")


class MRCItem(Base):
    """Join table: which hazmat items belong to which MRC.

    Quantity is intentionally NOT stored here — every line on a request
    starts at qty=1 and is operator-overridden per request. Pre-baking
    qty per (MRC, item) caused defaults to drift from real-world usage.
    """

    __tablename__ = "mrc_item"

    mrc_id: Mapped[int] = mapped_column(
        ForeignKey("mrc.id", ondelete="CASCADE"), primary_key=True
    )
    hazmat_item_id: Mapped[int] = mapped_column(
        ForeignKey("hazmat_item.id", ondelete="RESTRICT"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    mrc: Mapped[MRC] = relationship(back_populates="items")
    hazmat_item: Mapped[HazmatItem] = relationship()


class Request(Base, TimestampMixin):
    __tablename__ = "request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime)

    datetime_of_request: Mapped[datetime | None] = mapped_column(DateTime)
    lpo: Mapped[str | None] = mapped_column(String(128))
    workcenter: Mapped[str | None] = mapped_column(String(128))
    requestor_name: Mapped[str | None] = mapped_column(String(128))
    hazmat_location: Mapped[str | None] = mapped_column(String(128))
    base_location: Mapped[str | None] = mapped_column(String(64))

    source_mip_id: Mapped[int | None] = mapped_column(ForeignKey("mip.id", ondelete="SET NULL"))
    source_mrc_id: Mapped[int | None] = mapped_column(ForeignKey("mrc.id", ondelete="SET NULL"))
    pdf_path: Mapped[str | None] = mapped_column(String(512))

    lines: Mapped[list["RequestLine"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RequestLine.sort_order",
    )

    @property
    def is_finalized(self) -> bool:
        return self.finalized_at is not None


class RequestLine(Base):
    """Snapshot of a hazmat item at the moment it was added to a request.

    Display fields (spmig_code/nomenclature/niin) are copied at line
    creation and never re-read from the catalog. The hazmat_item_id
    soft FK is for traceability only.
    """

    __tablename__ = "request_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("request.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    hazmat_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("hazmat_item.id", ondelete="SET NULL")
    )

    spmig_code: Mapped[str | None] = mapped_column(String(64))
    nomenclature: Mapped[str | None] = mapped_column(String(256))
    niin: Mapped[str | None] = mapped_column(String(32))
    qty: Mapped[int | None] = mapped_column(Integer)

    request: Mapped[Request] = relationship(back_populates="lines")


class AuditLog(Base):
    """Append-only record of catalog mutations.

    Captures inserts/updates/deletes on MIP, MRC, SPMIG, HazmatItem,
    MRCItem. Request/RequestLine activity is operational and excluded.

    `entity_key` is the natural key (MIP code, "MIP/MRC", SPMIG code,
    etc.) so the log stays useful even after the row is deleted.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # create | update | delete
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    entity_key: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[str | None] = mapped_column(Text)
