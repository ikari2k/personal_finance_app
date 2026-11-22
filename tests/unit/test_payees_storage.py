"""Tests for app.storage.payees read/write round-tripping."""

from app.models.payee import PayeeAlias, PayeeBook
from app.storage.payees import read_payee_book, write_payee_book


def test_read_returns_an_empty_book_when_the_file_is_missing(tmp_path):
    assert read_payee_book(tmp_path / "payee_aliases.toml") == PayeeBook()


def test_write_then_read_round_trips_aliases_and_skipped(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    book = PayeeBook(
        aliases=[
            PayeeAlias(spelling="ZABKA Z8540 K.2", payee="Żabka"),
            PayeeAlias(spelling="Allegro", payee="Allegro"),
        ],
        skipped=["faktura numer"],
    )

    write_payee_book(book, path)

    assert read_payee_book(path) == book


def test_writing_over_an_existing_book_leaves_a_backup(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    write_payee_book(PayeeBook(skipped=["a"]), path)

    write_payee_book(PayeeBook(skipped=["b"]), path)

    assert (tmp_path / "payee_aliases.toml.bak").exists()


def test_read_skips_rows_missing_a_spelling_or_payee(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    path.write_text(
        '[[aliases]]\nspelling = "A"\npayee = ""\n\n'
        '[[aliases]]\nspelling = "B"\npayee = "Bee"\n'
    )

    assert read_payee_book(path).aliases == [PayeeAlias(spelling="B", payee="Bee")]
