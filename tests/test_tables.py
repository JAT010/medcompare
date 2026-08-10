"""Tests for table extraction (visiting hours, capacity, contacts, insurance)."""

from __future__ import annotations

from hospital_extractor.extraction.page_analyzer import analyze_page
from hospital_extractor.extraction.tables import (
    Table,
    classify_table,
    extract_table_records,
    parse_tables,
)

PAGE = "https://example.com/page"


def analyze(html: str, url: str = PAGE):
    return analyze_page(html, url)


class TestParseTables:
    def test_parses_header_and_data_rows(self) -> None:
        html = """
        <table>
          <tr><th>Day</th><th>Visiting Hours</th></tr>
          <tr><td>Monday</td><td>8 AM - 8 PM</td></tr>
          <tr><td>Sunday</td><td>10 AM - 6 PM</td></tr>
        </table>
        """
        tables = parse_tables(analyze(html).soup)
        assert len(tables) == 1
        table = tables[0]
        assert table.headers == ("Day", "Visiting Hours")
        assert ("Monday", "8 AM - 8 PM") in table.rows

    def test_skips_tables_inside_navigation(self) -> None:
        html = """
        <html><body>
        <nav><table><tr><td>Menu A</td><td>Menu B</td></tr></table></nav>
        <table><tr><th>Day</th><th>Time</th></tr><tr><td>Mon</td><td>9-5</td></tr></table>
        </body></html>
        """
        tables = parse_tables(analyze(html).soup)
        assert len(tables) == 1
        assert tables[0].headers == ("Day", "Time")


class TestClassifyTable:
    def test_visiting(self) -> None:
        assert classify_table(Table(("Day", "Visiting Hours"), ())) == "visiting"

    def test_capacity(self) -> None:
        assert classify_table(Table(("Facility", "Number of Beds"), ())) == "capacity"

    def test_department_needs_contact_hint(self) -> None:
        assert classify_table(Table(("Department", "Contact"), ())) == "department"
        assert classify_table(Table(("Department", "Head"), ())) is None

    def test_insurance(self) -> None:
        assert classify_table(Table(("Insurance Company", "TPA"), ())) == "insurance"

    def test_service(self) -> None:
        assert classify_table(Table(("Service", "Availability"), ())) == "service"

    def test_unknown(self) -> None:
        assert classify_table(Table(("Foo", "Bar"), ())) is None


class TestExtractTableRecords:
    def test_visiting_hours(self) -> None:
        html = """
        <table>
          <tr><th>Day</th><th>Visiting Hours</th></tr>
          <tr><td>Monday</td><td>8 AM - 8 PM</td></tr>
          <tr><td>Sunday</td><td>10 AM - 6 PM</td></tr>
        </table>
        """
        records = extract_table_records(analyze(html))
        assert len(records) == 1
        visiting = records[0]
        assert visiting.visiting_hours == "Monday: 8 AM - 8 PM; Sunday: 10 AM - 6 PM"
        assert visiting.extraction_method == "table"
        assert visiting.source_strength == "medium"

    def test_opd_table(self) -> None:
        html = """
        <table>
          <tr><th>Day</th><th>OPD Timings</th></tr>
          <tr><td>Mon - Sat</td><td>9:00 AM - 5:00 PM</td></tr>
        </table>
        """
        records = extract_table_records(analyze(html))
        assert records[0].opd_hours == "Mon - Sat: 9:00 AM - 5:00 PM"

    def test_capacity_table(self) -> None:
        html = """
        <table>
          <tr><th>Facility</th><th>Number of Beds</th></tr>
          <tr><td>Total Beds</td><td>650</td></tr>
          <tr><td>ICU Beds</td><td>80</td></tr>
          <tr><td>Operation Theatres</td><td>25</td></tr>
        </table>
        """
        records = extract_table_records(analyze(html))
        assert len(records) == 1
        infra = records[0]
        assert infra.total_beds == 650
        assert infra.icu_beds == 80
        assert infra.operation_theatres == 25

    def test_department_table_with_contact(self) -> None:
        html = """
        <table>
          <tr><th>Department</th><th>Contact</th></tr>
          <tr><td>Cardiology</td><td>+91 11 4000 0000</td></tr>
        </table>
        """
        records = extract_table_records(analyze(html))
        assert len(records) == 1
        assert records[0].name == "Cardiology"
        assert records[0].description == "Contact: +91 11 4000 0000"

    def test_insurance_table_with_flags(self) -> None:
        html = """
        <table>
          <tr><th>Company</th><th>Cashless</th><th>TPA</th></tr>
          <tr><td>Star Health</td><td>Yes</td><td>No</td></tr>
          <tr><td>ESIC</td><td>No</td><td>Yes</td></tr>
        </table>
        """
        records = extract_table_records(analyze(html))
        assert len(records) == 2
        by_name = {record.name: record for record in records}
        assert by_name["Star Health"].cashless is True
        assert by_name["Star Health"].tpa is None
        assert by_name["ESIC"].government_scheme is True

    def test_service_table(self) -> None:
        html = """
        <table>
          <tr><th>Service</th><th>Availability</th><th>Description</th></tr>
          <tr><td>Pharmacy</td><td>24x7</td><td>Open round the clock.</td></tr>
        </table>
        """
        records = extract_table_records(analyze(html))
        assert len(records) == 1
        assert records[0].name == "Pharmacy"
        assert records[0].availability == "24/7"

    def test_unclassifiable_table_produces_nothing(self) -> None:
        html = """
        <table>
          <tr><th>Name</th><th>Score</th></tr>
          <tr><td>Alice</td><td>90</td></tr>
        </table>
        """
        assert extract_table_records(analyze(html)) == []
