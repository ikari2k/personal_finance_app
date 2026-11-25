"""Tests for app.storage.payees read/write round-tripping."""

from app.models.payee import DESCRIPTION_FIELD, PayeeBook, PayeeRule
from app.storage.payees import read_payee_book, write_payee_book


def test_read_returns_an_empty_book_when_the_file_is_missing(tmp_path):
    assert read_payee_book(tmp_path / "payee_aliases.toml") == PayeeBook()


def test_write_then_read_round_trips_rules_with_several_fields_and_skipped(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    book = PayeeBook(
        rules=[
            PayeeRule(fields={DESCRIPTION_FIELD: "ZABKA Z8540 K.2"}, payee="Żabka"),
            PayeeRule(
                fields={DESCRIPTION_FIELD: "PayU", "Web address": "cinema-city.pl"},
                payee="Cinema City",
            ),
        ],
        skipped=["faktura numer"],
    )

    write_payee_book(book, path)

    assert read_payee_book(path) == book  # including the order, which decides ties


def test_writing_over_an_existing_book_leaves_a_backup(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    write_payee_book(PayeeBook(skipped=["a"]), path)

    write_payee_book(PayeeBook(skipped=["b"]), path)

    assert (tmp_path / "payee_aliases.toml.bak").exists()


def test_read_skips_rules_missing_a_payee_or_any_field_value(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    path.write_text(
        '[[rules]]\npayee = ""\n[rules.fields]\nDescription = "A"\n\n'
        '[[rules]]\npayee = "No fields"\n[rules.fields]\nDescription = " "\n\n'
        '[[rules]]\npayee = "Bee"\n[rules.fields]\nDescription = "B"\n'
    )

    assert read_payee_book(path).rules == [
        PayeeRule(fields={DESCRIPTION_FIELD: "B"}, payee="Bee")
    ]


def test_legacy_aliases_are_read_as_description_rules_and_rewritten_as_rules(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    path.write_text(
        'skipped = ["payu"]\n\n'
        '[[aliases]]\nspelling = "CZAJCZYK S.C."\npayee = "Piekarnia Czajczyk"\n\n'
        '[[aliases]]\nspelling = "Allegro"\npayee = "Allegro"\n'
    )

    book = read_payee_book(path)

    assert book.skipped == ["payu"]
    assert book.rules == [
        PayeeRule(
            fields={DESCRIPTION_FIELD: "CZAJCZYK S.C."}, payee="Piekarnia Czajczyk"
        ),
        PayeeRule(fields={DESCRIPTION_FIELD: "Allegro"}, payee="Allegro"),
    ]
    write_payee_book(book, path)
    assert "[[aliases]]" not in path.read_text()
    assert read_payee_book(path) == book  # nothing lost by the migration


def test_legacy_aliases_come_before_new_rules_so_new_rules_win_ties(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    path.write_text(
        '[[aliases]]\nspelling = "A"\npayee = "Old"\n\n'
        '[[rules]]\npayee = "New"\n[rules.fields]\nDescription = "A"\n'
    )

    assert [r.payee for r in read_payee_book(path).rules] == ["Old", "New"]


def test_split_groups_round_trip_and_default_to_none(tmp_path):
    path = tmp_path / "payee_aliases.toml"
    write_payee_book(PayeeBook(split=["zabka", "lidl"]), path)

    assert read_payee_book(path).split == ["zabka", "lidl"]
    (tmp_path / "old.toml").write_text('skipped = ["a"]\n')
    assert read_payee_book(tmp_path / "old.toml").split == []
