from __future__ import annotations

import re
from pathlib import Path

import pytest

from variance_copilot.config import ConfigError
from variance_copilot.generator.master import (
    Brand,
    CostCenter,
    GlAccount,
    MarketExclusivity,
    MasterData,
    read_master_data,
)

SPEC = Path("SPEC.md").read_text(encoding="utf-8")
SEEDS = Path("seeds")

THERAPEUTIC_AREAS = {
    "Cardiovascular": "CV", "Immunology": "IMM", "Oncology": "ONC",
    "Neuroscience": "NS", "Respiratory": "RES",
}  # fmt: skip
LIFECYCLE = {"Mature": "MATURE", "Growth": "GROWTH", "Decline": "DECLINE", "US": "LAUNCH"}
REAL_COMPANIES = (
    "Novartis", "Roche", "Pfizer", "Merck", "Sanofi", "Bayer", "AstraZeneca", "GSK", "Lilly",
    "AbbVie", "Amgen", "Takeda", "Boehringer", "Grifols", "Almirall", "McKesson", "Cardinal",
    "Cencora", "AmerisourceBergen", "Walgreens", "CVS", "Phoenix", "Alliance", "IQVIA",
    "Parexel", "Syneos", "ICON plc", "Medpace", "Accenture", "Deloitte", "PwC", "KPMG",
    "Lonza", "Catalent", "DHL", "Kuehne",
)  # fmt: skip


def spec_tables(section: str) -> list[dict[str, str]]:
    """Rows of every markdown table in a SPEC section, keyed by header."""
    body = SPEC.split(f"### {section}", 1)[1].split("\n### ", 1)[0]
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in body.splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if header is None:
            header = cells
        elif not set(line) <= set("|- "):
            rows.append(dict(zip(header, cells, strict=True)))
    return rows


@pytest.fixture(scope="module")
def master() -> MasterData:
    return read_master_data(SEEDS / "master")


def test_every_spec_account_is_seeded_with_its_attributes(master: MasterData) -> None:
    accounts: dict[str, GlAccount] = {a.gl_account: a for a in master["gl_account"]}

    spec_rows = spec_tables("7.5 Chart of accounts AUR1")
    assert len(spec_rows) == len(accounts)
    for row in spec_rows:
        account = accounts[row["Account"]]
        assert account.name == row["Name"]
        assert account.account_type == row.get("Type", "X")
        assert account.pnl_category == row.get("Category")
        assert account.normal_balance == row.get("Normal", account.normal_balance)
        if row.get("Functional area"):
            assert account.default_functional_area == row["Functional area"]
        notes = row.get("Notes", "")
        core = notes.split("`")[1] if "Core adjustment `" in notes else row.get("Core adjustment")
        assert account.core_adjustment == (core or None)
        if "Intercompany" in notes:
            assert account.is_intercompany


def test_revenue_and_cogs_accounts_default_to_their_functional_area(
    master: MasterData,
) -> None:
    for account in master["gl_account"]:
        if account.pnl_category in ("GROSS_SALES", "GTN_DEDUCTION", "OTHER_REVENUE"):
            assert account.default_functional_area == "F010"
        elif account.pnl_category == "COGS":
            assert account.default_functional_area == "F100"
        elif account.pnl_category in ("OPEX", "ALLOCATION"):
            assert account.default_functional_area is None


def test_every_spec_cost_center_is_seeded(master: MasterData) -> None:
    cost_centers: dict[str, CostCenter] = {c.cost_center: c for c in master["cost_center"]}

    for row in spec_tables("7.6 Profit centers and cost centers"):
        cost_center = cost_centers[row["Cost center"]]
        assert cost_center.company_code == row["Company"]
        assert cost_center.category == row["Category"]
        assert cost_center.functional_area == row["Functional area"]
        assert cost_center.profit_center == row["Profit center"]
    assert 40 <= len(cost_centers) <= 50


def test_cost_center_names_follow_the_naming_rule(master: MasterData) -> None:
    profit_centers = {p.profit_center for p in master["profit_center"]}
    for cost_center in master["cost_center"]:
        assert re.fullmatch(r"[A-Z]{2}\d{2}-[A-Z]+-[A-Z]+", cost_center.cost_center)
        assert cost_center.profit_center in profit_centers
        assert cost_center.profit_center.startswith(cost_center.company_code)


def test_every_spec_brand_is_seeded(master: MasterData) -> None:
    brands: dict[str, Brand] = {b.brand: b for b in master["brand"]}
    loe: list[MarketExclusivity] = master["market_exclusivity"]

    spec_rows = spec_tables("7.7 Products, customers, vendors, projects")
    assert len(spec_rows) == len(brands)
    for row in spec_rows:
        brand = brands[row["Brand"]]
        assert brand.therapeutic_area == THERAPEUTIC_AREAS[row["Therapeutic area"]]
        assert brand.modality == row["Modality"].upper().replace(" ", "_")
        lifecycle = next(value for key, value in row.items() if key.startswith("Lifecycle"))
        assert brand.lifecycle_stage == LIFECYCLE[lifecycle.split()[0].strip(";,")]
        dates = {(e.country_code, e.loe_date.isoformat()) for e in loe if e.brand == brand.brand}
        for part in row["Loss of exclusivity"].split(";"):
            found = re.search(r"(US|EU)? ?(\d{4}(-\d{2}-\d{2})?)", part)
            assert found
            market, when = found.group(1), found.group(2)
            countries = {"US": {"US"}, "EU": {"DE", "ES"}, None: {"US", "DE", "ES"}}[market]
            assert {c for c, d in dates if d.startswith(when)} >= countries


def test_materials_follow_the_naming_rule(master: MasterData) -> None:
    per_brand: dict[str, int] = {}
    for material in master["material"]:
        assert re.fullmatch(r"[A-Z]{3}-\d+(MG|MCG)-\d+[A-Z]*", material.material)
        assert material.material.split("-")[1] == material.strength
        per_brand[material.brand] = per_brand.get(material.brand, 0) + 1
    assert set(per_brand.values()) <= {2, 3}


def test_named_scenario_vendors_exist(master: MasterData) -> None:
    vendors = {v.name: v.category for v in master["vendor"]}

    assert vendors["Kairos Bio AG"] == "LICENSOR"
    assert vendors["Lumen Health Communications S.L."] == "MARKETING_AGENCY"
    assert vendors["Meridia Clinical Research"] == "CRO"


def test_customers_per_affiliate_and_intercompany(master: MasterData) -> None:
    third_party = [c for c in master["customer"] if c.channel != "INTERCOMPANY"]
    for country in ("US", "DE", "ES"):
        assert 3 <= sum(c.country_code == country for c in third_party) <= 8
    for customer in master["customer"]:
        assert (customer.channel == "INTERCOMPANY") == (customer.trading_partner is not None)


def test_no_real_company_names() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in SEEDS.rglob("*.yaml"))
    found = [name for name in REAL_COMPANIES if re.search(rf"\b{re.escape(name)}\b", text)]

    assert found == []


def test_invalid_row_reports_file_table_and_index(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("brand:\n  - [X, CV, PILL, MATURE]\n")

    with pytest.raises(ConfigError, match=r"(?s)bad\.yaml: brand\[0\].*modality"):
        read_master_data(tmp_path)


def test_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("segment:\n  - [IM, One]\n")
    (tmp_path / "b.yaml").write_text("segment:\n  - [IM, Two]\n")

    with pytest.raises(ConfigError, match="duplicate segment keys"):
        read_master_data(tmp_path)
