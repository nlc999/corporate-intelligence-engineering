"""Unit-level search contracts with a fake tuple cursor, not a real SQL engine."""

from dataclasses import dataclass

from osint_research.company_discovery.search import PostgresCompanySearch


@dataclass
class Col:
    name: str


COLUMNS = [
    Col("company_number"), Col("company_name"), Col("company_status"),
    Col("locality"), Col("matched_name"), Col("already_ingested"),
]


class FakeCursor:
    description = COLUMNS

    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, parameters=None):
        self.connection.executed.append((sql, parameters))
        self.rows = []
        if "SET TRANSACTION" in sql:
            return
        if "ch.company_name_normalised = %(norm_query)s" in sql:
            self.rows = [
                ("00000006", "GREENLEAF ENGINEERING LIMITED", "active", "Cardiff", None, False),
            ]
        elif "a.previous_name_normalised = %(norm_query)s" in sql:
            self.rows = [
                ("00000001", "ALDER RENEWABLES LIMITED", "active", "Leeds", "GREENLEAF ENGINEERING LIMITED", True),
                ("00000001", "ALDER RENEWABLES LIMITED", "active", "Leeds", "GREENLEAF-ENGINEERING LIMITED", True),
            ]
        elif "ch.company_name_normalised LIKE %(prefix_query)s" in sql:
            self.rows = [
                ("00000002", "GREENLEAF ENGINEERING SERVICES LIMITED", "active", "London", None, False),
            ]
        self.rows = self.rows[:parameters.get("fetch_limit", 10)]

    def fetchall(self):
        return self.rows


class FakeConnection:
    autocommit = False

    def __init__(self):
        self.executed = []

    def cursor(self):
        return FakeCursor(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_ranked_former_name_and_deduplication():
    conn = FakeConnection()
    result = PostgresCompanySearch(lambda: conn).search("GREENLEAF ENGINEERING LIMITED")
    assert [r.company_number for r in result] == ["00000006", "00000001", "00000002"]
    assert result[1].matched_name == "GREENLEAF ENGINEERING LIMITED"
    assert result[1].already_ingested is True
    assert result[1].company_name == "ALDER RENEWABLES LIMITED"
    assert conn.executed[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


def test_suggestions_use_only_fast_tiers():
    conn = FakeConnection()
    result = PostgresCompanySearch(lambda: conn).suggest("GREENLEAF ENGINEERING LIMITED")
    assert len(result) == 3
    assert len(conn.executed) <= 6  # transaction + five tiers
    assert not any("similarity(" in sql for sql, _ in conn.executed)


def test_short_suggestions_do_not_query():
    conn = FakeConnection()
    assert PostgresCompanySearch(lambda: conn).suggest("ab") == []
    assert conn.executed == []


def test_number_search_skips_name_tiers():
    conn = FakeConnection()
    PostgresCompanySearch(lambda: conn).search("00000003")
    assert len(conn.executed) == 2  # isolation + indexed exact number lookup


def test_connection_must_be_non_autocommit():
    conn = FakeConnection()
    conn.autocommit = True
    try:
        PostgresCompanySearch(lambda: conn).search("GREENLEAF")
    except ValueError as e:
        assert "autocommit=False" in str(e)
    else:
        raise AssertionError("Expected connection isolation guard")
