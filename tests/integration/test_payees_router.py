"""Integration tests for the payee management view."""

import json
from datetime import date
from decimal import Decimal

from app import config
from app.models.transaction import Transaction, TransactionType
from app.storage.ledger import read_ledger, write_ledger
from app.storage.payees import read_payee_book


def _txn(id_, description, *, payee="", amount="-10", **overrides) -> Transaction:
    fields = {
        "id": id_,
        "date": date(2026, 3, 5),
        "account_id": "chk",
        "category": "Food",
        "subcategory": "",
        "description": description,
        "amount": Decimal(amount),
        "type": TransactionType.EXPENSE,
        "payee": payee,
    }
    fields.update(overrides)
    return Transaction(**fields)


def _seed(*txns):
    write_ledger(list(txns), config.LEDGER_PATH)


def _toast(response) -> str:
    """The toast message carried in the HX-Trigger header (JSON, so decode it)."""
    return json.loads(response.headers["HX-Trigger"])["toast"]


def _payees_by_id():
    return {t.id: t.payee for t in read_ledger(config.LEDGER_PATH)}


def test_page_renders_empty(client):
    response = client.get("/payees")

    assert response.status_code == 200
    assert "Payees" in response.text
    assert "No payees yet" in response.text
    assert "0%" in response.text


def test_page_lists_groups_to_name_with_suggestion_and_counts(client):
    _seed(
        _txn("1", "ZABKA Z8540 K.2"),
        _txn("2", "ZABKA Z8540 K.1"),
        _txn("3", "LIDL WEGLARSKA"),
    )

    text = client.get("/payees").text

    assert "Name these" in text
    assert 'value="Zabka"' in text  # suggested from the most common spelling
    assert "Name 2 transactions" in text
    assert "ZABKA Z8540 K.2" in text and "ZABKA Z8540 K.1" in text
    assert 'name="key" value="zabka"' in text


def test_name_preview_shows_what_would_change_without_writing(client):
    _seed(
        _txn("1", "ZABKA Z1"), _txn("2", "ZABKA Z2"), _txn("3", "ZABKA Z3", payee="X")
    )

    text = client.get("/payees/name-preview?key=zabka&name=Żabka").text

    assert "<strong>2</strong>" in text  # the row that has a payee is left out
    assert "2 bank spellings" in text
    assert "ZABKA Z1" in text and "ZABKA Z3" not in text
    assert _payees_by_id() == {"1": "", "2": "", "3": "X"}


def test_name_preview_rejects_a_blank_name_and_a_stale_group(client):
    _seed(_txn("1", "ZABKA"))

    blank = client.get("/payees/name-preview?key=zabka&name=%20")
    stale = client.get("/payees/name-preview?key=nothing&name=Foo")

    assert "enter a payee name" in blank.text
    assert "Nothing left to name" in stale.text


def test_naming_a_group_names_rows_remembers_spellings_and_keeps_description(client):
    _seed(
        _txn("1", "ZABKA Z8540 K.2"),
        _txn("2", "ZABKA Z8540 K.1"),
        _txn("3", "LIDL"),
        _txn("4", "ZABKA Z9", payee="Keep me"),
    )

    response = client.post("/payees/name", data={"key": "zabka", "name": " Żabka "})

    assert response.status_code == 200
    assert _payees_by_id() == {"1": "Żabka", "2": "Żabka", "3": "", "4": "Keep me"}
    assert {t.description for t in read_ledger(config.LEDGER_PATH)} >= {
        "ZABKA Z8540 K.2",
        "ZABKA Z8540 K.1",
    }
    book = read_payee_book(config.PAYEE_ALIASES_PATH)
    assert {a.spelling for a in book.aliases} == {"ZABKA Z8540 K.2", "ZABKA Z8540 K.1"}
    assert {a.payee for a in book.aliases} == {"Żabka"}
    assert _toast(response) == "Named 2 transactions as Żabka"
    assert 'hx-swap-oob="true"' in response.text  # the page region refreshes


def test_naming_with_a_blank_name_changes_nothing(client):
    _seed(_txn("1", "ZABKA"))

    response = client.post("/payees/name", data={"key": "zabka", "name": ""})

    assert "enter a payee name" in response.text
    assert _payees_by_id() == {"1": ""}
    assert read_payee_book(config.PAYEE_ALIASES_PATH).aliases == []


def test_named_payees_appear_in_the_list_with_usage(client):
    _seed(
        _txn("1", "ZABKA Z1", amount="-5"),
        _txn("2", "ZABKA Z2", amount="-7"),
    )
    client.post("/payees/name", data={"key": "zabka", "name": "Żabka"})

    text = client.get("/payees").text

    assert "Żabka" in text
    assert "2 spellings" in text
    assert "100%" in text  # every transaction has a payee now


def test_skipping_a_group_hides_it_and_can_be_undone(client):
    _seed(_txn("1", "ZABKA"), _txn("2", "LIDL"))

    client.post("/payees/skip", data={"key": "zabka"})
    hidden = client.get("/payees").text
    client.post("/payees/unskip")
    restored = client.get("/payees").text

    assert 'name="key" value="zabka"' not in hidden
    assert 'name="key" value="lidl"' in hidden
    assert "1 group left unnamed" in hidden
    assert 'name="key" value="zabka"' in restored


def test_rename_updates_transactions_and_saved_spellings(client):
    _seed(_txn("1", "ZABKA Z1"), _txn("2", "LIDL", payee="Lidl"))
    client.post("/payees/name", data={"key": "zabka", "name": "Zabka"})

    response = client.post("/payees/rename", data={"old": "Zabka", "new": "Żabka"})

    assert response.status_code == 200
    assert _payees_by_id() == {"1": "Żabka", "2": "Lidl"}
    assert [a.payee for a in read_payee_book(config.PAYEE_ALIASES_PATH).aliases] == [
        "Żabka"
    ]
    assert _toast(response) == "Renamed 1 transaction to Żabka"


def test_rename_dialog_and_validation(client):
    _seed(_txn("1", "x", payee="Lidl"))

    form = client.get("/payees/rename?name=Lidl").text
    same = client.post("/payees/rename", data={"old": "Lidl", "new": "Lidl"}).text
    blank = client.post("/payees/rename", data={"old": "Lidl", "new": " "}).text

    assert "Renames 1 transaction" in form
    assert "already its name" in same
    assert "enter a payee name" in blank
    assert _payees_by_id() == {"1": "Lidl"}


def test_merge_preview_counts_what_would_move_then_merge_applies_it(client):
    _seed(
        _txn("1", "a", payee="Lidl"),
        _txn("2", "b", payee="LIDL Polska"),
        _txn("3", "c", payee="Lidl Krakow"),
        _txn("4", "d", payee="Zabka"),
    )
    data = {"names": ["Lidl", "LIDL Polska", "Lidl Krakow"], "name": "Lidl"}

    preview = client.post("/payees/merge-preview", data=data)
    assert "<strong>2</strong> transaction" in preview.text  # the Lidl row stays
    assert _payees_by_id()["2"] == "LIDL Polska"  # preview wrote nothing

    response = client.post("/payees/merge", data=data)

    assert _payees_by_id() == {"1": "Lidl", "2": "Lidl", "3": "Lidl", "4": "Zabka"}
    assert _toast(response) == "Merged into Lidl: 2 transactions"


def test_merge_needs_two_payees_and_a_name(client):
    _seed(_txn("1", "a", payee="Lidl"))

    one = client.post("/payees/merge-preview", data={"names": ["Lidl"], "name": "X"})
    blank = client.post(
        "/payees/merge-preview", data={"names": ["Lidl", "Other"], "name": ""}
    )

    assert "Tick at least two payees" in one.text
    assert "enter a payee name" in blank.text


def test_merge_retargets_saved_spellings(client):
    _seed(_txn("1", "ZABKA Z1"), _txn("2", "LIDL"))
    client.post("/payees/name", data={"key": "zabka", "name": "Zabka"})
    client.post("/payees/name", data={"key": "lidl", "name": "Lidl"})

    client.post("/payees/merge", data={"names": ["Zabka", "Lidl"], "name": "Groceries"})

    book = read_payee_book(config.PAYEE_ALIASES_PATH)
    assert {a.payee for a in book.aliases} == {"Groceries"}


def test_search_filters_by_name_or_bank_spelling_and_sorts(client):
    _seed(
        _txn("1", "ZABKA Z1", payee="Żabka", amount="-5"),
        _txn("2", "LIDL WEGLARSKA", payee="Lidl", amount="-50"),
        _txn("3", "LIDL 2043", payee="Lidl", amount="-50"),
    )

    by_spelling = client.get("/payees?part=table&q=weglarska").text
    by_total = client.get("/payees?part=table&sort=spent&dir=desc").text
    by_name = client.get("/payees?part=table&sort=name&dir=asc").text

    assert "Lidl" in by_spelling and "Żabka" not in by_spelling
    assert by_total.index("Lidl") < by_total.index("Żabka")
    assert by_name.index("Lidl") < by_name.index("Żabka")
    assert 'id="payees-content"' not in by_total  # the table fragment only


def test_transfers_never_show_up_for_naming(client):
    _seed(_txn("1", "TO SAVINGS", type=TransactionType.TRANSFER, transfer_id="T"))

    text = client.get("/payees").text

    assert 'name="key"' not in text


def test_nav_links_to_the_payees_page(client):
    assert 'href="/payees"' in client.get("/dashboard").text


def test_names_with_quotes_survive_the_rename_button_and_the_dialog(client):
    _seed(_txn("1", "x", payee='Dom "Pod Lipą" & Co'))

    page = client.get("/payees").text
    dialog = client.get("/payees/rename", params={"name": 'Dom "Pod Lipą" & Co'}).text

    assert "\\u0022Pod Lip" in page or "&#34;Pod Lip" in page  # encoded, not raw
    assert "Renames 1 transaction" in dialog
    client.post("/payees/rename", data={"old": 'Dom "Pod Lipą" & Co', "new": "Dom"})
    assert _payees_by_id() == {"1": "Dom"}


def test_table_shows_spent_and_received_in_separate_columns(client):
    _seed(
        _txn("1", "Shop", payee="Shop", amount="-40"),
        _txn("2", "Shop", payee="Shop", amount="15", type=TransactionType.INCOME),
        _txn(
            "3",
            "Employer",
            payee="Employer",
            amount="3000",
            type=TransactionType.INCOME,
        ),
    )

    text = client.get("/payees").text

    assert "Received" in text and "Spent" in text

    def row(name):
        return next(r for r in text.split("<tr>") if f'value="{name}"' in r)

    assert "40.00" in row("Shop") and "15.00" in row("Shop")
    assert "3,000.00" in row("Employer")
    assert "—" in row("Employer")  # nothing spent


def test_payees_sort_by_received(client):
    _seed(
        _txn("1", "a", payee="Small", amount="10", type=TransactionType.INCOME),
        _txn("2", "b", payee="Big", amount="900", type=TransactionType.INCOME),
    )

    text = client.get("/payees?part=table&sort=received&dir=desc").text

    assert text.index("Big") < text.index("Small")


def test_queue_and_preview_show_what_was_spent_and_received(client):
    _seed(
        _txn("1", "ACME", amount="-10"),
        _txn("2", "ACME", amount="90", type=TransactionType.INCOME),
    )

    page = client.get("/payees").text
    preview = client.get("/payees/name-preview?key=acme&name=Acme").text

    assert "10.00 spent" in page and "90.00 received" in page
    assert "10.00 spent" in preview and "90.00 received" in preview


def _order(text, *names):
    """Positions of each payee's checkbox in the list, to compare row order."""
    return [text.index(f'value="{name}"') for name in names]


def _two_payees():
    # Zabka has more transactions and more spending; Lidl comes first by name.
    _seed(
        _txn("1", "a", payee="Zabka", amount="-5"),
        _txn("2", "b", payee="Zabka", amount="-5"),
        _txn("3", "c", payee="Lidl", amount="-1"),
    )


def test_the_list_defaults_to_name_a_to_z(client):
    _two_payees()

    text = client.get("/payees").text

    lidl, zabka = _order(text, "Lidl", "Zabka")
    assert lidl < zabka


def test_a_chosen_sort_is_remembered_on_the_next_visit(client):
    _two_payees()

    client.get("/payees?sort=count&dir=desc")
    later = client.get("/payees").text

    lidl, zabka = _order(later, "Lidl", "Zabka")
    assert zabka < lidl  # most transactions first, as chosen earlier
    assert 'id="payee-sort" name="sort" value="count"' in later


def test_a_sort_in_the_url_beats_the_remembered_one(client):
    _two_payees()
    client.get("/payees?sort=count&dir=desc")

    text = client.get("/payees?sort=name&dir=desc").text

    lidl, zabka = _order(text, "Lidl", "Zabka")
    assert zabka < lidl  # Z to A, as asked now
    again = client.get("/payees").text
    assert _order(again, "Zabka", "Lidl")[0] < _order(again, "Zabka", "Lidl")[1]


def test_searching_and_swapping_the_list_keep_the_remembered_sort(client):
    _two_payees()
    client.get("/payees?sort=count&dir=desc")

    fragment = client.get("/payees?part=table&q=").text

    lidl, zabka = _order(fragment, "Lidl", "Zabka")
    assert zabka < lidl


def test_a_new_sort_without_a_direction_uses_its_own_natural_direction(client):
    _two_payees()
    client.get("/payees?sort=name&dir=asc")  # remembers "asc"

    text = client.get("/payees?sort=count").text

    lidl, zabka = _order(text, "Lidl", "Zabka")
    assert zabka < lidl  # count's natural order is biggest first, not the old "asc"


def test_unknown_remembered_values_fall_back_to_name_a_to_z(client):
    _two_payees()
    client.cookies.set("payee_sort", "bogus")
    client.cookies.set("payee_dir", "sideways")

    text = client.get("/payees").text

    lidl, zabka = _order(text, "Lidl", "Zabka")
    assert lidl < zabka


def test_changes_refresh_the_list_in_the_remembered_order(client):
    _two_payees()
    client.get("/payees?sort=count&dir=desc")

    response = client.post("/payees/rename", data={"old": "Lidl", "new": "Aldi"})

    aldi, zabka = _order(response.text, "Aldi", "Zabka")
    assert zabka < aldi  # still most transactions first, not renamed-name order
