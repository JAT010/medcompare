"""Tests for page analysis (document signals shared by every extractor)."""

from __future__ import annotations

from hospital_extractor.extraction.page_analyzer import Section, analyze_page

PAGE = "https://example.com/visiting"
NAV = """
<nav>
  <a href="/home">Home</a>
  <p>Menu item paragraph</p>
  <ul><li>Nav list item</li></ul>
  <h2>Navigation heading</h2>
</nav>
"""


def analyze(html: str, url: str = PAGE):
    return analyze_page(html, url)


class TestHeadingsAndSections:
    def test_headings_with_tag_names(self) -> None:
        page = analyze("<h1>Sunrise Hospital</h1><h2>About Us</h2>")
        assert page.headings == (("h1", "Sunrise Hospital"), ("h2", "About Us"))

    def test_sections_pair_heading_with_body(self) -> None:
        html = """
        <h2>Visiting Hours</h2>
        <p>Wards open from 8 AM to 8 PM every day.</p>
        <h2>Parking</h2>
        <p>Free parking for visitors.</p>
        """
        page = analyze(html)
        assert page.sections == (
            Section("Visiting Hours", "Wards open from 8 AM to 8 PM every day."),
            Section("Parking", "Free parking for visitors."),
        )

    def test_sections_skip_navigation_headings(self) -> None:
        page = analyze(f"{NAV}<h2>About</h2><p>Info here.</p>")
        headings = [section.heading for section in page.sections]
        assert headings == ["About"]


class TestFaq:
    def test_faq_from_details_summary(self) -> None:
        html = """
        <details>
          <summary>What are the visiting hours?</summary>
          Visiting hours are 8 AM to 8 PM.
        </details>
        """
        page = analyze(html)
        assert len(page.faq_items) == 1
        item = page.faq_items[0]
        assert item.question == "What are the visiting hours?"
        assert item.answer == "Visiting hours are 8 AM to 8 PM."

    def test_faq_from_html_container(self) -> None:
        html = """
        <div class="faq">
          <div class="question">Where is the hospital?</div>
          <div class="answer">Near the central bus stand.</div>
        </div>
        """
        page = analyze(html)
        assert len(page.faq_items) == 1
        assert page.faq_items[0].question == "Where is the hospital?"
        assert page.faq_items[0].answer == "Near the central bus stand."

    def test_placeholder_answers_filtered(self) -> None:
        html = """
        <div class="faq">
          <div class="question">Question one?</div>
          <div class="answer">lorem ipsum</div>
        </div>
        <details>
          <summary>Question two?</summary>
          Real answer here.
        </details>
        """
        page = analyze(html)
        assert [item.question for item in page.faq_items] == ["Question two?"]

    def test_duplicate_questions_deduplicated(self) -> None:
        html = """
        <div class="faq"><div class="question">Same question?</div>
          <div class="answer">First answer.</div></div>
        <details><summary>Same question?</summary>Second answer.</details>
        """
        page = analyze(html)
        assert len(page.faq_items) == 1

    def test_faq_from_jsonld(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"FAQPage",
         "mainEntity":[{"@type":"Question","name":"Do you have ICU?",
         "acceptedAnswer":{"@type":"Answer","text":"Yes, 80 ICU beds."}}]}
        </script>
        """
        page = analyze(html)
        assert len(page.faq_items) == 1
        assert page.faq_items[0].question == "Do you have ICU?"
        assert page.faq_items[0].answer == "Yes, 80 ICU beds."


class TestBreadcrumbs:
    def test_from_jsonld(self) -> None:
        html = """
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"BreadcrumbList",
         "itemListElement":[{"@type":"ListItem","position":1,"name":"Home"},
                            {"@type":"ListItem","position":2,"name":"Hospitals"}]}
        </script>
        """
        page = analyze(html)
        assert page.breadcrumbs == ("Home", "Hospitals")

    def test_from_html(self) -> None:
        html = """
        <nav class="breadcrumb">
          <a href="/">Home</a> > <a href="/hospitals">Hospitals</a>
        </nav>
        """
        page = analyze(html)
        assert page.breadcrumbs == ("Home", "Hospitals")


class TestContactSignals:
    def test_tel_numbers_from_links_and_text(self) -> None:
        html = """
        <a href="tel:+911140000000">Call us</a>
        <p>Reception: 011-4000-0001</p>
        """
        page = analyze(html)
        numbers = " ".join(page.tel_numbers)
        assert "+911140000000" in numbers
        assert "011-4000-0001" in numbers

    def test_emails(self) -> None:
        page = analyze('<a href="mailto:info@example.com">Email</a>')
        assert page.emails == ("info@example.com",)

    def test_address_links(self) -> None:
        html = '<a href="address:North Block, 1st Floor">Map</a>'
        page = analyze(html)
        assert page.address_links == ("address:North Block, 1st Floor",)


class TestContentScoping:
    def test_paragraphs_exclude_navigation(self) -> None:
        page = analyze(f"{NAV}<main><p>This is real content text.</p></main>")
        assert page.paragraphs == ("This is real content text.",)

    def test_lists_exclude_navigation(self) -> None:
        page = analyze(f"{NAV}<main><ul><li>Real list item</li></ul></main>")
        assert page.lists == ("Real list item",)

    def test_links_resolved_and_exclude_navigation(self) -> None:
        html = f"{NAV}<main><a href=\"/doctors\">Doctors</a></main>"
        page = analyze(html)
        assert page.links == ("https://example.com/doctors",)


class TestCards:
    def test_cards_from_broadened_vocabulary(self) -> None:
        html = """
        <div class="doctor-card"><p>Dr. A. Sharma</p></div>
        <div class="team-member"><p>Dr. B. Rao</p></div>
        <div class="plain"><p>Not a card</p></div>
        """
        page = analyze(html)
        assert len(page.cards) == 2

    def test_cards_exclude_navigation(self) -> None:
        page = analyze(f"{NAV}<div class=\"member-card\"><p>Dr. X</p></div>")
        assert len(page.cards) == 1


class TestMetadata:
    def test_meta_description(self) -> None:
        html = '<meta name="description" content="A 650-bed multi-speciality hospital.">'
        page = analyze(html)
        assert page.meta_description == "A 650-bed multi-speciality hospital."

    def test_canonical_url(self) -> None:
        html = '<link rel="canonical" href="https://example.com/canonical">'
        page = analyze(html)
        assert page.canonical_url == "https://example.com/canonical"

    def test_title_from_document(self) -> None:
        page = analyze("<html><head><title>Sunrise Hospital</title></head></html>")
        assert page.title == "Sunrise Hospital"
