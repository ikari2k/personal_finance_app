"""Integration tests for the payee management view."""

import json
import re
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
        # A row as a mapping with one payee field ("Place") would import it.
        "payee_fields": {"Place": description} if description.strip() else {},
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
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(
        _txn("1", "ZABKA Z8540 K.2"),
        _txn("2", "ZABKA Z8540 K.1"),
        _txn("3", "LIDL WEGLARSKA"),
    )

    text = client.get(
        "/payees/suggestions?tab=multiple"
    ).text  # Żabka has two spellings

    assert "Name these" in text
    assert 'value="Zabka"' in text  # suggested from the most common spelling
    assert "Name 2 transactions" in text
    assert "ZABKA Z8540 K.2" in text and "ZABKA Z8540 K.1" in text
    assert 'name="key" value="Place::zabka"' in text
    assert (
        'name="key" value="Place::lidl weglarska"' not in text
    )  # that one is a single


def test_name_preview_shows_what_would_change_without_writing(client):
    _seed(
        _txn("1", "ZABKA Z1"), _txn("2", "ZABKA Z2"), _txn("3", "ZABKA Z3", payee="X")
    )

    text = client.get("/payees/name-preview?key=Place::zabka&name=Żabka").text

    assert "<strong>2</strong>" in text  # the row that has a payee is left out
    assert "<strong>2</strong> rule" in text  # one per distinct description
    assert "ZABKA Z1" in text and "ZABKA Z3" not in text
    assert _payees_by_id() == {"1": "", "2": "", "3": "X"}


def test_name_preview_rejects_a_blank_name_and_a_stale_group(client):
    _seed(_txn("1", "ZABKA"))

    blank = client.get("/payees/name-preview?key=Place::zabka&name=%20")
    stale = client.get("/payees/name-preview?key=Place::nothing&name=Foo")

    assert "enter a payee name" in blank.text
    assert "Nothing left to name" in stale.text


def test_naming_a_group_names_rows_saves_rules_and_keeps_description(client):
    _seed(
        _txn("1", "ZABKA Z8540 K.2"),
        _txn("2", "ZABKA Z8540 K.1"),
        _txn("3", "LIDL"),
        _txn("4", "ZABKA Z9", payee="Keep me"),
    )

    response = client.post(
        "/payees/name", data={"key": "Place::zabka", "name": " Żabka "}
    )

    assert response.status_code == 200
    assert _payees_by_id() == {"1": "Żabka", "2": "Żabka", "3": "", "4": "Keep me"}
    assert {t.description for t in read_ledger(config.LEDGER_PATH)} >= {
        "ZABKA Z8540 K.2",
        "ZABKA Z8540 K.1",
    }
    book = read_payee_book(config.PAYEE_ALIASES_PATH)
    assert {r.fields["Place"] for r in book.rules} == {
        "ZABKA Z8540 K.2",
        "ZABKA Z8540 K.1",
    }
    assert {r.payee for r in book.rules} == {"Żabka"}
    assert _toast(response) == "Named 2 transactions as Żabka"
    assert 'hx-swap-oob="true"' in response.text  # the page region refreshes


def test_naming_with_a_blank_name_changes_nothing(client):
    _seed(_txn("1", "ZABKA"))

    response = client.post("/payees/name", data={"key": "Place::zabka", "name": ""})

    assert "enter a payee name" in response.text
    assert _payees_by_id() == {"1": ""}
    assert read_payee_book(config.PAYEE_ALIASES_PATH).rules == []


def test_named_payees_appear_in_the_list_with_usage(client):
    _seed(
        _txn("1", "ZABKA Z1", amount="-5"),
        _txn("2", "ZABKA Z2", amount="-7"),
    )
    client.post("/payees/name", data={"key": "Place::zabka", "name": "Żabka"})

    text = client.get("/payees").text

    assert "Żabka" in text
    assert "2 spellings" in text
    assert "100%" in text  # every transaction has a payee now


def test_skipping_a_group_hides_it_and_can_be_undone(client):
    _seed(_txn("1", "ZABKA"), _txn("2", "LIDL"))

    client.post("/payees/skip", data={"key": "Place::zabka"})
    hidden = client.get("/payees/suggestions").text
    client.post("/payees/unskip")
    restored = client.get("/payees/suggestions").text

    assert 'name="key" value="Place::zabka"' not in hidden
    assert 'name="key" value="Place::lidl"' in hidden
    assert "1 group dismissed" in hidden
    assert 'name="key" value="Place::zabka"' in restored


def test_rename_updates_transactions_and_saved_rules(client):
    _seed(_txn("1", "ZABKA Z1"), _txn("2", "LIDL", payee="Lidl"))
    client.post("/payees/name", data={"key": "Place::zabka", "name": "Zabka"})

    response = client.post("/payees/rename", data={"old": "Zabka", "new": "Żabka"})

    assert response.status_code == 200
    assert _payees_by_id() == {"1": "Żabka", "2": "Lidl"}
    assert [r.payee for r in read_payee_book(config.PAYEE_ALIASES_PATH).rules] == [
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


def test_merge_retargets_saved_rules(client):
    _seed(_txn("1", "ZABKA Z1"), _txn("2", "LIDL"))
    client.post("/payees/name", data={"key": "Place::zabka", "name": "Zabka"})
    client.post("/payees/name", data={"key": "Place::lidl", "name": "Lidl"})

    client.post("/payees/merge", data={"names": ["Zabka", "Lidl"], "name": "Groceries"})

    book = read_payee_book(config.PAYEE_ALIASES_PATH)
    assert {r.payee for r in book.rules} == {"Groceries"}


def test_search_filters_by_name_or_bank_spelling_and_sorts(client):
    _seed(
        _txn("1", "ZABKA Z1", payee="Żabka", amount="-5"),
        _txn("2", "LIDL WEGLARSKA", payee="Lidl", amount="-50"),
        _txn("3", "LIDL 2043", payee="Lidl", amount="-50"),
    )

    by_spelling = client.get("/payees?part=table&q=weglarska").text
    by_total = client.get("/payees?part=table&q=&sort=spent&dir=desc").text
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

    page = client.get("/payees/suggestions").text
    preview = client.get("/payees/name-preview?key=Place::acme&name=Acme").text

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


# ---------------------------------------------------------------------------
# Naming with fields
# ---------------------------------------------------------------------------


def _web(id_, description, site, **kw):
    return _txn(id_, description, payee_fields={"Web address": site}, **kw)


def test_the_dialog_offers_only_the_configured_payee_fields_with_the_default_ticked(
    client,
):
    _seed(_web("1", "PayU", "cinema-city.pl"), _web("2", "PayU", "cinema-city.pl"))

    text = client.get(
        "/payees/name-preview?key=Web address::cinema city&name=Cinema City"
    ).text

    assert 'name="fields" value="Web address"' in text
    assert 'name="fields" value="Description"' in text  # always offered
    assert 'name="fields" value="Counterparty account"' not in text
    assert 'value="Web address"\n               checked' in text
    assert "on 2 of 2 rows" in text


def test_the_dialog_previews_a_changed_choice_of_fields(client):
    _seed(
        _payu("1", "cinema-city.pl"),
        _payu("2", "allegro.pl"),
        _payu("3", "allegro.pl"),
    )
    url = "/payees/name-preview?key=Merchant::payu&name=PayU&chosen=1"

    def read(extra):
        return " ".join(client.get(url + extra).text.split())

    by_merchant = read("&fields=Merchant")
    by_web = read("&fields=Web address")
    both = read("&fields=Merchant&fields=Web address")

    assert "<strong>3</strong> transactions (" in by_merchant  # PayU is on all three
    assert "<strong>3</strong> transactions (" in by_web
    assert "<strong>2</strong> rules" in by_web  # one per web address
    assert "<strong>1</strong> rule " in by_merchant
    assert "Merchant + Web address" in both
    assert _payees_by_id() == {"1": "", "2": "", "3": ""}  # a preview writes nothing


def test_unticking_every_field_asks_for_at_least_one(client):
    _seed(_web("1", "PayU", "cinema-city.pl"))

    text = client.get(
        "/payees/name-preview?key=Web address::cinema city&name=X&chosen=1"
    ).text

    assert "Pick at least one field" in text
    client.post(
        "/payees/name",
        data={"key": "Web address::cinema city", "name": "X", "chosen": "1"},
    )
    assert _payees_by_id() == {"1": ""}


def test_naming_by_web_address_names_other_descriptions_and_saves_a_field_rule(client):
    _seed(
        _web("1", "PayU", "cinema-city.pl"),
        _web("2", "Przelewy24", "cinema-city.pl"),
        _web("3", "PayU", "allegro.pl"),
    )

    response = client.post(
        "/payees/name",
        data={
            "key": "Web address::cinema city",
            "name": "Cinema City",
            "chosen": "1",
            "fields": ["Web address"],
        },
    )

    assert _payees_by_id() == {"1": "Cinema City", "2": "Cinema City", "3": ""}
    [rule] = read_payee_book(config.PAYEE_ALIASES_PATH).rules
    assert rule.fields == {"Web address": "cinema-city.pl"}
    assert _toast(response) == "Named 2 transactions as Cinema City"


def test_a_field_missing_from_the_group_cannot_be_ticked_into_a_rule(client):
    _seed(_txn("1", "ZABKA"))

    text = client.get("/payees/name-preview?key=Place::zabka&name=Zabka").text

    assert 'name="fields" value="Web address"' not in text  # not in these rows
    assert 'name="fields" value="Counterparty account"' not in text


def test_every_payee_field_gets_a_tab_with_its_number_of_suggestions(client):
    _seed(
        _web("1", "PayU", "cinema-city.pl"),
        _web("2", "PayU", "allegro.pl"),
        _txn("3", "ZABKA"),
    )

    text = " ".join(client.get("/payees/suggestions").text.split())

    assert 'Web address <span class="payee-tab-count">2</span>' in text
    assert 'Place <span class="payee-tab-count">1</span>' in text
    assert "Suggest by" in text
    assert "Each suggestion is built from <strong>Web address</strong> alone" in text


def _payu(id_, site, **kw):
    return _txn(
        id_,
        "PayU",
        payee_fields={"Merchant": "PayU", "Web address": site, **kw.pop("fields", {})},
        **kw,
    )


def _shops():
    return [
        _payu("1", "cinema-city.pl", amount="-30"),
        _payu("2", "cinema-city.pl", amount="-20"),
        _payu("3", "allegro.pl", amount="-5", fields={"Title": "Order 1"}),
    ]


def test_each_queue_group_has_a_workspace_that_loads_when_opened(client):
    _seed(*_shops())

    page = client.get("/payees/suggestions").text

    assert 'class="payee-detail"' in page
    assert 'hx-get="/payees/group"' in page and 'hx-trigger="toggle once"' in page
    assert "Split by other fields, all 1 value" in page  # every row says "PayU"


def test_the_workspace_shows_every_payee_column_with_its_values_and_counts(client):
    _seed(*_shops())

    text = client.get("/payees/group?key=Merchant::payu").text

    for label in ("Merchant", "Web address", "Title"):
        assert f'name="fields" value="{label}"' in text
    assert (
        'name="fields" value="Description"' not in text
    )  # the bank text is not a payee field
    assert "cinema-city.pl <b>2×</b>" in text
    assert "allegro.pl <b>1×</b>" in text
    assert "on 1 of 3" in text  # Title is on one row only


def test_the_workspace_lists_every_spelling_not_only_three(client):
    # Store numbers differ, so the seven spellings are one group ("shop").
    _seed(*[_txn(str(i), f"SHOP {i}00 K.{i}") for i in range(7)])

    text = client.get("/payees/group?key=Place::shop").text

    assert "All 7 spellings" in text
    assert all(f"SHOP {i}00 K.{i}" in text for i in range(7))


def test_ticked_columns_define_the_combinations_listed(client):
    _seed(*_shops())

    by_site = client.get(
        "/payees/group?key=Merchant::payu&chosen=1&fields=Web address"
    ).text
    by_both = client.get(
        "/payees/group?key=Merchant::payu&chosen=1&fields=Web address&fields=Title"
    ).text

    assert "cinema-city.pl" in by_site and "allegro.pl" in by_site
    assert "Name 2" in by_site  # cinema-city.pl has two rows
    assert "<th>Web address</th>" in by_both and "<th>Title</th>" in by_both
    assert "Order 1" in by_both
    assert "no value in a ticked column" in " ".join(
        by_both.split()
    )  # 2 rows lack Title


def test_unticking_every_column_asks_for_one(client):
    _seed(*_shops())

    text = client.get("/payees/group?key=Merchant::payu&chosen=1").text

    assert "Pick at least one field" in text


def test_a_combination_is_previewed_as_one_rule_naming_only_its_rows(client):
    _seed(*_shops())
    combo = '{"Merchant": "PayU", "Web address": "cinema-city.pl"}'

    text = client.get(
        "/payees/name-preview",
        params={"key": "Merchant::payu", "name": "Cinema City", "combo": combo},
    ).text
    flat = " ".join(text.split())

    assert "<strong>2</strong> transactions" in flat  # not allegro.pl's row
    assert "<strong>1</strong> rule" in flat
    assert "<strong>Merchant</strong> PayU" in text
    assert "<strong>Web address</strong> cinema-city.pl" in text
    assert 'name="fields"' not in text  # the columns are fixed for a combination
    assert _payees_by_id() == {"1": "", "2": "", "3": ""}


def test_naming_one_combination_leaves_the_others_to_be_named_separately(client):
    _seed(*_shops())

    first = client.post(
        "/payees/name",
        data={
            "key": "Merchant::payu",
            "name": "Cinema City",
            "combo": '{"Web address": "cinema-city.pl"}',
        },
    )
    second = client.post(
        "/payees/name",
        data={
            "key": "Merchant::payu",
            "name": "Allegro",
            "combo": '{"Web address": "allegro.pl"}',
        },
    )

    assert _payees_by_id() == {"1": "Cinema City", "2": "Cinema City", "3": "Allegro"}
    rules = read_payee_book(config.PAYEE_ALIASES_PATH).rules
    assert [(r.fields, r.payee) for r in rules] == [
        ({"Web address": "cinema-city.pl"}, "Cinema City"),
        ({"Web address": "allegro.pl"}, "Allegro"),
    ]
    assert _toast(first) == "Named 2 transactions as Cinema City"
    assert _toast(second) == "Named 1 transaction as Allegro"


def test_the_workspace_stays_open_on_the_same_columns_after_naming_a_combination(
    client,
):
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(*_shops())

    response = client.post(
        "/payees/name",
        data={
            "key": "Merchant::payu",
            "name": "Cinema City",
            "combo": '{"Merchant": "PayU", "Web address": "cinema-city.pl"}',
        },
    )

    assert '<details class="payee-detail" open' in response.text
    assert (
        "<th>Merchant</th>" in response.text and "<th>Web address</th>" in response.text
    )
    assert "allegro.pl" in response.text  # what is left of the group


def test_naming_the_whole_group_still_works_and_closes_the_workspace(client):
    _seed(_payu("1", "cinema-city.pl"), _payu("2", "cinema-city.pl"))

    response = client.post(
        "/payees/name",
        data={
            "key": "Merchant::payu",
            "name": "Cinema City",
            "chosen": "1",
            "fields": ["Web address"],
        },
    )

    assert _payees_by_id() == {"1": "Cinema City", "2": "Cinema City"}
    assert '<details class="payee-detail" open' not in response.text


def test_a_malformed_combo_is_ignored_not_trusted(client):
    _seed(*_shops())

    for bad in ("{not json", "[1, 2]", "{}", '{"Web address": ""}', '{"x": 5}'):
        text = client.get(
            "/payees/name-preview",
            params={"key": "Merchant::payu", "name": "X", "combo": bad},
        ).text
        assert 'name="fields" value="Web address"' in text  # back to the group dialog


def test_a_combination_that_nothing_would_claim_says_so(client):
    _seed(*_shops())

    text = client.get(
        "/payees/name-preview",
        params={
            "key": "Merchant::payu",
            "name": "X",
            "combo": '{"Web address": "nowhere.pl"}',
        },
    ).text

    assert "Nothing would be named" in text


# ---------------------------------------------------------------------------
# "Name these" tabs: single spellings, several spellings, dividing a group
# ---------------------------------------------------------------------------


def _zabka_and_singles():
    return [
        _txn("1", "ZABKA Z8540 K.2"),
        _txn("2", "ZABKA Z8540 K.2"),
        _txn("3", "ZABKA Z8540 K.1"),
        _txn("4", "ZABKA ZC026 K.2"),
        _txn("5", "LIDL WEGLARSKA"),
    ]


def _keys(text):
    return re.findall(r'<input type="hidden" name="key" value="([^"]+)"', text)


def test_the_first_tab_shows_only_single_spelling_proposals_with_counts(client):
    _seed(*_zabka_and_singles())

    text = client.get("/payees/suggestions").text

    assert _keys(text) == ["Place::lidl weglarska"]
    assert 'aria-selected="true"' in text
    flat = " ".join(text.split())
    assert 'Single spelling <span class="payee-tab-count">1</span>' in flat
    assert 'Several spellings <span class="payee-tab-count">1</span>' in flat
    assert "Divide into" not in text  # nothing to divide in a single


def test_the_second_tab_shows_groups_of_several_spellings_with_a_divide_button(client):
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(*_zabka_and_singles())

    text = client.get("/payees/suggestions?tab=multiple").text

    assert _keys(text) == ["Place::zabka"]
    assert "Divide into 3" in text
    assert "Group with similar" not in text


def test_the_chosen_tab_is_remembered_and_applies_to_swaps(client):
    _seed(*_zabka_and_singles())

    fragment = client.get("/payees?part=queue&tab=multiple").text
    later = client.get("/payees/suggestions").text
    after_change = client.post("/payees/skip", data={"key": "Place::zabka"}).text

    assert (
        'name="key" value="Place::zabka"' in fragment
        and 'id="payees-content"' not in fragment
    )
    assert _keys(later) == ["Place::zabka"]  # the next visit opens on the same tab
    assert (
        'hx-swap-oob="true"' in after_change
    )  # and so does the refresh after a change


def test_an_unknown_tab_falls_back_to_the_first(client):
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(*_zabka_and_singles())

    assert _keys(client.get("/payees/suggestions?tab=bogus").text) == [
        "Place::lidl weglarska"
    ]


def test_dividing_a_group_turns_each_spelling_into_its_own_single_proposal(client):
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(*_zabka_and_singles())

    response = client.post("/payees/split", data={"key": "Place::zabka"})

    assert _toast(response) == "Divided into 3 single spellings"
    assert read_payee_book(config.PAYEE_ALIASES_PATH).split == ["Place::zabka"]
    single = client.get("/payees/suggestions?tab=single").text
    assert sorted(_keys(single)) == [
        "Place::exact:zabka z8540 k.1",
        "Place::exact:zabka z8540 k.2",
        "Place::exact:zabka zc026 k.2",
        "Place::lidl weglarska",  # was already a single
    ]
    assert "Group with similar" in single
    assert (
        _keys(client.get("/payees/suggestions?tab=multiple").text) == []
    )  # the group is gone


def test_a_divided_spelling_is_named_on_its_own(client):
    _seed(*_zabka_and_singles())
    client.post("/payees/split", data={"key": "Place::zabka"})

    response = client.post(
        "/payees/name",
        data={"key": "Place::exact:zabka z8540 k.2", "name": "Żabka Rynek"},
    )

    assert _payees_by_id() == {
        "1": "Żabka Rynek",
        "2": "Żabka Rynek",
        "3": "",
        "4": "",
        "5": "",
    }
    assert _toast(response) == "Named 2 transactions as Żabka Rynek"
    [rule] = read_payee_book(config.PAYEE_ALIASES_PATH).rules
    assert rule.fields == {"Place": "ZABKA Z8540 K.2"}  # exactly that value


def test_a_divided_spelling_previews_only_its_own_rows(client):
    _seed(*_zabka_and_singles())
    client.post("/payees/split", data={"key": "Place::zabka"})

    text = " ".join(
        client.get(
            "/payees/name-preview",
            params={"key": "Place::exact:zabka z8540 k.1", "name": "Z"},
        ).text.split()
    )

    assert "<strong>1</strong> transaction" in text
    assert "ZABKA Z8540 K.1" in text and "ZABKA Z8540 K.2" not in text


def test_grouping_with_similar_puts_the_spellings_back_into_one_proposal(client):
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(*_zabka_and_singles())
    client.post("/payees/split", data={"key": "Place::zabka"})

    response = client.post(
        "/payees/unsplit", data={"key": "Place::exact:zabka z8540 k.1"}
    )

    assert _toast(response) == "Grouped with similar"
    assert read_payee_book(config.PAYEE_ALIASES_PATH).split == []
    assert _keys(client.get("/payees/suggestions?tab=multiple").text) == [
        "Place::zabka"
    ]


def test_dividing_twice_is_harmless_and_the_book_remembers_it_once(client):
    _seed(*_zabka_and_singles())

    client.post("/payees/split", data={"key": "Place::zabka"})
    client.post("/payees/split", data={"key": "Place::zabka"})

    assert read_payee_book(config.PAYEE_ALIASES_PATH).split == ["Place::zabka"]


def test_each_tab_explains_when_it_is_empty_and_points_to_the_other(client):
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(_txn("1", "LIDL"))

    multiple = client.get("/payees/suggestions?tab=multiple").text
    client.post(
        "/payees/split", data={"key": "Place::lidl"}
    )  # nothing to divide: still fine

    assert "No groups of several values left in Place" in multiple
    assert "1 single value" in multiple


def test_the_workspace_of_a_divided_spelling_works(client):
    _seed(*_zabka_and_singles())
    client.post("/payees/split", data={"key": "Place::zabka"})

    text = client.get(
        "/payees/group", params={"key": "Place::exact:zabka z8540 k.2"}
    ).text

    assert "on 2 of 2" in text  # only that spelling's two rows
    assert "ZABKA Z8540 K.2" in text and "ZABKA Z8540 K.1" not in text


# ---------------------------------------------------------------------------
# What the workspace offers, and when
# ---------------------------------------------------------------------------


def test_the_workspace_is_offered_only_when_it_shows_something_new(client):
    _seed(
        # one spelling, one payee field: the card already shows everything
        _txn("1", "CZAJCZYK S.C.", payee_fields={"Place": "CZAJCZYK S.C."}),
        # one spelling, no payee field at all
        _txn("2", "LIDL WEGLARSKA"),
    )

    page = client.get("/payees").text

    assert 'class="payee-detail"' not in page
    assert "Split by other fields" not in page


def test_the_workspace_is_offered_when_several_payee_columns_have_values(client):
    _seed(_payu("1", "cinema-city.pl"), _payu("2", "cinema-city.pl"))

    page = " ".join(client.get("/payees/suggestions").text.split())

    assert 'class="payee-detail"' in page
    assert "Split by other fields, all 1 value" in page


def test_the_workspace_is_offered_for_more_spellings_than_the_card_lists(client):
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(*[_txn(str(i), f"SHOP {i}00 K.{i}") for i in range(5)])

    page = " ".join(client.get("/payees/suggestions?tab=multiple").text.split())

    assert 'class="payee-detail"' in page
    assert (
        "All 5 values" in page and "split by column" not in page
    )  # nothing to split by


def test_a_group_of_three_spellings_and_one_column_needs_no_workspace(client):
    client.headers["HX-Current-URL"] = "http://testserver/payees/suggestions"
    _seed(*[_txn(str(i), f"SHOP {i}00 K.{i}") for i in range(3)])

    page = client.get("/payees/suggestions?tab=multiple").text

    assert 'class="payee-detail"' not in page  # the card lists all three already
    assert "Divide into 3" in page


def test_rows_with_no_payee_field_are_not_offered_and_are_counted(client):
    _seed(
        _txn("1", "", payee_fields={}),
        _txn("2", "", payee_fields={}),
        _txn("3", "LIDL"),  # has a field
    )

    page = client.get("/payees/suggestions").text

    assert _keys(page) == ["Place::lidl"]
    assert "2 transactions have no mapped payee field saved" in " ".join(page.split())
    assert "Fill payee fields" in page
    preview = client.get("/payees/name-preview?key=Place::zabka&name=Z").text
    assert "Nothing left to name" in preview  # no group to name
    assert _payees_by_id() == {"1": "", "2": "", "3": ""}


def test_rows_without_a_payee_field_are_left_for_the_fill_tool(client):
    _seed(
        _txn("1", "CZAJCZYK S.C."),
        _txn("2", "CZAJCZYK S.C."),
        _txn("3", "", payee_fields={}),
    )

    named = client.post(
        "/payees/name", data={"key": "Place::czajczyk", "name": "Piekarnia"}
    )

    assert _payees_by_id() == {"1": "Piekarnia", "2": "Piekarnia", "3": ""}
    assert _toast(named) == "Named 2 transactions as Piekarnia"
    page = " ".join(client.get("/payees/suggestions").text.split())
    assert 'name="key" value="Place::czajczyk"' not in page  # nothing left to group
    assert (
        "1 transaction has no mapped payee field saved" in page
    )  # the fill tool's work


def test_the_page_says_how_many_rows_have_no_payee_field_yet(client):
    _seed(
        _txn("1", "CZAJCZYK S.C."),
        _txn("2", "", payee_fields={}),
        _txn("3", "NAMED", payee="X", payee_fields={}),  # named: not counted
    )

    page = " ".join(client.get("/payees/suggestions").text.split())

    assert "1 transaction has no mapped payee field saved" in page
    assert "can only be named by Description" in page
    assert 'class="payee-by"' not in page  # no per-card badge any more


def test_a_card_says_how_many_of_its_rows_have_each_other_field(client):
    _seed(
        _txn("1", "", payee_fields={"Place": "ACME", "Web address": "acme.pl"}),
        _txn("2", "", payee_fields={"Place": "ACME", "Web address": "acme.pl"}),
        _txn("3", "", payee_fields={"Place": "ACME", "Title": "Order"}),
    )

    text = " ".join(client.get("/payees/suggestions").text.split())

    assert "also Web address 2 of 3 · Title 1 of 3" in text
    assert "How many of this group's transactions also have a value" in text


# ---------------------------------------------------------------------------
# Suggestions per payee field
# ---------------------------------------------------------------------------


def _tab_counts(text):
    flat = " ".join(text.split())
    return dict(
        re.findall(
            r'role="tab"[^>]*>\s*([^<]+?)\s*<span class="payee-tab-count">(\d+)', flat
        )
    )


def test_field_tabs_follow_the_import_mappings_order_then_stray_labels(client):
    from app.models.import_mapping import ImportMapping, PayeeFieldColumn
    from app.storage.import_mappings import write_mapping

    write_mapping(
        ImportMapping(
            bank="X",
            columns={"date": 0, "description": 1, "amount": 2},
            payee_fields=[
                PayeeFieldColumn(label="Merchant", column=4),
                PayeeFieldColumn(label="Web address", column=5),
            ],
        ),
        config.IMPORT_MAPPINGS_DIR,
    )
    _seed(_txn("1", "x", payee_fields={"Old field": "ACME"}))

    counts = _tab_counts(client.get("/payees/suggestions").text)

    assert list(counts)[:3] == ["Merchant", "Web address", "Old field"]
    assert counts["Merchant"] == "0" and counts["Old field"] == "1"


def test_each_field_lists_its_own_suggestions_and_a_row_can_be_in_several(client):
    _seed(*_shops())

    by_merchant = _keys(client.get("/payees?part=queue&field=Merchant&tab=single").text)
    by_site = _keys(client.get("/payees?part=queue&field=Web address&tab=single").text)
    by_title = _keys(client.get("/payees?part=queue&field=Title&tab=single").text)

    assert by_merchant == ["Merchant::payu"]  # all three rows say PayU
    assert by_site == ["Web address::cinema city", "Web address::allegro"]
    assert by_title == ["Title::order"]  # only one row has a Title


def test_the_chosen_field_is_remembered_even_with_a_non_ascii_label(client):
    _seed(
        _txn("1", "A", payee_fields={"Place": "ACME"}),
        _txn("2", "B", payee_fields={"Tytuł": "Obiady"}),
    )

    chosen = client.get("/payees?part=queue&field=Tytuł&tab=single")
    later = client.get("/payees/suggestions")

    assert chosen.status_code == 200 and _keys(chosen.text) == ["Tytuł::obiady"]
    assert _keys(later.text) == ["Tytuł::obiady"]  # the next visit opens on it
    assert "Each suggestion is built from <strong>Tytuł</strong>" in " ".join(
        later.text.split()
    )


def test_an_unknown_field_falls_back_to_the_first_one(client):
    _seed(_txn("1", "ZABKA"))

    assert _keys(client.get("/payees?part=queue&field=Nonsense").text) == [
        "Place::zabka"
    ]


def test_naming_under_one_field_takes_the_rows_out_of_the_other_fields(client):
    _seed(*_shops())
    before = _tab_counts(client.get("/payees/suggestions").text)

    client.post(
        "/payees/name",
        data={
            "key": "Web address::cinema city",
            "name": "Cinema City",
            "chosen": "1",
            "fields": ["Web address"],
        },
    )
    after = _tab_counts(client.get("/payees/suggestions").text)

    assert before["Merchant"] == "1" and before["Web address"] == "2"
    assert after["Web address"] == "1"  # only allegro.pl is left
    assert _payees_by_id() == {"1": "Cinema City", "2": "Cinema City", "3": ""}
    # the PayU group in the Merchant tab now holds just the one unnamed row
    merchant = client.get("/payees?part=queue&field=Merchant&tab=single").text
    assert "1 transaction" in " ".join(merchant.split())


def test_the_workspace_shows_how_much_of_the_group_each_other_field_covers(client):
    _seed(*_shops())

    text = " ".join(client.get("/payees/group?key=Merchant::payu").text.split())

    assert "grouped by <strong>Merchant</strong>" in text
    assert "on 3 of 3 (100%)" in text  # Merchant and Web address
    assert "on 1 of 3 (33%)" in text  # Title
    assert 'value="Merchant" checked' in text.replace(
        "\n", " "
    )  # the group's own field


def test_a_rule_can_be_built_from_another_field_than_the_one_suggested_by(client):
    _seed(*_shops())

    client.post(
        "/payees/name",
        data={
            "key": "Merchant::payu",
            "name": "Allegro",
            "combo": '{"Web address": "allegro.pl"}',
        },
    )

    assert _payees_by_id() == {"1": "", "2": "", "3": "Allegro"}
    [rule] = read_payee_book(config.PAYEE_ALIASES_PATH).rules
    assert rule.fields == {"Web address": "allegro.pl"}


def test_with_no_payee_fields_anywhere_the_page_explains_how_to_set_them_up(client):
    text = " ".join(client.get("/payees/suggestions").text.split())

    assert "Suggest by" in text
    assert _tab_counts(text)["Description"] == "0"


def test_show_more_lists_twenty_more_suggestions(client):
    _seed(
        *[_txn(str(i), f"SHOP{chr(65 + i // 26)}{chr(65 + i % 26)}") for i in range(40)]
    )

    first = client.get("/payees?part=queue").text
    more = client.get("/payees?part=queue&limit=40").text

    assert len(_keys(first)) == 20 and "Show 20 more" in first
    assert len(_keys(more)) == 40 and "more</button>" not in more


def test_payee_list_links_to_its_transactions(client):
    _seed(_txn("1", "ZABKA", payee="Żabka & Co"))

    html = client.get("/payees?part=table").text

    assert 'href="/transactions?payee=%C5%BBabka%20%26%20Co&amp;' in html or (
        "/transactions?payee=%C5%BBabka%20%26%20Co&" in html
    )
    shown = client.get(
        "/transactions?payee=%C5%BBabka%20%26%20Co&account_id=&category=&search="
    ).text
    assert "ZABKA" in shown


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def _upload(client, name="Lidl", data=PNG, filename="logo.png"):
    return client.post(
        "/payees/logo", data={"name": name}, files={"file": (filename, data)}
    )


def test_uploaded_logo_shows_on_the_payee_list_and_is_served(client):
    _seed(_txn("1", "LIDL 1", payee="Lidl"))

    response = _upload(client)

    assert response.status_code == 200
    assert "Logo saved for Lidl" in response.headers["HX-Trigger"]
    table = client.get("/payees?part=table").text
    assert 'class="payee-logo ' in table
    url = table.split('class="payee-logo " src="')[1].split('"')[0]
    served = client.get(url.replace("&amp;", "&"))
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["x-content-type-options"] == "nosniff"


def test_logo_upload_rejects_non_images_and_unknown_payees(client):
    _seed(_txn("1", "LIDL 1", payee="Lidl"))

    bad = _upload(client, data=b"not an image", filename="x.png")
    unknown = _upload(client, name="Nobody")

    assert "PNG, JPEG" in bad.text and "HX-Trigger" not in bad.headers
    assert "No transaction has that payee" in unknown.text
    assert client.get("/payees/logo/0123456789abcdef.png").status_code == 404
    assert client.get("/payees/logo/..%2Fledger.csv").status_code == 404


def test_logo_can_be_replaced_and_removed(client):
    _seed(_txn("1", "LIDL 1", payee="Lidl"))
    _upload(client)

    assert "Replace the logo" in client.get("/payees/logo?name=Lidl").text
    removed = client.post("/payees/logo/remove", data={"name": "Lidl"})

    assert "Logo removed for Lidl" in removed.headers["HX-Trigger"]
    assert 'class="payee-logo ' not in client.get("/payees?part=table").text
    assert "Add a logo" in client.get("/payees/logo?name=Lidl").text


def test_rename_and_merge_carry_the_logo(client):
    _seed(_txn("1", "A", payee="Lidl"), _txn("2", "B", payee="Lidl 2"))
    _upload(client)

    client.post("/payees/rename", data={"old": "Lidl", "new": "Lidl Polska"})
    assert 'class="payee-logo ' in client.get("/payees?part=table").text

    client.post(
        "/payees/merge", data={"names": ["Lidl Polska", "Lidl 2"], "name": "Lidl 2"}
    )
    table = client.get("/payees?part=table").text
    assert table.count('class="payee-logo ') == 1


def test_transactions_list_and_payee_filter_show_the_logo(client):
    _seed(_txn("1", "LIDL 1", payee="Lidl"))
    _upload(client)

    rows = client.get("/transactions").text
    matches = client.get("/transactions/payee-search?q=lid").text

    assert "payee-logo-in" in rows and "has-logo" in rows
    assert 'class="payee-logo "' in matches


def test_payees_page_is_the_list_and_naming_is_its_own_page(client):
    _seed(_txn("1", "ZABKA Z1"), _txn("2", "IKEA", payee="IKEA"))

    listing = client.get("/payees").text
    naming = client.get("/payees/suggestions").text

    assert "All payees" in listing and 'id="payee-table"' in listing
    assert 'id="payee-queue-box"' not in listing
    assert 'href="/payees/suggestions"' in listing and "(1 suggestion)" in listing
    assert 'id="payee-queue-box"' in naming and 'id="payee-table"' not in naming
    assert 'href="/payees"' in naming and 'id="payee-dialog"' in naming


def test_an_action_re_renders_the_page_it_came_from(client):
    _seed(_txn("1", "ZABKA Z1"), _txn("2", "IKEA", payee="IKEA"))
    rename = {"old": "IKEA", "new": "Ikea"}

    from_list = client.post(
        "/payees/rename", data=rename, headers={"HX-Current-URL": "http://x/payees"}
    ).text
    from_naming = client.post(
        "/payees/skip",
        data={"key": "Place::zabka"},
        headers={"HX-Current-URL": "http://x/payees/suggestions"},
    ).text

    assert 'id="payee-table"' in from_list and 'id="payee-queue-box"' not in from_list
    assert (
        'id="payee-queue-box"' in from_naming and 'id="payee-table"' not in from_naming
    )


def test_payee_list_can_be_filtered_by_having_a_logo(client):
    _seed(_txn("1", "A", payee="Lidl"), _txn("2", "B", payee="Ikea"))
    _upload(client, name="Lidl")

    everyone = client.get("/payees?part=table").text
    with_logo = client.get("/payees?part=table&logo=with").text
    without = client.get("/payees?part=table&logo=without").text
    junk = client.get("/payees?part=table&logo=banana").text

    assert "Lidl" in everyone and "Ikea" in everyone
    assert "Lidl" in with_logo and "Ikea" not in with_logo
    assert "Ikea" in without and "Lidl" not in without
    assert "Lidl" in junk and "Ikea" in junk  # an unknown value is no filter
    page = client.get("/payees?logo=without").text
    assert '<option value="without" selected>' in page
    assert "&amp;logo=without" in without  # sort links keep the filter
    none = client.get("/payees?part=table&logo=with&q=ikea").text
    assert "No payee matches “ikea” (with a logo)." in none


def test_a_payee_marked_as_having_no_logo_leaves_the_missing_list(client):
    _seed(_txn("1", "A", payee="Lidl"), _txn("2", "B", payee="Ikea"))

    marked = client.post("/payees/logo/none", data={"name": "Ikea"})

    assert "Ikea has no logo" in marked.headers["HX-Trigger"]
    missing = client.get("/payees?part=table&logo=without").text
    none = client.get("/payees?part=table&logo=none").text
    assert "Lidl" in missing and "Ikea" not in missing
    assert "Ikea" in none and "Lidl" not in none
    assert "Needs a logo" in client.get("/payees/logo?name=Ikea").text
    client.post("/payees/logo/none", data={"name": "Ikea", "marked": "0"})
    assert "Ikea" in client.get("/payees?part=table&logo=without").text


def test_the_no_logo_mark_follows_rename_and_is_cleared_by_a_logo(client):
    _seed(_txn("1", "A", payee="Lidl"), _txn("2", "B", payee="Ikea"))
    client.post("/payees/logo/none", data={"name": "Ikea"})

    client.post("/payees/rename", data={"old": "Ikea", "new": "IKEA Poland"})
    assert "IKEA Poland" in client.get("/payees?part=table&logo=none").text

    _upload(client, name="IKEA Poland")
    assert "IKEA Poland" not in client.get("/payees?part=table&logo=none").text
    assert "IKEA Poland" in client.get("/payees?part=table&logo=with").text


def test_a_payee_with_a_logo_cannot_be_marked_as_having_none(client):
    _seed(_txn("1", "A", payee="Lidl"))
    _upload(client)

    refused = client.post("/payees/logo/none", data={"name": "Lidl"})

    assert "remove that first" in refused.text
    assert "Lidl" not in client.get("/payees?part=table&logo=none").text


def test_filters_survive_a_rename(client):
    _seed(
        _txn("1", "ZABKA Z1", payee="Żabka", amount="-5"),
        _txn("2", "LIDL 2043", payee="Lidl", amount="-50"),
    )
    client.get("/payees?part=table&q=lidl")

    response = client.post(
        "/payees/rename",
        data={"old": "Lidl", "new": "Lidl Polska"},
        headers={"HX-Current-URL": "http://testserver/payees"},
    )

    assert "Lidl Polska" in response.text
    assert "Żabka" not in response.text


def test_list_shows_payee_statistics(client):
    _seed(
        _txn("1", "ZABKA Z1", payee="Żabka", amount="-500"),
        _txn("2", "LIDL 1", payee="Lidl", amount="-5"),
        _txn("3", "LIDL 2", payee="Lidl", amount="-5"),
        _txn("4", "OTHER", payee="", amount="-1"),
    )

    html = client.get("/payees").text

    assert "Without a payee" in html and "25.0%" in html
    assert "Most transactions" in html and "Most money spent" in html
