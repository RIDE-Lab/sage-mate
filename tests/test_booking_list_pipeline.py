from types import SimpleNamespace

from sage_faculty_twin.service import DigitalTwinService


def test_no_status_filter_is_a_real_batch_item_not_end_of_stream():
    service = object.__new__(DigitalTwinService)
    calls = []
    def list_bookings(*, status):
        calls.append(status)
        return [] if status is None else [status]
    service._build_support = lambda: SimpleNamespace(list_bookings=list_bookings)
    assert service.list_bookings() == []
    assert service.list_bookings("待确认") == ["待确认"]
    assert calls == [None, "待确认"]
