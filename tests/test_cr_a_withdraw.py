"""CR-A: iedzīvotājs atsauc iesniegumu (tracker/CR-A.md)."""

import logging

import pytest

from app import storage

REASON = "Problēma jau ir atrisināta"
# Sēklas dati (app/storage.py SEED)
RECEIVED = "IES-2026-000001"
IN_PROGRESS = "IES-2026-000002"
ANSWERED = "IES-2026-000003"
FORWARDED = "IES-2026-000004"


@pytest.fixture
def seeded(client):
    storage.reset()
    return client


def withdraw(client, submission_id, reason=REASON):
    return client.post(
        f"/submissions/{submission_id}/withdraw", json={"reason": reason}
    )


def status_of(client, submission_id):
    return client.get(f"/submissions/{submission_id}").json()["status"]


def test_cr_a_ac1_received_is_withdrawn(seeded):
    before = seeded.get(f"/submissions/{RECEIVED}").json()

    response = withdraw(seeded, RECEIVED)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "WITHDRAWN"
    # Precizējums: termiņš nemainās
    assert body["dueDate"] == before["dueDate"]
    assert status_of(seeded, RECEIVED) == "WITHDRAWN"


def test_cr_a_ac2_in_progress_is_withdrawn(seeded):
    response = withdraw(seeded, IN_PROGRESS, "Jautājums atrisināts citādi")
    assert response.status_code == 200
    assert response.json()["status"] == "WITHDRAWN"


def test_cr_a_ac3_answered_rejected(seeded):
    response = withdraw(seeded, ANSWERED)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"
    assert status_of(seeded, ANSWERED) == "ANSWERED"


def test_cr_a_ac4_repeated_withdraw_rejected(seeded):
    assert withdraw(seeded, RECEIVED).status_code == 200

    response = withdraw(seeded, RECEIVED)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"
    # Otrs mēģinājums nerada otru audita ierakstu
    actions = [e["action"] for e in seeded.get(f"/submissions/{RECEIVED}/audit").json()]
    assert actions.count("WITHDRAW") == 1


def test_cr_a_ac5_unknown_id(seeded):
    response = withdraw(seeded, "IES-2026-999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"reason": ""},
        {"reason": " " * 20},
        {"reason": "a" * 9},
        {"reason": "a" * 501},
        {"reason": "  " + "a" * 9 + "  "},
    ],
    ids=["missing", "empty", "spaces", "too-short", "too-long", "short-after-strip"],
)
def test_cr_a_ac6_invalid_reason(seeded, payload):
    response = seeded.post(f"/submissions/{RECEIVED}/withdraw", json=payload)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [d["field"] for d in error["details"]] == ["reason"]
    assert status_of(seeded, RECEIVED) == "RECEIVED"


@pytest.mark.parametrize("length", [10, 500])
def test_cr_a_ac6_boundaries_accepted(seeded, length):
    assert withdraw(seeded, RECEIVED, "a" * length).status_code == 200


def test_cr_a_ac7_audit_entry(client, valid_payload):
    created = client.post("/submissions", json=valid_payload).json()

    assert withdraw(client, created["id"]).status_code == 200

    audit = client.get(f"/submissions/{created['id']}/audit").json()
    assert [e["action"] for e in audit] == ["CREATE", "WITHDRAW"]
    assert audit[1]["detail"] == REASON


def test_cr_a_ac8_no_personal_data_in_logs_or_errors(client, valid_payload, caplog):
    caplog.set_level(logging.DEBUG)
    created = client.post("/submissions", json=valid_payload).json()
    sid = created["id"]

    responses = [
        withdraw(client, sid),
        withdraw(client, sid),  # 409
        withdraw(client, "IES-2026-999999"),  # 404
        client.post(f"/submissions/{sid}/withdraw", json={"reason": "īss"}),  # 400
    ]

    sensitive = [
        valid_payload["personalCode"],
        valid_payload["fullName"],
        valid_payload["email"],
        valid_payload["body"],
    ]
    for value in sensitive:
        assert value not in caplog.text
        for response in responses[1:]:
            assert value not in response.text


def test_cr_a_forwarded_rejected_until_po_decides(seeded):
    # PIEŅĒMUMS: CR-A precizējums par FORWARDED ir "Atvērts". Līdz PO atbildei
    # atļauti tikai līgumā nosauktie statusi. Mainīt, kad pieteikums precizēts.
    response = withdraw(seeded, FORWARDED)
    assert response.status_code == 409
    assert status_of(seeded, FORWARDED) == "FORWARDED"


# --- Papildu gadījumi (robi pret pieņemšanas kritērijiem) ---


def audit_actions(client, submission_id):
    return [
        e["action"] for e in client.get(f"/submissions/{submission_id}/audit").json()
    ]


def test_cr_a_ac1_response_is_full_submission(seeded):
    before = seeded.get(f"/submissions/{RECEIVED}").json()

    body = withdraw(seeded, RECEIVED).json()

    # Līgums: 200 atbilde ir pilns Submission. Mainās tikai statuss.
    for field in ("id", "receivedAt", "dueDate", "replyChannel", "topic", "subject"):
        assert body[field] == before[field], field
    assert body["status"] == "WITHDRAWN"


def test_cr_a_ac2_due_date_unchanged(seeded):
    before = seeded.get(f"/submissions/{IN_PROGRESS}").json()

    response = withdraw(seeded, IN_PROGRESS)

    assert response.status_code == 200
    # Precizējums: dueDate nemainās (gan atbildē, gan saglabātajā ierakstā)
    assert response.json()["dueDate"] == before["dueDate"]
    assert (
        seeded.get(f"/submissions/{IN_PROGRESS}").json()["dueDate"] == before["dueDate"]
    )


def test_cr_a_ac3_rejected_has_no_withdraw_audit(seeded):
    assert withdraw(seeded, ANSWERED).status_code == 409
    assert "WITHDRAW" not in audit_actions(seeded, ANSWERED)


def test_cr_a_ac4_repeated_withdraw_keeps_status(seeded):
    assert withdraw(seeded, RECEIVED).status_code == 200
    assert (
        withdraw(seeded, RECEIVED, "Cits iemesls otrajā mēģinājumā").status_code == 409
    )

    assert status_of(seeded, RECEIVED) == "WITHDRAWN"
    audit = seeded.get(f"/submissions/{RECEIVED}/audit").json()
    details = [e["detail"] for e in audit if e["action"] == "WITHDRAW"]
    assert details == [REASON]


@pytest.mark.parametrize("reason", [123, None, True, ["a" * 20], {"t": "a" * 20}])
def test_cr_a_ac6_reason_not_string(seeded, reason):
    response = seeded.post(f"/submissions/{RECEIVED}/withdraw", json={"reason": reason})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [d["field"] for d in error["details"]] == ["reason"]
    assert status_of(seeded, RECEIVED) == "RECEIVED"


def test_cr_a_ac6_no_request_body(seeded):
    # Pieprasījumam nav JSON ķermeņa vispār. Līgumā requestBody ir obligāts, tāpēc
    # kļūda attiecas uz visu pieprasījumu (tāpat kā POST /submissions), nevis `reason`.
    response = seeded.post(f"/submissions/{RECEIVED}/withdraw")

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert [d["field"] for d in error["details"]] == ["request"]
    assert status_of(seeded, RECEIVED) == "RECEIVED"


@pytest.mark.parametrize(
    "reason",
    ["ā" * 10, "š" * 500, "āš" * 250],
    ids=["10-multibyte", "500-multibyte", "500-mixed"],
)
def test_cr_a_ac6_multibyte_boundaries_accepted(seeded, reason):
    # Garums skaitās rakstzīmēs, ne baitos (UTF-8 "ā" ir 2 baiti)
    assert withdraw(seeded, RECEIVED, reason).status_code == 200


@pytest.mark.parametrize(
    "reason", ["ā" * 9, "š" * 501], ids=["9-multibyte", "501-multibyte"]
)
def test_cr_a_ac6_multibyte_out_of_range_rejected(seeded, reason):
    response = withdraw(seeded, RECEIVED, reason)

    assert response.status_code == 400
    assert [d["field"] for d in response.json()["error"]["details"]] == ["reason"]
    assert status_of(seeded, RECEIVED) == "RECEIVED"


def test_cr_a_ac6_rejected_has_no_withdraw_audit(seeded):
    assert withdraw(seeded, RECEIVED, "īss").status_code == 400
    assert "WITHDRAW" not in audit_actions(seeded, RECEIVED)


def test_cr_a_ac7_audit_detail_is_stripped_reason(seeded):
    assert withdraw(seeded, RECEIVED, f"   {REASON}  \n").status_code == 200

    audit = seeded.get(f"/submissions/{RECEIVED}/audit").json()
    withdraw_entries = [e for e in audit if e["action"] == "WITHDRAW"]
    assert len(withdraw_entries) == 1
    assert withdraw_entries[0]["detail"] == REASON


def test_cr_a_ac8_reason_text_not_logged(client, valid_payload, caplog):
    caplog.set_level(logging.DEBUG)
    sid = client.post("/submissions", json=valid_payload).json()["id"]
    # Iedzīvotājs iemeslā var ierakstīt savus datus (sintētiski)
    reason = (
        f"Esmu {valid_payload['fullName']}, {valid_payload['email']}, "
        f"{valid_payload['personalCode']}. Problēma atrisināta."
    )

    response = withdraw(client, sid, reason)
    assert response.status_code == 200
    too_long = withdraw(client, sid, reason + "x" * 500)  # 400 vai 409

    assert reason not in caplog.text
    for value in (
        valid_payload["personalCode"],
        valid_payload["fullName"],
        valid_payload["email"],
        valid_payload["body"],
    ):
        assert value not in caplog.text
        assert value not in too_long.text


def test_cr_a_ac8_update_status_logs_no_personal_data(caplog):
    storage.reset()
    record = storage.SEED[0]
    caplog.set_level(logging.DEBUG)

    assert storage.update_status(RECEIVED, "IN_PROGRESS") is not None

    assert RECEIVED in caplog.text
    for field in ("personalCode", "fullName", "email", "body"):
        assert record[field] not in caplog.text, field
