"""Worker memory limit derived from /proc/meminfo."""

from worker import service


def test_default_grid_limit_is_a_share_of_the_available_memory(tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        1000000 kB\nMemAvailable:     800000 kB\n")
    monkeypatch.setattr(service, "MEMINFO_PATH", meminfo)
    assert service.DefaultMaxGridBytes() == int(800000 * 1024 * service.MEMORY_SHARE_FOR_VOLUME)


def test_default_grid_limit_falls_back_when_meminfo_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "MEMINFO_PATH", tmp_path / "missing")
    assert service.DefaultMaxGridBytes() == service.FALLBACK_MAX_GRID_BYTES
