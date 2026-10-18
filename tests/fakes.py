from judgments_core.source import Page, RawRecord


class FakeSource:
    """Pages of records with cursors "1", "2", ...; resumes after a cursor."""

    name = "fake"

    def __init__(self, pages):
        self.pages = pages  # list of lists of source ids
        self.started_from = []

    def iter_pages(self, cursor):
        self.started_from.append(cursor)
        start = int(cursor) if cursor else 0
        for number in range(start, len(self.pages)):
            records = [RawRecord(i, f'{{"id": "{i}"}}') for i in self.pages[number]]
            yield Page(records, str(number + 1))


class FakeStore:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.saved = []

    def get_state(self, key):
        return self.state.get(key)

    def save_page(self, source, records, state_key, cursor):
        self.saved.extend((source, r.source_id) for r in records)
        if cursor is not None:
            self.state[state_key] = cursor
