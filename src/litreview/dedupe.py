from __future__ import annotations

from litreview.models import PaperRow, normalize_title


class Deduper:
    """Keep the first hit; drop later rows that share a normalized title or DOI."""

    def __init__(self) -> None:
        self._titles: set[str] = set()
        self._dois: set[str] = set()
        self.kept = 0
        self.dropped = 0

    def keep(self, row: PaperRow) -> bool:
        title_key = normalize_title(row.title) if row.title else ""
        doi_key = row.doi.casefold().strip() if row.doi else ""

        if title_key and title_key in self._titles:
            self.dropped += 1
            return False
        if doi_key and doi_key in self._dois:
            self.dropped += 1
            return False

        if title_key:
            self._titles.add(title_key)
        if doi_key:
            self._dois.add(doi_key)
        self.kept += 1
        return True
