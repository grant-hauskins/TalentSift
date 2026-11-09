"""Masking hardening (T16): names, street addresses, and ZIP codes in the layouts the prototype audit found.

Each case is a real gap from docs/prototype_audit.md. `leaks` lists strings that must not survive masking;
`keeps` lists job-relevant strings that must.
"""

import pytest

from talentsift.masking import find_name_words, mask_text, name_was_masked

NAME_CASES = [
    # (resume text, strings that must be gone)
    ("Jane Doe (312) 555-0142 jane.doe@gmail.com\nSummary\nJane Doe is an engineer.", ["Jane", "Doe"]),
    ("Jane Doe (she/her)\nChicago, IL 60607\nJane led a team.", ["Jane", "Doe", "60607"]),
    ('Jane "JD" Doe\nSoftware Engineer\nReferences: ask Jane Doe', ["Jane", "Doe"]),
    ("Doe, Jane\nSoftware Engineer\nJane Doe built APIs.", ["Jane", "Doe"]),
    ("Jane Doe: Software Engineer\nJane Doe", ["Jane", "Doe"]),
    ("jane.doe@gmail.com | 312-555-0142\nJane Doe\nEngineer", ["Jane", "Doe"]),
    ("Contact\nJane Doe\n123 Main St", ["Jane", "Doe", "Main"]),
    ("Page 1 of 2\nJane Doe\nEngineer", ["Jane", "Doe"]),
    ("CURRICULUM VITAE - Jane Doe\nJane Doe", ["Jane", "Doe"]),
    ("Resume of Jane Doe\nSummary of skills. Jane Doe", ["Jane", "Doe"]),
    ("Software Engineer\nJane Doe\nSoftware Engineer with 5 years.", ["Jane", "Doe"]),
    ("Jane Doe Senior Software Engineer\nSenior Software Engineer at Acme", ["Jane", "Doe"]),
    ("JANE MCDONALD\nPrepared by Ms. McDonald", ["JANE", "MCDONALD", "McDonald"]),
    ("Mary-Jane Watson-Parker\nEngineer\nMary Jane Watson presented. Ms. Parker", ["Mary", "Jane", "Watson", "Parker"]),
    ("Sean O’Brien\nEngineer\nSean O'Brien, page 2", ["Sean", "Brien"]),
    ("Jane Doe\nPortfolio: JaneDoe.com | GitHub: janedoe | linkedin: in/jane-doe-1234", ["Jane", "Doe", "janedoe", "jane-doe"]),
]

ADDRESS_CASES = [
    ("123 MAIN STREET, CHICAGO, IL 60607", ["123 MAIN", "60607"]),
    ("123 main street, chicago, il 60607", ["123 main", "60607"]),
    ("Chicago, Illinois 60607", ["60607"]),
    ("Chicago IL, 60607", ["60607"]),
    ("Chicago, IL, 60607", ["60607"]),
    ("Chicago, Il 60607", ["60607"]),
    ("Chicago 60607", ["60607"]),
    ("ZIP: 60607", ["60607"]),
    ("123 Main St\nApt 4B\nChicago, IL\n60607", ["123 Main", "4B", "60607"]),
    ("221B Baker Street, Apt 3", ["221B", "Baker"]),
    ("12-34 56th Avenue", ["12-34", "56th"]),
    ("123 Broadway, New York, NY 10007", ["123 Broadway", "10007"]),
    ("456 Sunset Plaza, Unit 12", ["456 Sunset"]),
    ("Apt 4B, 123 Main St", ["4B", "123 Main"]),
    ("Post Office Box 123", ["Box 123"]),
    ("Toronto, ON M5V 2T6", ["M5V 2T6"]),
    ("Anytown, OH 43004-1234", ["43004"]),
]

KEEP_CASES = [
    # Job-relevant text the old rules wrongly masked.
    "5 Main responsibilities; 2012 - 2016 St. Xavier's College; 2019 Dr. Smith Research Award",
    "Handled 15 Court filings per week; maintained 120 Highway projects; 3 Way merge tooling; 2 Way radio",
    "Managed 250 Square feet lab; 40 Circle K stores; Top 10 Place finish; 5 Drive campaigns",
    "Certified to EN 50128 and BS 25999; supported HR 10000 employees; PO 45021 processing",
    "C++11 14 17, C++17 20",
    "Skills: ASP.NET, Node.js, socket.io, fast.ai, draw.io, vue.js",
    "Software Engineer @Google (2019-2022); used @Autowired and @angular/core",
    "Raised $12500 and served 10000 users across 43000 stores",
]


@pytest.mark.parametrize("text, leaks", NAME_CASES)
def test_the_name_is_masked_wherever_it_sits_in_the_header(text, leaks):
    result = mask_text(text)
    assert result.name_was_masked
    for leak in leaks:
        assert leak not in result.masked_text, result.masked_text


@pytest.mark.parametrize("line, leaks", ADDRESS_CASES)
def test_street_addresses_and_zip_codes_are_masked(line, leaks):
    masked = mask_text(f"Jane Doe\n{line}\nSUMMARY\nAnalyst.").masked_text
    assert "[ADDRESS]" in masked
    for leak in leaks:
        assert leak not in masked, masked


@pytest.mark.parametrize("line", KEEP_CASES)
def test_job_relevant_text_is_left_alone(line):
    masked = mask_text(f"Jane Doe\nSUMMARY\n{line}").masked_text
    assert masked == f"[NAME]\nSUMMARY\n{line}"


def test_the_employer_above_a_city_line_is_not_swallowed():
    masked = mask_text("Jane Doe\nEXPERIENCE\nSenior Data Engineer, Acme Robotics\nChicago, IL 60607\nBuilt pipelines").masked_text
    assert masked == "[NAME]\nEXPERIENCE\nSenior Data Engineer, Acme Robotics\n[ADDRESS]\nBuilt pipelines"


def test_short_names_do_not_erase_acronyms_or_months():
    assert mask_text("Ai Chen\nBuilt AI products with generative AI.").masked_text == "[NAME]\nBuilt AI products with generative AI."
    assert mask_text("May Ruby\nMay 2021 - Present: analyst").masked_text == "[NAME]\nMay 2021 - Present: analyst"
    assert mask_text("LAN DO\nConfigured LAN and WAN.").masked_text == "[NAME]\nConfigured [NAME] and WAN."  # header in caps: mask


def test_a_resume_with_no_findable_name_is_reported_not_guessed():
    for text in ("Madonna\nSinger\nMadonna toured.", "SUMMARY\nAnalyst with SQL skills.", "Software Engineer\nBuilds APIs."):
        assert find_name_words(text) == []
        assert not mask_text(text).name_was_masked
    assert name_was_masked("[NAME]\nSUMMARY") and not name_was_masked("SUMMARY")
