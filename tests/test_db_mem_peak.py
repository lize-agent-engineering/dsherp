"""The nightly's memory peak is reduced from the curve as a number, and says so when it cannot.

Four nightly runs uploaded a zero-byte db-mem-peak.txt without anyone noticing: the reduction
was `sort -t'\t' -k2 | tail -1`, and GNU sort exits 2 on a two-character '\t' - an error sent
to /dev/null behind a `|| true`. The numbers in the evidence document were picked out of the
raw curve by hand, which is the opposite of "審查方不用再自己重跑"."""
from infra import db_mem_peak


def test_the_peak_is_the_largest_value_not_the_last_line_alphabetically():
    """'99.9MiB' sorts after '625.5MiB' as text; the peak is 625.5."""
    lines = ['01:00:00\t99.9MiB / 2GiB\t4.88%',
             '01:00:15\t625.5MiB / 2GiB\t30.54%',
             '01:00:30\t100.2MiB / 2GiB\t4.89%']
    value, line = db_mem_peak.peak(lines)
    assert round(value, 1) == 625.5
    assert '625.5MiB' in line


def test_units_are_compared_after_conversion_not_as_text():
    value, _ = db_mem_peak.peak(['t\t1.5GiB / 2GiB', 't\t900MiB / 2GiB'])
    assert round(value, 1) == 1536.0, 'GiB 要换算，不能按数字大小直接比'


def test_lines_the_sampler_writes_when_the_container_is_gone_are_skipped_not_crashed_on():
    value, line = db_mem_peak.peak(['01:00:00\t无此容器', '01:00:15\t512MiB / 2GiB'])
    assert round(value, 1) == 512.0 and '512MiB' in line


def test_an_empty_or_missing_curve_is_an_error_not_an_empty_file(tmp_path, capsys):
    assert db_mem_peak.main(['x', str(tmp_path / 'nope.tsv')]) == 1
    assert '没有采样文件' in capsys.readouterr().err
    empty = tmp_path / 'e.tsv'
    empty.write_text('01:00:00\t无此容器\n')
    assert db_mem_peak.main(['x', str(empty)]) == 1
    assert '没有可解析的采样行' in capsys.readouterr().err


def test_an_unknown_unit_is_refused_rather_than_guessed_at():
    import pytest
    with pytest.raises(ValueError):
        db_mem_peak.mebibytes('12PB')
