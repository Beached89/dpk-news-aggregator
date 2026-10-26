"""Database engine, session factory, and schema models.

SQLite + SQLAlchemy now; swapping to Postgres later is just a DB_URL change.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, date as date_cls
from typing import Iterator, Optional

from sqlalchemy import (
    String, Text, Integer, Float, Date, DateTime, ForeignKey, UniqueConstraint,
    Table, Column, create_engine, func, event,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session,
)

from . import config


class Base(DeclarativeBase):
    pass


# Many-to-many: an article can carry several topic categories.
article_category = Table(
    "article_category", Base.metadata,
    Column("article_id", ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class Article(Base):
    __tablename__ = "articles"
    # The KCNA hash is shared across languages, so (hash, lang) is the natural key.
    __table_args__ = (UniqueConstraint("hash", "lang", name="uq_hash_lang"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hash: Mapped[str] = mapped_column(String(64), index=True)
    lang: Mapped[str] = mapped_column(String(4), index=True)
    # Origin site (kcna now; rodong later). Lets one item carry multiple sources.
    source: Mapped[str] = mapped_column(String(16), default="kcna", index=True)
    url: Mapped[str] = mapped_column(String(512))
    title: Mapped[Optional[str]] = mapped_column(Text, default=None)
    subtitle: Mapped[Optional[str]] = mapped_column(Text, default=None)
    body_html: Mapped[Optional[str]] = mapped_column(Text, default=None)
    body_text: Mapped[Optional[str]] = mapped_column(Text, default=None)
    published_date: Mapped[Optional[date_cls]] = mapped_column(Date, default=None, index=True)
    # The exact publication moment when the source states one (RSS `pubDate`).
    # KCNA/Rodong/VOK pages only give a day, so this stays NULL — and consumers
    # must then avoid inventing an hour: counting up from midnight made every
    # story published today read "16 hours ago" all afternoon. Stored naive UTC,
    # same convention as scraped_at.
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    dateline: Mapped[Optional[str]] = mapped_column(Text, default=None)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    # Cross-source dedup grouping (normalized title + date). Same story in KCNA
    # and Rodong shares this key, so the viewer can merge them into one article.
    dedup_key: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    # Rodong KO↔EN linkage: an English row's pair_hash points at the matched
    # Korean row's hash (and vice-versa, self for the Korean side). Set by the
    # photo-based pairing pass. NULL = unmatched.
    pair_hash: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    # Precomputed KO<->EN word-alignment for the learning view (JSON: paragraphs
    # of {ko, en, align} sentence pairs). Set on the Korean row at translate time.
    align_json: Mapped[Optional[str]] = mapped_column(Text, default=None)
    # Lifecycle: discovered -> fetched -> failed
    status: Mapped[str] = mapped_column(String(16), default="discovered", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)

    media: Mapped[list["Media"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    categories: Mapped[list["Category"]] = relationship(
        secondary=article_category, back_populates="articles"
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    label_ko: Mapped[str] = mapped_column(String(64))
    label_en: Mapped[str] = mapped_column(String(64))
    sort: Mapped[int] = mapped_column(Integer, default=0)

    articles: Mapped[list["Article"]] = relationship(
        secondary=article_category, back_populates="categories"
    )


class Media(Base):
    __tablename__ = "media"
    __table_args__ = (UniqueConstraint("article_id", "source_url", name="uq_article_src"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8), default="image")  # image | video
    source_url: Mapped[str] = mapped_column(String(1024))
    local_path: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), default=None, index=True)
    downloaded: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)

    article: Mapped[Article] = relationship(back_populates="media")


class AgriArticle(Base):
    """Agriculture/Foodstuffs topical archive — a flat list independent of the
    news pipeline (no photo gate, both languages captured for later analysis)."""
    __tablename__ = "agri_articles"
    __table_args__ = (UniqueConstraint("source", "hash", name="uq_agri_source_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), index=True)   # kcna | rodong
    hash: Mapped[str] = mapped_column(String(128), index=True)
    ko_title: Mapped[Optional[str]] = mapped_column(Text, default=None)
    en_title: Mapped[Optional[str]] = mapped_column(Text, default=None)
    ko_body: Mapped[Optional[str]] = mapped_column(Text, default=None)
    en_body: Mapped[Optional[str]] = mapped_column(Text, default=None)
    url: Mapped[str] = mapped_column(String(512), default="")
    published_date: Mapped[Optional[date_cls]] = mapped_column(Date, default=None, index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgriFact(Base):
    """A single quantified fact extracted from an agriculture article."""
    __tablename__ = "agri_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("agri_articles.id"), index=True)
    commodity: Mapped[str] = mapped_column(String(32), index=True)
    metric: Mapped[str] = mapped_column(String(8))          # area | mass | count
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    tonnes: Mapped[Optional[float]] = mapped_column(Float, default=None)
    kcal: Mapped[Optional[float]] = mapped_column(Float, default=None)
    is_estimate: Mapped[bool] = mapped_column(default=False)
    # True when the quantity was produced by the AI (Claude) estimator rather
    # than extracted from a stated number — surfaced as a "*" in the UI.
    is_ai: Mapped[bool] = mapped_column(default=False)
    ai_note: Mapped[Optional[str]] = mapped_column(Text, default=None)
    phrase: Mapped[Optional[str]] = mapped_column(Text, default=None)


class AgriMention(Base):
    """A commodity referenced in an article without (or in addition to) a
    measurable quantity. Lets the analysis list every foodstuff that is
    discussed — e.g. 'barley' in a harvest report — even when no tonnage or
    area is stated. `count` = how many times the commodity is named."""
    __tablename__ = "agri_mentions"
    __table_args__ = (UniqueConstraint("article_id", "commodity", name="uq_agri_mention"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("agri_articles.id"), index=True)
    commodity: Mapped[str] = mapped_column(String(32), index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)


class AgriPlace(Base):
    """A normalized place where a commodity is grown/raised/caught. De-dup key is
    `norm`; `level` + `province`/`region` drive hierarchy resolution so a county
    and the province containing it aren't double-counted."""
    __tablename__ = "agri_places"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    norm: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    level: Mapped[str] = mapped_column(String(16), default="farm")   # country|region|province|city|county|farm
    province: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    region: Mapped[Optional[str]] = mapped_column(String(32), default=None)


class AgriCommodity(Base):
    """Overlay/extension of the built-in commodity list. Lets AI-discovered
    sub-products (e.g. 'rice bran oil', 'rice syrup') exist as real commodities
    with their own calories. The built-in agri_data list is the base; rows here
    add to or override it by `key`."""
    __tablename__ = "agri_commodities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    en: Mapped[str] = mapped_column(String(96))
    cat: Mapped[str] = mapped_column(String(32), default="processed")
    kind: Mapped[str] = mapped_column(String(16), default="processed")   # primary|processed|livestock
    kcal_per_kg: Mapped[float] = mapped_column(Float, default=0.0)
    parent_key: Mapped[Optional[str]] = mapped_column(String(48), default=None)
    source: Mapped[str] = mapped_column(String(8), default="ai")          # ai | manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgriEndUse(Base):
    """A discovered end-use of a commodity (data-driven, replaces the built-in
    default split when present). One row per use: a fraction of the commodity's
    output that goes to food/seed/loss/feed/process/export/other, with a target
    commodity for process (sub-product) and feed (livestock)."""
    __tablename__ = "agri_enduses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commodity_key: Mapped[str] = mapped_column(String(48), index=True)
    label: Mapped[str] = mapped_column(String(96))
    frac: Mapped[float] = mapped_column(Float, default=0.0)
    use_type: Mapped[str] = mapped_column(String(8), default="other")    # food|seed|loss|feed|process|export|other
    target_key: Mapped[Optional[str]] = mapped_column(String(48), default=None)
    source: Mapped[str] = mapped_column(String(8), default="ai")          # ai | manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgriObservation(Base):
    """One article's claim that a place produced some tonnage of a commodity,
    stored as a low–high range. The aggregation reconciles repeated reports of the
    same place+commodity into a single range (no double-counting) and routes the
    result through end-uses. `kind`: rules | ai | manual (manual overrides)."""
    __tablename__ = "agri_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agri_articles.id"), index=True)
    place_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agri_places.id"), index=True, default=None)
    commodity: Mapped[str] = mapped_column(String(32), index=True)
    low_t: Mapped[Optional[float]] = mapped_column(Float, default=None)
    high_t: Mapped[Optional[float]] = mapped_column(Float, default=None)
    kind: Mapped[str] = mapped_column(String(8), default="rules")     # rules | ai | manual
    is_estimate: Mapped[bool] = mapped_column(default=False)
    note: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_engine = None
_SessionLocal: Optional[sessionmaker] = None


def get_engine():
    global _engine
    if _engine is None:
        config.ensure_dirs()
        is_sqlite = config.DB_URL.startswith("sqlite")
        # `timeout` makes a blocked writer wait (not fail) while another process
        # holds the write lock — essential now that several scraper containers
        # share one SQLite file.
        connect_args = {"check_same_thread": False, "timeout": 30.0} if is_sqlite else {}
        _engine = create_engine(config.DB_URL, connect_args=connect_args, future=True)
        if is_sqlite:
            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _rec):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                # WAL allows concurrent readers alongside a writer; busy_timeout
                # serializes writers gracefully instead of erroring.
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
    return _engine


def init_db() -> None:
    Base.metadata.create_all(get_engine())
    _migrate()


def _migrate() -> None:
    """create_all adds new tables but not new columns on existing tables, so
    add the `source` column to a pre-existing articles table if it's missing."""
    eng = get_engine()
    if not config.DB_URL.startswith("sqlite"):
        return
    with eng.begin() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(articles)").fetchall()]
        if cols and "source" not in cols:
            conn.exec_driver_sql("ALTER TABLE articles ADD COLUMN source VARCHAR(16) DEFAULT 'kcna'")
        if cols and "dedup_key" not in cols:
            conn.exec_driver_sql("ALTER TABLE articles ADD COLUMN dedup_key VARCHAR(64)")
        if cols and "pair_hash" not in cols:
            conn.exec_driver_sql("ALTER TABLE articles ADD COLUMN pair_hash VARCHAR(64)")
        if cols and "align_json" not in cols:
            conn.exec_driver_sql("ALTER TABLE articles ADD COLUMN align_json TEXT")
        if cols and "published_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE articles ADD COLUMN published_at DATETIME")
        # agri_facts: AI-estimate columns added later.
        fcols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(agri_facts)").fetchall()]
        if fcols and "is_ai" not in fcols:
            conn.exec_driver_sql("ALTER TABLE agri_facts ADD COLUMN is_ai BOOLEAN DEFAULT 0")
        if fcols and "ai_note" not in fcols:
            conn.exec_driver_sql("ALTER TABLE agri_facts ADD COLUMN ai_note TEXT")


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session)
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    s = get_session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
