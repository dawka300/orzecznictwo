from fakes import FakeSource, FakeStore

from judgments_core.importer import Importer


def test_saves_all_pages_and_last_cursor():
    store = FakeStore()
    result = Importer(FakeSource([["a", "b"], ["c"]]), store).run()
    assert store.saved == [("fake", "a"), ("fake", "b"), ("fake", "c")]
    assert store.state == {"fake:cursor": "2"}
    assert (result.pages, result.saved, result.finished) == (2, 3, True)


def test_resumes_from_saved_cursor():
    source = FakeSource([["a"], ["b"], ["c"]])
    store = FakeStore({"fake:cursor": "2"})
    Importer(source, store).run()
    assert source.started_from == ["2"]
    assert store.saved == [("fake", "c")]


def test_from_start_ignores_cursor():
    source = FakeSource([["a"], ["b"]])
    Importer(source, FakeStore({"fake:cursor": "1"}), resume=False).run()
    assert source.started_from == [None]


def test_limit_cuts_inside_a_page():
    store = FakeStore()
    result = Importer(FakeSource([["a", "b"], ["c", "d"]]), store, limit=3).run()
    assert [i for _, i in store.saved] == ["a", "b", "c"]
    assert (result.finished, result.stopped_by) == (False, "limit")


def test_time_budget_stops_after_current_page():
    times = iter([0, 10, 70, 130])
    store = FakeStore()
    result = Importer(
        FakeSource([["a"], ["b"], ["c"], ["d"]]),
        store,
        time_budget_s=60,
        clock=lambda: next(times),
    ).run()
    assert [i for _, i in store.saved] == ["a", "b"]
    assert store.state["fake:cursor"] == "2"
    assert result.stopped_by == "czas"


def test_rejected_records_are_not_saved_but_cursor_moves():
    store = FakeStore()
    result = Importer(
        FakeSource([["a", "x"], ["y"]]), store, accept=lambda r: r.source_id == "a"
    ).run()
    assert store.saved == [("fake", "a")]
    assert store.state["fake:cursor"] == "2"
    assert result.rejected == 2


def test_dry_run_only_outputs():
    seen = []
    result = Importer(FakeSource([["a"], ["b"]]), outputs=[seen.append]).run()
    assert [r.source_id for r in seen] == ["a", "b"]
    assert result.saved == 2
