"""Generate 20 fictional PDF resumes and 2 role profiles for demos and tests.

    python scripts/make_sample_resumes.py

Everything here is invented: names, employers, schools, emails (@example.com), phone numbers (555-01xx), and
addresses. The output is deterministic (reportlab's invariant mode), so re-running produces identical files.

Planted cases:
* strong, borderline, and weak fits for both roles (Business Data Analyst, Operations Coordinator)
* a name-swap pair: two resumes identical except for the name and contact details
* a resume whose second page is a scanned image (parsed as "partial", always routed to review)
* an image-only resume (parsed as "needs_ocr", skipped with a clear message)
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
RESUME_DIR = ROOT / "data" / "resumes" / "samples"
ROLE_DIR = ROOT / "data" / "samples" / "roles"

ROLES = {
    "business_data_analyst": {
        "title": "Business Data Analyst",
        "auto_reject_threshold": 40,
        "top_n": 5,
        "posting_text": """Business Data Analyst
Fictional Foods Co. is hiring a Business Data Analyst to turn sales and supply data into decisions for store and finance leaders.

Responsibilities
- Build and maintain weekly sales reports
- Partner with finance and operations on forecasting

Requirements:
- SQL queries to analyze sales data
- Dashboards in Tableau or Power BI
- Advanced Excel with pivot tables and VLOOKUP
- Present findings to non-technical stakeholders

Nice to have:
- Python with pandas for data cleaning
- A/B testing and statistics
- Data warehouse or ETL pipelines
""",
    },
    "operations_coordinator": {
        "title": "Operations Coordinator",
        "auto_reject_threshold": 40,
        "top_n": 3,
        "posting_text": """Operations Coordinator
Fictional Freight Partners needs an Operations Coordinator to keep warehouse schedules, vendors, and inventory running smoothly.

Responsibilities
- Coordinate daily operations across two warehouses
- Prepare weekly operations reports

Requirements:
- Scheduling and calendar coordination for crews and deliveries
- Vendor management and purchase orders
- Inventory tracking and cycle counts
- Write standard operating procedures (SOPs)

Nice to have:
- Logistics and shipping coordination
- ERP systems such as SAP or NetSuite
- Event planning
- Recent graduate preferred
""",
    },
}

# Reusable experience bullets. Strong variants echo a criterion closely; weak variants touch it lightly.
B = {
    "sql": "Wrote SQL queries to analyze sales data across 40 stores.",
    "sql_weak": "Ran basic SQL queries for weekly reports.",
    "dash": "Built dashboards in Tableau and Power BI for finance leaders.",
    "dash_weak": "Updated an existing Tableau report each Monday.",
    "excel": "Advanced Excel models with pivot tables and VLOOKUP for budget tracking.",
    "excel_weak": "Kept team budgets in Excel spreadsheets.",
    "present": "Presented findings to non-technical stakeholders in monthly business reviews.",
    "present_weak": "Shared findings with my manager each week.",
    "python": "Used Python and pandas for data cleaning of vendor files.",
    "ab": "Designed A/B testing for promotions and reported statistics on lift.",
    "etl": "Maintained ETL pipelines into the company data warehouse.",
    "sched": "Managed scheduling and calendar coordination for 30 crews and daily deliveries.",
    "sched_weak": "Helped with scheduling for the front desk.",
    "vendor": "Led vendor management and issued purchase orders to 25 suppliers.",
    "vendor_weak": "Called vendors about late shipments.",
    "inventory": "Ran inventory tracking and weekly cycle counts across two warehouses.",
    "inventory_weak": "Counted stock in the back room at closing.",
    "inventory_mid": "Tracked inventory for the office supply closet.",
    "sop": "Authored standard operating procedures (SOPs) for receiving and dispatch.",
    "logistics": "Coordinated logistics and shipping with three regional carriers.",
    "erp": "Entered orders in ERP systems such as SAP and NetSuite.",
    "event": "Ran event planning for the annual customer open house.",
    "barista": "Prepared drinks and handled cash at a busy cafe.",
    "service": "Resolved customer questions at the counter with a friendly attitude.",
    "design": "Designed logos and brand guides in Adobe Illustrator.",
    "print": "Prepared print layouts for brochures and posters.",
    "teach": "Taught algebra lesson plans to four classes of 30 students.",
    "coach": "Coached the debate club to a regional tournament.",
}


@dataclass
class Resume:
    filename: str
    name: str
    email: str
    phone: str
    address: str
    summary: str
    jobs: list[tuple[str, str, list[str]]]  # (title, "Employer, dates", bullets)
    education: list[str]
    skills: str
    linkedin: str = ""
    scanned_page: list[str] = field(default_factory=list)  # lines drawn as an image on page 2
    image_only: bool = False


def _jobs(*specs):
    return list(specs)


RESUMES = [
    # --- Business Data Analyst: strong -------------------------------------------------------------
    Resume("01_avery_exampleton.pdf", "Avery Exampleton", "avery.exampleton@example.com", "(312) 555-0101",
           "101 Example Street, Anytown, OH 43001",
           "Business data analyst with five years of retail analytics experience.",
           _jobs(("Senior Data Analyst", "Fictional Grocers, 2021 - 2025", [B["sql"], B["dash"], B["present"], B["ab"]]),
                 ("Data Analyst", "Sample Retail Group, 2019 - 2021", [B["excel"], B["python"], B["etl"]])),
           ["B.S. Business Analytics, Example State University, 2019"],
           "SQL, Tableau, Power BI, Excel, Python, pandas", "linkedin.com/in/averyexampleton"),
    Resume("02_jordan_sampleton.pdf", "Jordan Sampleton", "jordan.sampleton@example.com", "(312) 555-0102",
           "202 Sample Avenue, Anytown, OH 43002",
           "Analyst who turns messy sales data into clear decisions.",
           _jobs(("Data Analyst", "Placeholder Pantry, 2020 - 2025", [B["sql"], B["dash"], B["excel"], B["present"]]),
                 ("Analytics Intern", "Mock Markets, 2019 - 2020", [B["python"], B["ab"]])),
           ["B.A. Economics, Fictional College, 2019"],
           "SQL, Power BI, Excel, Python", "linkedin.com/in/jordansampleton"),
    Resume("03_riley_placeholder.pdf", "Riley Placeholder", "riley.placeholder@example.com", "(312) 555-0103",
           "303 Demo Road, Anytown, OH 43003",
           "Reporting analyst focused on finance and supply data.",
           _jobs(("Reporting Analyst", "Example Foods Distribution, 2020 - 2025", [B["sql"], B["dash"], B["excel"], B["present"], B["etl"]]),
                 ("Finance Assistant", "Sample Bank, 2018 - 2020", [B["python"]])),
           ["B.S. Finance, Example State University, 2018"],
           "SQL, Tableau, Excel, ETL"),
    # --- Business Data Analyst: borderline ---------------------------------------------------------
    Resume("04_morgan_testwell.pdf", "Morgan Testwell", "morgan.testwell@example.com", "(312) 555-0104",
           "404 Mock Lane, Anytown, OH 43004",
           "Finance associate moving into analytics.",
           _jobs(("Finance Associate", "Fictional Furniture, 2021 - 2025", [B["sql"], B["excel"], B["present"], B["dash_weak"]])),
           ["B.S. Accounting, Fictional College, 2021"],
           "SQL, Excel"),
    Resume("05_casey_mockford.pdf", "Casey Mockford", "casey.mockford@example.com", "(312) 555-0105",
           "505 Sample Court, Anytown, OH 43005",
           "Marketing coordinator who builds reports for campaign teams.",
           _jobs(("Marketing Coordinator", "Demo Brands, 2020 - 2025", [B["sql_weak"], B["dash"], B["excel"], B["present"]])),
           ["B.A. Communications, Example State University, 2020"],
           "Tableau, Power BI, Excel"),
    Resume("06_quinn_draftly.pdf", "Quinn Draftly", "quinn.draftly@example.com", "(312) 555-0106",
           "606 Example Way, Anytown, OH 43006",
           "Operations analyst with a strong reporting background.",
           _jobs(("Operations Analyst", "Sample Logistics, 2021 - 2025", [B["sql"], B["dash"], B["excel"], B["present_weak"]])),
           ["B.S. Information Systems, Fictional College, 2021"],
           "SQL, Tableau, Excel"),
    # --- Operations Coordinator: strong ------------------------------------------------------------
    Resume("07_taylor_fakename.pdf", "Taylor Fakename", "taylor.fakename@example.com", "(614) 555-0107",
           "707 Placeholder Drive, Anytown, OH 43007",
           "Operations coordinator who keeps warehouses, vendors, and crews on schedule.",
           _jobs(("Operations Coordinator", "Example Freight, 2020 - 2025", [B["sched"], B["vendor"], B["inventory"], B["sop"]]),
                 ("Logistics Assistant", "Mock Movers, 2018 - 2020", [B["logistics"], B["erp"], B["event"]])),
           ["B.S. Supply Chain Management, Example State University, 2018"],
           "Scheduling, vendor management, inventory, SAP, NetSuite"),
    Resume("08_dana_notreal.pdf", "Dana Notreal", "dana.notreal@example.com", "(614) 555-0108",
           "808 Sample Boulevard, Anytown, OH 43008",
           "Warehouse operations lead with a process improvement focus.",
           _jobs(("Warehouse Operations Lead", "Fictional Fulfillment, 2019 - 2025", [B["sched"], B["vendor"], B["inventory"], B["sop"], B["logistics"]])),
           ["A.A.S. Logistics, Example Community College, 2017"],
           "Scheduling, purchasing, inventory control"),
    Resume("09_skyler_imaginary.pdf", "Skyler Imaginary", "skyler.imaginary@example.com", "(614) 555-0109",
           "909 Demo Terrace, Anytown, OH 43009",
           "Office and facilities coordinator for a multi-site distributor.",
           _jobs(("Facilities Coordinator", "Sample Supply Co., 2020 - 2025", [B["sched"], B["vendor"], B["inventory"], B["sop"], B["erp"]]),
                 ("Office Assistant", "Example Clinic, 2018 - 2020", [B["excel_weak"]])),
           ["B.A. Business Administration, Fictional College, 2018"],
           "SAP, NetSuite, Excel"),
    # --- Operations Coordinator: borderline --------------------------------------------------------
    Resume("10_rowan_pretend.pdf", "Rowan Pretend", "rowan.pretend@example.com", "(614) 555-0110",
           "1010 Mock Street, Anytown, OH 43010",
           "Shift supervisor ready for a coordinator role.",
           _jobs(("Shift Supervisor", "Fictional Foods Warehouse, 2021 - 2025", [B["sched"], B["inventory"], B["sop"], B["vendor_weak"]])),
           ["High School Diploma, Example High School, 2016"],
           "Scheduling, inventory"),
    Resume("11_emerson_madeup.pdf", "Emerson Madeup", "emerson.madeup@example.com", "(614) 555-0111",
           "1111 Sample Place, Anytown, OH 43011",
           "Purchasing assistant with shipping experience.",
           _jobs(("Purchasing Assistant", "Demo Hardware, 2020 - 2025", [B["vendor"], B["logistics"], B["sched"], B["inventory"]])),
           ["B.S. Business, Example State University, 2020"],
           "Purchasing, shipping"),
    Resume("12_finley_hypothetical.pdf", "Finley Hypothetical", "finley.hypothetical@example.com", "(614) 555-0112",
           "1212 Example Circle, Anytown, OH 43012",
           "Event and office coordinator.",
           _jobs(("Office Coordinator", "Sample Events LLC, 2021 - 2025", [B["sched"], B["event"], B["vendor"], B["inventory_mid"]])),
           ["B.A. Hospitality, Fictional College, 2021"],
           "Event planning, scheduling"),
    # --- Weak for both roles ----------------------------------------------------------------------------
    Resume("13_harper_fictive.pdf", "Harper Fictive", "harper.fictive@example.com", "(614) 555-0113",
           "1313 Demo Avenue, Anytown, OH 43013",
           "Friendly barista who loves serving the morning rush.",
           _jobs(("Barista", "Example Coffee House, 2021 - 2025", [B["barista"], B["service"]])),
           ["High School Diploma, Example High School, 2020"],
           "Espresso, customer service"),
    Resume("14_kendall_inventa.pdf", "Kendall Inventa", "kendall.inventa@example.com", "(614) 555-0114",
           "1414 Sample Road, Anytown, OH 43014",
           "Graphic designer for small businesses.",
           _jobs(("Graphic Designer", "Mock Studio, 2019 - 2025", [B["design"], B["print"]])),
           ["B.F.A. Graphic Design, Fictional Art Institute, 2019"],
           "Adobe Illustrator, InDesign"),
    Resume("15_reese_nominal.pdf", "Reese Nominal", "reese.nominal@example.com", "(614) 555-0115",
           "1515 Example Lane, Anytown, OH 43015",
           "Math teacher exploring a career change.",
           _jobs(("Math Teacher", "Example Middle School, 2016 - 2025", [B["teach"], B["coach"]])),
           ["B.S. Mathematics Education, Example State University, 2016"],
           "Lesson planning, classroom management"),
    # --- Hybrid: borderline for both --------------------------------------------------------------------
    Resume("16_sage_exampleberg.pdf", "Sage Exampleberg", "sage.exampleberg@example.com", "(614) 555-0116",
           "1616 Placeholder Way, Anytown, OH 43016",
           "Store operations assistant with some reporting experience.",
           _jobs(("Store Operations Assistant", "Sample Mart, 2020 - 2025", [B["sql_weak"], B["excel"], B["present"], B["sched"], B["inventory"], B["vendor_weak"]])),
           ["A.A. Business, Example Community College, 2019"],
           "Excel, scheduling"),
    # --- Name-swap pair: identical except name and contact details ----------------------------------
    Resume("17_emily_sampleworth.pdf", "Emily Sampleworth", "emily.sampleworth@example.com", "(312) 555-0117",
           "1717 Example Street, Anytown, OH 43017",
           "Emily is a data analyst who builds reporting for retail teams.",
           _jobs(("Data Analyst", "Fictional Outfitters, 2021 - 2025", [B["sql"], B["dash"], B["excel"], B["present"], B["python"]])),
           ["B.S. Statistics, Example State University, 2021"],
           "SQL, Tableau, Excel, Python", "linkedin.com/in/emilysampleworth"),
    Resume("18_jamal_testerfield.pdf", "Jamal Testerfield", "jamal.testerfield@example.com", "(312) 555-0118",
           "1818 Sample Avenue, Anytown, OH 43018",
           "Jamal is a data analyst who builds reporting for retail teams.",
           _jobs(("Data Analyst", "Fictional Outfitters, 2021 - 2025", [B["sql"], B["dash"], B["excel"], B["present"], B["python"]])),
           ["B.S. Statistics, Example State University, 2021"],
           "SQL, Tableau, Excel, Python", "linkedin.com/in/jamaltesterfield"),
    # --- Parsing edge cases --------------------------------------------------------------------------------
    Resume("19_parker_partialton.pdf", "Parker Partialton", "parker.partialton@example.com", "(614) 555-0119",
           "1919 Demo Drive, Anytown, OH 43019",
           "Retail supervisor with reporting duties. Page 2 of this resume was scanned.",
           _jobs(("Retail Supervisor", "Example Outlet, 2020 - 2025", [B["sql_weak"], B["excel_weak"]])),
           [], "",
           scanned_page=["EXPERIENCE (continued)", "Assistant Manager, Sample Shoes, 2017 - 2020",
                         "- Built dashboards in Tableau for district managers.",
                         "EDUCATION", "B.S. Management, Example State University, 2017"]),
    Resume("20_logan_scannerly.pdf", "Logan Scannerly", "logan.scannerly@example.com", "(614) 555-0120",
           "2020 Sample Street, Anytown, OH 43020",
           "Inventory clerk. This whole resume is a scanned image.",
           _jobs(("Inventory Clerk", "Example Depot, 2019 - 2025", [B["inventory"], B["vendor_weak"]])),
           ["High School Diploma, Example High School, 2018"], "Inventory", image_only=True),
]


def resume_lines(resume: Resume) -> list[tuple[str, str]]:
    """(style, text) lines in reading order. Styles: name, contact, heading, job, text, bullet."""
    contact = " | ".join(part for part in (resume.email, resume.phone, resume.linkedin) if part)
    lines = [("name", resume.name), ("contact", contact), ("contact", resume.address), ("text", ""),
             ("heading", "SUMMARY"), ("text", resume.summary), ("text", ""), ("heading", "EXPERIENCE")]
    for title, where, bullets in resume.jobs:
        lines.append(("job", f"{title}, {where}"))
        lines.extend(("bullet", f"- {bullet}") for bullet in bullets)
    if resume.education:
        lines += [("text", ""), ("heading", "EDUCATION")] + [("text", item) for item in resume.education]
    if resume.skills:
        lines += [("text", ""), ("heading", "SKILLS"), ("text", resume.skills)]
    return lines


FONTS = {
    "name": ("Helvetica-Bold", 18),
    "contact": ("Helvetica", 10),
    "heading": ("Helvetica-Bold", 11),
    "job": ("Helvetica-Bold", 10),
    "text": ("Helvetica", 10),
    "bullet": ("Helvetica", 10),
}


def _picture_of_text(lines: list[str]) -> ImageReader:
    image = Image.new("RGB", (1000, 40 + 34 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((30, 20 + index * 34), line, fill="black", font_size=24)
    return ImageReader(image)


def write_pdf(resume: Resume, path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    pdf.setTitle("Resume (fictional sample)")
    width, height = letter
    margin = 54
    if resume.image_only:
        lines = [text for _, text in resume_lines(resume)]
        pdf.drawImage(_picture_of_text(lines), margin, height - margin - 600, width=width - 2 * margin, height=600, preserveAspectRatio=True)
        pdf.showPage()
        pdf.save()
        return

    y = height - margin
    for style, text in resume_lines(resume):
        font, size = FONTS[style]
        pdf.setFont(font, size)
        for chunk in simpleSplit(text, font, size, width - 2 * margin) or [""]:
            pdf.drawString(margin, y, chunk)
            y -= size + 6
    pdf.showPage()
    if resume.scanned_page:
        pdf.drawImage(_picture_of_text(resume.scanned_page), margin, height - margin - 300, width=width - 2 * margin, height=300, preserveAspectRatio=True)
        pdf.showPage()
    pdf.save()


def main() -> int:
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    ROLE_DIR.mkdir(parents=True, exist_ok=True)
    for stem, profile in ROLES.items():
        (ROLE_DIR / f"{stem}.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    for resume in RESUMES:
        write_pdf(resume, RESUME_DIR / resume.filename)
    print(f"Wrote {len(RESUMES)} resumes to {RESUME_DIR.relative_to(ROOT)} and {len(ROLES)} role profiles to {ROLE_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
