"""Every homonym row in BUILD_SPEC §4.4, plus the rule-layer contract."""

import pytest

from pipeline.config import load_entities
from pipeline.entities import has_hard_event, infer_region, match_text

ENT = load_entities()


def gps(text):
    return set(match_text(text, ENT).gp_ids)


def sectors(text):
    return set(match_text(text, ENT).sector_ids)


# --------------------------------------------------------------- homonyms

@pytest.mark.parametrize("text,forbidden", [
    ("Apollo 11 anniversary exhibition opens at the space museum", "apollo"),
    ("NASA marks the Apollo programme anniversary", "apollo"),
    ("Apollo Hospitals reports higher patient volumes", "apollo"),
    ("Apollo Tyres opens a new plant", "apollo"),
    ("A concert at the Apollo Theater", "apollo"),
    ("Guggenheim Museum unveils new wing in Bilbao", "guggenheim"),
    ("Winner of a Guggenheim Fellowship announced", "guggenheim"),
    ("Penske Automotive Group, which trades as PAG, posts record quarter", "pag"),
    ("Bain & Company report says consulting demand rebounds", "bain-capital"),
    ("Basepoint Business Centres opens new workspace in Reading", "basepoint"),
    ("HSBC reports group pre-tax profit and a wider net interest margin", "hsbc-am"),
    ("Johns Hopkins Bayview Medical Center expands its emergency department", "bayview"),
    ("The property sits on Bayview Avenue near the park", "bayview"),
])
def test_homonyms_are_not_matched(text, forbidden):
    assert forbidden not in gps(text)


def test_bare_nb_never_matches():
    """BUILD_SPEC §4.4: never match the bare letters NB."""
    assert "neuberger-berman" not in gps("Nurse NB confirmed the patient was stable")
    assert "neuberger-berman" not in gps("NB: the meeting starts at nine")
    assert "neuberger-berman" in gps("Neuberger Berman closes its latest private debt fund")


def test_otf_needs_bdc_context():
    """OTF is a weak alias: the ticker alone is not enough."""
    assert "otf" not in gps("The team completed one-third of the work using an OTF process")
    assert "otf" in gps("OTF, the Blue Owl BDC, reported a lower NAV")
    assert "otf" in gps("Blue Owl Technology Finance reported second quarter results")


def test_cifc_requires_credit_context():
    assert "cifc" in gps("CIFC prices a $400m CLO")
    assert "cifc" not in gps("CIFC is listed in the directory")


def test_acronym_matching_is_case_sensitive():
    """PAG, NB, OTF, CIFC, ABL, CLO, MSR, ABS, BDC match case-sensitively."""
    assert "pag" in gps("PAG Credit closes an Asia private credit fund in Hong Kong")
    assert "pag" not in gps("The pag file format is used for pagination")


def test_word_boundaries():
    assert "kkr" not in gps("The KKRX index rose")
    assert "kkr" in gps("KKR Credit led the unitranche financing")


# ------------------------------------------------------------ correct hits

@pytest.mark.parametrize("text,expected", [
    ("Blue Owl Technology Finance reports Q2 NAV of $17.20", {"blue-owl", "otf"}),
    ("Bayview Asset Management acquires a $10bn mortgage servicing portfolio", {"bayview"}),
    ("Bain Capital launches JB Aircraft Finance", {"bain-capital"}),
    ("FS KKR Capital Corp declares a quarterly dividend", {"kkr"}),
    ("Atlas SP prices a $600m asset-backed securitisation", {"apollo"}),
    ("CIFC Asset Management resets a 2021 CLO", {"cifc"}),
    ("Pretium adds to its single-family rental portfolio with new financing", {"pretium"}),
    ("HSBC Asset Management launches a private credit strategy", {"hsbc-am"}),
])
def test_tracked_gps_are_matched(text, expected):
    assert expected <= gps(text)


# ---------------------------------------------------------------- sectors

def test_sectors_requiring_credit_context():
    """"software" alone is not a credit story (BUILD_SPEC §4.5)."""
    assert "software" not in sectors("A software company launches a new product suite")
    assert "software" in sectors("Lenders reassess recurring-revenue loans to software borrowers")


def test_sector_keywords():
    assert "clo" in sectors("CLO AAA spreads tighten to 120bp as issuance sets a record")
    assert "mortgage" in sectors("MSR portfolio trades as delinquency rates rise")
    assert "private-credit" in sectors("European direct lending deal count falls in Q3")
    assert "aircraft-leasing" in sectors("An aviation ABS prices at par")
    assert "gp-stakes" in sectors("A GP stakes fund buys a minority stake in the manager")


def test_real_estate_requires_credit_context():
    assert "real-estate" not in sectors("Commercial real estate transaction volumes rose in August")
    assert "real-estate" in sectors("Commercial real estate loan maturities drive CMBS refinancing")


# ---------------------------------------------------------------- regions

def test_region_inference():
    assert infer_region("European direct lending deal count falls in Q3", ENT) == "Europe"
    assert infer_region("PAG closes an Asia private credit fund in Hong Kong", ENT) == "Asia"
    assert infer_region("The Federal Reserve cut rates, Wall Street rallied", ENT) == "US"
    assert infer_region("A generic sentence about nothing", ENT) == "Global"


# ----------------------------------------------------------- hard events

def test_hard_event_terms():
    assert has_hard_event("Borrower misses interest payment, restructuring likely", ENT)
    assert has_hard_event("Non-accruals rise at the BDC", ENT)
    assert not has_hard_event("Manager hires a new head of marketing", ENT)


# ------------------------------------------------------------ candidacy

def test_candidacy_contract():
    """The rule layer only decides 'worth a model's attention'.

    A PE buyout by KKR IS a candidate — it names a tracked GP. Excluding it is
    Agent 2's job, and doing it here would hide the decision from the audit log.
    """
    m = match_text("KKR agrees to buy a theme-park operator for $4bn", ENT)
    assert m.is_candidate and "kkr" in m.gp_ids
    assert not match_text("Local bakery wins an award", ENT).is_candidate


def test_veto_is_recorded():
    m = match_text("Bain & Company report says consulting demand rebounds", ENT)
    assert "bain-capital" in m.vetoed_gp_ids
    assert "bain-capital" not in m.gp_ids


def test_every_gp_matches_its_own_name():
    for gp in ENT.gps:
        probe = f"{gp.name} announced a new private credit fund and a credit facility"
        assert gp.id in gps(probe), f"{gp.id} does not match its own canonical name"


def test_every_sector_has_primary_gps_that_exist():
    ids = {g.id for g in ENT.gps}
    for s in ENT.sectors:
        assert set(s.primary_gps) <= ids, f"{s.id} references unknown GPs"


def test_every_gp_references_known_sectors():
    ids = {s.id for s in ENT.sectors}
    for g in ENT.gps:
        assert set(g.sectors) <= ids, f"{g.id} references unknown sectors"


def test_every_gp_has_a_credit_scope():
    for g in ENT.gps:
        assert "IN SCOPE" in g.credit_scope, f"{g.id} has no credit_scope"
