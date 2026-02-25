import pytest
from core.scraper import Scraper


def test_extract_version_from_title():
    scraper = Scraper()

    # Bracketed version
    assert (
        scraper._extract_version_from_title("Game Title [v1.0.5] [Developer]")
        == "1.0.5"
    )
    assert scraper._extract_version_from_title("Game Title [1.0.2] [PC/Mac]") == "1.0.2"
    assert scraper._extract_version_from_title("Game Title [v2.0 Beta]") == "2.0 Beta"
    assert (
        scraper._extract_version_from_title("Game Title [v3.1a Hotfix]")
        == "3.1a Hotfix"
    )
    assert scraper._extract_version_from_title("Another Game [Version 4.5]") == "4.5"
    assert (
        scraper._extract_version_from_title("Missing Brackets Game v1.5 [Windows]")
        == "1.5"
    )
    assert (
        scraper._extract_version_from_title("Game Title [v0.1.2.3.4] (No Spaces)")
        == "0.1.2.3.4"
    )

    # Chapter/Episode formats
    assert scraper._extract_version_from_title("Game Title [Ch.3] [RenPy]") == "Ch.3"
    assert scraper._extract_version_from_title("Game Title [Ep.5] [HTML]") == "Ep.5"
    assert (
        scraper._extract_version_from_title("Game Title [S2 E3] [Windows]") == "S2 E3"
    )
    assert (
        scraper._extract_version_from_title("Game Title [Part 3] [MacOS]") == "Part 3"
    )
    assert (
        scraper._extract_version_from_title("Visual Novel [Chapter 4 v1.2]")
        == "Chapter 4 v1.2"
    )

    # Missing version completely
    assert (
        scraper._extract_version_from_title("Game Title [RenPy] [Windows]") == "Unknown"
    )
    assert scraper._extract_version_from_title("Just the Title") == "Unknown"
