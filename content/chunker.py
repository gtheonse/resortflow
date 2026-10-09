"""T037 — Structure-aware chunking.

Produces ``graph.models.Chunk`` instances directly (the frozen contract from
feature 001 — see graph/models.py and specs/003-grounding-retrieval/plan.md).
This module does not define a parallel ``Chunk`` type; see ``rag/models.py``
for the internal-only types (``RawChunk`` etc.) used as intermediate values.

Different content types get different chunk boundaries, per plan.md's "Why
Structure-Aware Chunking?":

- ``dining``: one overview chunk per venue, plus one chunk per menu section
  (keeps related dishes on the same cuisine section together).
- ``rooms``: one chunk per room category (keeps the room's amenities and its
  upgrade path in one place).
- ``wellness``: one chunk per treatment / fitness offering, plus a short
  spa-level and fitness-level overview chunk each.
- ``entertainment``: one chunk per venue, including its event calendar.
- ``policies``: one chunk per clause (a regulatory-parsing boundary — this
  is what lets the jurisdiction-specific gaming clauses be retrieved and
  cited independently of unrelated house rules).
- ``faq``: one chunk per question (the natural boundary already present in
  the source).

Any chunk whose assembled text exceeds ``_SPLIT_THRESHOLD`` characters is
run through ``RecursiveCharacterTextSplitter`` as an overlap-aware second
pass — in this corpus that's a safety net for future long-form content
rather than something the current corpus triggers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from content.loader import KNOWN_DOMAINS, load_corpus
from graph.models import Chunk

_SPLIT_THRESHOLD = 1200
"""Above this, a chunk's assembled text gets a second overlap-aware split
pass. Set comfortably above the longest single corpus item this phase ships
(the jurisdiction-specific gaming-policy clauses, ~750-850 chars with their
`[jurisdiction / regulator]` prefix) — splitting a policy clause would
break the "one clause = one citable, independently retrievable chunk"
design plan.md calls for (regulatory parsing boundary), and would silently
break every gold_chunk_id in rag/eval/*.yaml that references it by its
un-suffixed id."""
_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=1200,
    chunk_overlap=150,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    d = date.fromisoformat(value)
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _finalize(
    *,
    chunk_id: str,
    tenant_id: str,
    content: str,
    source_uri: str,
    valid_from: datetime | None,
    valid_to: datetime | None,
) -> list[Chunk]:
    """Wrap assembled chunk text into one or more ``Chunk`` instances,
    splitting long text as an overlap-aware second pass."""
    text = content.strip()
    if len(text) <= _SPLIT_THRESHOLD:
        pieces = [text]
    else:
        pieces = _SPLITTER.split_text(text)

    chunks: list[Chunk] = []
    for i, piece in enumerate(pieces):
        cid = chunk_id if len(pieces) == 1 else f"{chunk_id}-p{i}"
        chunks.append(
            Chunk(
                chunk_id=cid,
                tenant_id=tenant_id,
                content=piece,
                source_uri=source_uri,
                valid_from=valid_from,
                valid_to=valid_to,
                content_hash=_content_hash(piece),
            )
        )
    return chunks


def _fmt_allergens(item: dict[str, Any]) -> str:
    bits = []
    if item.get("allergens"):
        bits.append("Allergens: " + ", ".join(item["allergens"]) + ".")
    if item.get("dietary_tags"):
        bits.append("Dietary tags: " + ", ".join(item["dietary_tags"]) + ".")
    return " ".join(bits)


def chunk_dining(data: dict[str, Any], tenant_id: str, source_uri: str) -> list[Chunk]:
    valid_from = _parse_date(data.get("valid_from"))
    valid_to = _parse_date(data.get("valid_to"))
    out: list[Chunk] = []

    for venue in data.get("venues", []):
        vid = venue["id"]
        vf = _parse_date(venue.get("valid_from")) or valid_from
        vt = _parse_date(venue.get("valid_to")) or valid_to

        overview = (
            f"{venue['name']} is a {venue['cuisine']} restaurant at "
            f"{'Meridian Grand' if tenant_id == 'MGV' else 'Meridian Harbor'}. "
            f"Location: {venue.get('location_zone', 'n/a')}. "
            f"Hours: {venue.get('hours', 'n/a')}. "
            f"Dress code: {venue.get('dress_code', 'n/a')}. "
            f"{venue.get('description', '').strip()}"
        )
        out.extend(
            _finalize(
                chunk_id=f"{vid}-overview",
                tenant_id=tenant_id,
                content=overview,
                source_uri=f"{source_uri}#{vid}.overview",
                valid_from=vf,
                valid_to=vt,
            )
        )

        for section in venue.get("menu_sections", []):
            section_name = section["section"]
            lines = [f"{venue['name']} — {section_name} menu section."]
            for item in section.get("items", []):
                line = f"{item['name']}: {item.get('description', '')} Price: ${item.get('price', 'n/a')}."
                extra = _fmt_allergens(item)
                if extra:
                    line += " " + extra
                lines.append(line)
            slug = section_name.lower().replace(" ", "-")
            out.extend(
                _finalize(
                    chunk_id=f"{vid}-{slug}",
                    tenant_id=tenant_id,
                    content="\n".join(lines),
                    source_uri=f"{source_uri}#{vid}.{slug}",
                    valid_from=vf,
                    valid_to=vt,
                )
            )

    return out


def chunk_rooms(data: dict[str, Any], tenant_id: str, source_uri: str) -> list[Chunk]:
    valid_from = _parse_date(data.get("valid_from"))
    valid_to = _parse_date(data.get("valid_to"))
    out: list[Chunk] = []

    for room in data.get("room_types", []):
        rid = room["id"]
        vf = _parse_date(room.get("valid_from")) or valid_from
        vt = _parse_date(room.get("valid_to")) or valid_to

        upgrade = f"Upgrade path: {room['upgrade_path']}." if room.get("upgrade_path") else "No further upgrade path — top category."
        content = (
            f"{room['name']} ({room['category']} category) is a room type at "
            f"{'Meridian Grand' if tenant_id == 'MGV' else 'Meridian Harbor'}, "
            f"{room.get('square_feet', 'n/a')} square feet, {room.get('beds', 'n/a')}, "
            f"max occupancy {room.get('max_occupancy', 'n/a')}. View: {room.get('view', 'n/a')}. "
            f"{room.get('description', '').strip()} "
            f"Amenities: {', '.join(room.get('amenities', []))}. {upgrade}"
        )
        out.extend(
            _finalize(
                chunk_id=rid,
                tenant_id=tenant_id,
                content=content,
                source_uri=f"{source_uri}#{rid}",
                valid_from=vf,
                valid_to=vt,
            )
        )

    return out


def chunk_wellness(data: dict[str, Any], tenant_id: str, source_uri: str) -> list[Chunk]:
    valid_from = _parse_date(data.get("valid_from"))
    valid_to = _parse_date(data.get("valid_to"))
    out: list[Chunk] = []

    spa = data.get("spa", {})
    if spa:
        overview = (
            f"{spa['name']} is located at {spa.get('location_zone', 'n/a')}, "
            f"open {spa.get('hours', 'n/a')}."
        )
        spa_id = f"{tenant_id.lower()}-well-spa-overview"
        out.extend(
            _finalize(
                chunk_id=spa_id,
                tenant_id=tenant_id,
                content=overview,
                source_uri=f"{source_uri}#spa.overview",
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )
        for treatment in spa.get("treatments", []):
            tid = treatment["id"]
            content = (
                f"{treatment['name']} is a spa treatment at {spa['name']}, "
                f"{treatment.get('duration_minutes', 'n/a')} minutes, ${treatment.get('price', 'n/a')}. "
                f"{treatment.get('description', '').strip()}"
            )
            out.extend(
                _finalize(
                    chunk_id=tid,
                    tenant_id=tenant_id,
                    content=content,
                    source_uri=f"{source_uri}#{tid}",
                    valid_from=valid_from,
                    valid_to=valid_to,
                )
            )

    fitness = data.get("fitness", {})
    if fitness:
        overview = (
            f"{fitness['name']} is located at {fitness.get('location_zone', 'n/a')}, "
            f"open {fitness.get('hours', 'n/a')}."
        )
        fit_id = f"{tenant_id.lower()}-well-fitness-overview"
        out.extend(
            _finalize(
                chunk_id=fit_id,
                tenant_id=tenant_id,
                content=overview,
                source_uri=f"{source_uri}#fitness.overview",
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )
        for offering in fitness.get("offerings", []):
            oid = offering["id"]
            content = (
                f"{offering['name']} is a fitness offering at {fitness['name']}. "
                f"{offering.get('description', '').strip()}"
            )
            out.extend(
                _finalize(
                    chunk_id=oid,
                    tenant_id=tenant_id,
                    content=content,
                    source_uri=f"{source_uri}#{oid}",
                    valid_from=valid_from,
                    valid_to=valid_to,
                )
            )

    return out


def chunk_entertainment(data: dict[str, Any], tenant_id: str, source_uri: str) -> list[Chunk]:
    valid_from = _parse_date(data.get("valid_from"))
    valid_to = _parse_date(data.get("valid_to"))
    out: list[Chunk] = []

    for venue in data.get("venues", []):
        vid = venue["id"]
        lines = [
            (f"{venue['name']} is an entertainment venue at "
            f"{'Meridian Grand' if tenant_id == 'MGV' else 'Meridian Harbor'}, "
            f"capacity {venue.get('capacity', 'n/a')}, seating: {venue.get('seating', 'n/a')}, "
            f"located at {venue.get('location_zone', 'n/a')}."),
            venue.get("description", "").strip(),
        ]
        for event in venue.get("upcoming_events", []):
            dark = ", ".join(event.get("dark_nights", [])) or "none"
            lines.append(
                f"Event: {event['title']}. Typical showtimes: "
                f"{', '.join(event.get('typical_showtimes', []))}. Dark nights: {dark}."
            )
        out.extend(
            _finalize(
                chunk_id=vid,
                tenant_id=tenant_id,
                content="\n".join(lines),
                source_uri=f"{source_uri}#{vid}",
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )

    return out


def chunk_policies(data: dict[str, Any], tenant_id: str, source_uri: str) -> list[Chunk]:
    valid_from = _parse_date(data.get("valid_from"))
    valid_to = _parse_date(data.get("valid_to"))
    jurisdiction = data.get("jurisdiction", "n/a")
    regulator = data.get("regulator", "n/a")
    out: list[Chunk] = []

    for clause in data.get("clauses", []):
        cid = clause["id"]
        content = (
            f"[{jurisdiction} / {regulator}] {clause['title']}: {clause.get('text', '').strip()}"
        )
        out.extend(
            _finalize(
                chunk_id=cid,
                tenant_id=tenant_id,
                content=content,
                source_uri=f"{source_uri}#{cid}",
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )

    return out


def chunk_faq(data: dict[str, Any], tenant_id: str, source_uri: str) -> list[Chunk]:
    valid_from = _parse_date(data.get("valid_from"))
    valid_to = _parse_date(data.get("valid_to"))
    out: list[Chunk] = []

    for entry in data.get("entries", []):
        eid = entry["id"]
        content = f"Q: {entry['question']}\nA: {entry.get('answer', '').strip()}"
        out.extend(
            _finalize(
                chunk_id=eid,
                tenant_id=tenant_id,
                content=content,
                source_uri=f"{source_uri}#{eid}",
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )

    return out


_CHUNKERS = {
    "dining": chunk_dining,
    "rooms": chunk_rooms,
    "wellness": chunk_wellness,
    "entertainment": chunk_entertainment,
    "policies": chunk_policies,
    "faq": chunk_faq,
}


def chunk_domain(domain: str, data: dict[str, Any], tenant_id: str) -> list[Chunk]:
    if domain not in _CHUNKERS:
        raise ValueError(f"no chunker registered for domain {domain!r}")
    source_uri = f"content/corpus/{tenant_id.lower()}/{domain}.yaml"
    return _CHUNKERS[domain](data, tenant_id, source_uri)


def chunk_tenant(tenant_id: str) -> list[Chunk]:
    """Load and chunk every domain for one tenant."""
    corpus = load_corpus(tenant_id)
    chunks: list[Chunk] = []
    for domain in KNOWN_DOMAINS:
        chunks.extend(chunk_domain(domain, corpus[domain], tenant_id.upper()))
    return chunks


def chunk_all(tenant_ids: Iterable[str] = ("MGV", "MHB")) -> list[Chunk]:
    """Load and chunk every domain for every tenant."""
    chunks: list[Chunk] = []
    for tenant_id in tenant_ids:
        chunks.extend(chunk_tenant(tenant_id))
    return chunks
