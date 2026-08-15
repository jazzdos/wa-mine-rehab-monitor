from pathlib import Path

from wa_mine_monitor.config import load_config


def test_load_config_resolves_data_root(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'run:\n  data_root: "~/data/wa-mine-monitor"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    cfg = load_config(cfg_file)
    assert cfg.run.data_root == Path("~/data/wa-mine-monitor").expanduser()
    assert cfg.run.redistribute_public is False
    assert cfg.sources.minedex_public_export_blocked is True
