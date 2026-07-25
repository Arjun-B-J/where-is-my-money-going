"""Default rows created on first run: categories, tagging rules, payee notes.

Two rules govern what may live here.

**Nothing personal.** The seed knows nobody: it creates no people and no rule
names an individual. People are discovered from the data instead, by
`app.services.friend_detector`.

**Nothing that only matches one person's statements.** Rules here are either
well-known national merchants or structural patterns that hold for any Indian
bank statement (`ATM-WDL`, `BBPS`, EMI amortisation lines). Anything narrower
belongs in a user's own rule list.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.llm.prompts import CATEGORIES
from app.models import Category, MerchantNote, Rule, TxnDirection

# Presentation only — the category names themselves come from
# app.llm.prompts.CATEGORIES so the model and the database cannot disagree.
_CATEGORY_STYLE: dict[str, tuple[str, str, bool]] = {
    # name: (icon, colour, is_essential)
    "food": ("🍽", "#FB923C", False),
    "groceries": ("🛒", "#84CC16", True),
    "transport": ("🚗", "#06B6D4", False),
    "rent": ("🏠", "#A855F7", True),
    "utilities": ("💡", "#F59E0B", True),
    "entertainment": ("🎬", "#EC4899", False),
    "shopping": ("🛍", "#F97316", False),
    "health": ("🩺", "#EF4444", True),
    "subscriptions": ("🔁", "#8B5CF6", False),
    "salary": ("💼", "#10B981", False),
    "investments": ("📈", "#0EA5E9", False),
    "loan_given": ("💸", "#6366F1", False),
    "loan_taken": ("💰", "#14B8A6", False),
    "loan_repayment": ("↩️", "#0891B2", False),
    "cash": ("💵", "#94A3B8", False),
    "uncategorized": ("❓", "#94A3B8", False),
}


# Lower priority number wins. Structural patterns are checked before merchant
# names because they are more specific about what the transaction *is*.
DEFAULT_RULES: list[dict] = [
    # ---- Structural: true of any Indian bank or card statement ----
    {"name": "Salary credit", "priority": 10,
     "pattern": r"(?i)\b(?:SALARY|SAL\s+CREDIT|NEFT-CR-.*SAL)\b",
     "direction": TxnDirection.CREDIT, "category": "salary"},
    {"name": "ATM withdrawal", "priority": 10,
     "pattern": r"(?i)ATM[-\s]?WDL|ATM\s+CASH|NWD-", "category": "cash"},
    {"name": "Credit-card bill payment", "priority": 12,
     "pattern": r"(?i)CREDIT\s*CARD.*(?:AUTO\s*PAY|PAYMENT)|CC\s+PAYMENT|CARD\s+AUTO\s*PAY",
     "category": "loan_repayment", "subcategory": "credit_card"},
    {"name": "EMI amortisation", "priority": 12,
     "pattern": r"(?i)(?:Principal|Interest)\s+Amount\s+Amortization|\bEMI\b",
     "category": "loan_repayment", "subcategory": "emi"},
    {"name": "Electricity via BBPS", "priority": 20,
     "pattern": r"(?i)BBPS.*(?:ELECTRIC|POWER|ENERGY)|\bBESCOM\b|ELECTRICITY\s+BOARD",
     "category": "utilities", "subcategory": "electricity"},
    {"name": "Broadband / fibre", "priority": 20,
     "pattern": r"(?i)BROADBAND|FIBERNET|FIBRE|\bISP\b",
     "category": "utilities", "subcategory": "internet"},
    {"name": "Mobile recharge", "priority": 20,
     "pattern": r"(?i)RECHARGE|PREPAID\s+MOBILE|POSTPAID\s+BILL",
     "category": "utilities", "subcategory": "mobile"},
    {"name": "Systematic investment plan", "priority": 20,
     "pattern": r"(?i)\bSIP\b|MUTUAL\s+FUND|INDEX\s+FUND|\bNIFTY\b|BROKING",
     "category": "investments"},
    {"name": "Metro / rail", "priority": 30,
     "pattern": r"(?i)METRO\s*RAIL|\bMETRO\b|\bIRCTC\b|\bBMRCL\b",
     "category": "transport", "subcategory": "metro"},
    {"name": "Fuel", "priority": 30,
     "pattern": r"(?i)FUEL\s+STATION|PETROL|\bHPCL\b|\bBPCL\b|INDIAN\s+OIL",
     "category": "transport", "subcategory": "fuel"},
    {"name": "Cafeteria", "priority": 30,
     "pattern": r"(?i)CAFETERIA|CANTEEN", "category": "food", "subcategory": "cafeteria"},
    {"name": "Pharmacy", "priority": 30,
     "pattern": r"(?i)PHARMAC|MEDIC(?:AL|INE)|CHEMIST",
     "category": "health", "subcategory": "pharmacy"},

    # ---- Well-known national merchants ----
    {"name": "Food delivery", "priority": 50,
     "pattern": r"(?i)\b(?:SWIGGY|ZOMATO|BUNDL\s+TECH|EATCLUB)\b",
     "category": "food", "subcategory": "delivery"},
    {"name": "Quick commerce", "priority": 50,
     "pattern": r"(?i)\b(?:BLINKIT|ZEPTO|INSTAMART|BIGBASKET|DUNZO)\b",
     "category": "groceries", "subcategory": "quick_commerce"},
    {"name": "Ride hailing", "priority": 50,
     "pattern": r"(?i)\b(?:UBER|OLA\s+CABS|RAPIDO|BLUSMART)\b",
     "category": "transport", "subcategory": "cab"},
    {"name": "Marketplaces", "priority": 60,
     "pattern": r"(?i)\b(?:AMAZON|FLIPKART|MYNTRA|AJIO|NYKAA|MEESHO)\b",
     "category": "shopping"},
    {"name": "Streaming and music", "priority": 40,
     "pattern": r"(?i)\b(?:NETFLIX|SPOTIFY|HOTSTAR|PRIME\s+VIDEO|YOUTUBE\s*PREMIUM|APPLE\.COM/BILL)\b",
     "category": "subscriptions", "subcategory": "streaming"},
    {"name": "Fitness", "priority": 40,
     "pattern": r"(?i)CULT\.?FIT|FITNESS\s+CLUB|\bGYM\b",
     "category": "subscriptions", "subcategory": "fitness"},
    {"name": "Ticketing", "priority": 60,
     "pattern": r"(?i)BOOKMYSHOW|\bPVR\b|\bINOX\b|CINEMA",
     "category": "entertainment"},
    {"name": "Coffee shops", "priority": 60,
     "pattern": r"(?i)STARBUCKS|COFFEE\s+(?:DAY|ROASTER)|THIRD\s+WAVE|BLUE\s+TOKAI",
     "category": "food", "subcategory": "coffee"},
    {"name": "Bill-payment aggregators", "priority": 80,
     "pattern": r"(?i)CRED\.?CLUB|\bCREDCLUB\b",
     "category": "loan_repayment", "subcategory": "aggregator"},
]


# Seeded notes for the report's "why notable" column. Structural only: a note
# about a specific payee is something each user adds for themselves, via
# /merchant-notes.
DEFAULT_MERCHANT_NOTES: list[dict] = [
    {"pattern": "SALARY", "note": "Salary credit — income, not spend", "priority": 10},
    {"pattern": "EMPLOYER", "note": "Salary credit — income, not spend", "priority": 10},
    {"pattern": "ATM", "note": "Cash withdrawal — where it went is untracked", "priority": 20},
    {"pattern": "CREDIT CARD PAYMENT", "note": "Card bill settlement, not new spend", "priority": 20},
    {"pattern": "CRED", "note": "Card bill paid through an aggregator", "priority": 30},
    {"pattern": "EMI", "note": "Instalment on a converted purchase", "priority": 30},
    {"pattern": "RENT", "note": "Recurring monthly housing cost", "priority": 30},
]


def seed_categories(db: Session) -> int:
    inserted = 0
    for name, _description in CATEGORIES:
        if db.query(Category).filter_by(name=name).first():
            continue
        icon, color, essential = _CATEGORY_STYLE.get(name, ("•", "#94A3B8", False))
        db.add(Category(name=name, icon=icon, color=color, is_essential=essential))
        inserted += 1
    db.commit()
    return inserted


def seed_rules(db: Session) -> int:
    inserted = 0
    for rule in DEFAULT_RULES:
        if db.query(Rule).filter_by(name=rule["name"]).first():
            continue
        db.add(Rule(**rule))
        inserted += 1
    db.commit()
    return inserted


def seed_merchant_notes(db: Session) -> int:
    inserted = 0
    for note in DEFAULT_MERCHANT_NOTES:
        if db.query(MerchantNote).filter_by(pattern=note["pattern"]).first():
            continue
        db.add(MerchantNote(**note))
        inserted += 1
    db.commit()
    return inserted


def seed_all(db: Session) -> dict[str, int]:
    """Idempotent. Safe to call on every startup and every pipeline run."""
    return {
        "categories": seed_categories(db),
        "rules": seed_rules(db),
        "merchant_notes": seed_merchant_notes(db),
    }
