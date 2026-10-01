"""PII masking: deterministic, thorough, and leaves job-relevant text alone."""

import pytest

from talentsift.masking import MaskingError, contains_image_content, find_name_words, mask_text

RESUME = """Jordan A. Rivera | jordan.rivera@example.com | (312) 555-0142 | linkedin.com/in/jordanrivera
123 Example Street, Apt 4B, Anytown, OH 43004
Portfolio: https://jordanrivera.dev/projects. Twitter @jrivera_data

SUMMARY
Data analyst. Jordan built SQL dashboards in Tableau and Power BI. Skilled in ASP.NET and Node.js.
Grant writing for nonprofits. RIVERA led a team of four. Call +44 20 7946 0958 or 312.555.0199 ext. 12.

EXPERIENCE
Data Analyst, Acme Corp, 2019 - 2023
- Processed 1,500,000 rows for 12000 customers and cut reporting time 15%.

EDUCATION
B.S. Statistics, State University
2014 - 2018
Class of 2018, GPA 3.8
"""


@pytest.fixture
def masked():
    return mask_text(RESUME).masked_text


def test_emails_are_masked(masked):
    assert "jordan.rivera@example.com" not in masked
    assert "[EMAIL]" in masked


@pytest.mark.parametrize("phone", ["(312) 555-0142", "+44 20 7946 0958", "312.555.0199 ext. 12"])
def test_phones_are_masked(masked, phone):
    assert phone not in masked


@pytest.mark.parametrize(
    "phone", ["312-555-0142", "+1 312 555 0142", "3125550142", "(312)555-0142", "+33 1 23 45 67 89"]
)
def test_phone_formats(phone):
    assert mask_text(f"Call me at {phone} today").masked_text == "Call me at [PHONE] today"


def test_urls_and_social_links_are_masked(masked):
    for value in ["linkedin.com/in/jordanrivera", "https://jordanrivera.dev", "@jrivera_data"]:
        assert value not in masked
    assert "Portfolio: [URL]." in masked  # sentence punctuation survives


def test_address_with_zip_is_masked(masked):
    assert "123 Example Street" not in masked
    assert "43004" not in masked
    assert "Anytown" not in masked
    assert masked.splitlines()[1] == "[ADDRESS]"


@pytest.mark.parametrize(
    "line",
    [
        "4500 North Maple Ave., Suite 210",
        "Springfield, IL 62704",
        "PO Box 1234",
        "Mailing code 62704-1234",
    ],
)
def test_address_formats(line):
    masked = mask_text(line).masked_text
    assert "[ADDRESS]" in masked
    assert not any(char.isdigit() for char in masked.replace("[ADDRESS]", ""))


def test_name_is_masked_everywhere(masked):
    assert "Jordan" not in masked
    assert "Rivera" not in masked and "RIVERA" not in masked
    assert masked.startswith("[NAME] | [EMAIL]")
    assert "[NAME] built SQL dashboards" in masked
    assert "[NAME] led a team" in masked


def test_job_relevant_text_is_untouched(masked):
    for keep in ["SQL dashboards in Tableau and Power BI", "ASP.NET and Node.js", "1,500,000 rows", "12000 customers"]:
        assert keep in masked
    assert "Grant writing" in masked  # a capitalized common word that is not the applicant's name


def test_grad_years_untouched_by_default(masked):
    assert "Data Analyst, Acme Corp, 2019 - 2023" in masked
    assert "Class of 2018" in masked and "2014 - 2018" in masked


def test_grad_years_masked_when_flag_on():
    result = mask_text(RESUME, mask_grad_years=True)
    masked = result.masked_text
    assert "Class of [YEAR]" in masked
    assert "[YEAR] - [YEAR]" in masked  # year range on its own line inside EDUCATION
    assert "Data Analyst, Acme Corp, 2019 - 2023" in masked  # work history keeps its years
    assert result.counts["grad_years"] == 3


def test_masking_is_deterministic():
    assert mask_text(RESUME) == mask_text(RESUME)


def test_counts_are_reported():
    counts = mask_text(RESUME).counts
    assert counts["emails"] == 1
    assert counts["phones"] == 3
    assert counts["urls"] == 3
    assert counts["names"] >= 3


def test_first_line_heuristic():
    assert find_name_words("Resume\n\nJane Doe\nAnalyst") == ["Jane", "Doe"]
    assert find_name_words("Name: Ana María López-García, MBA\n...") == ["Ana", "María", "López-García"]
    assert find_name_words("SUMMARY\nAnalyst with 5 years") == []
    assert find_name_words("Data Analyst 2024 Portfolio\n") == []


def test_name_swap_pair_masks_identically():
    body = "\nSUMMARY\nAnalyst. {first} automated SQL reports.\n"
    first = mask_text("Emily Sampleton | emily.s@example.com" + body.format(first="Emily")).masked_text
    second = mask_text("Jamal Testwood | jamal.t@example.com" + body.format(first="Jamal")).masked_text
    assert first == second


def test_image_content_is_removed_and_guarded():
    text = "Jane Doe\nPhoto: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk\n<img src='x.png'>"
    result = mask_text(text)
    assert result.counts["images"] == 2
    assert not contains_image_content(result.masked_text)
    assert "[IMAGE]" in result.masked_text


def test_guard_raises_if_image_content_survives(monkeypatch):
    from talentsift import masking

    monkeypatch.setattr(masking, "_IMAGE_REMOVAL_PATTERNS", masking._IMAGE_PATTERNS[:1])  # simulate a gap
    with pytest.raises(MaskingError):
        masking.mask_text("Jane Doe\n" + "A" * 300)
