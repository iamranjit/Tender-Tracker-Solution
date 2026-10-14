"""Format checks for Indian business identifiers (PAN, GSTIN, Udyam, CIN/LLPIN, DPIIT).

These only check *format* (and the GSTIN check digit); they don't verify that a
number is actually registered.
"""

from __future__ import annotations

import re

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
UDYAM_RE = re.compile(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$")
CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")
LLPIN_RE = re.compile(r"^[A-Z]{3}-[0-9]{4}$")
DPIIT_RE = re.compile(r"^DIPP[0-9]{3,8}$")

ENTITY_TYPES = [
    "Private Limited Company",
    "Public Limited Company",
    "One Person Company",
    "Limited Liability Partnership",
    "Partnership Firm",
    "Sole Proprietorship",
    "Other",
]
COMPANY_TYPES = {"Private Limited Company", "Public Limited Company", "One Person Company"}
MSME_CATEGORIES = ["micro", "small", "medium"]

# 4th character of a PAN identifies the holder type.
PAN_HOLDER = {"C": "Company", "F": "Firm / LLP", "P": "Individual", "H": "HUF", "A": "AOP", "T": "Trust"}

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}  # fmt: skip

_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_digit(first14: str) -> str:
    """GSTIN check character (mod-36 Luhn variant used by GSTN)."""
    total = 0
    for i, ch in enumerate(first14):
        product = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def gstin_problem(gstin: str) -> str | None:
    if not GSTIN_RE.match(gstin):
        return "GSTIN must be 15 characters, e.g. 29ABCDE1234F1Z5"
    if gstin[:2] not in GST_STATE_CODES:
        return f"GSTIN starts with an unknown state code '{gstin[:2]}'"
    if gstin_check_digit(gstin[:14]) != gstin[14]:
        return "GSTIN check digit doesn't match - please re-check the number"
    return None


def pan_problem(pan: str) -> str | None:
    return None if PAN_RE.match(pan) else "PAN must look like ABCDE1234F (5 letters, 4 digits, 1 letter)"


def udyam_problem(udyam: str) -> str | None:
    return None if UDYAM_RE.match(udyam) else "Udyam number must look like UDYAM-KR-03-0012345"


def registration_problem(number: str, entity_type: str) -> str | None:
    if entity_type in COMPANY_TYPES and not CIN_RE.match(number):
        return "CIN must be 21 characters, e.g. U72900KA2023PTC123456"
    if entity_type == "Limited Liability Partnership" and not LLPIN_RE.match(number):
        return "LLPIN must look like AAB-1234"
    return None


def dpiit_problem(number: str) -> str | None:
    return None if DPIIT_RE.match(number) else "DPIIT recognition number usually looks like DIPP12345"


def identifier_problems(company: dict) -> list[str]:
    """Hard format errors in the company identifiers (empty values are allowed).

    The DPIIT number format isn't formally specified, so it is only a UI hint, not an error here.
    """
    checks = [
        ("pan", pan_problem),
        ("gstin", gstin_problem),
        ("udyam_number", udyam_problem),
    ]
    problems = [msg for key, check in checks if (value := company.get(key)) and (msg := check(value))]
    reg = company.get("registration_number")
    if reg and (msg := registration_problem(reg, company.get("entity_type", ""))):
        problems.append(msg)
    if company.get("msme_category") and company["msme_category"] not in MSME_CATEGORIES:
        problems.append(f"MSME category must be one of {MSME_CATEGORIES}")
    if company.get("entity_type") and company["entity_type"] not in ENTITY_TYPES:
        problems.append(f"Entity type must be one of {ENTITY_TYPES}")
    return problems
