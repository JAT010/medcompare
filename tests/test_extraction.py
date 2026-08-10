"""Tests for the entity extractors (synthetic HTML fixtures only)."""

from __future__ import annotations

from hospital_extractor.extraction.accreditations import extract_accreditations
from hospital_extractor.extraction.awards import extract_awards
from hospital_extractor.extraction.contact import extract_contact
from hospital_extractor.extraction.departments import extract_departments
from hospital_extractor.extraction.doctors import HospitalContext, extract_doctors
from hospital_extractor.extraction.emergency import extract_emergency_services
from hospital_extractor.extraction.facilities import extract_facilities
from hospital_extractor.extraction.hospital import (
    extract_hospital_overview,
    extract_infrastructure,
)
from hospital_extractor.extraction.insurance import extract_insurance
from hospital_extractor.extraction.other import extract_other
from hospital_extractor.extraction.page_analyzer import analyze_page
from hospital_extractor.extraction.procedures import extract_procedures
from hospital_extractor.extraction.programs import extract_programs
from hospital_extractor.extraction.services import extract_services
from hospital_extractor.extraction.specialities import extract_specialities
from hospital_extractor.extraction.technology import extract_technologies
from hospital_extractor.extraction.treatments import extract_treatments
from hospital_extractor.extraction.visiting import extract_visiting

PAGE = "https://example.com/page"


def analyze(html: str, url: str = PAGE):
    return analyze_page(html, url)


class TestHospital:
    def test_overview_from_jsonld_and_about(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Super Speciality Hospital",
         "description":"A super-speciality hospital with 650+ beds and 200+ doctors.",
         "url":"https://example.com/"}
        </script></head>
        <body><h1>Saket Super Speciality Hospital</h1>
        <h2>About Us</h2>
        <p>Established in 2012. We are a 650+ bedded super-speciality hospital
        with 200+ doctors.</p>
        </body></html>
        """
        page = analyze(html, "https://example.com/hospital-network/saket")
        overview = extract_hospital_overview(page, website="https://example.com/")
        assert overview is not None
        assert overview.name == "Saket Super Speciality Hospital"
        assert overview.hospital_type == "super-speciality"
        assert overview.doctor_count == 200
        assert overview.website == "https://example.com/"
        assert overview.source_url == "https://example.com/hospital-network/saket"

    def test_overview_with_multiple_blocks(self) -> None:
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@type":"MedicalOrganization","name":"Max Healthcare","url":"https://example.com/"}
        </script>
        <script type="application/ld+json">
        {"@type":"LocalBusiness","name":"Saket Super Speciality Hospital",
         "url":"https://example.com/hospital-network/saket"}
        </script>
        </head>
        <body><h1>Saket Super Speciality Hospital</h1></body></html>
        """
        page = analyze(html, "https://example.com/hospital-network/saket")
        overview = extract_hospital_overview(page, website="https://example.com/")
        assert overview is not None
        assert overview.name == "Saket Super Speciality Hospital"
        assert overview.description is None

    def test_infrastructure_capacity(self) -> None:
        html = """
        <html><body><h2>About Us</h2>
        <p>650+ bedded hospital with 25 modular operation theatres and 170+ critical care beds.</p>
        </body></html>
        """
        infra = extract_infrastructure(analyze(html))
        assert infra is not None
        assert infra.total_beds == 650
        assert infra.operation_theatres == 25
        assert infra.critical_care_beds == 170


class TestContact:
    def test_from_jsonld_and_tel_links(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","name":"Saket Hospital",
         "address":{"streetAddress":"Press Enclave Road","addressLocality":"Saket",
                    "addressRegion":"Delhi","postalCode":"110017","addressCountry":"IN"},
         "telephone":"+91 11 4000 0000",
         "geo":{"latitude":28.5245,"longitude":77.2063}}
        </script></head>
        <body><a href="tel:+911140000000">Call</a>
        <a href="mailto:care@example.com">Email</a></body></html>
        """
        contact = extract_contact(analyze(html))
        assert contact is not None
        assert contact.city == "Saket"
        assert contact.state == "Delhi"
        assert contact.postal_code == "110017"
        assert contact.phone == "+91 11 4000 0000"
        assert contact.email == "care@example.com"
        assert contact.latitude == 28.5245

    def test_none_when_no_contact(self) -> None:
        assert extract_contact(analyze("<html><body><p>Hello</p></body></html>")) is None


class TestDoctors:
    def _context(self) -> HospitalContext:
        return HospitalContext(
            target_url="https://example.com/hospital-network/saket-max-super-speciality-hospital-delhi",
            slug="saket-max-super-speciality-hospital-delhi",
            name="Max Super Speciality Hospital, Saket",
            city="New Delhi",
            location_text="new delhi saket",
        )

    def test_from_cards_with_probable_association(self) -> None:
        html = """
        <html><body>
        <div class="doctor-card">
          <a href="/doctor/dr-john-doe"><h3>Dr. John Doe</h3></a>
          <p class="designation">Senior Consultant</p>
          <p>Experience: 25+ Years</p>
          <p>Cardiology, Interventional</p>
        </div>
        </body></html>
        """
        page = analyze(html, "https://example.com/doctor/cardiologists-in-saket")
        records = extract_doctors(page, self._context())
        assert len(records) == 1
        doctor = records[0]
        assert doctor.name == "Dr. John Doe"
        assert doctor.designation == "Senior Consultant"
        assert doctor.experience == "25+ Years"
        assert doctor.speciality == "Cardiology, Interventional"
        assert doctor.profile_url == "https://example.com/doctor/dr-john-doe"
        assert doctor.hospital_association == "probable"

    def test_from_physician_jsonld_confirmed(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Physician","name":"Dr. Jane Smith",
         "medicalSpecialty":["Cardiology"],
         "worksFor":{"name":"Max Super Speciality Hospital, Saket"}}
        </script></head><body></body></html>
        """
        page = analyze(html, "https://example.com/doctor/dr-jane-smith")
        records = extract_doctors(page, self._context())
        assert len(records) == 1
        assert records[0].name == "Dr. Jane Smith"
        assert records[0].speciality == "Cardiology"
        assert records[0].hospital_association == "confirmed"

    def test_works_for_other_hospital_is_not_associated(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Physician","name":"Dr. X","worksFor":{"name":"Some Other Hospital"}}
        </script></head></html>
        """
        page = analyze(html, "https://example.com/doctor/dr-x")
        records = extract_doctors(page, self._context())
        assert records[0].hospital_association == "not_associated"


class TestSpecialities:
    def test_from_links(self) -> None:
        html = """
        <html><body>
        <a href="/speciality/cardiac-sciences">Cardiac Sciences</a>
        <a href="/speciality/oncology">Oncology</a>
        </body></html>
        """
        records = extract_specialities(analyze(html))
        names = {record.name for record in records}
        assert names == {"Cardiac Sciences", "Oncology"}

    def test_boilerplate_links_skipped(self) -> None:
        html = '<html><body><a href="/speciality/oncology">Our Specialities</a></body></html>'
        assert extract_specialities(analyze(html)) == []


class TestDepartments:
    def test_from_links(self) -> None:
        html = (
            '<html><body><a href="/departments/cardiology">'
            "Cardiology Department</a></body></html>"
        )
        records = extract_departments(analyze(html))
        assert [record.name for record in records] == ["Cardiology Department"]


class TestTreatments:
    def test_speciality_from_url(self) -> None:
        html = """
        <html><body>
        <a href="/treatment/knee-replacement/orthopaedics">Knee Replacement</a>
        </body></html>
        """
        records = extract_treatments(analyze(html))
        assert records[0].name == "Knee Replacement"
        assert records[0].speciality == "Orthopaedics"


class TestProcedures:
    def test_from_h1_detail(self) -> None:
        html = """
        <html><body><h1>Angioplasty</h1>
        <h2>Overview</h2><p>A minimally invasive procedure to open blocked arteries.</p>
        </body></html>
        """
        page = analyze(html, "https://example.com/procedure/angioplasty/cardiac")
        records = extract_procedures(page)
        assert len(records) == 1
        assert records[0].name == "Angioplasty"
        assert records[0].speciality == "Cardiac"


class TestTechnology:
    def test_from_technology_headings(self) -> None:
        html = """
        <html><body>
        <h2>3 Tesla MRI</h2><p>High-resolution imaging.</p>
        <h2>Da Vinci Robot</h2><p>Robotic surgery.</p>
        </body></html>
        """
        page = analyze(html, "https://example.com/technology")
        records = extract_technologies(page)
        names = {record.name for record in records}
        assert "3 Tesla MRI" in names
        assert any(record.category == "robotics" for record in records)


class TestFacilities:
    def test_from_faq(self) -> None:
        html = """
        <html><body><div class="faq">
        <h3>Is there a 24x7 pharmacy?</h3><p>Yes, our pharmacy is open 24x7.</p>
        </div></body></html>
        """
        page = analyze(html, "https://example.com/faq")
        records = extract_facilities(page)
        assert records
        assert records[0].name == "Is there a 24x7 pharmacy?"
        assert records[0].availability == "24/7"


class TestServices:
    def test_from_faq(self) -> None:
        html = """
        <html><body><div class="faq">
        <h3>Do you offer virtual consultations?</h3><p>Yes, teleconsultation is available.</p>
        </div></body></html>
        """
        page = analyze(html, "https://example.com/faq")
        records = extract_services(page)
        assert any("virtual" in record.name.lower() for record in records)


class TestEmergency:
    def test_from_page(self) -> None:
        html = """
        <html><body><h1>24x7 Emergency</h1>
        <a href="tel:911">Ambulance</a>
        <p>Our emergency department is open round the clock. Call +91 11 4000 0000.</p>
        </body></html>
        """
        page = analyze(html, "https://example.com/emergency")
        records = extract_emergency_services(page)
        assert records
        assert any(record.availability == "24/7" for record in records)
        assert any(record.contact for record in records)


class TestInsurance:
    def test_from_tpa_section(self) -> None:
        html = """
        <html><body><h2>TPA's in our network</h2>
        <p>Acko General Insurance, Star Health Insurance</p>
        </body></html>
        """
        page = analyze(html, "https://example.com/insurance")
        records = extract_insurance(page)
        names = {record.name for record in records}
        assert "Acko General Insurance" in names
        assert "Star Health Insurance" in names

    def test_cashless_flag_from_faq(self) -> None:
        html = """
        <html><body><div class="faq">
        <h3>Is cashless insurance available?</h3><p>Yes, we are cashless with all major TPAs.</p>
        </div></body></html>
        """
        page = analyze(html, "https://example.com/faq")
        records = extract_insurance(page)
        assert records
        assert records[0].cashless is True


class TestAccreditations:
    def test_from_section(self) -> None:
        html = """
        <html><body><h2>NABH Accreditation</h2>
        <p>Re-accredited by NABH in 2023.</p>
        </body></html>
        """
        page = analyze(html, "https://example.com/accreditation")
        records = extract_accreditations(page)
        assert records
        assert records[0].name == "NABH Accreditation"
        assert records[0].year == 2023


class TestAwards:
    def test_from_cards_with_year(self) -> None:
        html = """
        <html><body>
        <div class="award-card">
          <h3>Best Hospital of the Year (2022)</h3>
          <p>Awarded by National Health Awards.</p>
        </div>
        </body></html>
        """
        page = analyze(html, "https://example.com/awards")
        records = extract_awards(page)
        assert records
        assert records[0].name == "Best Hospital of the Year"
        assert records[0].year == 2022
        assert "National Health Awards" in records[0].organization


class TestPrograms:
    def test_from_faq(self) -> None:
        html = """
        <html><body><div class="faq">
        <h3>Do you offer master health check-up plans?</h3><p>Yes, we have preventive packages.</p>
        </div></body></html>
        """
        page = analyze(html, "https://example.com/faq")
        records = extract_programs(page)
        assert records
        assert records[0].category == "health check-up"


class TestVisiting:
    def test_from_faq(self) -> None:
        html = """
        <html><body><div class="faq">
        <h3>What are the visiting hours?</h3><p>Visiting hours are 8 AM to 8 PM.</p>
        <h3>How do I book an appointment?</h3><p>Call our helpline to book an appointment.</p>
        </div></body></html>
        """
        page = analyze(html, "https://example.com/faq")
        visiting = extract_visiting(page)
        assert visiting is not None
        assert "8 AM to 8 PM" in visiting.visiting_hours
        assert "helpline" in visiting.appointment_information

    def test_opd_hours_from_jsonld(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Hospital","openingHoursSpecification":[
          {"dayOfWeek":"Monday","opens":"09:00","closes":"17:00"}]}
        </script></head></html>
        """
        visiting = extract_visiting(analyze(html))
        assert visiting is not None
        assert "Monday" in visiting.opd_hours
        assert visiting.extraction_method == "jsonld"


class TestOther:
    def test_unmatched_faq_becomes_label_value(self) -> None:
        html = """
        <html><body><div class="faq">
        <h3>Are pets allowed inside the hospital?</h3>
        <p>Service animals are welcome in common areas.</p>
        </div></body></html>
        """
        page = analyze(html, "https://example.com/faq")
        records = extract_other(page)
        assert records
        assert records[0].label == "Are pets allowed inside the hospital?"
        assert records[0].value == "Service animals are welcome in common areas."
