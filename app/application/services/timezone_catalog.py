from datetime import UTC, datetime
from functools import cache
from importlib.resources import files
from zoneinfo import ZoneInfo


@cache
def _read_tab_rows(filename: str) -> tuple[tuple[str, ...], ...]:
    text = (files("tzdata.zoneinfo") / filename).read_text(encoding="utf-8")
    return tuple(
        tuple(line.split("\t"))
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    )


@cache
def _load_country_names() -> dict[str, str]:
    return {row[0]: row[1] for row in _read_tab_rows("iso3166.tab") if len(row) >= 2}


@cache
def _load_zones_by_country() -> dict[str, tuple[str, ...]]:
    zones: dict[str, list[str]] = {}
    for row in _read_tab_rows("zone.tab"):
        # zone.tab columns: country code, coordinates, zone id, optional comment.
        if len(row) < 3:
            continue
        zones.setdefault(row[0], []).append(row[2])
    return {code: tuple(sorted(ids)) for code, ids in zones.items()}


class TimezoneCatalog:
    """Countries and their IANA timezones, read from the `tzdata` package.

    A country is listed only if zone.tab has at least one zone for it. Offsets are computed on the fly because they
    change with daylight saving time and zone reforms.
    """

    def countries(self) -> list[tuple[str, str]]:
        names = _load_country_names()
        zones = _load_zones_by_country()
        return sorted(
            ((code, names[code]) for code in zones if code in names),
            key=lambda item: (item[1], item[0]),
        )

    def has_country(self, country_code: str) -> bool:
        return country_code in _load_zones_by_country() and country_code in _load_country_names()

    def zone_ids_for(self, country_code: str, at: datetime | None = None) -> list[str]:
        """Zone ids of the country ordered by current UTC offset, then by id."""
        moment = at or datetime.now(UTC)
        zone_ids = _load_zones_by_country().get(country_code, ())
        return sorted(zone_ids, key=lambda zone_id: (self._offset_seconds(zone_id, moment), zone_id))

    def zone_ids_with_same_offset(self, zone_id: str, at: datetime | None = None) -> list[str]:
        """Every catalog zone whose UTC offset right now equals the offset of `zone_id`, the zone itself included."""
        moment = at or datetime.now(UTC)
        target = self._offset_seconds(zone_id, moment)
        catalog_zone_ids = {zone for zones in _load_zones_by_country().values() for zone in zones}
        catalog_zone_ids.add(zone_id)
        return sorted(zone for zone in catalog_zone_ids if self._offset_seconds(zone, moment) == target)

    def utc_offset(self, zone_id: str, at: datetime | None = None) -> str:
        total_minutes = self._offset_seconds(zone_id, at or datetime.now(UTC)) // 60
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        return f"{sign}{hours:02d}:{minutes:02d}"

    @staticmethod
    def label(zone_id: str) -> str:
        return zone_id.rsplit("/", 1)[-1].replace("_", " ")

    @staticmethod
    def _offset_seconds(zone_id: str, moment: datetime) -> int:
        offset = moment.astimezone(ZoneInfo(zone_id)).utcoffset()
        assert offset is not None
        return int(offset.total_seconds())
