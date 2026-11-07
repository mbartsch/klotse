import pytest

from klotse.ui.filter_expr import FilterError, looks_like_expression, parse


def resolver(**cells):
    """resolve(name) over a dict {normalized name: (value, unit)}; unknown names -> None."""
    return lambda name: cells.get(name.lower().replace("_", "").replace(".", ""))


POD = dict(restarts=(4, None), status=("CrashLoopBackOff", None), name=("web-1", None), namespace=("shop", None), node=("n1", None),
           cpureq=(0.1, "cpu"), cpulim=(0.2, "cpu"), memreq=(64.0, "mem"), memlim=(256.0, "mem"), age=(7200, "age"), cpuused=(None, "cpu"))


def matches(text, **cells):
    return parse(text).matches(resolver(**{**POD, **cells}))


def test_numbers_compare_as_numbers():
    assert matches("Restarts != 0") and matches("Restarts > 3") and not matches("Restarts > 4") and matches("Restarts >= 4")
    assert matches("restarts = 4") and matches("Restarts == 4") and matches("Restarts < 5") and not matches("Restarts <= 3")


def test_columns_can_be_compared_with_each_other_and_with_arithmetic():
    assert matches("CPULim = 2*CPUReq") and not matches("CPULim = CPUReq") and matches("CPULim > CPUReq")
    assert matches("MemLim = MemReq * 4") and matches("MemLim / 2 > MemReq") and not matches("MemLim > 3 * MemReq * 2")


def test_literals_use_the_unit_of_the_other_side():
    assert matches("CPULim >= 200m") and matches("CPUReq < 0.5") and matches("CPUReq = 100m")
    assert matches("MemLim = 256Mi") and matches("MemLim < 1Gi") and matches("MemReq > 32Mi")
    assert matches("Age > 1h") and matches("Age < 1d") and matches("Age = 120m") and not matches("Age > 3h")


def test_text_columns_equality_wildcards_regex_and_case():
    assert matches("Status = crashloopbackoff") and matches("Status != Running") and matches("Name = web-*") and not matches("Name = db-*")
    assert matches("Status ~ crash") and matches("Name ~ ^web-[0-9]$") and matches("Status !~ pending") and not matches("Status !~ crash")
    assert matches("Namespace != kube-system") and matches("Name = 'web-1'") and matches('Name = "WEB-1"')
    assert matches("Name > a") and not matches("Name < a")


def test_and_or_and_brackets():
    assert matches("Restarts > 3 and Status ~ crash") and not matches("Restarts > 9 and Status ~ crash")
    assert matches("Restarts > 9 or Status ~ crash") and matches("Restarts > 9 || Namespace = shop") and matches("Restarts > 3 && Name = web-1")
    assert matches("Restarts > 9 and Namespace = nope or Status ~ crash")  # and binds tighter than or
    assert not matches("Restarts > 9 and (Namespace = nope or Status ~ crash)")
    assert matches("(Restarts > 9 or Namespace = shop) and Node = n1")


def test_blank_values_match_nothing():
    assert not matches("CPUUsed > 0") and not matches("CPUUsed = 0") and not matches("CPUUsed != 0")


def test_unknown_names_on_the_left_are_not_columns_and_on_the_right_are_text():
    assert not matches("nosuch = x")  # no such column or label: no row matches
    assert matches("Status = Running", status=("Running", None)) and matches("app = web", app=("web", None))
    assert matches("label.app = web", labelapp=("web", None))


def test_ip_addresses_and_dotted_words_are_text_not_numbers():
    assert matches("IP = 10.0.0.5", ip=("10.0.0.5", None)) and matches("Name ~ web.1")


def test_errors_are_sentences_for_the_user():
    for text, part in [("Restarts >", "at the end"), ("(Restarts > 1", "not closed"), ("Restarts 3", "comparison"), ("Name ~ [", "regular expression"),
                       ("", "empty"), ("Restarts > 1 and", "at the end"), ("> 3", "instead of")]:
        with pytest.raises(FilterError) as error:
            parse(text)
        assert part in str(error.value), (text, str(error.value))


def test_units_are_checked_against_the_columns():
    units = lambda name: {"age": "age", "cpulim": "cpu", "memlim": "mem", "restarts": ""}.get(name.lower())
    for good in ("Age > 5m", "CPULim > 500m", "MemLim > 2Gi", "Restarts > 3"):
        parse(good).validate(units)
    for bad, part in [("Age > 5", "needs a unit"), ("Age > 5x", "not an age"), ("CPULim > 5zz", "CPU amount"), ("MemLim > 5zz", "memory amount"),
                      ("Restarts > 5m", "plain number")]:
        with pytest.raises(FilterError) as error:
            parse(bad).validate(units)
        assert part in str(error.value), (bad, str(error.value))


def test_what_counts_as_an_expression():
    assert looks_like_expression("Restarts > 3") and looks_like_expression("a=b") and looks_like_expression("Name ~ x")
    assert not looks_like_expression("web-1") and not looks_like_expression("kube system") and not looks_like_expression("")
    assert not looks_like_expression("name 'a=b'")  # a comparison inside quotes is text
