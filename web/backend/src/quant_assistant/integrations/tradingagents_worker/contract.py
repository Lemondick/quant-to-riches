"""Strict JSON contracts shared by the API and the isolated standard-library worker."""

ROLES = ("bull", "bear", "reviewer")


def validate_role(value, role, request):
    if not isinstance(value, dict):
        raise ValueError("invalid_output")
    common = {"symbol", "data_date"}
    expected = common | (
        {"summary", "disagreements", "questions"} if role == "reviewer" else {"points"}
    )
    if set(value) != expected:
        raise ValueError("invalid_output")
    if any(value[key] != request[key] for key in common):
        raise ValueError("invalid_output")
    valid_ids = {f["fact_id"] for f in request["facts"]}

    def text(item, maximum=500):
        if not isinstance(item, str) or not item.strip() or len(item) > maximum:
            raise ValueError("invalid_output")

    def points(items):
        if not isinstance(items, list) or not 1 <= len(items) <= 3:
            raise ValueError("invalid_output")
        for item in items:
            if not isinstance(item, dict) or set(item) != {"text", "fact_ids"}:
                raise ValueError("invalid_output")
            text(item["text"])
            refs = item["fact_ids"]
            if (
                not isinstance(refs, list)
                or not 1 <= len(refs) <= 15
                or any(not isinstance(ref, str) or ref not in valid_ids for ref in refs)
            ):
                raise ValueError("invalid_output")

    if role == "reviewer":
        text(value["summary"], 800)
        points(value["disagreements"])
        questions = value["questions"]
        if not isinstance(questions, list) or not 1 <= len(questions) <= 5:
            raise ValueError("invalid_output")
        for item in questions:
            text(item, 300)
    else:
        points(value["points"])
    return value


def validate_report(report, request):
    if not isinstance(report, dict) or set(report) != set(ROLES):
        raise ValueError("invalid_output")
    for role in ROLES:
        validate_role(report[role], role, request)
    return report
